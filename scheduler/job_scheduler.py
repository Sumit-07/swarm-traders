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

        # 06:55 — System startup
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

        # 15:20 — Force close intraday positions
        self._add_job("force_close", self._run_force_close, "15:20")

        # 15:30 — Market close, compliance audit
        self._add_job("eod_review", self._run_eod_graph, "15:30")

        # 15:45 — Strategy review
        self._add_job("strategy_review", self._strategy_review, "15:45")

        # 17:15 — System sleep
        self._add_job("system_sleep", self._system_sleep, "17:15")

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

    def _system_startup(self):
        """06:55 — Initialize all agents."""
        logger.info("System startup initiated")
        for agent_id, agent in self.agents.items():
            try:
                agent.start()
            except Exception as e:
                logger.error(f"Failed to start {agent_id}: {e}")

        if self.telegram:
            self.telegram.send_message("Trading system online. All agents starting.")

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

        state = {**self._initial_state}
        state["current_phase"] = "MARKET_OPEN"

        try:
            result = graph.invoke(state)
            signals = result.get("pending_signals", [])
            if signals:
                logger.info(f"Signal loop: {len(signals)} signals detected")
        except Exception as e:
            logger.error(f"Signal loop failed: {e}")

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
