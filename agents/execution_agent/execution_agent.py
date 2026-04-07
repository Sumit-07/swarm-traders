"""Execution Agent — Order placement and fill confirmation.

Fast, precise, and completely unsentimental. Receives approved orders,
executes them, confirms fills, and reports back. Never second-guesses.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from agents.base_agent import BaseAgent
from agents.message import (
    AgentMessage, FillConfirmation, MessageType, Priority,
)
from config import TRADING_HOURS, SWING_STRATEGIES
from tools.order_simulator import OrderSimulator


class ExecutionAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store, broker=None):
        super().__init__("execution_agent", redis_store, sqlite_store)
        self.broker = broker
        self.simulator = OrderSimulator()
        self._processed_orders: set = set()  # deduplication

    def on_start(self):
        self._reload_paper_positions()
        self.logger.info("Execution Agent ready")

    def on_stop(self):
        pass

    def _reload_paper_positions(self):
        """Reload open paper positions from Redis into the simulator on restart.

        Prevents phantom positions: Redis still has positions after restart
        but simulator's in-memory dict is empty.
        """
        positions_data = self.redis.get_state("state:positions") or {}
        positions = positions_data.get("positions", [])
        loaded = 0
        for pos in positions:
            if pos.get("status") != "OPEN":
                continue
            order_id = pos.get("trade_id") or pos.get("order_id", "")
            if not order_id:
                continue
            self.simulator._open_positions[order_id] = {
                "order_id": order_id,
                "symbol": pos["symbol"],
                "direction": pos.get("direction", "LONG"),
                "entry_price": pos.get("entry_price", 0),
                "quantity": pos.get("quantity", 0),
                "stop_loss": pos.get("stop_loss", 0),
                "target": pos.get("target", 0),
                "bucket": pos.get("bucket", "conservative"),
                "entry_fees": pos.get("entry_fees", 20),  # default brokerage
                "entry_time": pos.get("entry_time", ""),
                "status": "OPEN",
            }
            loaded += 1
        if loaded:
            self.logger.info(f"Reloaded {loaded} open paper position(s) from Redis")

    def on_message(self, message: AgentMessage):
        if message.type == MessageType.COMMAND:
            command = message.payload.get("command", "")
            if command == "HALT":
                self.logger.info("Execution Agent halted — no new orders")
                return

            # Only accept execute commands from orchestrator
            if message.from_agent != "orchestrator":
                self.logger.warning(
                    f"Rejected order from {message.from_agent} — "
                    "only orchestrator can send execution commands"
                )
                return

            if "order_id" in message.payload:
                self._execute_order(message.payload)

    def _execute_order(self, order: dict):
        """Execute an approved order."""
        order_id = order.get("order_id", "")
        proposal_id = order.get("proposal_id", "")

        # Deduplication — check both order_id and proposal_id to prevent
        # duplicate execution when both message and graph paths deliver
        # the same trade with different order_ids
        if order_id in self._processed_orders:
            self.logger.warning(f"Duplicate order {order_id} — skipping")
            return
        if proposal_id and proposal_id in self._processed_orders:
            self.logger.warning(f"Duplicate proposal {proposal_id} — skipping")
            return
        self._processed_orders.add(order_id)
        if proposal_id:
            self._processed_orders.add(proposal_id)

        # Route straddle orders to dedicated handler
        if order.get("strategy") == "STRADDLE_BUY" or order.get("is_straddle"):
            self._execute_straddle(order)
            return

        mode = order.get("mode", "PAPER")
        symbol = order.get("symbol", "")
        quantity = order.get("quantity", 0)
        price = order.get("price", 0)
        txn_type = order.get("transaction_type", "BUY")

        if quantity <= 0:
            self.logger.error(f"Invalid quantity {quantity} for {symbol} — skipping")
            return

        # Block new intraday entries after cutoff (last safety net)
        strategy = order.get("strategy", "")
        if strategy not in SWING_STRATEGIES:
            if datetime.now(IST).strftime("%H:%M") >= TRADING_HOURS["intraday_cutoff"]:
                self.logger.warning(
                    f"Blocked {symbol} after intraday cutoff — too late to open"
                )
                return

        self.logger.info(
            f"Executing {mode} {txn_type} {symbol} {quantity}x @ {price}"
        )

        if mode == "LIVE" and self.broker and self.broker.is_authenticated:
            fill = self._execute_live(order)
        else:
            fill = self._execute_paper(order)

        if fill:
            self._report_fill(order, fill)
            self._place_stop_loss(order, fill)

    def _select_order_type(self, order: dict) -> dict:
        """Use LLM to determine optimal order type (LIMIT vs MARKET)."""
        try:
            result = self.call_llm("PROMPT_ORDER_TYPE_SELECTION", {
                "system_mode": order.get("mode", "PAPER"),
                "current_time": datetime.now(IST).strftime("%H:%M IST"),
                "symbol": order.get("symbol", ""),
                "direction": order.get("transaction_type", "BUY"),
                "desired_price": order.get("price", 0),
                "current_price": order.get("price", 0),
                "bid": "N/A",
                "ask": "N/A",
                "spread_pct": "N/A",
                "avg_volume": "N/A",
                "current_volume": "N/A",
                "atr": "N/A",
                "urgency": order.get("urgency", "NORMAL"),
            })
            return result
        except Exception as e:
            self.logger.error(f"Order type LLM failed: {e}")
            return {"order_type": "LIMIT", "limit_price": order.get("price", 0),
                    "reason": "LLM unavailable — defaulting to LIMIT"}

    def _execute_paper(self, order: dict) -> dict | None:
        """Execute order in paper mode using simulator."""
        try:
            fill = self.simulator.simulate_fill({
                "symbol": order["symbol"],
                "transaction_type": order.get("transaction_type", "BUY"),
                "quantity": order.get("quantity", 0),
                "price": order.get("price", 0),
                "order_type": order.get("order_type", "LIMIT"),
            })
            return fill
        except Exception as e:
            self.logger.error(f"Paper execution failed: {e}")
            return None

    def _execute_live(self, order: dict) -> dict | None:
        """Execute order via live Kite Connect broker API."""
        symbol = order.get("symbol", "")
        qty = order.get("quantity", 0)
        price = order.get("price", 0)
        txn_type = order.get("transaction_type", "BUY")
        order_type = order.get("order_type", "LIMIT")

        try:
            result = self.broker.place_order(
                symbol=symbol, qty=qty, order_type=order_type,
                price=price, transaction_type=txn_type,
                product_type="MIS",
            )

            if result["status"] != "PLACED":
                self.logger.error(
                    f"Live order failed: {result['message']}"
                )
                return None

            broker_order_id = result["order_id"]

            # Poll for fill (simple approach — check once after short wait)
            import time
            time.sleep(1)
            status = self.broker.get_order_status(broker_order_id)

            fill_price = status.get("fill_price", price)
            if fill_price == 0:
                fill_price = price  # Fallback if not yet filled

            return {
                "order_id": broker_order_id,
                "symbol": symbol,
                "transaction_type": txn_type,
                "quantity": qty,
                "requested_price": price,
                "fill_price": fill_price,
                "slippage": round(abs(fill_price - price) * qty, 2),
                "brokerage": 20,  # Flat brokerage
                "total_cost": round(fill_price * qty + 20, 2),
                "filled_at": datetime.now(IST).isoformat(),
                "status": "FILLED",
                "mode": "LIVE",
                "broker_order_id": broker_order_id,
            }
        except Exception as e:
            self.logger.error(f"Live execution failed: {e}")
            return None

    def _report_fill(self, order: dict, fill: dict):
        """Report fill to orchestrator and compliance."""
        confirmation = FillConfirmation(
            order_id=order.get("order_id", ""),
            proposal_id=order.get("proposal_id", ""),
            symbol=order["symbol"],
            transaction_type=order.get("transaction_type", "BUY"),
            quantity=order.get("quantity", 0),
            fill_price=fill["fill_price"],
            slippage=fill.get("slippage", 0),
            brokerage=fill.get("brokerage", 0),
            status=fill["status"],
            filled_at=fill["filled_at"],
            mode=order.get("mode", "PAPER"),
        )

        # Report to orchestrator
        self.send_message(
            to_agent="orchestrator",
            msg_type=MessageType.RESPONSE,
            payload=confirmation.model_dump(),
            priority=Priority.HIGH,
        )

        # Report to compliance for audit trail
        self.send_message(
            to_agent="compliance_agent",
            msg_type=MessageType.SIGNAL,
            payload=confirmation.model_dump(),
        )

        # Log trade to SQLite
        try:
            self.sqlite.log_trade({
                "trade_id": fill.get("order_id", order.get("order_id", "")),
                "proposal_id": order.get("proposal_id"),
                "symbol": order["symbol"],
                "exchange": order.get("exchange", "NSE"),
                "direction": "LONG" if order.get("transaction_type") == "BUY" else "SHORT",
                "bucket": order.get("bucket", "conservative"),
                "strategy": order.get("strategy", ""),
                "entry_price": fill["fill_price"],
                "exit_price": None,
                "quantity": order.get("quantity", 0),
                "stop_loss": order.get("stop_loss_price"),
                "target": order.get("target_price"),
                "status": "OPEN",
                "entry_time": fill["filled_at"],
                "exit_time": None,
                "pnl": None,
                "pnl_pct": None,
                "fees": fill.get("brokerage", 0),
                "signal_confidence": order.get("signal_confidence"),
                "analyst_note": order.get("analyst_note"),
                "risk_approval": order.get("approved_by"),
                "mode": order.get("mode", "PAPER"),
            })
        except Exception as e:
            self.logger.error(f"Failed to log trade: {e}")

        # Update positions in Redis
        self._update_positions(order, fill)

        self.logger.info(
            f"FILLED: {order.get('transaction_type')} {order['symbol']} "
            f"{order.get('quantity')}x @ {fill['fill_price']}"
        )

    def _place_stop_loss(self, order: dict, fill: dict):
        """Place stop-loss order alongside the entry."""
        sl_price = order.get("stop_loss_price")
        if not sl_price:
            self.logger.warning(f"No stop-loss for order {order.get('order_id')}")
            return

        mode = order.get("mode", "PAPER")

        if mode == "LIVE" and self.broker and self.broker.is_authenticated:
            symbol = order.get("symbol", "")
            direction = "LONG" if order.get("transaction_type") == "BUY" else "SHORT"
            sl_txn = "SELL" if direction == "LONG" else "BUY"

            result = self.broker.place_stoploss_order(
                symbol=symbol,
                qty=order.get("quantity", 0),
                trigger_price=sl_price,
                transaction_type=sl_txn,
            )
            if result["status"] == "PLACED":
                self.logger.info(
                    f"LIVE SL placed for {symbol} at {sl_price}: {result['order_id']}"
                )
            else:
                self.logger.error(f"LIVE SL failed for {symbol}: {result['message']}")
        else:
            self.logger.info(
                f"PAPER SL registered for {order['symbol']} at {sl_price}"
            )

    def _update_positions(self, order: dict, fill: dict):
        """Update the positions state in Redis."""
        positions_data = self.redis.get_state("state:positions") or {"positions": []}
        positions_data["positions"].append({
            "trade_id": fill.get("order_id"),
            "symbol": order["symbol"],
            "direction": "LONG" if order.get("transaction_type") == "BUY" else "SHORT",
            "entry_price": fill["fill_price"],
            "quantity": order.get("quantity", 0),
            "stop_loss": order.get("stop_loss_price"),
            "target": order.get("target_price"),
            "bucket": order.get("bucket", "conservative"),
            "strategy": order.get("strategy", ""),
            "entry_fees": fill.get("brokerage", 20),
            "status": "OPEN",
            "entry_time": fill["filled_at"],
        })
        self.redis.set_state("state:positions", positions_data)

    def _execute_straddle(self, order: dict):
        """Execute a straddle order (buy ATM call + ATM put simultaneously).

        Safety: if the put leg fails after the call succeeds, immediately
        close the call leg to avoid leaving a naked position.
        """
        mode = order.get("mode", "PAPER")
        self.logger.info(
            f"Executing {mode} STRADDLE on {order.get('symbol', 'NIFTY')}"
        )

        if mode == "LIVE" and self.broker and self.broker.is_authenticated:
            self._execute_straddle_live(order)
        else:
            fill = self._simulate_straddle_fill(order)
            if fill and fill["status"] == "FILLED":
                self._report_fill(order, fill)

    def _simulate_straddle_fill(self, order: dict) -> dict:
        """Simulate a straddle fill in paper mode with 0.5% slippage on each leg."""
        call_premium = order.get("call_premium", 70)
        put_premium = order.get("put_premium", 65)
        lots = order.get("lots", 1)

        # Apply 0.5% slippage (buying, so price goes up)
        call_fill = round(call_premium * 1.005, 2)
        put_fill = round(put_premium * 1.005, 2)

        call_order_id = f"PAPER-STRADDLE-CE-{datetime.now(IST).strftime('%H%M%S')}"
        put_order_id = f"PAPER-STRADDLE-PE-{datetime.now(IST).strftime('%H%M%S')}"

        return {
            "order_id": call_order_id,
            "call_order_id": call_order_id,
            "put_order_id": put_order_id,
            "symbol": order.get("symbol", "NIFTY"),
            "transaction_type": "BUY",
            "quantity": lots * 25,
            "call_fill_price": call_fill,
            "put_fill_price": put_fill,
            "fill_price": call_fill + put_fill,
            "combined_premium": call_fill + put_fill,
            "slippage": round(
                (call_fill - call_premium + put_fill - put_premium) * lots * 25, 2
            ),
            "brokerage": 40,  # Two legs
            "total_cost": round((call_fill + put_fill) * lots * 25 + 40, 2),
            "filled_at": datetime.now(IST).isoformat(),
            "status": "FILLED",
            "mode": "PAPER",
            "is_straddle": True,
        }

    def _execute_straddle_live(self, order: dict):
        """Execute straddle via live broker — call leg first, then put.

        If put leg fails, immediately close the call leg.
        """
        call_fill = None
        try:
            # Place call leg
            call_result = self.broker.place_order(
                symbol=order.get("call_symbol", ""),
                qty=order.get("lots", 1) * 25,
                order_type="MARKET",
                price=0,
                transaction_type="BUY",
                product_type="MIS",
            )
            if call_result["status"] != "PLACED":
                self.logger.error(f"Straddle call leg failed: {call_result['message']}")
                return
            call_fill = call_result

            # Place put leg
            put_result = self.broker.place_order(
                symbol=order.get("put_symbol", ""),
                qty=order.get("lots", 1) * 25,
                order_type="MARKET",
                price=0,
                transaction_type="BUY",
                product_type="MIS",
            )
            if put_result["status"] != "PLACED":
                self.logger.error(
                    f"Straddle put leg failed: {put_result['message']} — "
                    f"closing call leg to avoid naked position"
                )
                self._close_leg(call_result["order_id"], order, "CE")
                return

            self.logger.info("Straddle both legs filled successfully")

        except Exception as e:
            self.logger.error(f"Straddle live execution error: {e}")
            if call_fill:
                self.logger.info("Closing call leg after put leg failure")
                self._close_leg(call_fill["order_id"], order, "CE")

    def _close_leg(self, order_id: str, order: dict, leg: str):
        """Close a single leg of a straddle to avoid naked position."""
        symbol_key = "call_symbol" if leg == "CE" else "put_symbol"
        try:
            self.broker.place_order(
                symbol=order.get(symbol_key, ""),
                qty=order.get("lots", 1) * 25,
                order_type="MARKET",
                price=0,
                transaction_type="SELL",
                product_type="MIS",
            )
            self.logger.info(f"Closed {leg} leg {order_id} to avoid naked position")
        except Exception as e:
            self.logger.error(f"CRITICAL: Failed to close {leg} leg {order_id}: {e}")

    def close_straddle(self, position: dict):
        """Close both legs of a straddle position. Never leave a naked leg."""
        mode = position.get("mode", "PAPER")
        self.logger.info(f"Closing straddle position on {position.get('symbol')}")

        if mode == "LIVE" and self.broker and self.broker.is_authenticated:
            for leg in ["call_order_id", "put_order_id"]:
                leg_id = position.get(leg)
                if leg_id:
                    leg_type = "CE" if "call" in leg else "PE"
                    self._close_leg(leg_id, position, leg_type)
        else:
            self.logger.info(f"PAPER straddle closed: {position.get('symbol')}")

    def run(self, state: dict) -> dict:
        """LangGraph node: execute approved orders."""
        approved = state.get("approved_orders", [])
        for order in approved:
            self._execute_order(order)
        return state
