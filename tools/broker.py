"""Kite Connect broker wrapper for order operations.

Thin wrapper that delegates to kite_broker.py functions.
Provides the same interface that agents expect (is_authenticated, place_order, etc.)
"""

from datetime import datetime

from tools.logger import get_agent_logger

logger = get_agent_logger("broker")


class KiteBroker:
    """Kite Connect broker wrapper.

    Agents interact with this class. It delegates live order operations
    to kite_broker.py and maintains the authenticated KiteConnect client.
    """

    def __init__(self, api_key: str, api_secret: str, redirect_uri: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.redirect_uri = redirect_uri
        self.kite = None
        self._authenticated = False

    def set_kite_client(self, kite) -> None:
        """Inject an authenticated KiteConnect client (called after auth)."""
        self.kite = kite
        self._authenticated = True
        logger.info("KiteBroker: Kite client set and authenticated.")

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated and self.kite is not None

    def place_order(self, symbol: str, qty: int, order_type: str,
                    price: float, transaction_type: str,
                    product_type: str = "MIS") -> dict:
        """Place an order via Kite Connect.

        Returns: {order_id, status, message}
        """
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        from tools.kite_broker import place_order

        product_map = {"INTRADAY": "MIS", "CNC": "CNC", "MARGIN": "NRML"}
        kite_product = product_map.get(product_type, product_type)

        logger.bind(log_type="trade").info(
            "LIVE ORDER: %s %s %dx @ %.2f (%s, %s)",
            transaction_type, symbol, qty, price, order_type, product_type,
        )

        try:
            order_id = place_order(
                kite=self.kite,
                symbol=symbol,
                transaction_type=transaction_type,
                quantity=qty,
                order_type=order_type,
                price=price,
                product=kite_product,
            )
            logger.bind(log_type="trade").info("ORDER PLACED: %s", order_id)
            return {
                "order_id": order_id,
                "status": "PLACED",
                "message": "",
            }
        except Exception as e:
            logger.error("Order placement failed: %s", e)
            return {"order_id": "", "status": "FAILED", "message": str(e)}

    def place_stoploss_order(self, symbol: str, qty: int,
                             trigger_price: float,
                             transaction_type: str = "SELL",
                             product_type: str = "MIS") -> dict:
        """Place a stop-loss market order.

        Returns: {order_id, status, message}
        """
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        from tools.kite_broker import place_stoploss_order

        product_map = {"INTRADAY": "MIS", "CNC": "CNC", "MARGIN": "NRML"}
        kite_product = product_map.get(product_type, product_type)

        logger.bind(log_type="trade").info(
            "LIVE SL ORDER: %s %s %dx trigger @ %.2f",
            transaction_type, symbol, qty, trigger_price,
        )

        try:
            order_id = place_stoploss_order(
                kite=self.kite,
                symbol=symbol,
                transaction_type=transaction_type,
                quantity=qty,
                trigger_price=trigger_price,
                product=kite_product,
            )
            logger.info("SL ORDER PLACED: %s", order_id)
            return {
                "order_id": order_id,
                "status": "PLACED",
                "message": "",
            }
        except Exception as e:
            logger.error("SL order failed: %s", e)
            return {"order_id": "", "status": "FAILED", "message": str(e)}

    def get_order_status(self, order_id: str) -> dict:
        """Get status of a specific order."""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        from tools.kite_broker import get_order_status

        try:
            status = get_order_status(self.kite, order_id)
            return {
                "order_id": order_id,
                "status": status["status"],
                "filled_qty": status["filled_quantity"],
                "fill_price": status["average_price"],
                "message": "",
            }
        except Exception as e:
            logger.error("Get order status failed: %s", e)
            return {"order_id": order_id, "status": "ERROR", "message": str(e)}

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order."""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        from tools.kite_broker import cancel_order

        success = cancel_order(self.kite, order_id)
        return {
            "order_id": order_id,
            "status": "CANCELLED" if success else "FAILED",
            "message": "",
        }

    def get_positions(self) -> list[dict]:
        """Get all open positions from broker."""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        from tools.kite_broker import get_positions

        try:
            positions = get_positions(self.kite)
            return [
                {
                    "symbol": p["symbol"],
                    "quantity": abs(p["quantity"]),
                    "direction": "LONG" if p["quantity"] > 0 else "SHORT",
                    "avg_price": p["average_price"],
                    "ltp": p["last_price"],
                    "pnl": p["pnl"],
                    "product_type": p["product"],
                }
                for p in positions
            ]
        except Exception as e:
            logger.error("Get positions failed: %s", e)
            return []

    def get_funds(self) -> dict:
        """Get available funds/margin."""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        from tools.kite_broker import get_margins

        try:
            margins = get_margins(self.kite)
            return {
                "total_balance": margins["total"],
                "available_balance": margins["available"],
                "used_margin": margins["used"],
            }
        except Exception as e:
            logger.error("Get funds failed: %s", e)
            return {}

    def exit_position(self, symbol: str, qty: int, direction: str,
                      product_type: str = "MIS") -> dict:
        """Exit an open position by placing an opposite order."""
        txn_type = "SELL" if direction == "LONG" else "BUY"
        return self.place_order(
            symbol=symbol, qty=qty, order_type="MARKET",
            price=0, transaction_type=txn_type,
            product_type=product_type,
        )
