"""Live position monitor — reconciles broker positions with Redis state.

Runs periodically to:
1. Sync broker positions with local Redis state
2. Check for stop-loss/target triggers on paper positions
3. Detect and flag discrepancies between broker and local state
4. Force-close all positions at intraday cutoff
"""

from datetime import datetime, time

from tools.logger import get_agent_logger

logger = get_agent_logger("position_monitor")

INTRADAY_CUTOFF = time(15, 20)


class PositionMonitor:
    def __init__(self, redis_store, sqlite_store, broker=None, simulator=None):
        self.redis = redis_store
        self.sqlite = sqlite_store
        self.broker = broker
        self.simulator = simulator

    def sync_positions(self) -> dict:
        """Sync broker positions with Redis state.

        Returns: {synced: int, discrepancies: list}
        """
        if not self.broker or not self.broker.is_authenticated:
            return {"synced": 0, "discrepancies": [], "mode": "PAPER"}

        try:
            broker_positions = self.broker.get_positions()
        except Exception as e:
            logger.error(f"Failed to fetch broker positions: {e}")
            return {"synced": 0, "discrepancies": [str(e)]}

        local_data = self.redis.get_state("state:positions") or {"positions": []}
        local_positions = local_data.get("positions", [])

        discrepancies = []
        synced = 0

        # Build lookup of local positions by symbol
        local_by_symbol = {}
        for p in local_positions:
            if p.get("status") == "OPEN":
                local_by_symbol[p["symbol"]] = p

        # Check broker positions against local
        for bp in broker_positions:
            symbol_raw = bp["symbol"].replace("NSE:", "").replace("-EQ", "")
            local = local_by_symbol.get(symbol_raw)

            if local:
                # Update LTP and unrealized P&L
                local["ltp"] = bp.get("ltp", 0)
                local["unrealized_pnl"] = bp.get("pnl", 0)
                synced += 1

                # Check quantity mismatch
                if local.get("quantity") != bp.get("quantity"):
                    discrepancies.append({
                        "symbol": symbol_raw,
                        "issue": "quantity_mismatch",
                        "local_qty": local.get("quantity"),
                        "broker_qty": bp.get("quantity"),
                    })
            else:
                # Position exists at broker but not locally
                discrepancies.append({
                    "symbol": symbol_raw,
                    "issue": "broker_only",
                    "broker_qty": bp.get("quantity"),
                    "broker_pnl": bp.get("pnl"),
                })

        # Check for local positions not at broker
        broker_symbols = {
            bp["symbol"].replace("NSE:", "").replace("-EQ", "")
            for bp in broker_positions
        }
        for symbol, local in local_by_symbol.items():
            if symbol not in broker_symbols:
                discrepancies.append({
                    "symbol": symbol,
                    "issue": "local_only",
                    "local_qty": local.get("quantity"),
                })

        # Save updated positions back to Redis
        self.redis.set_state("state:positions", local_data)

        if discrepancies:
            logger.warning(f"Position discrepancies: {discrepancies}")

        return {"synced": synced, "discrepancies": discrepancies, "mode": "LIVE"}

    def check_paper_exits(self, get_price_fn) -> list[dict]:
        """Check all paper positions for stop-loss/target triggers.

        Args:
            get_price_fn: callable(symbol) -> current_price

        Returns: List of closed position dicts.
        """
        if not self.simulator:
            return []

        closed = []
        for pos in list(self.simulator.open_positions):
            try:
                price = get_price_fn(pos["symbol"])
                fill, reason = self.simulator.check_exits(
                    pos, current_price=price, current_time=datetime.now(),
                )
                if fill:
                    result = self.simulator.close_position(
                        pos["order_id"], fill, reason,
                    )
                    closed.append(result)

                    # Update Redis
                    self._remove_redis_position(pos["symbol"])

                    # Update SQLite
                    self._update_trade_exit(pos, result)

                    logger.bind(log_type="trade").info(
                        f"Paper exit: {pos['symbol']} {reason} "
                        f"P&L: {result.get('pnl', 0):+.2f}"
                    )
            except Exception as e:
                logger.error(f"Error checking exit for {pos['symbol']}: {e}")

        return closed

    def force_close_all(self, get_price_fn=None) -> list[dict]:
        """Force close all open positions (EOD cutoff).

        For LIVE mode: exits via broker market orders.
        For PAPER mode: exits via simulator.

        Returns: List of closed position dicts.
        """
        closed = []

        # Paper positions
        if self.simulator and self.simulator.open_positions:
            if get_price_fn:
                paper_closed = self.simulator.force_close_all(get_price_fn)
                closed.extend(paper_closed)

        # Live positions
        if self.broker and self.broker.is_authenticated:
            try:
                broker_positions = self.broker.get_positions()
                for bp in broker_positions:
                    result = self.broker.exit_position(
                        symbol=bp["symbol"],
                        qty=bp["quantity"],
                        direction=bp["direction"],
                    )
                    closed.append({
                        "symbol": bp["symbol"],
                        "exit_reason": "CLOSED_EOD",
                        "broker_result": result,
                    })
                    logger.bind(log_type="trade").info(
                        f"LIVE force close: {bp['symbol']} {bp['quantity']}x"
                    )
            except Exception as e:
                logger.error(f"Force close live positions failed: {e}")

        # Clear Redis positions
        self.redis.set_state("state:positions", {"positions": []})

        logger.info(f"Force closed {len(closed)} positions")
        return closed

    def get_portfolio_summary(self) -> dict:
        """Get current portfolio summary combining all sources."""
        local_data = self.redis.get_state("state:positions") or {"positions": []}
        positions = [p for p in local_data.get("positions", [])
                     if p.get("status") == "OPEN"]

        total_deployed = sum(
            p.get("entry_price", 0) * p.get("quantity", 0)
            for p in positions
        )
        unrealized_pnl = sum(
            p.get("unrealized_pnl", 0) for p in positions
        )

        return {
            "open_count": len(positions),
            "total_deployed": round(total_deployed, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "positions": positions,
        }

    def _remove_redis_position(self, symbol: str):
        """Remove a closed position from Redis state."""
        data = self.redis.get_state("state:positions") or {"positions": []}
        data["positions"] = [
            p for p in data["positions"]
            if not (p.get("symbol") == symbol and p.get("status") == "OPEN")
        ]
        self.redis.set_state("state:positions", data)

    def _update_trade_exit(self, position: dict, result: dict):
        """Update the trade record in SQLite with exit info."""
        try:
            self.sqlite.update_trade(
                trade_id=position.get("order_id", ""),
                updates={
                    "exit_price": result.get("exit_price", 0),
                    "exit_time": result.get("exit_time", ""),
                    "pnl": result.get("pnl", 0),
                    "pnl_pct": result.get("pnl_pct", 0),
                    "fees": result.get("total_fees", 0),
                    "status": result.get("status", "CLOSED"),
                },
            )
        except Exception as e:
            logger.error(f"Failed to update trade exit: {e}")
