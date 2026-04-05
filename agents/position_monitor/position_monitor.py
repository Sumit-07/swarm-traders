"""Position Monitor Agent — watches open positions during market hours.

Runs a pure Python loop every 5 minutes. Zero LLM calls. Checks positions
against strategy-aware thresholds. Sends POSITION_ALERT to Orchestrator
when a threshold is crossed. Never places orders directly.
"""

from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from agents.base_agent import BaseAgent
from agents.message import AgentMessage, MessageType, Priority
from agents.position_monitor.thresholds import get_thresholds, MonitorThresholds
from tools.logger import get_agent_logger

IST = ZoneInfo("Asia/Kolkata")
logger = get_agent_logger("position_monitor")

MARKET_OPEN = dt_time(9, 15)
MONITOR_STOP = dt_time(15, 20)


class PositionMonitorAgent(BaseAgent):
    """Sensor agent — monitors open positions, escalates to Orchestrator."""

    def __init__(self, redis_store, sqlite_store):
        super().__init__("position_monitor", redis_store, sqlite_store)

    def on_start(self):
        self.logger.info("Position Monitor ready. Pure Python, zero LLM calls.")

    def on_stop(self):
        pass

    def on_message(self, message: AgentMessage):
        # Sensor agent — doesn't process incoming messages
        pass

    def run(self, state: dict) -> dict:
        """LangGraph node (unused — monitor runs via scheduler)."""
        return state

    # ── Main entry point (called by scheduler) ──────────────────────────────

    def monitor_positions(self) -> int:
        """Run one full check cycle across all open positions.

        Called every 5 minutes by SwarmScheduler.
        Returns number of alerts sent.
        """
        now = datetime.now(IST)

        if not self._is_monitoring_active(now):
            return 0

        # Guard: system halted
        mode_data = self.redis.get_state("state:system_mode") or {}
        if mode_data.get("mode") == "HALTED":
            return 0

        positions = self._get_open_positions()
        if not positions:
            return 0

        alerts_sent = 0
        for position in positions:
            alert = self._check_position(position, now)
            if alert:
                self._send_alert(alert)
                self._log_alert(alert, position)
                alerts_sent += 1

        # Log the monitoring tick regardless
        self._log_tick(len(positions), alerts_sent, now)

        if alerts_sent:
            self.logger.info(
                "Monitor cycle: %d positions, %d alerts",
                len(positions), alerts_sent,
            )
        return alerts_sent

    # ── Position check ──────────────────────────────────────────────────────

    def _check_position(self, position: dict, now: datetime) -> dict | None:
        """Check a single position against its strategy thresholds.

        Returns an alert dict if any threshold is crossed, else None.
        Stops at first trigger — one alert per cycle per position.
        """
        symbol = position["symbol"]
        trade_id = position.get("trade_id", "")

        # Look up strategy from trades table
        strategy_name = self._get_strategy_name(trade_id)
        if not strategy_name:
            return None

        # Load thresholds
        try:
            thresholds = get_thresholds(strategy_name)
        except KeyError:
            self.logger.warning("No thresholds for %s — skipping.", strategy_name)
            return None

        # Guard: cooldown active
        if self._is_in_cooldown(trade_id, thresholds.cooldown_minutes):
            return None

        # Guard: grace period
        entry_time_str = position.get("entry_time", "")
        minutes_in_trade = self._minutes_since_entry(entry_time_str, now)
        if minutes_in_trade < thresholds.grace_period_minutes:
            return None

        # Get current market data
        tick = self._get_latest_tick(symbol)
        if not tick:
            return None

        current_price = tick.get("ltp") or tick.get("last_price") or tick.get("close", 0)
        entry_price = position.get("entry_price", 0)
        direction = position.get("direction", "LONG")

        if not current_price or not entry_price:
            return None

        # Calculate move from entry (positive = favorable, negative = adverse)
        if direction == "LONG":
            move_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            move_pct = ((entry_price - current_price) / entry_price) * 100

        # Enrich position with looked-up fields for alert building
        position = {
            **position,
            "strategy_name": strategy_name,
            "stop_loss_price": position.get("stop_loss", 0),
            "target_price": position.get("target", 0),
        }

        # Options: use premium-based monitoring
        if thresholds.strategy_type == "options":
            return self._check_options_position(
                position, tick, thresholds, minutes_in_trade, now,
            )

        # Equity: standard threshold checks
        stop_price = position.get("stop_loss_price", 0) or 0
        target_price = position.get("target_price", 0) or 0

        if direction == "LONG":
            stop_distance_total = entry_price - stop_price if stop_price else 0
            stop_distance_remain = current_price - stop_price if stop_price else 0
            target_distance_total = target_price - entry_price if target_price else 0
            target_distance_remain = target_price - current_price if target_price else 0
        else:
            stop_distance_total = stop_price - entry_price if stop_price else 0
            stop_distance_remain = stop_price - current_price if stop_price else 0
            target_distance_total = entry_price - target_price if target_price else 0
            target_distance_remain = current_price - target_price if target_price else 0

        stop_proximity_pct = (
            (stop_distance_remain / stop_distance_total * 100)
            if stop_distance_total else 100
        )
        target_proximity_pct = (
            (target_distance_remain / target_distance_total * 100)
            if target_distance_total else 100
        )

        # 1. Adverse move
        if move_pct < -thresholds.adverse_move_pct:
            return self._build_alert(
                position, tick, "adverse_move",
                f"Down {abs(move_pct):.2f}% from entry "
                f"(threshold: {thresholds.adverse_move_pct}%)",
                abs(move_pct), thresholds, minutes_in_trade,
            )

        # 2. Stop proximity
        if 0 < stop_proximity_pct < thresholds.stop_proximity_pct:
            return self._build_alert(
                position, tick, "stop_proximity",
                f"Only {stop_proximity_pct:.1f}% of stop distance remaining",
                stop_proximity_pct, thresholds, minutes_in_trade,
            )

        # 3. Adverse velocity (single candle)
        last_candle_move = self._get_last_candle_move(symbol, direction)
        if last_candle_move < -thresholds.adverse_velocity_pct:
            return self._build_alert(
                position, tick, "adverse_velocity",
                f"Single candle adverse move: {abs(last_candle_move):.2f}% "
                f"(threshold: {thresholds.adverse_velocity_pct}%)",
                abs(last_candle_move), thresholds, minutes_in_trade,
            )

        # 4. Time warning (intraday only)
        if thresholds.time_warning_minutes > 0:
            time_to_close = self._minutes_to_forced_close(now)
            if 0 < time_to_close <= thresholds.time_warning_minutes:
                return self._build_alert(
                    position, tick, "time_warning",
                    f"{time_to_close} min to forced close at 3:20 PM. "
                    f"P&L: {move_pct:+.2f}%",
                    time_to_close, thresholds, minutes_in_trade,
                )

        # 5. Favorable move
        if move_pct > thresholds.favorable_move_pct:
            return self._build_alert(
                position, tick, "favorable_move",
                f"Up {move_pct:.2f}% from entry "
                f"(threshold: {thresholds.favorable_move_pct}%). "
                f"Consider locking gains.",
                move_pct, thresholds, minutes_in_trade,
            )

        # 6. Target proximity
        if 0 < target_proximity_pct < thresholds.target_proximity_pct:
            return self._build_alert(
                position, tick, "target_proximity",
                f"Only {target_proximity_pct:.1f}% of target distance remaining.",
                target_proximity_pct, thresholds, minutes_in_trade,
            )

        # 7. Favorable velocity
        if last_candle_move > thresholds.favorable_velocity_pct:
            return self._build_alert(
                position, tick, "favorable_velocity",
                f"Single candle surge: {last_candle_move:.2f}% "
                f"(threshold: {thresholds.favorable_velocity_pct}%)",
                last_candle_move, thresholds, minutes_in_trade,
            )

        return None

    def _check_options_position(
        self, position: dict, tick: dict,
        thresholds: MonitorThresholds, minutes_in_trade: int,
        now: datetime,
    ) -> dict | None:
        """Options-specific monitoring — watches premium value."""
        entry_premium = position.get("entry_premium", 0) or position.get("entry_price", 0)
        current_premium = tick.get("ltp") or tick.get("last_price", entry_premium)

        if not entry_premium:
            return None

        premium_change_pct = ((current_premium - entry_premium) / entry_premium) * 100

        # Premium decay
        if premium_change_pct < -thresholds.premium_decay_pct:
            return self._build_alert(
                position, tick, "premium_decay",
                f"Premium down {abs(premium_change_pct):.1f}% from entry "
                f"(threshold: {thresholds.premium_decay_pct}%)",
                abs(premium_change_pct), thresholds, minutes_in_trade,
            )

        # Premium surge
        if premium_change_pct > thresholds.premium_surge_pct:
            return self._build_alert(
                position, tick, "premium_surge",
                f"Premium up {premium_change_pct:.1f}% from entry. "
                f"Consider early exit.",
                premium_change_pct, thresholds, minutes_in_trade,
            )

        # Time warning
        if thresholds.time_warning_minutes > 0:
            time_to_close = self._minutes_to_forced_close(now)
            if 0 < time_to_close <= thresholds.time_warning_minutes:
                return self._build_alert(
                    position, tick, "time_warning",
                    f"{time_to_close} min to 3:20 PM. "
                    f"Premium P&L: {premium_change_pct:+.1f}%",
                    time_to_close, thresholds, minutes_in_trade,
                )

        return None

    # ── Alert builder ───────────────────────────────────────────────────────

    def _build_alert(
        self, position: dict, tick: dict,
        trigger_type: str, trigger_description: str,
        trigger_value: float, thresholds: MonitorThresholds,
        minutes_in_trade: int,
    ) -> dict:
        """Build the structured alert payload sent to Orchestrator."""
        snapshot = self.redis.get_market_data("data:market_snapshot") or {}
        nifty = snapshot.get("nifty", snapshot.get("NIFTY 50", {}))
        vix_data = snapshot.get("indiavix", snapshot.get("INDIA VIX", {}))

        current_price = tick.get("ltp") or tick.get("last_price", 0)

        return {
            "alert_type": "POSITION_ALERT",
            "trigger_type": trigger_type,
            "trigger_value": round(trigger_value, 4),
            "trigger_description": trigger_description,
            "strategy_type": thresholds.strategy_type,
            "cooldown_minutes": thresholds.cooldown_minutes,

            "position": {
                "trade_id": position.get("trade_id", ""),
                "symbol": position["symbol"],
                "direction": position.get("direction", "LONG"),
                "strategy_name": position.get("strategy_name", ""),
                "bucket": position.get("bucket", "conservative"),
                "entry_price": position.get("entry_price", 0),
                "current_price": current_price,
                "entry_time": position.get("entry_time", ""),
                "minutes_in_trade": minutes_in_trade,
                "stop_loss_price": position.get("stop_loss_price", 0),
                "target_price": position.get("target_price", 0),
                "quantity": position.get("quantity", 0),
                "entry_premium": position.get("entry_premium", 0),
                "current_premium": (
                    current_price
                    if thresholds.strategy_type == "options" else 0
                ),
                "original_analyst_note": position.get("analyst_note", ""),
                "original_entry_conditions": position.get("entry_conditions", ""),
            },

            "market_context": {
                "nifty_price": nifty.get("ltp", 0) if isinstance(nifty, dict) else 0,
                "nifty_change": nifty.get("change_pct", 0) if isinstance(nifty, dict) else 0,
                "vix": vix_data.get("ltp", 0) if isinstance(vix_data, dict) else 0,
                "volume_ratio": tick.get("volume_ratio", 1.0),
            },

            "timestamp": datetime.now(IST).isoformat(),
        }

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _send_alert(self, alert: dict) -> None:
        """Send POSITION_ALERT to Orchestrator via BaseAgent messaging."""
        self.send_message(
            to_agent="orchestrator",
            msg_type=MessageType.POSITION_ALERT,
            payload=alert,
            priority=Priority.HIGH,
        )
        self.logger.info(
            "ALERT sent: %s | %s | trigger=%s value=%.2f",
            alert["position"]["symbol"],
            alert["position"]["strategy_name"],
            alert["trigger_type"],
            alert["trigger_value"],
        )

    def _get_open_positions(self) -> list[dict]:
        """Get open positions from Redis."""
        data = self.redis.get_state("state:positions") or {}
        positions = data.get("positions", [])
        if isinstance(positions, list):
            return [p for p in positions if p.get("status") == "OPEN"]
        return []

    def _get_strategy_name(self, trade_id: str) -> str:
        """Look up strategy name from trades table."""
        if not trade_id:
            return ""
        rows = self.sqlite.query(
            "SELECT strategy FROM trades WHERE trade_id = :tid",
            {"tid": trade_id},
        )
        if rows:
            return rows[0].get("strategy", "")
        return ""

    def _get_latest_tick(self, symbol: str) -> dict | None:
        """Get latest tick data from Redis."""
        return self.redis.get_market_data(f"data:watchlist_ticks:{symbol}")

    def _get_last_candle_move(self, symbol: str, direction: str) -> float:
        """Returns % move of the most recent 5-min candle (direction-adjusted).

        Returns 0.0 if candle data not available (safe degradation).
        """
        candle = self.redis.get_market_data(f"data:last_candle:{symbol}")
        if not candle:
            return 0.0
        open_ = candle.get("open", 0)
        close_ = candle.get("close", 0)
        if not open_:
            return 0.0
        raw_move = ((close_ - open_) / open_) * 100
        return raw_move if direction == "LONG" else -raw_move

    def _is_in_cooldown(self, trade_id: str, cooldown_minutes: int) -> bool:
        """Check if this position was alerted recently."""
        rows = self.sqlite.query(
            "SELECT MAX(alerted_at) as last_alert FROM monitor_alerts "
            "WHERE trade_id = :trade_id",
            {"trade_id": trade_id},
        )
        if not rows or not rows[0].get("last_alert"):
            return False
        last_alert = datetime.fromisoformat(rows[0]["last_alert"])
        if last_alert.tzinfo is None:
            last_alert = last_alert.replace(tzinfo=IST)
        return (datetime.now(IST) - last_alert).total_seconds() < cooldown_minutes * 60

    def _minutes_since_entry(self, entry_time: str, now: datetime) -> int:
        """Calculate minutes since position was opened."""
        if not entry_time:
            return 9999  # treat missing entry time as "old enough"
        try:
            entry = datetime.fromisoformat(entry_time)
            if entry.tzinfo is None:
                entry = entry.replace(tzinfo=IST)
            return max(0, int((now - entry).total_seconds() / 60))
        except (ValueError, TypeError):
            return 9999

    def _minutes_to_forced_close(self, now: datetime) -> int:
        """Minutes remaining until 3:20 PM forced close."""
        forced_close = now.replace(hour=15, minute=20, second=0, microsecond=0)
        delta = (forced_close - now).total_seconds()
        return max(0, int(delta / 60))

    def _is_monitoring_active(self, now: datetime) -> bool:
        """Check if we're within market monitoring hours."""
        return MARKET_OPEN <= now.time() <= MONITOR_STOP

    def _log_alert(self, alert: dict, position: dict) -> None:
        """Log alert to SQLite for cooldown tracking and audit."""
        self.sqlite.execute("""
            INSERT INTO monitor_alerts
            (trade_id, symbol, strategy_name, trigger_type, trigger_value,
             trigger_description, alerted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            position.get("trade_id", ""),
            position["symbol"],
            position.get("strategy_name", ""),
            alert["trigger_type"],
            alert["trigger_value"],
            alert["trigger_description"],
            alert["timestamp"],
        ])

    def _log_tick(self, positions_checked: int, alerts_sent: int,
                  now: datetime) -> None:
        """Log monitoring tick to SQLite for audit."""
        self.sqlite.execute("""
            INSERT INTO monitor_ticks
            (checked_at, positions_checked, alerts_sent)
            VALUES (?, ?, ?)
        """, [now.isoformat(), positions_checked, alerts_sent])
