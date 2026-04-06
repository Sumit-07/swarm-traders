"""Risk Strategist Agent — Risk bucket strategy selection.

Manages the monthly risk allocation for high-risk, high-reward options trades.
Disciplined speculator — thinks in expected value, not win rate.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from agents.base_agent import BaseAgent
from agents.message import AgentMessage, MessageType, Priority
from config import CAPITAL, RISK_LIMITS, RISK_STRATEGIES


class RiskStrategistAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store):
        super().__init__("risk_strategist", redis_store, sqlite_store)
        self._todays_strategy: dict | None = None
        self._monthly_allocation_used: float = 0

    def on_start(self):
        self.logger.info("Risk Strategist ready for risk bucket strategy selection")

    def on_stop(self):
        pass

    def on_wake(self):
        """Load relevant learnings from knowledge graph into system prompt."""
        from memory.knowledge_graph import load_memories
        regime = "unknown"
        strategy_data = self.redis.get_state("state:active_strategy") or {}
        if strategy_data:
            regime = strategy_data.get("regime", "unknown")
        memories = load_memories(self.sqlite, "risk_strategist", regime, "options")
        if memories:
            self._extra_context = memories
            self.logger.info(f"Loaded {len(memories.splitlines())} learnings from knowledge graph")

    def on_message(self, message: AgentMessage):
        if message.type == MessageType.COMMAND:
            command = message.payload.get("command", "")
            if command == "SELECT_STRATEGY":
                self._select_risk_strategy(message.payload)

    def _select_risk_strategy(self, data: dict):
        """Select today's risk bucket strategy using LLM."""
        remaining = CAPITAL["risk_bucket_monthly"] - self._monthly_allocation_used

        if remaining <= 0:
            self._todays_strategy = {
                "strategy": "NO_TRADE",
                "rationale": "Monthly risk allocation fully deployed",
            }
        else:
            market = self.redis.get_market_data("data:market_snapshot") or {}
            vix_data = market.get("indiavix", market.get("vix", {}))
            nifty = market.get("nifty", {})

            try:
                result = self.call_llm("PROMPT_RISK_STRATEGY_SELECTION", {
                    "allocation_used": self._monthly_allocation_used,
                    "allocation_remaining": remaining,
                    "calendar_events": data.get("economic_events", "None"),
                    "vix": vix_data.get("ltp", "N/A"),
                    "nifty_atm": "N/A",
                    "banknifty_atm": "N/A",
                    "expiry_date": "N/A",
                    "dte": "N/A",
                    "call_premium": "N/A",
                    "put_premium": "N/A",
                    "iv_percentile": "N/A",
                    "day_of_week": datetime.now(IST).strftime("%A"),
                    "nifty_trend": nifty.get("change", "unknown"),
                    "banknifty_trend": market.get("banknifty", {}).get("change", "unknown"),
                    "fii_options_summary": "N/A",
                })
                # Validate strategy name
                strategy_name = result.get("strategy", "NO_TRADE")
                if strategy_name not in RISK_STRATEGIES:
                    self.logger.warning(
                        f"LLM suggested unknown risk strategy '{strategy_name}', "
                        f"falling back to NO_TRADE"
                    )
                    result = {"strategy": "NO_TRADE",
                              "rationale": f"Unknown strategy '{strategy_name}'"}

                # Validate total cost within remaining budget
                total_cost = result.get("total_cost", 0)
                if total_cost > remaining:
                    self.logger.warning(
                        f"LLM risk strategy cost {total_cost} exceeds "
                        f"remaining {remaining}, falling back to NO_TRADE"
                    )
                    result = {"strategy": "NO_TRADE",
                              "rationale": "Cost exceeds remaining allocation"}
                self._todays_strategy = result
            except Exception as e:
                self.logger.error(f"Risk strategy LLM failed: {e}")
                self._todays_strategy = {
                    "strategy": "NO_TRADE",
                    "rationale": f"LLM unavailable — conservative fallback",
                    "confidence": "LOW",
                    "allocation_remaining": remaining,
                }

        # Send to orchestrator
        self.send_message(
            to_agent="orchestrator",
            msg_type=MessageType.SIGNAL,
            payload={
                "signal": "strategy_proposal",
                "bucket": "risk",
                **self._todays_strategy,
            },
            priority=Priority.HIGH,
        )
        self.logger.info(f"Risk strategy: {self._todays_strategy['strategy']}")

    def run(self, state: dict) -> dict:
        """LangGraph node: morning risk strategy selection."""
        self._select_risk_strategy(state)
        if self._todays_strategy:
            state["risk_strategy"] = self._todays_strategy
        return state
