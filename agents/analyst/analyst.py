"""Analyst Agent — Signal generator.

Takes strategy config from Strategist and generates trade signals
by calculating indicators and validating entry conditions.
Disciplined and precise — follows the config exactly.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from agents.base_agent import BaseAgent
from agents.message import (
    AgentMessage, MessageType, Priority, TradeProposal,
)
from config import CAPITAL, RISK_LIMITS


class AnalystAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store):
        super().__init__("analyst", redis_store, sqlite_store)
        self._strategy_config: dict | None = None
        self._pending_signals: list = []  # max 2 proposals in queue

    def on_start(self):
        self.logger.info("Analyst ready for signal generation")

    def on_stop(self):
        pass

    def on_wake(self):
        """Load relevant learnings from knowledge graph into system prompt."""
        from memory.knowledge_graph import load_memories
        regime = "unknown"
        strategy_type = "all"
        strategy_data = self.redis.get_state("state:active_strategy") or {}
        if strategy_data:
            regime = strategy_data.get("regime", "unknown")
            strategy_type = strategy_data.get("strategy", "all")
        memories = load_memories(self.sqlite, "analyst", regime, strategy_type)
        if memories:
            self._extra_context = memories
            self.logger.info(f"Loaded {len(memories.splitlines())} learnings from knowledge graph")

    def on_message(self, message: AgentMessage):
        if message.type == MessageType.COMMAND:
            command = message.payload.get("command", "")
            if command == "SET_STRATEGY":
                self._strategy_config = message.payload
                self.logger.info(
                    f"Strategy config received: {message.payload.get('strategy_name')}"
                )
            elif command == "SCAN_WATCHLIST":
                self._scan_watchlist()
            elif command == "HALT":
                self._strategy_config = None
                self.logger.info("Analyst halted — clearing strategy config")

    def _scan_watchlist(self):
        """Scan watchlist symbols against strategy entry conditions."""
        if not self._strategy_config:
            self.logger.info("No strategy config — skipping scan")
            return

        watchlist = self._strategy_config.get("watchlist", [])
        strategy = self._strategy_config.get("strategy_name", "")
        self.logger.info(f"Scanning {len(watchlist)} symbols for {strategy}")

        for symbol in watchlist:
            tick_data = self.redis.get_market_data(f"data:watchlist_ticks:{symbol}")
            if not tick_data:
                continue

            signal = self._check_entry_conditions(symbol, tick_data, strategy)
            if signal:
                # Validate signal with LLM for additional context
                validated = self._validate_signal_with_llm(signal, tick_data)
                if validated and validated.get("signal_valid", False):
                    signal["confidence"] = validated.get("confidence", "MEDIUM")
                    signal["analyst_note"] = validated.get("analyst_note", "")
                    if validated.get("suggested_target"):
                        signal["suggested_target"] = validated["suggested_target"]
                    if validated.get("suggested_stop"):
                        signal["suggested_stop"] = validated["suggested_stop"]
                    self._submit_trade_proposal(signal)
                elif validated:
                    self.logger.info(
                        f"Signal invalidated by LLM for {symbol}: "
                        f"{validated.get('invalidation_reason', 'N/A')}"
                    )

    def _check_entry_conditions(self, symbol: str, tick_data: dict,
                                strategy: str) -> dict | None:
        """Check if entry conditions are met for a symbol (rule-based pre-filter)."""
        rsi = tick_data.get("rsi")
        volume_ratio = tick_data.get("volume_ratio")
        close = tick_data.get("close")

        if rsi is None or close is None:
            return None

        entry_conditions = self._strategy_config.get("entry_conditions", {})
        direction = entry_conditions.get("direction", "LONG")

        # RSI Mean Reversion: buy when RSI < 32
        if strategy == "RSI_MEAN_REVERSION" and direction == "LONG":
            try:
                threshold = float(entry_conditions.get("entry_threshold", 32))
            except (ValueError, TypeError):
                threshold = 32
            needs_volume = entry_conditions.get("volume_confirmation", True)

            if rsi < threshold:
                if needs_volume and volume_ratio and volume_ratio < 1.2:
                    return None
                return {
                    "symbol": symbol,
                    "direction": "LONG",
                    "signal_type": "RSI_OVERSOLD",
                    "entry_price": close,
                    "rsi": rsi,
                    "volume_ratio": volume_ratio,
                }

        # Opening Range Breakout
        if strategy == "OPENING_RANGE_BREAKOUT":
            pass

        # ADX-based strategies: SWING_MOMENTUM and VOLATILITY_ADJUSTED_SWING
        if strategy in ("SWING_MOMENTUM", "VOLATILITY_ADJUSTED_SWING"):
            adx = tick_data.get("adx")
            if adx is None:
                return None
            try:
                threshold = float(entry_conditions.get("entry_threshold", 25))
            except (ValueError, TypeError):
                threshold = 28 if strategy == "VOLATILITY_ADJUSTED_SWING" else 25
            needs_volume = entry_conditions.get("volume_confirmation", True)

            if adx > threshold and direction == "LONG":
                # RSI should be in momentum range (55-70)
                if rsi is not None and not (55 <= rsi <= 70):
                    return None
                if needs_volume and volume_ratio and volume_ratio < 1.3:
                    return None
                return {
                    "symbol": symbol,
                    "direction": "LONG",
                    "signal_type": "ADX_MOMENTUM",
                    "entry_price": close,
                    "rsi": rsi,
                    "adx": adx,
                    "volume_ratio": volume_ratio,
                }

        return None

    def _validate_signal_with_llm(self, signal: dict,
                                   tick_data: dict) -> dict | None:
        """Use LLM to validate a signal with broader market context."""
        snapshot = self.redis.get_market_data("data:market_snapshot") or {}
        nifty = snapshot.get("nifty", {})

        try:
            result = self.call_llm("PROMPT_SIGNAL_VALIDATION", {
                "strategy_name": self._strategy_config.get("strategy_name", ""),
                "strategy_confidence": self._strategy_config.get("confidence", "MEDIUM"),
                "available_capital": self._strategy_config.get("available_capital", CAPITAL["conservative_bucket"]),
                "symbol": signal["symbol"],
                "signal_type": signal["direction"],
                "trigger_indicator": signal.get("signal_type", "RSI"),
                "trigger_value": signal.get("rsi", "N/A"),
                "entry_condition_spec": str(self._strategy_config.get("entry_conditions", {})),
                "rsi": tick_data.get("rsi", "N/A"),
                "macd_value": tick_data.get("macd", "N/A"),
                "macd_signal": tick_data.get("macd_signal", "N/A"),
                "macd_hist": "N/A",
                "vwap": tick_data.get("vwap", "N/A"),
                "current_price": tick_data.get("close", "N/A"),
                "vwap_deviation": "N/A",
                "current_volume": tick_data.get("volume", "N/A"),
                "avg_volume": "N/A",
                "volume_ratio": tick_data.get("volume_ratio", "N/A"),
                "atr": tick_data.get("atr", "N/A"),
                "atr_pct": "N/A",
                "day_low": "N/A",
                "day_high": "N/A",
                "nifty_direction": nifty.get("change", "N/A"),
                "nifty_change": "N/A",
                "signal_time": datetime.now(IST).strftime("%H:%M IST"),
                "minutes_open": "N/A",
                "stock_news": "None",
            })
            return result
        except Exception as e:
            self.logger.error(f"Signal validation LLM failed: {e}")
            # Fall through — accept signal without LLM validation
            return {"signal_valid": True, "confidence": "MEDIUM",
                    "analyst_note": "LLM unavailable — accepted by rule-based filter"}

    def _submit_trade_proposal(self, signal: dict):
        """Submit a trade proposal to the risk agent."""
        if len(self._pending_signals) >= 2:
            self.logger.info("Max 2 pending signals — skipping new signal")
            return

        # Get stop/target — check exit_conditions dict first, then top-level keys
        exit_conditions = self._strategy_config.get("exit_conditions", {})
        target_pct = exit_conditions.get("target_pct") or self._strategy_config.get("target_pct", 2.0)
        stop_pct = exit_conditions.get("stop_loss_pct") or self._strategy_config.get("stop_loss_pct", 1.5)
        entry_price = signal["entry_price"]
        strategy_name = self._strategy_config.get("strategy_name", "")

        # Position sizing: use risk-based sizing (1.5% capital at risk)
        capital = CAPITAL["conservative_bucket"]
        max_risk = capital * RISK_LIMITS["max_single_trade_risk_pct"]
        risk_per_share = entry_price * stop_pct / 100
        quantity = max(1, int(max_risk / risk_per_share)) if risk_per_share > 0 else 1

        # VAS: apply 0.57× position size modifier
        if strategy_name == "VOLATILITY_ADJUSTED_SWING":
            quantity = max(1, int(quantity * 0.57))

        # Build analyst note based on signal type
        if signal.get("signal_type") == "ADX_MOMENTUM":
            note = (
                f"ADX at {signal.get('adx', 0):.1f}, RSI at {signal.get('rsi', 0):.1f} "
                f"with volume {signal.get('volume_ratio', 0):.1f}x average"
            )
        else:
            note = (
                f"RSI at {signal.get('rsi', 0):.1f} with "
                f"volume {signal.get('volume_ratio', 0):.1f}x average"
            )

        proposal = TradeProposal(
            symbol=signal["symbol"],
            direction=signal["direction"],
            signal_type=signal["signal_type"],
            entry_price=entry_price,
            quantity_suggested=quantity,
            stop_loss=round(entry_price * (1 - stop_pct / 100), 2),
            target=round(entry_price * (1 + target_pct / 100), 2),
            signal_confidence="MEDIUM",
            indicator_snapshot={
                "rsi": signal.get("rsi"),
                "adx": signal.get("adx"),
                "volume_ratio": signal.get("volume_ratio"),
            },
            bucket=self._strategy_config.get("bucket", "conservative"),
            analyst_note=note,
        )

        self._pending_signals.append(proposal.proposal_id)

        self.send_message(
            to_agent="risk_agent",
            msg_type=MessageType.SIGNAL,
            payload=proposal.model_dump(),
            priority=Priority.HIGH,
            requires_response=True,
        )
        self.logger.info(
            f"Trade proposal submitted: {signal['symbol']} "
            f"{signal['direction']} @ {entry_price}"
        )

        # Log signal to SQLite
        try:
            self.sqlite.log_signal({
                "signal_id": proposal.proposal_id,
                "symbol": signal["symbol"],
                "strategy": self._strategy_config.get("strategy_name", ""),
                "signal_type": signal["signal_type"],
                "indicator_snapshot": signal,
                "confidence": "MEDIUM",
                "valid": 1,
                "invalidation_reason": None,
            })
        except Exception as e:
            self.logger.warning(f"Failed to log signal: {e}")

    def run(self, state: dict) -> dict:
        """LangGraph node: scan watchlist for signals."""
        strategy = state.get("conservative_strategy")
        if strategy:
            self._strategy_config = {
                "strategy_name": strategy.get("strategy"),
                "watchlist": strategy.get("watchlist", []),
                "entry_conditions": strategy.get("entry_conditions", {}),
                "exit_conditions": strategy.get("exit_conditions", {}),
                "target_pct": strategy.get("exit_conditions", {}).get("target_pct") or strategy.get("target_pct"),
                "stop_loss_pct": strategy.get("exit_conditions", {}).get("stop_loss_pct") or strategy.get("stop_loss_pct"),
                "bucket": "conservative",
            }
        self._scan_watchlist()
        state["pending_signals"] = self._pending_signals.copy()
        return state
