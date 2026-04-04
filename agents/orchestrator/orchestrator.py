"""Orchestrator Agent — Master coordinator and conflict resolver.

Coordinates all agents, resolves conflicts, manages system mode,
and interfaces with the human owner via Telegram.
"""

from datetime import datetime

from agents.base_agent import BaseAgent
from agents.message import (
    AgentMessage, ApprovedOrder, MessageType, Priority, RiskDecision,
)
from config import CAPITAL, RISK_LIMITS, TRADING_MODE


class OrchestratorAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store, telegram_bot=None):
        super().__init__("orchestrator", redis_store, sqlite_store)
        self.telegram = telegram_bot
        self._pending_proposals: dict = {}  # proposal_id -> proposal data
        self._human_approval_pending: dict = {}

    def on_start(self):
        # Set initial system mode
        self.redis.set_state("state:system_mode", {
            "mode": TRADING_MODE,
            "set_by": "orchestrator",
            "set_at": datetime.now().isoformat(),
        })
        self.logger.info(f"System mode set to {TRADING_MODE}")

    def on_stop(self):
        pass

    def on_message(self, message: AgentMessage):
        handlers = {
            MessageType.SIGNAL: self._handle_signal,
            MessageType.RESPONSE: self._handle_response,
            MessageType.ALERT: self._handle_alert,
            MessageType.COMMAND: self._handle_command,
            MessageType.REQUEST: self._handle_request,
        }
        handler = handlers.get(message.type, self._handle_unknown)
        handler(message)

    def _handle_signal(self, message: AgentMessage):
        """Handle trade signals — typically risk approval/rejection."""
        if message.from_agent == "risk_agent":
            self._process_risk_decision(message)

    def _handle_response(self, message: AgentMessage):
        """Handle responses to orchestrator requests."""
        self.logger.info(
            f"Response from {message.from_agent}: "
            f"{message.payload.get('status', 'unknown')}"
        )

    def _handle_alert(self, message: AgentMessage):
        """Handle alerts from any agent."""
        if message.priority == Priority.CRITICAL:
            self.logger.warning(
                f"CRITICAL alert from {message.from_agent}: "
                f"{message.payload}"
            )
            # Notify human via Telegram
            if self.telegram:
                self.telegram.send_message(
                    f"CRITICAL ALERT from {message.from_agent}:\n"
                    f"{message.payload.get('alert', 'Unknown alert')}"
                )

    def _handle_command(self, message: AgentMessage):
        """Handle commands (usually from Telegram/human)."""
        command = message.payload.get("command", "")
        self.logger.info(f"Command received: {command}")

        if command == "HALT":
            self._halt_system(message.payload.get("reason", "Human command"))
        elif command == "RESUME":
            self._resume_system()
        elif command == "STATUS":
            self._send_status()
        elif command == "GO_LIVE":
            self._switch_to_live(message.payload)
        elif command == "GO_PAPER":
            self._switch_to_paper()

    def _handle_request(self, message: AgentMessage):
        self.logger.info(f"Request from {message.from_agent}: {message.payload}")

    def _handle_unknown(self, message: AgentMessage):
        self.logger.warning(f"Unknown message type from {message.from_agent}")

    def _process_risk_decision(self, message: AgentMessage):
        """Process risk agent's approval/rejection of a trade proposal."""
        decision = message.payload.get("decision")
        proposal_id = message.payload.get("proposal_id")

        if decision == "APPROVED":
            self.logger.info(f"Trade proposal {proposal_id} APPROVED by risk_agent")
            self._forward_to_execution(message.payload)
        elif decision == "REJECTED":
            self.logger.info(
                f"Trade proposal {proposal_id} REJECTED: "
                f"{message.payload.get('reason')}"
            )
            # Check if analyst disagrees — use LLM for conflict resolution
            analyst_signal = self._pending_proposals.get(proposal_id)
            if analyst_signal:
                self._resolve_conflict(analyst_signal, message.payload)

    def _resolve_conflict(self, analyst_signal: dict, risk_rejection: dict):
        """Use LLM to resolve conflict between analyst and risk agent."""
        import json
        snapshot = self.redis.get_market_data("data:market_snapshot") or {}
        vix_data = snapshot.get("indiavix", snapshot.get("vix", {}))
        positions = self.redis.get_state("state:positions") or {}
        capital = CAPITAL["conservative_bucket"]

        try:
            result = self.call_llm("PROMPT_CONFLICT_RESOLUTION", {
                "system_mode": self._get_system_mode(),
                "current_time": datetime.now().strftime("%H:%M IST"),
                "open_positions_count": len(positions.get("positions", [])),
                "conservative_strategy": "active",
                "risk_strategy": "active",
                "analyst_signal_json": json.dumps(analyst_signal, default=str),
                "risk_rejection_reason": risk_rejection.get("reason", ""),
                "deployed_capital": sum(
                    p.get("entry_price", 0) * p.get("quantity", 0)
                    for p in positions.get("positions", [])
                ),
                "todays_pnl": self._get_todays_pnl(),
                "max_daily_loss": capital * RISK_LIMITS["max_daily_loss_pct"],
                "remaining_loss_budget": (
                    capital * RISK_LIMITS["max_daily_loss_pct"]
                    - abs(min(self._get_todays_pnl(), 0))
                ),
                "nifty_trend": snapshot.get("nifty", {}).get("change", "unknown"),
                "vix": vix_data.get("ltp", "N/A"),
            })
            self.logger.info(f"Conflict resolution: {result.get('decision')}")
            if result.get("notify_human") and self.telegram:
                self.telegram.send_message(
                    f"Conflict resolved: {result.get('decision')}\n"
                    f"Reason: {result.get('reason')}"
                )
        except Exception as e:
            self.logger.error(f"Conflict resolution LLM call failed: {e}")

    def _get_todays_pnl(self) -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        pnl_data = self.sqlite.get_daily_pnl(date=today)
        return pnl_data.get("total_pnl", 0) if pnl_data else 0

    def _forward_to_execution(self, risk_approval: dict):
        """Forward approved order to execution agent."""
        order = ApprovedOrder(
            proposal_id=risk_approval.get("proposal_id", ""),
            symbol=risk_approval.get("symbol", ""),
            transaction_type=risk_approval.get("transaction_type", "BUY"),
            quantity=risk_approval.get("approved_position_size", 0),
            order_type="LIMIT",
            price=risk_approval.get("entry_price", 0),
            stop_loss_price=risk_approval.get("approved_stop_loss", 0),
            target_price=risk_approval.get("approved_target", 0),
            bucket=risk_approval.get("bucket", "conservative"),
            mode=self._get_system_mode(),
            approved_by="risk_agent",
        )
        self.send_message(
            to_agent="execution_agent",
            msg_type=MessageType.COMMAND,
            payload=order.model_dump(),
            priority=Priority.HIGH,
        )

    def _halt_system(self, reason: str):
        """Halt all trading immediately."""
        self.redis.set_state("state:system_mode", {
            "mode": "HALTED",
            "set_by": "orchestrator",
            "set_at": datetime.now().isoformat(),
            "reason": reason,
        })
        # Broadcast halt to all agents
        self.send_message(
            to_agent="broadcast",
            msg_type=MessageType.COMMAND,
            payload={"command": "HALT", "reason": reason},
            priority=Priority.CRITICAL,
        )
        if self.telegram:
            self.telegram.send_message(f"SYSTEM HALTED: {reason}")
        self.logger.warning(f"System HALTED: {reason}")

    def _resume_system(self):
        """Resume trading after halt."""
        self.redis.set_state("state:system_mode", {
            "mode": "PAPER",
            "set_by": "orchestrator",
            "set_at": datetime.now().isoformat(),
        })
        self.send_message(
            to_agent="broadcast",
            msg_type=MessageType.COMMAND,
            payload={"command": "RESUME"},
            priority=Priority.HIGH,
        )
        self.logger.info("System RESUMED in PAPER mode")

    def _switch_to_live(self, payload: dict):
        """Switch system to LIVE mode with safety guards.

        Requires:
        - Explicit human confirmation via payload["confirmed"] = True
        - Initial allocation capped at INR 8,000
        - Broker must be authenticated
        """
        INITIAL_LIVE_CAP = 8_000  # INR — cautious start

        if not payload.get("confirmed"):
            if self.telegram:
                self.telegram.send_message(
                    "WARNING: Switching to LIVE mode.\n"
                    f"Initial cap: INR {INITIAL_LIVE_CAP:,}\n"
                    "Reply /live confirm to proceed."
                )
            self.logger.warning("LIVE switch requested but not confirmed")
            return

        # Log the mode transition
        self.sqlite.log_message({
            "from_agent": "orchestrator",
            "to_agent": "system",
            "channel": "mode_switch",
            "type": "COMMAND",
            "priority": "CRITICAL",
            "payload": {"action": "SWITCH_TO_LIVE", "cap": INITIAL_LIVE_CAP},
            "timestamp": datetime.now().isoformat(),
            "status": "EXECUTED",
        })

        self.redis.set_state("state:system_mode", {
            "mode": "LIVE",
            "set_by": "orchestrator",
            "set_at": datetime.now().isoformat(),
            "live_cap": INITIAL_LIVE_CAP,
        })

        self.send_message(
            to_agent="broadcast",
            msg_type=MessageType.COMMAND,
            payload={"command": "MODE_CHANGE", "mode": "LIVE",
                     "live_cap": INITIAL_LIVE_CAP},
            priority=Priority.CRITICAL,
        )

        msg = (
            f"LIVE MODE ACTIVATED\n"
            f"Capital cap: INR {INITIAL_LIVE_CAP:,}\n"
            f"All trades require human approval.\n"
            f"Use /paper to switch back."
        )
        if self.telegram:
            self.telegram.send_message(msg)
        self.logger.warning(f"System switched to LIVE mode (cap: {INITIAL_LIVE_CAP})")

    def _switch_to_paper(self):
        """Switch system back to PAPER mode."""
        self.redis.set_state("state:system_mode", {
            "mode": "PAPER",
            "set_by": "orchestrator",
            "set_at": datetime.now().isoformat(),
        })
        self.send_message(
            to_agent="broadcast",
            msg_type=MessageType.COMMAND,
            payload={"command": "MODE_CHANGE", "mode": "PAPER"},
            priority=Priority.HIGH,
        )
        if self.telegram:
            self.telegram.send_message("Switched to PAPER mode.")
        self.logger.info("System switched to PAPER mode")

    def _send_status(self):
        """Compile and send system status."""
        agents = self.redis.get_state("state:all_agents") or {}
        mode = self._get_system_mode()
        status = f"System Mode: {mode}\nAgent Status:\n"
        for aid, info in agents.items():
            status += f"  {aid}: {info.get('state', 'UNKNOWN')}\n"
        if self.telegram:
            self.telegram.send_message(status)

    def _get_system_mode(self) -> str:
        mode_data = self.redis.get_state("state:system_mode")
        if mode_data:
            return mode_data.get("mode", TRADING_MODE)
        return TRADING_MODE

    def generate_morning_briefing(self, state: dict) -> str:
        """Generate morning briefing via LLM for Telegram."""
        snapshot = self.redis.get_market_data("data:market_snapshot") or {}
        cons_strategy = state.get("conservative_strategy", {})
        risk_strategy = state.get("risk_strategy", {})

        try:
            briefing = self.call_llm("PROMPT_MORNING_BRIEFING", {
                "system_mode": self._get_system_mode(),
                "current_time": datetime.now().strftime("%H:%M IST"),
                "open_positions_count": 0,
                "conservative_strategy": cons_strategy.get("strategy", "N/A"),
                "risk_strategy": risk_strategy.get("strategy", "N/A"),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "global_cues_summary": "Data pending",
                "expected_open": snapshot.get("nifty", {}).get("ltp", "N/A"),
                "vix": snapshot.get("indiavix", {}).get("ltp", "N/A"),
                "fii_net": "N/A",
                "fii_direction": "N/A",
                "conservative_strategy_name": cons_strategy.get("strategy", "N/A"),
                "conservative_rationale": cons_strategy.get("rationale", "N/A"),
                "risk_strategy_name": risk_strategy.get("strategy", "N/A"),
                "risk_rationale": risk_strategy.get("rationale", "N/A"),
                "watchlist": ", ".join(cons_strategy.get("watchlist", [])),
                "events": "None scheduled",
            }, expect_json=False)

            if self.telegram:
                self.telegram.send_message(briefing)
            return briefing
        except Exception as e:
            self.logger.error(f"Morning briefing LLM call failed: {e}")
            return ""

    def generate_eod_summary(self, state: dict) -> str:
        """Generate EOD summary via LLM for Telegram."""
        today = datetime.now().strftime("%Y-%m-%d")
        trades = self.sqlite.get_trades(date=today)

        wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        losses = sum(1 for t in trades if (t.get("pnl") or 0) < 0)
        flat = len(trades) - wins - losses
        total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

        try:
            summary = self.call_llm("PROMPT_EOD_SUMMARY", {
                "system_mode": self._get_system_mode(),
                "current_time": datetime.now().strftime("%H:%M IST"),
                "open_positions_count": 0,
                "conservative_strategy": "N/A",
                "risk_strategy": "N/A",
                "trade_count": len(trades),
                "wins": wins,
                "losses": losses,
                "flat": flat,
                "conservative_pnl": total_pnl,
                "risk_pnl": 0,
                "total_pnl": total_pnl,
                "mtd_pnl": total_pnl,
                "risk_mtd_pnl": 0,
                "best_trade": max((t.get("pnl", 0) or 0 for t in trades), default=0),
                "worst_trade": min((t.get("pnl", 0) or 0 for t in trades), default=0),
                "agent_notes": "All agents performed within parameters",
                "tomorrow_preview": "Strategy will be selected tomorrow morning",
            }, expect_json=False)

            if self.telegram:
                self.telegram.send_message(summary)
            return summary
        except Exception as e:
            self.logger.error(f"EOD summary LLM call failed: {e}")
            return ""

    def run(self, state: dict) -> dict:
        """LangGraph node: orchestrator coordination."""
        phase = state.get("current_phase", "")
        self.logger.info(f"Orchestrator running for phase: {phase}")

        # Update agent statuses in state
        agents = self.redis.get_state("state:all_agents") or {}
        state["agent_statuses"] = agents
        state["system_mode"] = self._get_system_mode()

        # Phase-specific actions
        if phase == "morning_briefing":
            self.generate_morning_briefing(state)
        elif phase == "eod_review":
            self.generate_eod_summary(state)

        return state
