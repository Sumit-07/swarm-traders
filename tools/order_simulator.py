"""Paper trading order simulator.

Simulates realistic order execution with slippage and brokerage.
Used in PAPER mode instead of actual broker API calls.

Supports: entry fills, stop-loss/target monitoring, time-based exits,
position tracking, and P&L calculation.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
from uuid import uuid4

from config import SIMULATION, RISK_LIMITS
from tools.logger import get_agent_logger

logger = get_agent_logger("order_simulator")

INTRADAY_CUTOFF = time(15, 20)


class OrderSimulator:
    SLIPPAGE_PCT = SIMULATION["slippage_pct"]           # 0.05%
    BROKERAGE_PER_ORDER = SIMULATION["brokerage_per_order"]  # INR 20
    STT_INTRADAY_SELL_PCT = SIMULATION["stt_intraday_sell_pct"]  # 0.1%

    def __init__(self):
        self._open_positions: dict[str, dict] = {}  # order_id -> position

    @property
    def open_positions(self) -> list[dict]:
        return list(self._open_positions.values())

    def simulate_fill(self, order: dict) -> dict:
        """Simulate order execution with slippage.

        Args:
            order: {symbol, transaction_type (BUY/SELL), quantity, price, order_type}

        Returns: {
            order_id, symbol, transaction_type, quantity,
            requested_price, fill_price, slippage, brokerage,
            total_cost, filled_at, status
        }
        """
        price = order["price"]
        txn_type = order["transaction_type"]

        # Apply slippage: worse for the trader
        if txn_type == "BUY":
            fill_price = price * (1 + self.SLIPPAGE_PCT)
        else:  # SELL
            fill_price = price * (1 - self.SLIPPAGE_PCT)

        fill_price = round(fill_price, 2)
        slippage = round(abs(fill_price - price) * order["quantity"], 2)
        brokerage = self.BROKERAGE_PER_ORDER
        total_cost = round(fill_price * order["quantity"] + brokerage, 2)

        result = {
            "order_id": str(uuid4()),
            "symbol": order["symbol"],
            "transaction_type": txn_type,
            "quantity": order["quantity"],
            "requested_price": price,
            "fill_price": fill_price,
            "slippage": slippage,
            "brokerage": brokerage,
            "total_cost": total_cost,
            "filled_at": datetime.now(IST).isoformat(),
            "status": "FILLED",
        }

        logger.bind(log_type="trade").info(
            f"PAPER {txn_type} {order['symbol']} {order['quantity']}x "
            f"@ {fill_price} (requested {price}, slippage {slippage})"
        )
        return result

    def open_position(self, fill: dict, direction: str,
                      stop_loss: float, target: float,
                      bucket: str = "conservative") -> dict:
        """Register an open position after a fill.

        Returns: The position dict stored internally.
        """
        position = {
            "order_id": fill["order_id"],
            "symbol": fill["symbol"],
            "direction": direction,
            "entry_price": fill["fill_price"],
            "quantity": fill["quantity"],
            "stop_loss": stop_loss,
            "target": target,
            "bucket": bucket,
            "entry_fees": fill["brokerage"],
            "entry_time": fill["filled_at"],
            "status": "OPEN",
        }
        self._open_positions[fill["order_id"]] = position
        return position

    def simulate_stoploss(self, position: dict,
                          current_price: float) -> dict | None:
        """Check if stop-loss is triggered for a position.

        Args:
            position: {symbol, direction, entry_price, stop_loss, quantity}
            current_price: current market price

        Returns: Fill dict if stop triggered, None otherwise.
        """
        stop_loss = position["stop_loss"]
        direction = position["direction"]

        triggered = False
        if direction == "LONG" and current_price <= stop_loss:
            triggered = True
        elif direction == "SHORT" and current_price >= stop_loss:
            triggered = True

        if not triggered:
            return None

        # Simulate the stop-loss exit
        txn_type = "SELL" if direction == "LONG" else "BUY"
        return self.simulate_fill({
            "symbol": position["symbol"],
            "transaction_type": txn_type,
            "quantity": position["quantity"],
            "price": stop_loss,
            "order_type": "STOPLOSS",
        })

    def simulate_target(self, position: dict,
                        current_price: float) -> dict | None:
        """Check if target is hit for a position.

        Returns: Fill dict if target reached, None otherwise.
        """
        target = position["target"]
        direction = position["direction"]

        triggered = False
        if direction == "LONG" and current_price >= target:
            triggered = True
        elif direction == "SHORT" and current_price <= target:
            triggered = True

        if not triggered:
            return None

        txn_type = "SELL" if direction == "LONG" else "BUY"
        return self.simulate_fill({
            "symbol": position["symbol"],
            "transaction_type": txn_type,
            "quantity": position["quantity"],
            "price": target,
            "order_type": "TARGET",
        })

    def check_exits(self, position: dict, current_price: float,
                    current_time: datetime = None) -> tuple[dict | None, str]:
        """Check all exit conditions for a position in priority order.

        Returns: (fill_dict_or_None, exit_reason)
        Exit reasons: "STOP", "TARGET", "TIME", "NONE"
        """
        # 1. Stop-loss
        stop_fill = self.simulate_stoploss(position, current_price)
        if stop_fill:
            return stop_fill, "CLOSED_STOP"

        # 2. Target
        target_fill = self.simulate_target(position, current_price)
        if target_fill:
            return target_fill, "CLOSED_TARGET"

        # 3. Time-based intraday exit
        if current_time:
            t = current_time.time() if isinstance(current_time, datetime) else current_time
            if t >= INTRADAY_CUTOFF:
                txn_type = "SELL" if position["direction"] == "LONG" else "BUY"
                fill = self.simulate_fill({
                    "symbol": position["symbol"],
                    "transaction_type": txn_type,
                    "quantity": position["quantity"],
                    "price": current_price,
                    "order_type": "TIME_EXIT",
                })
                return fill, "CLOSED_TIME"

        return None, "NONE"

    def close_position(self, order_id: str, exit_fill: dict,
                       exit_reason: str) -> dict:
        """Close a tracked position and calculate P&L.

        Returns: Completed position dict with pnl fields.
        """
        position = self._open_positions.pop(order_id, None)
        if not position:
            return {"error": f"Position {order_id} not found"}

        entry_price = position["entry_price"]
        exit_price = exit_fill["fill_price"]
        quantity = position["quantity"]

        if position["direction"] == "LONG":
            raw_pnl = (exit_price - entry_price) * quantity
        else:
            raw_pnl = (entry_price - exit_price) * quantity

        total_fees = position["entry_fees"] + exit_fill["brokerage"]
        # Add STT on sell side for intraday
        exit_value = exit_price * quantity
        total_fees += exit_value * self.STT_INTRADAY_SELL_PCT

        pnl = round(raw_pnl - total_fees, 2)
        pnl_pct = round((pnl / (entry_price * quantity)) * 100, 4)

        position.update({
            "exit_price": exit_price,
            "exit_time": exit_fill["filled_at"],
            "exit_reason": exit_reason,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "total_fees": round(total_fees, 2),
            "status": exit_reason,
        })

        logger.bind(log_type="trade").info(
            f"CLOSED {position['symbol']} {exit_reason}: "
            f"P&L INR {pnl:+.2f} ({pnl_pct:+.2f}%)"
        )
        return position

    def force_close_all(self, get_price_fn) -> list[dict]:
        """Force close all open positions (EOD or emergency).

        Args:
            get_price_fn: callable(symbol) -> current_price

        Returns: List of closed position dicts.
        """
        closed = []
        for order_id in list(self._open_positions.keys()):
            pos = self._open_positions[order_id]
            price = get_price_fn(pos["symbol"])
            if not price or price <= 0:
                logger.warning(
                    f"No valid price for {pos['symbol']} — skipping force close"
                )
                continue
            txn_type = "SELL" if pos["direction"] == "LONG" else "BUY"
            fill = self.simulate_fill({
                "symbol": pos["symbol"],
                "transaction_type": txn_type,
                "quantity": pos["quantity"],
                "price": price,
                "order_type": "FORCE_CLOSE",
            })
            result = self.close_position(order_id, fill, "CLOSED_EOD")
            closed.append(result)
        return closed
