"""Strategist Agent — Conservative bucket strategy selection.

Selects ONE trading strategy every morning based on market conditions.
Evidence-driven and cautious — prefers inaction over uncertain action.
"""

from datetime import datetime

from agents.base_agent import BaseAgent
from agents.message import AgentMessage, MessageType, Priority, StrategyConfig
from config import CONSERVATIVE_STRATEGIES, DEFAULT_WATCHLIST


VIX_CHANGE_THRESHOLD = 3.0   # re-evaluate if VIX moved > 3 points
SENTIMENT_FLIP = {
    "BULLISH": "BEARISH", "BEARISH": "BULLISH",
}


class StrategistAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store):
        super().__init__("strategist", redis_store, sqlite_store)
        self._todays_strategy: dict | None = None
        self._morning_vix: float | None = None
        self._morning_sentiment: str | None = None

    def on_start(self):
        self.logger.info("Strategist ready for morning strategy selection")

    def on_stop(self):
        pass

    def on_wake(self):
        """Load relevant learnings from knowledge graph into system prompt."""
        from memory.knowledge_graph import load_memories
        regime = "unknown"
        strategy_data = self.redis.get_state("state:active_strategy") or {}
        if strategy_data:
            regime = strategy_data.get("regime", "unknown")
        memories = load_memories(self.sqlite, "strategist", regime, "all")
        if memories:
            self._extra_context = memories
            self.logger.info(f"Loaded {len(memories.splitlines())} learnings from knowledge graph")

    def on_message(self, message: AgentMessage):
        if message.type == MessageType.COMMAND:
            command = message.payload.get("command", "")
            if command == "SELECT_STRATEGY":
                self._select_morning_strategy(message.payload)
            elif command == "REEVAL_STRATEGY":
                self._midday_reevaluation()
            elif command == "REVIEW_STRATEGY":
                self._review_strategy(message.payload)
        elif message.type == MessageType.REQUEST:
            if message.payload.get("request") == "current_strategy":
                self._respond_current_strategy(message)

    def _select_morning_strategy(self, data: dict):
        """Select today's conservative trading strategy using LLM."""
        from config import CAPITAL
        market = self.redis.get_market_data("data:market_snapshot") or {}
        vix_data = market.get("indiavix", market.get("vix", {}))
        nifty = market.get("nifty", {})

        self.logger.info(
            f"Market data for strategy: Nifty={nifty.get('ltp', 'N/A')}, "
            f"BankNifty={market.get('banknifty', {}).get('ltp', 'N/A')}, "
            f"VIX={vix_data.get('ltp', 'N/A')}"
        )

        # Pull news summary from Redis (populated by data agent)
        news = self.redis.get_market_data("data:news_summary") or {}
        global_cues = news.get("global_cues", {})
        global_summary = (
            f"US: {global_cues.get('us_markets', 'N/A')} | "
            f"Asia: {global_cues.get('asian_markets', 'N/A')} | "
            f"Crude: {global_cues.get('crude_oil', 'N/A')} | "
            f"DXY/INR: {global_cues.get('dxy_usdinr', 'N/A')}"
        ) if global_cues else "No data"

        risk_events = news.get("risk_events_next_24h", [])
        economic_events = "\n".join(f"- {e}" for e in risk_events) if risk_events else "None"

        fii_dii = news.get("fii_dii_flow", "N/A")

        self.logger.info(
            f"News for strategy: sentiment={news.get('overall_sentiment', 'N/A')}, "
            f"headlines={len(news.get('headlines', []))}"
        )

        variables = {
            "capital": CAPITAL["conservative_bucket"],
            "trend_direction": nifty.get("change", "unknown"),
            "adx_value": "N/A",
            "nifty_close": nifty.get("ltp", "N/A"),
            "banknifty_close": market.get("banknifty", {}).get("ltp", "N/A"),
            "vix_current": vix_data.get("ltp", 15),
            "vix_avg": "N/A",
            "fii_3day": fii_dii,
            "global_summary": global_summary,
            "sgx_nifty": "N/A",
            "economic_events": economic_events,
            "available_capital": CAPITAL["conservative_bucket"],
            "swing_positions": 0,
            "yesterday_pnl": data.get("yesterday_pnl", 0),
        }

        try:
            result = self.call_llm("PROMPT_MORNING_STRATEGY_SELECTION", variables)

            # Validate strategy name
            strategy_name = result.get("strategy", "NO_TRADE")
            if strategy_name not in CONSERVATIVE_STRATEGIES:
                self.logger.warning(
                    f"LLM suggested unknown strategy '{strategy_name}', "
                    f"falling back to NO_TRADE"
                )
                strategy_name = "NO_TRADE"
                result["strategy"] = strategy_name

            self._todays_strategy = result
        except Exception as e:
            self.logger.error(f"Strategy selection LLM failed: {e}, using fallback")
            self._todays_strategy = self._fallback_strategy(vix_data.get("ltp", 15))

        # Snapshot for mid-day comparison
        self._morning_vix = vix_data.get("ltp")
        self._morning_sentiment = news.get("overall_sentiment")

        # Send to orchestrator
        self.send_message(
            to_agent="orchestrator",
            msg_type=MessageType.SIGNAL,
            payload={
                "signal": "strategy_proposal",
                "bucket": "conservative",
                **self._todays_strategy,
            },
            priority=Priority.HIGH,
        )
        self.logger.info(f"Strategy selected: {strategy_name}")

    def _midday_reevaluation(self):
        """12 PM check — only switch strategy if conditions changed materially."""
        if not self._todays_strategy:
            self.logger.info("No morning strategy set — skipping mid-day reeval")
            return

        market = self.redis.get_market_data("data:market_snapshot") or {}
        vix_data = market.get("indiavix", market.get("vix", {}))
        current_vix = vix_data.get("ltp")

        news = self.redis.get_market_data("data:news_summary") or {}
        current_sentiment = news.get("overall_sentiment", "UNKNOWN")

        # Check if VIX moved significantly
        vix_changed = False
        if self._morning_vix is not None and current_vix is not None:
            vix_delta = abs(float(current_vix) - float(self._morning_vix))
            if vix_delta >= VIX_CHANGE_THRESHOLD:
                vix_changed = True
                self.logger.info(
                    f"VIX shifted {vix_delta:.1f} pts "
                    f"({self._morning_vix} → {current_vix})"
                )

        # Check if sentiment flipped (BULLISH↔BEARISH)
        sentiment_flipped = (
            self._morning_sentiment is not None
            and SENTIMENT_FLIP.get(self._morning_sentiment) == current_sentiment
        )
        if sentiment_flipped:
            self.logger.info(
                f"Sentiment flipped: {self._morning_sentiment} → {current_sentiment}"
            )

        if not vix_changed and not sentiment_flipped:
            self.logger.info(
                f"Mid-day reeval: no material change "
                f"(VIX {self._morning_vix}→{current_vix}, "
                f"sentiment {self._morning_sentiment}→{current_sentiment}). "
                f"Keeping {self._todays_strategy.get('strategy')}"
            )
            self.send_message(
                to_agent="orchestrator",
                msg_type=MessageType.SIGNAL,
                payload={
                    "signal": "midday_reeval",
                    "changed": False,
                    "strategy": self._todays_strategy.get("strategy"),
                    "vix_morning": self._morning_vix,
                    "vix_now": current_vix,
                    "sentiment_morning": self._morning_sentiment,
                    "sentiment_now": current_sentiment,
                },
            )
            return

        # Material change detected — re-select strategy
        self.logger.info(
            "Mid-day reeval: material change detected, re-selecting strategy"
        )
        self._select_morning_strategy({})

    def _review_strategy(self, data: dict):
        """Review today's strategy performance (3:45 PM) using LLM."""
        if not self._todays_strategy:
            self.logger.info("No strategy to review")
            return

        try:
            result = self.call_llm("PROMPT_STRATEGY_REVIEW", {
                "capital": CAPITAL["conservative_bucket"],
                "strategy_name": self._todays_strategy.get("strategy", "N/A"),
                "morning_rationale": self._todays_strategy.get("rationale", "N/A"),
                "regime_forecast": self._todays_strategy.get("regime", "N/A"),
                "trades_taken": data.get("trades_taken", 0),
                "wins": data.get("wins", 0),
                "losses": data.get("losses", 0),
                "pnl": data.get("pnl", 0),
                "deviation": data.get("deviation", "None"),
                "actual_regime": data.get("actual_regime", "Unknown"),
            })
            self.logger.info(f"Strategy review: {result}")
        except Exception as e:
            self.logger.error(f"Strategy review LLM failed: {e}")

    def _fallback_strategy(self, vix: float) -> dict:
        """Rule-based fallback when LLM is unavailable.

        3-tier VIX framework:
        - VIX > 32:  NO_TRADE (extreme fear)
        - VIX 22-32: VOLATILITY_ADJUSTED_SWING (high-VIX regime)
        - VIX > 18:  NIFTY_OPTIONS_BUYING (elevated)
        - VIX <= 18: RSI_MEAN_REVERSION (normal)
        """
        if vix > 32:
            return {"strategy": "NO_TRADE",
                    "rationale": f"VIX {vix} extreme — capital preservation",
                    "watchlist": [], "confidence": "LOW"}
        elif vix >= 22:
            return {"strategy": "VOLATILITY_ADJUSTED_SWING",
                    "rationale": f"High VIX ({vix}) — swing with wider stops and reduced size",
                    "watchlist": DEFAULT_WATCHLIST,
                    "entry_conditions": {"indicator": "ADX", "entry_threshold": "28",
                                         "volume_confirmation": True, "direction": "LONG"},
                    "exit_conditions": {"target_pct": 5.5, "stop_loss_pct": 3.5,
                                        "time_exit": "15:00", "trailing_stop": True},
                    "capital_allocation_pct": 30, "max_trades": 1,
                    "regime": "HIGH_VOLATILITY", "confidence": "MEDIUM"}
        elif vix > 18:
            return {"strategy": "NIFTY_OPTIONS_BUYING",
                    "rationale": f"Elevated VIX ({vix})",
                    "watchlist": ["NIFTY"], "confidence": "MEDIUM"}
        return {"strategy": "RSI_MEAN_REVERSION",
                "rationale": f"Stable VIX ({vix})",
                "watchlist": DEFAULT_WATCHLIST,
                "entry_conditions": {"indicator": "RSI", "entry_threshold": "32",
                                     "volume_confirmation": True, "direction": "LONG"},
                "exit_conditions": {"target_pct": 2.0, "stop_loss_pct": 1.5,
                                    "time_exit": "15:00", "trailing_stop": False},
                "capital_allocation_pct": 40, "max_trades": 2,
                "regime": "RANGING", "confidence": "MEDIUM"}

    def _respond_current_strategy(self, message: AgentMessage):
        self.send_message(
            to_agent=message.from_agent,
            msg_type=MessageType.RESPONSE,
            payload={"strategy": self._todays_strategy},
            correlation_id=message.message_id,
        )

    def run(self, state: dict) -> dict:
        """LangGraph node: morning strategy selection."""
        self._select_morning_strategy(state)
        if self._todays_strategy:
            state["conservative_strategy"] = self._todays_strategy
        return state
