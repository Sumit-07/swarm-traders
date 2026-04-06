"""Order placement and account management via Kite Connect.

Only used in LIVE trading mode. In PAPER mode, all calls
are routed to order_simulator.py instead.
"""

import os

from tools.logger import get_agent_logger

logger = get_agent_logger("kite_broker")


def _assert_live_mode():
    """Guard — raises if called in PAPER mode."""
    if os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE":
        raise RuntimeError(
            "Order placement called in PAPER mode. "
            "This is a bug — route to order_simulator.py instead."
        )


def place_order(
    kite,
    symbol: str,
    transaction_type: str,
    quantity: int,
    order_type: str = "LIMIT",
    price: float = 0.0,
    trigger_price: float = 0.0,
    product: str = "MIS",
    tag: str = "",
) -> str:
    """Place an order on NSE via Kite Connect.

    Returns order_id string from Kite.
    """
    _assert_live_mode()

    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=(
                kite.TRANSACTION_TYPE_BUY
                if transaction_type == "BUY"
                else kite.TRANSACTION_TYPE_SELL
            ),
            quantity=quantity,
            product=_map_product(kite, product),
            order_type=_map_order_type(kite, order_type),
            price=price if order_type in ("LIMIT", "SL") else None,
            trigger_price=trigger_price if order_type in ("SL", "SL-M") else None,
            tag=tag[:20] if tag else None,
        )
        logger.bind(log_type="trade").info(
            "Order placed: %s %s %s x%d @ %.2f -> order_id=%s",
            transaction_type, symbol, order_type, quantity, price, order_id,
        )
        return str(order_id)

    except Exception as e:
        logger.error("Order placement failed for %s: %s", symbol, e)
        raise


def place_stoploss_order(
    kite,
    symbol: str,
    transaction_type: str,
    quantity: int,
    trigger_price: float,
    price: float = 0.0,
    product: str = "MIS",
) -> str:
    """Place a stop-loss market order."""
    return place_order(
        kite=kite,
        symbol=symbol,
        transaction_type=transaction_type,
        quantity=quantity,
        order_type="SL-M",
        trigger_price=trigger_price,
        price=price or trigger_price * 0.99,
        product=product,
    )


def get_order_status(kite, order_id: str) -> dict:
    """Return current status of an order."""
    _assert_live_mode()
    orders = kite.orders()
    for order in orders:
        if str(order["order_id"]) == str(order_id):
            return {
                "order_id": order_id,
                "status": order["status"],
                "filled_quantity": order["filled_quantity"],
                "average_price": order["average_price"],
                "symbol": order["tradingsymbol"],
            }
    raise ValueError(f"Order {order_id} not found.")


def cancel_order(kite, order_id: str) -> bool:
    """Cancel a pending order. Returns True on success."""
    _assert_live_mode()
    try:
        kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
        logger.info("Order %s cancelled.", order_id)
        return True
    except Exception as e:
        logger.error("Failed to cancel order %s: %s", order_id, e)
        return False


def get_positions(kite) -> list[dict]:
    """Return all open intraday (MIS) positions."""
    _assert_live_mode()
    positions = kite.positions()
    return [
        {
            "symbol": p["tradingsymbol"],
            "quantity": p["quantity"],
            "average_price": p["average_price"],
            "last_price": p["last_price"],
            "pnl": p["pnl"],
            "product": p["product"],
        }
        for p in positions["day"]
        if p["quantity"] != 0
    ]


def get_holdings(kite) -> list[dict]:
    """Return all delivery (CNC) holdings."""
    _assert_live_mode()
    return kite.holdings()


def get_margins(kite) -> dict:
    """Return available margin for equity segment."""
    _assert_live_mode()
    margins = kite.margins(segment="equity")
    return {
        "available": margins["available"]["live_balance"],
        "used": margins["utilised"]["debits"],
        "total": margins["available"]["live_balance"] + margins["utilised"]["debits"],
    }


def _map_order_type(kite, order_type: str) -> str:
    mapping = {
        "LIMIT": kite.ORDER_TYPE_LIMIT,
        "MARKET": kite.ORDER_TYPE_MARKET,
        "SL": kite.ORDER_TYPE_SL,
        "SL-M": kite.ORDER_TYPE_SLM,
    }
    if order_type not in mapping:
        raise ValueError(
            f"Unknown order_type: {order_type}. Use LIMIT, MARKET, SL, or SL-M."
        )
    return mapping[order_type]


def _map_product(kite, product: str) -> str:
    mapping = {
        "MIS": kite.PRODUCT_MIS,
        "CNC": kite.PRODUCT_CNC,
        "NRML": kite.PRODUCT_NRML,
    }
    return mapping.get(product, kite.PRODUCT_MIS)


def slice_order_if_needed(
    symbol:    str,
    quantity:  int,
) -> list[int]:
    """
    Checks if order quantity exceeds NSE freeze limit.
    If so, slices into multiple sub-orders.
    Returns list of quantities to send as separate orders.

    At current capital levels (1-3 lots), this will almost never trigger.
    But it must exist in the architecture for correctness.
    """
    from config import CONTRACT_SPECIFICATIONS

    spec = CONTRACT_SPECIFICATIONS.get(symbol, {})
    freeze_limit = spec.get("freeze_limit", 999999)
    lot_size = spec.get("lot_size", 1)

    if quantity <= freeze_limit:
        return [quantity]

    # Slice into freeze-limit sized chunks, each a valid lot multiple
    slices = []
    remaining = quantity
    while remaining > 0:
        chunk = min(remaining, freeze_limit)
        # Round down to nearest lot
        valid_chunk = (chunk // lot_size) * lot_size
        if valid_chunk == 0:
            break
        slices.append(valid_chunk)
        remaining -= valid_chunk

    return slices
