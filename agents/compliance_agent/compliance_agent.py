"""Compliance Agent — Auditor and rule enforcer.

Watches everything, records with perfect accuracy, flags violations.
The agent that would survive a regulatory review.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from agents.base_agent import BaseAgent
from agents.message import AgentMessage, MessageType, Priority
from config import CAPITAL, RISK_LIMITS


class ComplianceAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store):
        super().__init__("compliance_agent", redis_store, sqlite_store)
        self._todays_trades: list[dict] = []
        self._violations: list[dict] = []

    def on_start(self):
        self.logger.info("Compliance Agent ready for monitoring")

    def on_stop(self):
        pass

    def on_message(self, message: AgentMessage):
        if message.type == MessageType.SIGNAL:
            # Trade record from execution agent
            if message.from_agent == "execution_agent":
                self._record_trade(message.payload)
        elif message.type == MessageType.COMMAND:
            command = message.payload.get("command", "")
            if command == "RUN_AUDIT":
                self._run_eod_audit()

    def _record_trade(self, trade_data: dict):
        """Record a trade for today's audit."""
        self._todays_trades.append(trade_data)
        self.logger.info(
            f"Trade recorded: {trade_data.get('symbol')} "
            f"{trade_data.get('transaction_type')}"
        )

        # Real-time violation checks
        self._check_realtime_violations(trade_data)

    def _check_realtime_violations(self, trade: dict):
        """Check for violations in real-time as trades come in."""
        # Check options trade limit
        if trade.get("bucket") == "risk":
            cost = trade.get("fill_price", 0) * trade.get("quantity", 0)
            if cost > RISK_LIMITS["max_options_trade_inr"]:
                self._flag_violation(
                    trade_id=trade.get("order_id", ""),
                    rule="max_options_trade",
                    details=(
                        f"Options trade cost INR {cost:.0f} exceeds "
                        f"limit of INR {RISK_LIMITS['max_options_trade_inr']}"
                    ),
                    severity="HIGH",
                    agent="execution_agent",
                )

    def _flag_violation(self, trade_id: str, rule: str, details: str,
                        severity: str, agent: str):
        """Flag a rule violation."""
        violation = {
            "trade_id": trade_id,
            "rule_violated": rule,
            "details": details,
            "severity": severity,
            "responsible_agent": agent,
            "timestamp": datetime.now(IST).isoformat(),
        }
        self._violations.append(violation)
        self.logger.warning(f"VIOLATION: {rule} — {details}")

        # Alert orchestrator for HIGH severity
        if severity == "HIGH":
            self.send_message(
                to_agent="orchestrator",
                msg_type=MessageType.ALERT,
                payload={"alert": "compliance_violation", **violation},
                priority=Priority.HIGH,
            )

    def _run_eod_audit(self):
        """Run end-of-day compliance audit using rule checks + LLM analysis."""
        import json
        today = datetime.now(IST).strftime("%Y-%m-%d")
        trades = self.sqlite.get_trades(date=today)

        # Reset violations for fresh audit
        self._violations = []

        # Rule-based checks first
        capital = CAPITAL["conservative_bucket"]
        max_single_risk = capital * RISK_LIMITS["max_single_trade_risk_pct"]
        max_daily_loss = capital * RISK_LIMITS["max_daily_loss_pct"]

        largest_risk = 0
        max_open = 0
        daily_pnl = 0

        for trade in trades:
            entry = trade.get("entry_price", 0)
            stop = trade.get("stop_loss", 0)
            qty = trade.get("quantity", 0)
            pnl = trade.get("pnl", 0) or 0
            daily_pnl += pnl

            if entry and stop and qty:
                risk = abs(entry - stop) * qty
                largest_risk = max(largest_risk, risk)
                if risk > max_single_risk:
                    self._flag_violation(
                        trade_id=trade.get("trade_id", ""),
                        rule="max_single_trade_risk",
                        details=f"Risk INR {risk:.0f} > limit INR {max_single_risk:.0f}",
                        severity="HIGH",
                        agent="risk_agent",
                    )

        # Use LLM for comprehensive audit analysis
        try:
            llm_audit = self.call_llm("PROMPT_EOD_AUDIT", {
                "trades_json": json.dumps(trades[:20], default=str),
                "max_single_risk": max_single_risk,
                "max_daily_loss": max_daily_loss,
                "max_positions": RISK_LIMITS["max_simultaneous_positions"],
                "largest_risk": largest_risk,
                "trade_count": len(trades),
                "max_open": max_open,
                "after_time_positions": 0,
                "averaging_detected": False,
                "daily_pnl": daily_pnl,
            })

            # Merge LLM-detected violations with rule-based ones
            llm_violations = llm_audit.get("violations", [])
            for v in llm_violations:
                if v not in self._violations:
                    self._violations.append(v)

            compliance_score = llm_audit.get("compliance_score", 100)
            notes = llm_audit.get("notes", "")
        except Exception as e:
            self.logger.error(f"Compliance audit LLM failed: {e}")
            compliance_score = (
                100 if not self._violations
                else max(0, 100 - len(self._violations) * 20)
            )
            notes = f"Rule-based audit only (LLM unavailable). {datetime.now(IST).isoformat()}"

        # Compile audit report
        audit_report = {
            "audit_date": today,
            "total_trades": len(trades),
            "violations": self._violations,
            "compliance_score": compliance_score,
            "notes": notes,
            "report_json": {
                "trades_audited": len(trades),
                "violations_found": len(self._violations),
                "violation_details": self._violations,
            },
        }

        # Save to SQLite
        try:
            self.sqlite.log_audit(audit_report)
        except Exception as e:
            self.logger.error(f"Failed to save audit: {e}")

        # Report to orchestrator
        self.send_message(
            to_agent="orchestrator",
            msg_type=MessageType.RESPONSE,
            payload={
                "report": "eod_audit",
                "date": today,
                "trades": len(trades),
                "violations": len(self._violations),
                "compliance_score": compliance_score,
            },
        )

        self.logger.info(
            f"EOD Audit complete: {len(trades)} trades, "
            f"{len(self._violations)} violations, "
            f"score={compliance_score}"
        )

    def run(self, state: dict) -> dict:
        """LangGraph node: run EOD audit."""
        self._run_eod_audit()
        return state
