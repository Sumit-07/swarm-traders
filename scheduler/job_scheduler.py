"""Job scheduler for daily agent wake/sleep times and graph invocations.

Uses APScheduler to trigger the correct sub-graph at the right IST time.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import TRADING_HOURS
from tools.logger import get_agent_logger

logger = get_agent_logger("scheduler")

IST = ZoneInfo("Asia/Kolkata")


class SwarmScheduler:
    def __init__(self, agents: dict, graphs: dict, telegram_bot=None):
        """
        Args:
            agents: dict of {agent_id: agent_instance}
            graphs: dict of {graph_name: compiled_graph}
            telegram_bot: TelegramBot instance
        """
        self.agents = agents
        self.graphs = graphs
        self.telegram = telegram_bot
        self.scheduler = BackgroundScheduler(timezone=IST)
        self._initial_state = {}

    def setup_schedule(self):
        """Register all daily scheduled jobs."""

        # 00:05 — Kite token refresh (runs every day including weekends)
        self.scheduler.add_job(
            self._refresh_kite_token,
            CronTrigger(hour=0, minute=5, timezone=IST),
            id="kite_token_refresh",
            replace_existing=True,
        )

        # 06:55 — System startup (wake agents, morning prep)
        self._add_job("system_startup", self._system_startup, "06:55")

        # 07:00 — Data Agent wakes
        self._add_job("data_agent_wake", self._wake_agent, "07:00",
                       args=["data_agent"])

        # 08:00 — Strategists wake + morning graph
        self._add_job("morning_strategy", self._run_morning_graph, "08:00")

        # 09:00 — Trading agents wake
        self._add_job("analyst_wake", self._wake_agent, "09:00",
                       args=["analyst"])
        self._add_job("risk_agent_wake", self._wake_agent, "09:00",
                       args=["risk_agent"])
        self._add_job("execution_agent_wake", self._wake_agent, "09:00",
                       args=["execution_agent"])

        # 09:30–15:00 — Signal loop every 5 minutes
        self.scheduler.add_job(
            self._run_signal_loop,
            CronTrigger(
                minute="*/5",
                hour="9-14",
                day_of_week="mon-fri",
                timezone=IST,
            ),
            id="signal_loop",
            replace_existing=True,
        )
        # Also run at 15:00 (last signal check)
        self._add_job("signal_loop_final", self._run_signal_loop, "15:00")

        # 09:15–15:20 — Position monitor every 5 minutes
        self.scheduler.add_job(
            self._run_position_monitor,
            CronTrigger(
                minute="*/5",
                hour="9-15",
                day_of_week="mon-fri",
                timezone=IST,
            ),
            id="position_monitor",
            replace_existing=True,
        )

        # 15:20 — Force close intraday positions
        self._add_job("force_close", self._run_force_close, "15:20")

        # 15:30 — Market close, compliance audit
        self._add_job("eod_review", self._run_eod_graph, "15:30")

        # 15:45 — Strategy review
        self._add_job("strategy_review", self._strategy_review, "15:45")

        # 15:50 — Optimizer meeting (post-market learning)
        self._add_job("optimizer_meeting", self._run_optimizer_meeting, "15:50")

        # Weekly Sunday 6 PM — Archive stale learnings
        self.scheduler.add_job(
            self._archive_stale_learnings,
            CronTrigger(hour=18, minute=0, day_of_week="sun", timezone=IST),
            id="weekly_archive",
            replace_existing=True,
        )

        # 17:15 — System sleep
        self._add_job("system_sleep", self._system_sleep, "17:15")

        # ── LT_Advisor jobs ──────────────────────────────────────────────
        # Morning scan — every day at 8:00 AM IST (including weekends)
        self.scheduler.add_job(
            self._lt_morning_scan,
            CronTrigger(hour=8, minute=0, timezone=IST),
            id="lt_morning_scan",
            replace_existing=True,
        )

        # Midday VIX check — weekdays only, 12:30 PM IST
        self._add_job("lt_midday_check", self._lt_midday_check, "12:30")

        # EOD check — weekdays only, 15:45 PM IST
        self._add_job("lt_eod_check", self._lt_eod_check, "15:45")

        # Weekly summary — every Saturday, 10:00 AM IST
        self.scheduler.add_job(
            self._lt_weekly_summary,
            CronTrigger(hour=10, minute=0, day_of_week="sat", timezone=IST),
            id="lt_weekly_summary",
            replace_existing=True,
        )

        logger.info("All daily jobs scheduled")

    def start(self):
        """Start the scheduler."""
        self.setup_schedule()
        self.scheduler.start()
        logger.info("Swarm scheduler started")

    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Swarm scheduler stopped")

    def catchup(self):
        """Manually trigger the full morning sequence.

        Use when the system missed the scheduled startup (e.g. restart at 10 AM).
        Runs: Kite re-auth → wake all agents → morning strategy graph.
        The signal loop and position monitor are already on cron and will
        pick up automatically on the next 5-minute tick.
        """
        logger.info("CATCHUP triggered — running full morning sequence")

        if self.telegram:
            self.telegram.send_message(
                "Catchup initiated. Running: auth → wake → morning strategy..."
            )

        # Step 1: Re-authenticate Kite + wake agents
        self._system_startup()

        # Step 2: Run morning strategy graph
        self._run_morning_graph()

        if self.telegram:
            self.telegram.send_message(
                "Catchup complete. Signal loop will run on next 5-min tick."
            )

    # --- Helper ---

    def _add_job(self, job_id: str, func, time_str: str, args=None):
        """Add a daily cron job at the given IST time (HH:MM)."""
        hour, minute = time_str.split(":")
        self.scheduler.add_job(
            func,
            CronTrigger(
                hour=int(hour),
                minute=int(minute),
                day_of_week="mon-fri",
                timezone=IST,
            ),
            id=job_id,
            replace_existing=True,
            args=args or [],
        )

    def _get_base_state(self) -> dict:
        """Get the initial SwarmState for graph invocations."""
        return {
            "system_mode": "PAPER",
            "current_phase": "",
            "trading_day": datetime.now(IST).strftime("%Y-%m-%d"),
            "market_data_ready": False,
            "last_data_update": "",
            "market_snapshot": {},
            "watchlist_data": {},
            "conservative_strategy": None,
            "risk_strategy": None,
            "strategy_approved": False,
            "strategy_approval_time": None,
            "pending_signals": [],
            "approved_orders": [],
            "rejected_proposals": [],
            "active_positions": [],
            "agent_statuses": {},
            "human_approval_pending": False,
            "human_response": None,
            "error": None,
            "halt_reason": None,
        }

    # --- Scheduled Actions ---

    def _refresh_kite_token(self):
        """00:05 daily — Re-authenticate Kite after midnight token expiry.

        Retries 3 times with 2-minute gaps. Sends CRITICAL alert if all fail.
        """
        import time as _time

        orchestrator = self.agents.get("orchestrator")
        if not orchestrator or not orchestrator.broker:
            logger.info("No broker configured — skipping token refresh.")
            return

        max_retries = 3
        retry_delay = 120  # 2 minutes

        for attempt in range(1, max_retries + 1):
            try:
                from tools.kite_auth import load_or_refresh_token
                from tools.market_data import set_kite_client
                from tools.kite_market_data import build_instrument_cache

                kite = load_or_refresh_token(orchestrator.telegram)
                orchestrator.kite = kite
                set_kite_client(kite)
                orchestrator.broker.set_kite_client(kite)
                build_instrument_cache(kite)
                logger.info("Kite token refreshed successfully (attempt %d).", attempt)
                if self.telegram:
                    self.telegram.send_message(
                        "Kite token refreshed at midnight. Ready for tomorrow."
                    )
                return
            except Exception as e:
                logger.error(
                    "Kite token refresh failed (attempt %d/%d): %s",
                    attempt, max_retries, e,
                )
                if attempt < max_retries:
                    _time.sleep(retry_delay)

        # All retries exhausted — CRITICAL alert
        logger.critical("Kite token refresh FAILED after %d attempts.", max_retries)
        if self.telegram:
            self.telegram.send_message(
                "CRITICAL: Kite token refresh FAILED after 3 attempts.\n"
                "The system will use yfinance fallback data.\n"
                "Live trading is NOT possible until re-authenticated.\n"
                "Send /authenticate to retry manually."
            )

    def _system_startup(self):
        """06:55 — Wake all agents and ensure Kite auth is valid.

        The 00:05 job handles normal daily token refresh, but this serves as
        a fallback for server migrations, restarts, or missed midnight jobs.
        """
        logger.info("System startup initiated (daily wake + auth check)")

        # Step 1: Ensure Kite token is valid (fallback re-auth)
        orchestrator = self.agents.get("orchestrator")
        if orchestrator and orchestrator.broker:
            try:
                from tools.kite_auth import load_or_refresh_token
                from tools.market_data import set_kite_client
                from tools.kite_market_data import build_instrument_cache

                kite = load_or_refresh_token(orchestrator.telegram)
                orchestrator.kite = kite
                set_kite_client(kite)
                orchestrator.broker.set_kite_client(kite)
                build_instrument_cache(kite)
                logger.info("Kite auth verified at startup.")
            except Exception as e:
                logger.error(f"Kite auth failed at startup: {e}")
                if self.telegram:
                    self.telegram.send_message(
                        f"WARNING: Kite auth failed at startup: {e}\n"
                        "System will use yfinance fallback data."
                    )

        # Step 2: Wake all agents (they were put to sleep at 17:15)
        for agent_id, agent in self.agents.items():
            try:
                agent.wake()
            except Exception as e:
                logger.error(f"Failed to wake {agent_id}: {e}")

        if self.telegram:
            self.telegram.send_message("Trading system online. All agents waking up.")

    def _wake_agent(self, agent_id: str):
        """Wake a specific agent from sleep."""
        agent = self.agents.get(agent_id)
        if agent:
            agent.wake()

    def _run_morning_graph(self):
        """08:00 — Run morning strategy selection graph."""
        logger.info("Running morning strategy graph")
        graph = self.graphs.get("morning")
        if not graph:
            logger.error("Morning graph not built")
            return

        state = self._get_base_state()
        state["current_phase"] = "PRE_MARKET"

        try:
            result = graph.invoke(state)
            self._initial_state = result
            logger.info(
                f"Morning graph complete. "
                f"Strategy: {result.get('conservative_strategy', {}).get('strategy', 'N/A')}"
            )
        except Exception as e:
            logger.error(f"Morning graph failed: {e}")

    def _run_signal_loop(self):
        """Every 5 min during market hours — run signal detection graph."""
        graph = self.graphs.get("signal")
        if not graph:
            return

        strategy_name = (
            self._initial_state.get("conservative_strategy", {}).get("strategy", "N/A")
            if self._initial_state else "N/A"
        )
        logger.info(f"Signal loop tick — strategy: {strategy_name}")

        state = {**self._initial_state}
        state["current_phase"] = "MARKET_OPEN"

        try:
            result = graph.invoke(state)
            signals = result.get("pending_signals", [])
            if signals:
                logger.info(f"Signal loop: {len(signals)} signals detected")
            else:
                logger.info("Signal loop: no signals this tick")
        except Exception as e:
            logger.error(f"Signal loop failed: {e}")

    def _run_position_monitor(self):
        """Every 5 min during market hours — check open positions for threshold breaches."""
        monitor = self.agents.get("position_monitor")
        if not monitor:
            return
        try:
            alerts = monitor.monitor_positions()
            if alerts:
                logger.info(f"Position monitor: {alerts} alert(s) sent")
        except Exception as e:
            logger.error(f"Position monitor failed: {e}")

    def _run_force_close(self):
        """15:20 — Force close all intraday positions."""
        logger.info("Force close check")
        graph = self.graphs.get("force_close")
        if not graph:
            return

        state = {**self._initial_state}
        state["current_phase"] = "MARKET_CLOSE"

        try:
            graph.invoke(state)
        except Exception as e:
            logger.error(f"Force close failed: {e}")

    def _run_eod_graph(self):
        """15:30 — Run end-of-day review graph."""
        logger.info("Running EOD review graph")
        graph = self.graphs.get("eod")
        if not graph:
            return

        state = {**self._initial_state}
        state["current_phase"] = "POST_MARKET"

        try:
            graph.invoke(state)
        except Exception as e:
            logger.error(f"EOD graph failed: {e}")

    def _strategy_review(self):
        """15:45 — Strategist reviews today's performance."""
        strategist = self.agents.get("strategist")
        if strategist:
            from agents.message import MessageType
            strategist.on_message(
                type("FakeMsg", (), {
                    "type": MessageType.COMMAND,
                    "payload": {"command": "REVIEW_STRATEGY"},
                    "from_agent": "scheduler",
                    "priority": "NORMAL",
                })()
            )

    def _run_optimizer_meeting(self):
        """15:50 — Run post-market Optimizer meeting."""
        logger.info("Checking optimizer meeting guards...")

        # Guard 1: minimum 2 trades today
        orchestrator = self.agents.get("orchestrator")
        if not orchestrator:
            return

        sqlite = orchestrator.sqlite
        today = datetime.now(IST).strftime("%Y-%m-%d")

        trades_today = sqlite.query(
            "SELECT COUNT(*) as cnt FROM trades "
            "WHERE DATE(entry_time) = :today AND status = 'CLOSED'",
            {"today": today},
        )
        trade_count = trades_today[0]["cnt"] if trades_today else 0

        if trade_count < 2:
            msg = (
                f"No optimizer meeting today — only {trade_count} trade(s) "
                f"completed (minimum 2 required)."
            )
            logger.info(msg)
            if self.telegram:
                self.telegram.send_message(msg)
            return

        # Guard 2: system not HALTED all day
        mode_data = orchestrator.redis.get_state("state:system_mode") or {}
        if mode_data.get("mode") == "HALTED":
            msg = "No optimizer meeting today — system was in HALTED mode."
            logger.info(msg)
            if self.telegram:
                self.telegram.send_message(msg)
            return

        # Guard 3: not already run today
        existing = sqlite.query(
            "SELECT id FROM optimizer_meetings WHERE meeting_date = :today",
            {"today": today},
        )
        if existing:
            msg = "No optimizer meeting today — already ran."
            logger.info(msg)
            if self.telegram:
                self.telegram.send_message(msg)
            return

        # Build meeting state
        logger.info("Starting optimizer meeting. Trades today: %d", trade_count)
        trades = sqlite.get_trades(date=today)
        signals = sqlite.query(
            "SELECT * FROM signals WHERE DATE(created_at) = :today",
            {"today": today},
        )

        # Get strategy info from Redis
        strategy_data = orchestrator.redis.get_state("state:active_strategy") or {}
        snapshot = orchestrator.redis.get_market_data("data:market_snapshot") or {}
        nifty = snapshot.get("nifty", snapshot.get("NIFTY 50", {}))
        vix_data = snapshot.get("indiavix", snapshot.get("INDIA VIX", {}))

        # Calculate P&L
        daily_pnl = sqlite.get_daily_pnl(today) or {}
        conservative_pnl = daily_pnl.get("conservative_pnl", 0) or 0
        risk_pnl = daily_pnl.get("risk_pnl", 0) or 0

        initial_state = {
            "date": today,
            "trade_count": trade_count,
            "conservative_pnl": conservative_pnl,
            "risk_pnl": risk_pnl,
            "regime": strategy_data.get("regime", "unknown"),
            "vix": vix_data.get("ltp", 0) if isinstance(vix_data, dict) else 0,
            "nifty_change_pct": nifty.get("change_pct", 0) if isinstance(nifty, dict) else 0,
            "trades_data": trades,
            "signals_data": signals,
            "strategy_selected": strategy_data.get("strategy", "N/A"),
            "morning_rationale": strategy_data.get("rationale", "N/A"),
            "morning_confidence": strategy_data.get("confidence", "N/A"),
            "risk_strategy": strategy_data.get("risk_strategy", "N/A"),
            "instrument": strategy_data.get("instrument", "N/A"),
        }

        # Run meeting graph
        meeting_graph = self.graphs.get("meeting")
        if not meeting_graph:
            logger.error("Meeting graph not built")
            return

        try:
            meeting_graph.invoke(initial_state)
            logger.info("Optimizer meeting completed successfully.")
        except Exception as e:
            logger.error("Optimizer meeting failed: %s", e)
            # Notification guarantee: always send something to Telegram
            if self.telegram:
                self.telegram.send_message(
                    f"OPTIMIZER REPORT — {today}\n"
                    f"Meeting failed: {str(e)[:200]}\n"
                    f"Conservative: {conservative_pnl:.0f} | "
                    f"Risk: {risk_pnl:.0f}"
                )

    def _archive_stale_learnings(self):
        """Sunday 6 PM — Archive stale learnings from knowledge graph."""
        orchestrator = self.agents.get("orchestrator")
        if not orchestrator:
            return

        from memory.knowledge_graph import archive_stale_learnings
        count = archive_stale_learnings(orchestrator.sqlite)
        if count > 0 and self.telegram:
            active = orchestrator.sqlite.query(
                "SELECT COUNT(*) as cnt FROM learnings WHERE archived = FALSE"
            )
            active_count = active[0]["cnt"] if active else 0
            self.telegram.send_message(
                f"Knowledge graph maintenance: {count} stale learnings archived. "
                f"Active learnings: {active_count}"
            )

    def _get_lt_advisor(self):
        """Lazily create an LTAdvisor instance."""
        orchestrator = self.agents.get("orchestrator")
        if not orchestrator:
            return None
        from agents.lt_advisor.lt_advisor import LTAdvisor
        return LTAdvisor(redis=orchestrator.redis, db=orchestrator.sqlite)

    def _lt_morning_scan(self):
        """08:00 daily — LT_Advisor morning opportunity scan."""
        advisor = self._get_lt_advisor()
        if advisor:
            advisor.run(run_type="MORNING")

    def _lt_midday_check(self):
        """12:30 weekdays — LT_Advisor midday VIX check."""
        advisor = self._get_lt_advisor()
        if advisor:
            advisor.run(run_type="MIDDAY")

    def _lt_eod_check(self):
        """15:45 weekdays — LT_Advisor EOD check."""
        advisor = self._get_lt_advisor()
        if advisor:
            advisor.run(run_type="EOD")

    def _lt_weekly_summary(self):
        """Saturday 10:00 — LT_Advisor weekly summary."""
        advisor = self._get_lt_advisor()
        if advisor:
            advisor.run(run_type="WEEKLY")

    def _system_sleep(self):
        """17:15 — All agents enter sleep mode."""
        logger.info("System entering sleep mode")
        for agent_id, agent in self.agents.items():
            try:
                agent.sleep()
            except Exception as e:
                logger.error(f"Failed to sleep {agent_id}: {e}")

        if self.telegram:
            self.telegram.send_message("Trading system entering sleep mode. Good night.")
