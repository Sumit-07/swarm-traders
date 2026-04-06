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

    def _review_trade_proposal(self, message: AgentMessage):
        """Review a trade proposal against 5 risk checks.

        Phase 2: pure Python rule checks.
        Phase 4: will use LLM with PROMPT_TRADE_REVIEW for edge cases.
        """
        proposal = message.payload
        symbol = proposal.get("symbol", "")
        entry_price = proposal.get("entry_price", 0)
        stop_loss = proposal.get("stop_loss", 0)
        quantity = proposal.get("quantity_suggested", 1)
        bucket = proposal.get("bucket", "conservative")

        # Determine capital base
        if bucket == "risk":
            capital = CAPITAL["risk_bucket_monthly"]
        else:
            capital = CAPITAL["conservative_bucket"]

        # Calculate risk
        risk_per_share = abs(entry_price - stop_loss)
        capital_at_risk = risk_per_share * quantity
        risk_pct = capital_at_risk / capital if capital > 0 else 1.0

        # --- 5 Risk Checks ---
        checks = {}

        # Check 1: Single trade risk <= 2%
        max_risk = capital * RISK_LIMITS["max_single_trade_risk_pct"]
        checks["single_trade_risk"] = capital_at_risk <= max_risk

        # Check 2: Daily loss budget available
        max_daily_loss = capital * RISK_LIMITS["max_daily_loss_pct"]
        remaining_budget = max_daily_loss - abs(min(self._todays_pnl, 0))
        checks["daily_loss_budget"] = remaining_budget > capital_at_risk

        # Check 3: Not exceeding max positions (counted per bucket)
        positions = self.redis.get_state("state:positions") or {}
        open_count = len([p for p in positions.get("positions", [])
                         if p.get("status") == "OPEN"
                         and p.get("bucket", "conservative") == bucket])
        max_pos = (RISK_LIMITS["max_risk_positions"] if bucket == "risk"
                   else RISK_LIMITS["max_simultaneous_positions"])
        checks["max_positions"] = open_count < max_pos

        # Check 4: Not in cooldown
        if self._cooldown_until and datetime.now(IST) < self._cooldown_until:
            self._in_cooldown = True
        else:
            self._in_cooldown = False
            self._cooldown_until = None
        checks["not_in_cooldown"] = not self._in_cooldown

        # Check 5: Stop-loss is logical (not too tight or too wide)
        atr_reasonable = risk_per_share > 0 and risk_pct < 0.05
        checks["stop_loss_logical"] = atr_reasonable

        # --- Decision ---
        all_passed = all(checks.values())

        # Calculate proper position size
        if all_passed and risk_per_share > 0:
            approved_size = int(max_risk / risk_per_share)
            approved_size = max(1, min(approved_size, quantity))
        else:
            approved_size = 0

        # Find the failing check
        failing_check = None
        if not all_passed:
            for check_name, passed in checks.items():
                if not passed:
                    failing_check = check_name
                    break

        # Use LLM for nuanced review (enhances but never overrides rule checks)
        llm_review = self._llm_trade_review(
            proposal, checks, capital_at_risk, risk_pct, remaining_budget
        )

        # LLM can flag for human attention but cannot approve a failed rule check
        flag_human = risk_pct > 0.015 or llm_review.get("flag_human", False)

        decision = RiskDecision(
            proposal_id=proposal.get("proposal_id", ""),
            decision="APPROVED" if all_passed else "REJECTED",
            reason=(
                llm_review.get("reason", f"All 5 checks passed. Risk: {risk_pct:.1%}")
                if all_passed
                else f"Failed check: {failing_check}"
            ),
            approved_position_size=approved_size,
            approved_stop_loss=llm_review.get("approved_stop_loss", stop_loss),
            approved_target=llm_review.get("approved_target", proposal.get("target", 0)),
            risk_pct_final=risk_pct,
            flag_human=flag_human,
        )

        # Send decision to orchestrator
        self.send_message(
            to_agent="orchestrator",
            msg_type=MessageType.SIGNAL,
            payload={
                **decision.model_dump(),
                "symbol": symbol,
                "entry_price": entry_price,
                "bucket": bucket,
                "transaction_type": "BUY" if proposal.get("direction") == "LONG" else "SELL",
                "checks": checks,
            },
            priority=Priority.HIGH,
            correlation_id=message.message_id,
        )

        if all_passed:
            self.logger.info(
                f"APPROVED {symbol}: risk={risk_pct:.1%}, size={approved_size}"
            )
        else:
            self.logger.info(
                f"REJECTED {symbol}: risk={risk_pct:.1%}, size={approved_size}, "
                f"failed={failing_check}, checks={checks}"
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

    def run(self, state: dict) -> dict:
        """LangGraph node: review pending signals."""
        # In graph mode, signals come through state rather than messages
        pending = state.get("pending_signals", [])
        state["approved_orders"] = []
        state["rejected_proposals"] = []

        self.logger.info(f"Reviewing {len(pending)} pending signals")
        return state
