"""Execution Agent — Order placement and fill confirmation.

Fast, precise, and completely unsentimental. Receives approved orders,
executes them, confirms fills, and reports back. Never second-guesses.
"""

from datetime import datetime

from agents.base_agent import BaseAgent
from agents.message import (
    AgentMessage, FillConfirmation, MessageType, Priority,
)
from tools.order_simulator import OrderSimulator


class ExecutionAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store, broker=None):
        super().__init__("execution_agent", redis_store, sqlite_store)
        self.broker = broker
        self.simulator = OrderSimulator()
        self._processed_orders: set = set()  # deduplication

    def on_start(self):
        self.logger.info("Execution Agent ready")

    def on_stop(self):
        pass

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

        # Deduplication
        if order_id in self._processed_orders:
            self.logger.warning(f"Duplicate order {order_id} — skipping")
            return
        self._processed_orders.add(order_id)

        mode = order.get("mode", "PAPER")
        symbol = order.get("symbol", "")
        quantity = order.get("quantity", 0)
        price = order.get("price", 0)
        txn_type = order.get("transaction_type", "BUY")

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
                "current_time": datetime.now().strftime("%H:%M IST"),
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
        """Execute order via live broker API."""
        # Phase 2 stub — will be implemented in Phase 6
        self.logger.warning("Live execution not yet implemented — falling back to paper")
        return self._execute_paper(order)

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

        self.logger.info(
            f"Stop-loss placed for {order['symbol']} at {sl_price}"
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
            "status": "OPEN",
            "entry_time": fill["filled_at"],
        })
        self.redis.set_state("state:positions", positions_data)

    def run(self, state: dict) -> dict:
        """LangGraph node: execute approved orders."""
        approved = state.get("approved_orders", [])
        for order in approved:
            self._execute_order(order)
        return state
