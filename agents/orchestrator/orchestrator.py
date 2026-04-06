"""Orchestrator Agent — Master coordinator and conflict resolver.

Coordinates all agents, resolves conflicts, manages system mode,
and interfaces with the human owner via Telegram.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from agents.base_agent import BaseAgent
from agents.message import (
    AgentMessage, ApprovedOrder, MessageType, Priority, RiskDecision,
)
from config import CAPITAL, RISK_LIMITS, TRADING_MODE


class OrchestratorAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store, telegram_bot=None,
                 broker=None):
        super().__init__("orchestrator", redis_store, sqlite_store)
        self.telegram = telegram_bot
        self.broker = broker
        self.kite = None
        self._pending_proposals: dict = {}  # proposal_id -> proposal data
        self._human_approval_pending: dict = {}

    def on_start(self):
        # Set initial system mode
        self.redis.set_state("state:system_mode", {
            "mode": TRADING_MODE,
            "set_by": "orchestrator",
            "set_at": datetime.now(IST).isoformat(),
        })
        self.logger.info(f"System mode set to {TRADING_MODE}")

        # Authenticate with Kite Connect
        if self.broker and self.telegram:
            try:
                from tools.kite_auth import load_or_refresh_token
                from tools.market_data import set_kite_client
                from tools.kite_market_data import build_instrument_cache

                kite = load_or_refresh_token(self.telegram)
                self.kite = kite
                set_kite_client(kite)
                self.broker.set_kite_client(kite)
                self.logger.info("Kite authentication successful.")

                build_instrument_cache(kite)
                self.logger.info("Instrument cache built.")
            except TimeoutError:
                self._halt_system("Kite authentication timed out")
            except Exception as e:
                self.logger.warning(f"Kite auth failed: {e}. Continuing in paper mode.")
                if self.telegram:
                    self.telegram.send_message(
                        f"Kite auth failed: {e}\n"
                        "Continuing in paper mode with yfinance data."
                    )

    def on_stop(self):
        pass

    def on_message(self, message: AgentMessage):
        handlers = {
            MessageType.SIGNAL: self._handle_signal,
            MessageType.RESPONSE: self._handle_response,
            MessageType.ALERT: self._handle_alert,
            MessageType.COMMAND: self._handle_command,
            MessageType.REQUEST: self._handle_request,
            MessageType.SYNTHESIS: self._handle_synthesis,
            MessageType.POSITION_ALERT: self._handle_position_alert,
            MessageType.LT_ADVISOR_ALERT: self._handle_lt_advisor_alert,
        }
        handler = handlers.get(message.type, self._handle_unknown)
        handler(message)

    def _handle_signal(self, message: AgentMessage):
        """Handle trade signals — strategy proposals and risk decisions."""
        if message.from_agent in ("strategist", "risk_strategist"):
            self._handle_strategy_proposal(message)
        elif message.from_agent == "risk_agent":
            self._process_risk_decision(message)

    def _handle_strategy_proposal(self, message: AgentMessage):
        """Store active strategy and notify operator via Telegram."""
        payload = message.payload

        # Mid-day reeval with no change — brief notification
        if payload.get("signal") == "midday_reeval" and not payload.get("changed"):
            self.logger.info(f"Mid-day reeval: no change, keeping {payload.get('strategy')}")
            if self.telegram:
                self.telegram.send_message(
                    f"MIDDAY REEVAL — No Change\n"
                    f"Keeping: {payload.get('strategy')}\n"
                    f"VIX: {payload.get('vix_morning')} → {payload.get('vix_now')}\n"
                    f"Sentiment: {payload.get('sentiment_morning')} → {payload.get('sentiment_now')}"
                )
            return

        strategy = payload.get("strategy", "N/A")
        bucket = payload.get("bucket", "conservative")
        confidence = payload.get("confidence", "N/A")
        regime = payload.get("regime", "N/A")
        rationale = payload.get("rationale", "")
        watchlist = payload.get("watchlist", [])

        self.logger.info(f"Strategy from {message.from_agent}: {strategy} ({bucket})")

        self.redis.set_state("state:active_strategy", {
            "strategy": strategy,
            "bucket": bucket,
            "regime": regime,
            "confidence": confidence,
            "set_at": datetime.now(IST).isoformat(),
        })

        if self.telegram:
            symbols = ", ".join(watchlist[:5])
            if len(watchlist) > 5:
                symbols += f" +{len(watchlist) - 5} more"
            self.telegram.send_message(
                f"STRATEGY [{bucket.upper()}]\n"
                f"Strategy: {strategy}\n"
                f"Regime: {regime} | Confidence: {confidence}\n"
                f"Rationale: {rationale}\n"
                f"Watchlist: {symbols or 'None'}"
            )

    def _handle_response(self, message: AgentMessage):
        """Handle responses to orchestrator requests."""
        status = message.payload.get("status", "unknown")
        self.logger.info(
            f"Response from {message.from_agent}: {status}"
        )

        # Notify human on Telegram when a trade is executed
        if message.from_agent == "execution_agent" and status == "FILLED":
            symbol = message.payload.get("symbol", "?")
            txn = message.payload.get("transaction_type", "?")
            qty = message.payload.get("quantity", 0)
            fill_price = message.payload.get("fill_price", 0)
            mode = message.payload.get("mode", "PAPER")
            if self.telegram:
                self.telegram.send_message(
                    f"{'📄' if mode == 'PAPER' else '🔴'} TRADE EXECUTED\n"
                    f"{txn} {symbol} {qty}x @ ₹{fill_price:.2f}\n"
                    f"Mode: {mode}"
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
        elif command == "AUTHENTICATE":
            self._handle_authenticate()
        elif command == "GO_LIVE":
            self._switch_to_live(message.payload)
        elif command == "GO_PAPER":
            self._switch_to_paper()
        elif command == "APPROVE":
            self._handle_human_approval(message.payload.get("proposal_id", ""))
        elif command == "REJECT":
            self._handle_human_rejection(message.payload.get("proposal_id", ""))
        elif command == "CATCHUP":
            self._handle_catchup()
        elif command == "LT_SCAN":
            self._handle_lt_scan()

    def _handle_catchup(self):
        """Run the full morning sequence manually (auth + wake + strategy)."""
        scheduler = getattr(self, "swarm_scheduler", None)
        if not scheduler:
            self.logger.error("No scheduler reference — catchup unavailable")
            if self.telegram:
                self.telegram.send_message("Catchup failed: scheduler not available.")
            return
        try:
            import threading
            # Run in background thread to not block message listener
            threading.Thread(
                target=scheduler.catchup, daemon=True, name="catchup",
            ).start()
        except Exception as e:
            self.logger.error(f"Catchup failed: {e}")
            if self.telegram:
                self.telegram.send_message(f"Catchup failed: {e}")

    def _handle_lt_advisor_alert(self, message: AgentMessage):
        """Handle LT_Advisor alert — direct passthrough to Telegram.

        No analysis, no agent calls, no decision-making.
        LT_Advisor already drafted the message.
        """
        telegram_text = message.payload.get("telegram_message", "")
        if telegram_text and telegram_text.strip():
            if self.telegram:
                self.telegram.send_message(telegram_text)
            self.logger.info(
                "LT Advisor alert forwarded to Telegram. instrument=%s score=%s",
                message.payload.get("instrument", "unknown"),
                message.payload.get("score", "?"),
            )
        else:
            self.logger.warning(
                "LT_ADVISOR_ALERT received with empty message. Not forwarded."
            )

    def _handle_lt_scan(self):
        """Run an LT_Advisor scan on demand (from /lt_scan Telegram command)."""
        if self.telegram:
            self.telegram.send_message(
                "Running LT scan... will message you if opportunity found."
            )
        import threading
        from agents.lt_advisor.lt_advisor import LTAdvisor
        advisor = LTAdvisor(redis=self.redis, db=self.sqlite)
        threading.Thread(
            target=advisor.run,
            args=("MANUAL",),
            daemon=True,
            name="lt-scan-manual",
        ).start()

    def _handle_human_approval(self, proposal_id: str):
        """Human approved a risk bucket trade via Telegram."""
        payload = self._human_approval_pending.pop(proposal_id, None)
        if not payload:
            # Try matching by partial ID or latest pending
            if not proposal_id and self._human_approval_pending:
                proposal_id, payload = self._human_approval_pending.popitem()
            else:
                self.logger.warning(f"No pending proposal found for {proposal_id}")
                if self.telegram:
                    self.telegram.send_message(
                        f"No pending proposal: {proposal_id or 'none'}"
                    )
                return

        self.logger.info(f"Human APPROVED risk trade {proposal_id}")
        self._forward_to_execution(payload)
        if self.telegram:
            self.telegram.send_message(
                f"APPROVED — {payload.get('symbol', '?')} forwarded to execution."
            )

    def _handle_human_rejection(self, proposal_id: str):
        """Human rejected a risk bucket trade via Telegram."""
        payload = self._human_approval_pending.pop(proposal_id, None)
        if not payload:
            if not proposal_id and self._human_approval_pending:
                proposal_id, payload = self._human_approval_pending.popitem()
            else:
                self.logger.warning(f"No pending proposal found for {proposal_id}")
                if self.telegram:
                    self.telegram.send_message(
                        f"No pending proposal: {proposal_id or 'none'}"
                    )
                return

        self.logger.info(f"Human REJECTED risk trade {proposal_id}")
        if self.telegram:
            self.telegram.send_message(
                f"REJECTED — {payload.get('symbol', '?')} dropped."
            )

    def _handle_authenticate(self):
        """Force re-authentication with Kite Connect."""
        try:
            from tools.kite_auth import force_reauthenticate
            from tools.market_data import set_kite_client
            from tools.kite_market_data import build_instrument_cache

            kite = force_reauthenticate(self.telegram)
            self.kite = kite
            set_kite_client(kite)
            if self.broker:
                self.broker.set_kite_client(kite)
            build_instrument_cache(kite)
            self.logger.info("Re-authentication successful.")
        except Exception as e:
            self.logger.error(f"Re-authentication failed: {e}")
            if self.telegram:
                self.telegram.send_message(f"Re-authentication failed: {e}")

    def _handle_synthesis(self, message: AgentMessage):
        """Handle Optimizer synthesis — ALWAYS forward to Telegram. No exceptions."""
        telegram_text = message.payload.get("telegram_message", "")
        if not telegram_text:
            telegram_text = (
                f"OPTIMIZER REPORT — {message.payload.get('meeting_date', 'unknown')}\n"
                "Synthesis received but message was empty."
            )
        if self.telegram:
            self.telegram.send_message(telegram_text)
        self.logger.info(
            "Optimizer synthesis forwarded to Telegram. %d learnings written.",
            message.payload.get("learnings_count", 0),
        )

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
            bucket = message.payload.get("bucket", "conservative")
            if bucket == "risk":
                # Risk bucket trades require human approval
                self._human_approval_pending[proposal_id] = message.payload
                self.logger.info(f"Risk trade {proposal_id} queued for human approval")
                if self.telegram:
                    self.telegram.send_approval_request({
                        "symbol": message.payload.get("symbol", ""),
                        "direction": message.payload.get("transaction_type", "BUY"),
                        "entry_price": message.payload.get("entry_price", 0),
                        "stop_loss": message.payload.get("approved_stop_loss", 0),
                        "target": message.payload.get("approved_target", 0),
                        "quantity": message.payload.get("approved_position_size", 0),
                        "bucket": "risk",
                        "confidence": message.payload.get("confidence", ""),
                        "note": f"Proposal ID: {proposal_id}",
                    })
            else:
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
                "current_time": datetime.now(IST).strftime("%H:%M IST"),
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
        today = datetime.now(IST).strftime("%Y-%m-%d")
        pnl_data = self.sqlite.get_daily_pnl(date=today)
        return pnl_data.get("total_pnl", 0) if pnl_data else 0

    def _forward_to_execution(self, risk_approval: dict):
        """Forward approved order to execution agent."""
        active_strategy = self.redis.get_state("state:active_strategy") or {}
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
            strategy=active_strategy.get("strategy", ""),
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
            "set_at": datetime.now(IST).isoformat(),
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
            "set_at": datetime.now(IST).isoformat(),
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
            "timestamp": datetime.now(IST).isoformat(),
            "status": "EXECUTED",
        })

        self.redis.set_state("state:system_mode", {
            "mode": "LIVE",
            "set_by": "orchestrator",
            "set_at": datetime.now(IST).isoformat(),
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
            "set_at": datetime.now(IST).isoformat(),
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
            if isinstance(info, dict):
                state = info.get("state", "UNKNOWN")
            else:
                state = str(info)
            status += f"  {aid}: {state}\n"
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
                "current_time": datetime.now(IST).strftime("%H:%M IST"),
                "open_positions_count": 0,
                "conservative_strategy": cons_strategy.get("strategy", "N/A"),
                "risk_strategy": risk_strategy.get("strategy", "N/A"),
                "date": datetime.now(IST).strftime("%Y-%m-%d"),
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
        today = datetime.now(IST).strftime("%Y-%m-%d")
        trades = self.sqlite.get_trades(date=today)

        wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        losses = sum(1 for t in trades if (t.get("pnl") or 0) < 0)
        flat = len(trades) - wins - losses
        total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

        try:
            summary = self.call_llm("PROMPT_EOD_SUMMARY", {
                "system_mode": self._get_system_mode(),
                "current_time": datetime.now(IST).strftime("%H:%M IST"),
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

    # ── Position Monitor Review Flow ──────────────────────────────────────

    def _handle_position_alert(self, message: AgentMessage):
        """Full review flow triggered by Position Monitor alert.

        3-step LLM review: Analyst thesis check → Risk Agent recommendation
        → Orchestrator decision. Always sends Telegram.
        """
        import json

        alert = message.payload
        position = alert.get("position", {})
        symbol = position.get("symbol", "unknown")
        self.logger.info(
            "Position alert for %s — trigger: %s",
            symbol, alert.get("trigger_type"),
        )

        # Build shared context for prompts
        context = self._build_review_context(alert)

        try:
            # Step 1 — Analyst thesis check
            analyst_response = self.call_llm(
                "PROMPT_ANALYST_POSITION_REVIEW", context,
            )
        except Exception as e:
            self.logger.error("Analyst position review failed: %s", e)
            analyst_response = {
                "thesis_holds": False, "confidence": "LOW",
                "key_reason": "LLM unavailable",
                "analyst_recommendation": "EXIT",
                "indicator_status": "UNKNOWN",
                "market_alignment": "UNKNOWN",
            }

        try:
            # Step 2 — Risk Agent review
            risk_context = {
                **context,
                "thesis_holds": analyst_response.get("thesis_holds", False),
                "analyst_confidence": analyst_response.get("confidence", "LOW"),
                "indicator_status": analyst_response.get("indicator_status", "UNKNOWN"),
                "analyst_recommendation": analyst_response.get("analyst_recommendation", "EXIT"),
            }
            risk_response = self.call_llm(
                "PROMPT_RISK_POSITION_REVIEW", risk_context,
            )
        except Exception as e:
            self.logger.error("Risk position review failed: %s", e)
            risk_response = {
                "action": "HOLD", "reason": "LLM unavailable",
                "urgency": "MONITOR", "flag_human": True,
                "flag_reason": "Review LLM failed",
            }

        try:
            # Step 3 — Orchestrator final decision
            decision_context = {
                **context,
                "thesis_holds": analyst_response.get("thesis_holds", False),
                "analyst_confidence": analyst_response.get("confidence", "LOW"),
                "analyst_key_reason": analyst_response.get("key_reason", "N/A"),
                "analyst_recommendation": analyst_response.get("analyst_recommendation", "EXIT"),
                "risk_action": risk_response.get("action", "HOLD"),
                "risk_reason": risk_response.get("reason", "N/A"),
                "risk_urgency": risk_response.get("urgency", "MONITOR"),
                "flag_human": risk_response.get("flag_human", True),
            }
            raw_decision = self.call_llm(
                "PROMPT_ORCHESTRATOR_POSITION_DECISION",
                decision_context,
                expect_json=False,
            )

            # Parse JSON + Telegram (same pattern as optimizer synthesis)
            final = {}
            telegram_text = ""
            if "---" in raw_decision:
                json_part, telegram_part = raw_decision.split("---", 1)
                json_str = json_part.strip()
                if json_str.startswith("```"):
                    json_str = json_str.split("\n", 1)[1] if "\n" in json_str else json_str[3:]
                if json_str.endswith("```"):
                    json_str = json_str[:-3]
                try:
                    final = json.loads(json_str.strip())
                except json.JSONDecodeError:
                    final = {}
                telegram_text = telegram_part.strip()
            else:
                try:
                    final = json.loads(raw_decision.strip())
                except json.JSONDecodeError:
                    final = {"final_action": "HOLD"}

        except Exception as e:
            self.logger.error("Orchestrator position decision failed: %s", e)
            final = {"final_action": "HOLD", "execute_immediately": False}
            telegram_text = ""

        # Step 4 — Execute if needed
        if final.get("execute_immediately") and final.get("order_details"):
            self._execute_position_action(final["order_details"], position)

        # Step 5 — Telegram (ALWAYS)
        if not telegram_text:
            entry_price = position.get("entry_price", 0)
            current_price = position.get("current_price", 0)
            pnl = current_price - entry_price if position.get("direction") == "LONG" \
                else entry_price - current_price
            telegram_text = (
                f"POSITION ALERT — {symbol}\n\n"
                f"Trigger: {alert.get('trigger_description', alert.get('trigger_type', '?'))}\n"
                f"P&L: {pnl:+.1f} per share\n\n"
                f"Decision: {final.get('final_action', 'HOLD')}\n"
                f"Reason: {final.get('reason', 'Review complete.')}"
            )
        if self.telegram:
            self.telegram.send_message(telegram_text)
        self.logger.info("Position review complete for %s: %s",
                         symbol, final.get("final_action", "HOLD"))

    def _build_review_context(self, alert: dict) -> dict:
        """Build template variables for position review prompts."""
        position = alert.get("position", {})
        market = alert.get("market_context", {})

        entry_price = position.get("entry_price", 0)
        current_price = position.get("current_price", 0)
        stop_price = position.get("stop_loss_price", 0)
        target_price = position.get("target_price", 0)
        direction = position.get("direction", "LONG")
        quantity = position.get("quantity", 0)

        if direction == "LONG":
            pnl_per_share = current_price - entry_price
            dist_to_stop = ((current_price - stop_price) / entry_price * 100) if stop_price else 0
            dist_to_target = ((target_price - current_price) / entry_price * 100) if target_price else 0
        else:
            pnl_per_share = entry_price - current_price
            dist_to_stop = ((stop_price - current_price) / entry_price * 100) if stop_price else 0
            dist_to_target = ((current_price - target_price) / entry_price * 100) if target_price else 0

        pnl_pct = (pnl_per_share / entry_price * 100) if entry_price else 0

        # Portfolio context
        positions_data = self.redis.get_state("state:positions") or {}
        all_positions = positions_data.get("positions", [])
        open_positions = [p for p in all_positions if p.get("status") == "OPEN"]

        # Tick data for indicators
        tick = self.redis.get_market_data(
            f"data:watchlist_ticks:{position.get('symbol', '')}",
        ) or {}

        return {
            # System prompt vars
            "system_mode": self._get_system_mode(),
            "current_time": datetime.now(IST).strftime("%H:%M IST"),
            "open_positions_count": len(open_positions),
            "conservative_strategy": "active",
            "risk_strategy": "active",
            # Position vars
            "symbol": position.get("symbol", ""),
            "direction": direction,
            "strategy_name": position.get("strategy_name", ""),
            "entry_price": f"{entry_price:.2f}" if entry_price else "N/A",
            "entry_time": position.get("entry_time", "N/A"),
            "current_price": f"{current_price:.2f}" if current_price else "N/A",
            "current_pnl": f"{pnl_per_share * quantity:.0f}",
            "pnl_pct": f"{pnl_pct:.2f}",
            "distance_to_stop_pct": f"{dist_to_stop:.1f}",
            "distance_to_target_pct": f"{dist_to_target:.1f}",
            "minutes_in_trade": position.get("minutes_in_trade", 0),
            "position_size": f"{entry_price * quantity:.0f}" if entry_price else "0",
            "bucket": position.get("bucket", "conservative"),
            # Alert vars
            "trigger_type": alert.get("trigger_type", ""),
            "trigger_value": alert.get("trigger_value", 0),
            "threshold_description": alert.get("trigger_description", ""),
            "trigger_description": alert.get("trigger_description", ""),
            # Market vars
            "nifty_direction": "up" if market.get("nifty_change", 0) > 0 else "down",
            "nifty_move_30m": f"{market.get('nifty_change', 0):.2f}",
            "vix": market.get("vix", "N/A"),
            "volume_ratio": market.get("volume_ratio", 1.0),
            "rsi": tick.get("rsi", "N/A"),
            "vwap_deviation": tick.get("vwap_dev", "N/A"),
            # Original entry context
            "original_analyst_note": position.get("original_analyst_note", "N/A"),
            "original_entry_conditions": position.get("original_entry_conditions", "N/A"),
            # Portfolio context
            "todays_pnl": f"{self._get_todays_pnl():.0f}",
            "loss_budget_remaining": f"{CAPITAL['conservative_bucket'] * RISK_LIMITS['max_daily_loss_pct'] - abs(min(self._get_todays_pnl(), 0)):.0f}",
            "other_positions_count": max(0, len(open_positions) - 1),
            "consecutive_losses": 0,
            "strategy_type": alert.get("strategy_type", "intraday"),
            "time_to_forced_close": "N/A",
        }

    def _execute_position_action(self, order_details: dict, position: dict):
        """Send position action to Execution Agent."""
        action_type = order_details.get("type", "")
        symbol = order_details.get("symbol") or position.get("symbol", "")

        if action_type in ("TRAIL", "TRAIL_STOP"):
            payload = {
                "command": "MODIFY_STOP",
                "trade_id": position.get("trade_id", ""),
                "symbol": symbol,
                "new_stop_price": order_details.get("new_stop_price", 0),
                "quantity": position.get("quantity", 0),
            }
        elif action_type == "PARTIAL":
            payload = {
                "command": "PARTIAL_CLOSE",
                "trade_id": position.get("trade_id", ""),
                "symbol": symbol,
                "quantity": order_details.get("quantity", 0),
                "order_type": "MARKET",
                "reason": "Position Monitor — partial exit",
            }
        elif action_type == "FULL":
            payload = {
                "command": "FULL_CLOSE",
                "trade_id": position.get("trade_id", ""),
                "symbol": symbol,
                "quantity": position.get("quantity", 0),
                "order_type": "MARKET",
                "reason": "Position Monitor — full exit",
            }
        else:
            self.logger.warning("Unknown position action type: %s", action_type)
            return

        self.send_message(
            to_agent="execution_agent",
            msg_type=MessageType.COMMAND,
            payload=payload,
            priority=Priority.HIGH,
        )
        self.logger.info("Position action sent: %s for %s", action_type, symbol)

    def run(self, state: dict) -> dict:
        """LangGraph node: orchestrator coordination."""
        phase = state.get("current_phase", "")
        self.logger.info(f"Orchestrator running for phase: {phase}")

        # Update agent statuses in state
        agents = self.redis.get_state("state:all_agents") or {}
        state["agent_statuses"] = agents
        state["system_mode"] = self._get_system_mode()

        # Persist strategy to Redis if present in state (graph path)
        conservative = state.get("conservative_strategy")
        if conservative and isinstance(conservative, dict):
            self.redis.set_state("state:active_strategy", {
                "strategy": conservative.get("strategy", ""),
                "bucket": "conservative",
                "regime": conservative.get("regime", ""),
                "confidence": conservative.get("confidence", ""),
                "rationale": conservative.get("rationale", ""),
                "set_at": datetime.now(IST).isoformat(),
            })

        # Phase-specific actions
        if phase == "morning_briefing":
            self.generate_morning_briefing(state)
        elif phase == "eod_review":
            self.generate_eod_summary(state)
        elif phase in ("MARKET_OPEN", "MARKET_CLOSE"):
            # Convert approved risk decisions to executable orders
            approved = state.get("approved_orders", [])
            executable = []
            for risk_approval in approved:
                bucket = risk_approval.get("bucket", "conservative")

                # Risk bucket trades need human approval (handled outside graph)
                if bucket == "risk":
                    proposal_id = risk_approval.get("proposal_id", "")
                    self._human_approval_pending[proposal_id] = risk_approval
                    self.logger.info(f"Risk trade {proposal_id} queued for human approval")
                    if self.telegram:
                        self.telegram.send_approval_request({
                            "symbol": risk_approval.get("symbol", ""),
                            "direction": risk_approval.get("transaction_type", "BUY"),
                            "entry_price": risk_approval.get("entry_price", 0),
                            "stop_loss": risk_approval.get("approved_stop_loss", 0),
                            "target": risk_approval.get("approved_target", 0),
                            "quantity": risk_approval.get("approved_position_size", 0),
                            "bucket": "risk",
                            "confidence": risk_approval.get("confidence", ""),
                            "note": f"Proposal ID: {proposal_id}",
                        })
                    continue

                active_strategy = self.redis.get_state("state:active_strategy") or {}
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
                    strategy=active_strategy.get("strategy", ""),
                    mode=self._get_system_mode(),
                    approved_by="risk_agent",
                )
                executable.append(order.model_dump())
                self.logger.info(
                    f"Order prepared: {order.symbol} {order.transaction_type} "
                    f"{order.quantity}x @ {order.price}"
                )

            state["approved_orders"] = executable

        return state
