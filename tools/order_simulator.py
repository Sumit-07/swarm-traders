"""Paper trading order simulator.

Simulates realistic order execution with slippage and brokerage.
Used in PAPER mode instead of actual broker API calls.
"""

from datetime import datetime
from uuid import uuid4

from config import SIMULATION
from tools.logger import get_agent_logger

logger = get_agent_logger("order_simulator")


class OrderSimulator:
    SLIPPAGE_PCT = SIMULATION["slippage_pct"]           # 0.05%
    BROKERAGE_PER_ORDER = SIMULATION["brokerage_per_order"]  # INR 20

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
        total_cost = round(fill_price * order["quantity"] + self.BROKERAGE_PER_ORDER, 2)

        result = {
            "order_id": str(uuid4()),
            "symbol": order["symbol"],
            "transaction_type": txn_type,
            "quantity": order["quantity"],
            "requested_price": price,
            "fill_price": fill_price,
            "slippage": slippage,
            "brokerage": self.BROKERAGE_PER_ORDER,
            "total_cost": total_cost,
            "filled_at": datetime.now().isoformat(),
            "status": "FILLED",
        }

        logger.bind(log_type="trade").info(
            f"PAPER {txn_type} {order['symbol']} {order['quantity']}x "
            f"@ {fill_price} (requested {price}, slippage {slippage})"
        )
        return result

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
