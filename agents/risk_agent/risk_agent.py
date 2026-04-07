"""Risk Agent — Last line of defence before execution.

Reviews every trade proposal through 5 lenses. If in doubt, says no.
Position sizing is the most important variable in trading.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from agents.base_agent import BaseAgent
from agents.message import (
    AgentMessage, MessageType, Priority, RiskDecision,
)
from config import CAPITAL, RISK_LIMITS


class RiskAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store):
        super().__init__("risk_agent", redis_store, sqlite_store)
        self._consecutive_losses: int = 0
        self._in_cooldown: bool = False
        self._cooldown_until: datetime | None = None
        self._todays_pnl: float = 0.0
        self._review_cache: dict = {}  # "symbol:direction" -> {"decision": ..., "ts": ...}
        self._processed_proposals: set = set()  # dedup proposal_ids across paths

    def on_start(self):
        self.logger.info("Risk Agent ready — all proposals will be reviewed")

    def on_stop(self):
        pass

    def on_message(self, message: AgentMessage):
        if message.type == MessageType.SIGNAL:
            if message.from_agent == "analyst":
                self._review_trade_proposal(message)
        elif message.type == MessageType.COMMAND:
            command = message.payload.get("command", "")
            if command == "UPDATE_PNL":
                self._todays_pnl = message.payload.get("pnl", 0)
            elif command == "RECORD_LOSS":
                self._record_loss()
            elif command == "RECORD_WIN":
                self._consecutive_losses = 0

    def _check_review_cache(self, symbol: str, direction: str) -> dict | None:
        """Return cached decision if same symbol+direction was reviewed within 1 hour."""
        import time as _time
        cache_key = f"{symbol}:{direction}"
        cached = self._review_cache.get(cache_key)
        if cached and (_time.time() - cached["ts"]) < 3600:
            return cached
        return None

    def _update_review_cache(self, symbol: str, direction: str, decision: str):
        """Cache a review decision for 1 hour."""
        import time as _time
        # Evict stale entries (> 1 hour old) and trim processed proposals
        now = _time.time()
        if len(self._processed_proposals) > 100:
            self._processed_proposals.clear()
        self._review_cache = {
            k: v for k, v in self._review_cache.items()
            if now - v["ts"] < 3600
        }
        self._review_cache[f"{symbol}:{direction}"] = {
            "decision": decision, "ts": now,
        }

    def _review_trade_proposal(self, message: AgentMessage):
        """Review a trade proposal against 5 risk checks (message-driven path).

        Uses shared _review_proposal_data for the checks, then adds LLM review
        and sends messages to orchestrator and analyst.
        """
        self._refresh_todays_pnl()
        proposal = message.payload
        proposal_id = proposal.get("proposal_id", "")

        # Dedup: skip if already reviewed via graph run() path
        if proposal_id and proposal_id in self._processed_proposals:
            self.logger.info(f"Proposal {proposal_id} already reviewed — skipping message path")
            return
        if proposal_id:
            self._processed_proposals.add(proposal_id)

        symbol = proposal.get("symbol", "")
        direction = proposal.get("direction", "LONG")
        entry_price = proposal.get("entry_price", 0)
        stop_loss = proposal.get("stop_loss", 0)
        quantity = proposal.get("quantity_suggested", 1)
        bucket = proposal.get("bucket", "conservative")

        # Check cache — skip full review if same symbol+direction was reviewed recently
        cached = self._check_review_cache(symbol, direction)
        if cached and cached["decision"] == "REJECTED":
            self.logger.info(f"CACHED REJECT {symbol} {direction} — skipping review")
            self.send_message(
                to_agent="analyst",
                msg_type=MessageType.RESPONSE,
                payload={
                    "proposal_id": proposal.get("proposal_id", ""),
                    "decision": "REJECTED",
                },
                priority=Priority.NORMAL,
            )
            return

        # Run the 5 rule checks
        decision_payload = self._review_proposal_data(proposal)
        checks = decision_payload.get("checks", {})
        all_passed = decision_payload["decision"] == "APPROVED"

        # Cache the decision
        self._update_review_cache(symbol, direction, decision_payload["decision"])

        # Only call LLM when rules pass — no point in LLM review for rejected trades
        llm_review = {}
        if all_passed:
            if bucket == "risk":
                capital = CAPITAL["risk_bucket_monthly"]
            else:
                capital = CAPITAL["conservative_bucket"]
            risk_per_share = abs(entry_price - stop_loss)
            capital_at_risk = risk_per_share * quantity
            risk_pct = capital_at_risk / capital if capital > 0 else 1.0
            max_daily_loss = capital * RISK_LIMITS["max_daily_loss_pct"]
            remaining_budget = max_daily_loss - abs(min(self._todays_pnl, 0))

            llm_review = self._llm_trade_review(
                proposal, checks, capital_at_risk, risk_pct, remaining_budget
            )
            flag_human = risk_pct > 0.015 or llm_review.get("flag_human", False)

            # Override reason and SL/target with LLM suggestions if available
            if llm_review.get("reason"):
                decision_payload["reason"] = llm_review["reason"]
            if llm_review.get("approved_stop_loss"):
                decision_payload["approved_stop_loss"] = llm_review["approved_stop_loss"]
            if llm_review.get("approved_target"):
                decision_payload["approved_target"] = llm_review["approved_target"]
            decision_payload["flag_human"] = flag_human
        else:
            decision_payload["flag_human"] = False

        # Send decision to orchestrator
        self.send_message(
            to_agent="orchestrator",
            msg_type=MessageType.SIGNAL,
            payload=decision_payload,
            priority=Priority.HIGH,
            correlation_id=message.message_id,
        )

        # Notify analyst so it can clear the pending signal
        self.send_message(
            to_agent="analyst",
            msg_type=MessageType.RESPONSE,
            payload={
                "proposal_id": proposal.get("proposal_id", ""),
                "decision": decision_payload["decision"],
            },
            priority=Priority.NORMAL,
        )

    def _llm_trade_review(self, proposal: dict, checks: dict,
                          capital_at_risk: float, risk_pct: float,
                          remaining_budget: float) -> dict:
        """Use LLM for nuanced trade review alongside rule checks."""
        positions = self.redis.get_state("state:positions") or {}
        capital = CAPITAL["conservative_bucket"]

        try:
            result = self.call_llm("PROMPT_TRADE_REVIEW", {
                "max_single_trade_risk": capital * RISK_LIMITS["max_single_trade_risk_pct"],
                "max_daily_loss": capital * RISK_LIMITS["max_daily_loss_pct"],
                "max_positions": RISK_LIMITS["max_simultaneous_positions"],
                "total_capital": capital,
                "todays_pnl": self._todays_pnl,
                "loss_budget_remaining": remaining_budget,
                "open_positions": len(positions.get("positions", [])),
                "consecutive_losses": self._consecutive_losses,
                "in_cooldown": self._in_cooldown,
                "symbol": proposal.get("symbol", ""),
                "direction": proposal.get("direction", "BUY"),
                "entry_price": proposal.get("entry_price", 0),
                "suggested_stop": proposal.get("stop_loss", 0),
                "suggested_target": proposal.get("target", 0),
                "proposed_shares": proposal.get("quantity_suggested", 1),
                "capital_at_risk": capital_at_risk,
                "risk_pct": f"{risk_pct:.1%}",
                "available_capital": capital,
                "open_positions_list": str(positions.get("positions", [])),
                "sector_exposure": "N/A",
                "check_1": "PASS" if checks.get("single_trade_risk") else "FAIL",
                "check_2": "PASS" if checks.get("daily_loss_budget") else "FAIL",
                "check_3": "PASS" if checks.get("max_positions") else "FAIL",
                "check_4": "PASS" if checks.get("not_in_cooldown") else "FAIL",
                "check_5": "PASS" if checks.get("stop_loss_logical") else "FAIL",
            })
            return result
        except Exception as e:
            self.logger.error(f"Risk review LLM failed: {e}")
            return {}

    def _record_loss(self):
        """Record a consecutive loss and check cooldown trigger."""
        self._consecutive_losses += 1
        if self._consecutive_losses >= RISK_LIMITS["consecutive_loss_cooldown"]:
            from datetime import timedelta
            self._in_cooldown = True
            self._cooldown_until = datetime.now(IST) + timedelta(
                minutes=RISK_LIMITS["cooldown_duration_minutes"]
            )
            self.logger.warning(
                f"COOLDOWN TRIGGERED: {self._consecutive_losses} consecutive losses. "
                f"Trading halted until {self._cooldown_until.isoformat()}"
            )
            # Alert orchestrator
            self.send_message(
                to_agent="orchestrator",
                msg_type=MessageType.ALERT,
                payload={
                    "alert": "cooldown_triggered",
                    "consecutive_losses": self._consecutive_losses,
                    "cooldown_until": self._cooldown_until.isoformat(),
                },
                priority=Priority.HIGH,
            )

    def _review_proposal_data(self, proposal: dict) -> dict:
        """Review a trade proposal dict through 5 risk checks.

        Shared logic used by both on_message() and run() paths.
        Returns the full decision payload.
        """
        symbol = proposal.get("symbol", "")
        entry_price = proposal.get("entry_price", 0)
        stop_loss = proposal.get("stop_loss", 0)
        quantity = proposal.get("quantity_suggested", 1)
        bucket = proposal.get("bucket", "conservative")

        if bucket == "risk":
            capital = CAPITAL["risk_bucket_monthly"]
        else:
            capital = CAPITAL["conservative_bucket"]

        risk_per_share = abs(entry_price - stop_loss)
        capital_at_risk = risk_per_share * quantity
        risk_pct = capital_at_risk / capital if capital > 0 else 1.0

        checks = {}
        max_risk = capital * RISK_LIMITS["max_single_trade_risk_pct"]
        checks["single_trade_risk"] = capital_at_risk <= max_risk

        max_daily_loss = capital * RISK_LIMITS["max_daily_loss_pct"]
        remaining_budget = max_daily_loss - abs(min(self._todays_pnl, 0))
        checks["daily_loss_budget"] = remaining_budget > capital_at_risk

        positions = self.redis.get_state("state:positions") or {}
        open_count = len([p for p in positions.get("positions", [])
                         if p.get("status") == "OPEN"
                         and p.get("bucket", "conservative") == bucket])
        max_pos = (RISK_LIMITS["max_risk_positions"] if bucket == "risk"
                   else RISK_LIMITS["max_simultaneous_positions"])
        checks["max_positions"] = open_count < max_pos

        if self._cooldown_until and datetime.now(IST) < self._cooldown_until:
            self._in_cooldown = True
        else:
            self._in_cooldown = False
            self._cooldown_until = None
        checks["not_in_cooldown"] = not self._in_cooldown

        atr_reasonable = risk_per_share > 0 and risk_pct < 0.05
        checks["stop_loss_logical"] = atr_reasonable

        all_passed = all(checks.values())

        if all_passed and risk_per_share > 0:
            approved_size = int(max_risk / risk_per_share)
            approved_size = max(1, min(approved_size, quantity))
        else:
            approved_size = 0

        failing_check = None
        if not all_passed:
            for check_name, passed in checks.items():
                if not passed:
                    failing_check = check_name
                    break

        decision = RiskDecision(
            proposal_id=proposal.get("proposal_id", ""),
            decision="APPROVED" if all_passed else "REJECTED",
            reason=(
                f"All 5 checks passed. Risk: {risk_pct:.1%}"
                if all_passed
                else f"Failed check: {failing_check}"
            ),
            approved_position_size=approved_size,
            approved_stop_loss=stop_loss,
            approved_target=proposal.get("target", 0),
            risk_pct_final=risk_pct,
            flag_human=risk_pct > 0.015,
        )

        decision_payload = {
            **decision.model_dump(),
            "symbol": symbol,
            "entry_price": entry_price,
            "bucket": bucket,
            "transaction_type": "BUY" if proposal.get("direction") == "LONG" else "SELL",
            "checks": checks,
        }

        if all_passed:
            self.logger.info(
                f"APPROVED {symbol}: risk={risk_pct:.1%}, size={approved_size}"
            )
        else:
            self.logger.info(
                f"REJECTED {symbol}: risk={risk_pct:.1%}, size={approved_size}, "
                f"failed={failing_check}, checks={checks}"
            )

        return decision_payload

    def _refresh_todays_pnl(self):
        """Load today's realized PnL from SQLite (fallback for when no UPDATE_PNL message received)."""
        today = datetime.now(IST).strftime("%Y-%m-%d")
        try:
            pnl_data = self.sqlite.get_daily_pnl(date=today)
            if pnl_data:
                self._todays_pnl = pnl_data.get("total_pnl", 0) or 0
        except Exception as e:
            self.logger.error(f"Failed to refresh PnL: {e}")

    def run(self, state: dict) -> dict:
        """LangGraph node: review pending signals through 5 risk checks + LLM."""
        self._refresh_todays_pnl()
        pending = state.get("pending_signals", [])
        approved = []
        rejected = []

        self.logger.info(f"Reviewing {len(pending)} pending signals (graph mode)")

        for signal in pending:
            if not isinstance(signal, dict):
                continue

            symbol = signal.get("symbol", "")
            direction = signal.get("direction", "LONG")
            proposal_id = signal.get("proposal_id", "")

            # Mark as processed so message path skips it
            if proposal_id:
                self._processed_proposals.add(proposal_id)

            # Check cache — skip full review if recently reviewed
            cached = self._check_review_cache(symbol, direction)
            if cached and cached["decision"] == "REJECTED":
                self.logger.info(f"CACHED REJECT {symbol} {direction} (graph)")
                rejected.append({"decision": "REJECTED", "symbol": symbol,
                                 "reason": "cached_rejection"})
                continue

            decision_payload = self._review_proposal_data(signal)

            # LLM review for approved trades (same as message path)
            if decision_payload["decision"] == "APPROVED":
                bucket = signal.get("bucket", "conservative")
                entry_price = signal.get("entry_price", 0)
                stop_loss = signal.get("stop_loss", 0)
                quantity = signal.get("quantity_suggested", 1)
                if bucket == "risk":
                    capital = CAPITAL["risk_bucket_monthly"]
                else:
                    capital = CAPITAL["conservative_bucket"]
                risk_per_share = abs(entry_price - stop_loss)
                capital_at_risk = risk_per_share * quantity
                risk_pct = capital_at_risk / capital if capital > 0 else 1.0
                max_daily_loss = capital * RISK_LIMITS["max_daily_loss_pct"]
                remaining_budget = max_daily_loss - abs(min(self._todays_pnl, 0))

                try:
                    llm_review = self._llm_trade_review(
                        signal, decision_payload.get("checks", {}),
                        capital_at_risk, risk_pct, remaining_budget,
                    )
                except Exception as e:
                    self.logger.error(f"Risk LLM review failed in graph: {e}")
                    llm_review = {}

                if llm_review.get("reason"):
                    decision_payload["reason"] = llm_review["reason"]
                if llm_review.get("approved_stop_loss"):
                    decision_payload["approved_stop_loss"] = llm_review["approved_stop_loss"]
                if llm_review.get("approved_target"):
                    decision_payload["approved_target"] = llm_review["approved_target"]
                flag_human = risk_pct > 0.015 or llm_review.get("flag_human", False)
                decision_payload["flag_human"] = flag_human
                approved.append(decision_payload)
            else:
                rejected.append(decision_payload)

            # Cache the decision
            self._update_review_cache(symbol, direction, decision_payload["decision"])

        state["approved_orders"] = approved
        state["rejected_proposals"] = rejected
        return state
