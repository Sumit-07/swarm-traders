"""Analyst Agent — Signal generator.

Takes strategy config from Strategist and generates trade signals
by calculating indicators and validating entry conditions.
Disciplined and precise — follows the config exactly.
"""

import time as _time
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from agents.base_agent import BaseAgent
from agents.message import (
    AgentMessage, MessageType, Priority, TradeProposal,
)
from config import CAPITAL, RISK_LIMITS, TRADING_HOURS, SWING_STRATEGIES
from tools.cost_estimator import (
    estimate_equity_roundtrip_cost,
    estimate_options_roundtrip_cost,
    is_trade_viable,
)


class AnalystAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store):
        super().__init__("analyst", redis_store, sqlite_store)
        self._strategy_config: dict | None = None
        self._pending_signals: dict = {}  # proposal_id -> timestamp

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
        elif message.type == MessageType.RESPONSE:
            # Risk agent decision — clear pending signal
            proposal_id = message.payload.get("proposal_id")
            self._pending_signals.pop(proposal_id, None)
            self.logger.debug(
                f"Proposal {proposal_id} resolved ({message.payload.get('decision')}), "
                f"pending: {len(self._pending_signals)}"
            )

    def _scan_watchlist(self):
        """Scan watchlist symbols against strategy entry conditions."""
        # Clear stale pending signals (>2 min without risk agent response)
        now = _time.time()
        stale = [pid for pid, ts in self._pending_signals.items() if now - ts > 120]
        for pid in stale:
            self.logger.warning(f"Clearing stale pending signal {pid} (>2 min)")
            self._pending_signals.pop(pid)

        # Block intraday strategies after no_new_trades cutoff
        strategy_name = (self._strategy_config or {}).get("strategy_name", "")
        if strategy_name not in SWING_STRATEGIES:
            if datetime.now(IST).strftime("%H:%M") >= TRADING_HOURS["no_new_trades"]:
                self.logger.info("Past intraday cutoff — no new signals")
                return

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
                # Pre-LLM cost check — reject if profit < 2× costs
                cost_viable, cost_reason = self._validate_signal_cost(signal)
                if not cost_viable:
                    self.logger.info(f"Signal rejected by cost check for {symbol}: {cost_reason}")
                    continue

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

        # RSI Mean Reversion: LONG when RSI < 32, SHORT when RSI > 68
        if strategy == "RSI_MEAN_REVERSION":
            try:
                threshold = float(entry_conditions.get("entry_threshold", 32))
            except (ValueError, TypeError):
                threshold = 32
            short_threshold = entry_conditions.get("short_threshold", 68)
            needs_volume = entry_conditions.get("volume_confirmation", True)

            if rsi < threshold and direction in ("LONG", "BOTH"):
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
            if rsi > short_threshold and direction in ("SHORT", "BOTH"):
                if needs_volume and volume_ratio and volume_ratio < 1.2:
                    return None
                return {
                    "symbol": symbol,
                    "direction": "SHORT",
                    "signal_type": "RSI_OVERBOUGHT",
                    "entry_price": close,
                    "rsi": rsi,
                    "volume_ratio": volume_ratio,
                }

        # VWAP Reversion: LONG when price far below VWAP, SHORT when far above
        if strategy == "VWAP_REVERSION":
            vwap = tick_data.get("vwap")
            if vwap is None or vwap == 0:
                return None
            vwap_dev_pct = (close - vwap) / vwap * 100

            try:
                threshold = float(entry_conditions.get("entry_threshold", -1.2))
            except (ValueError, TypeError):
                threshold = -1.2

            if vwap_dev_pct < threshold and direction in ("LONG", "BOTH"):
                return {
                    "symbol": symbol,
                    "direction": "LONG",
                    "signal_type": "VWAP_BELOW",
                    "entry_price": close,
                    "rsi": rsi,
                    "vwap": vwap,
                    "vwap_deviation_pct": round(vwap_dev_pct, 2),
                    "volume_ratio": volume_ratio,
                }
            if vwap_dev_pct > abs(threshold) and direction in ("SHORT", "BOTH"):
                return {
                    "symbol": symbol,
                    "direction": "SHORT",
                    "signal_type": "VWAP_ABOVE",
                    "entry_price": close,
                    "rsi": rsi,
                    "vwap": vwap,
                    "vwap_deviation_pct": round(vwap_dev_pct, 2),
                    "volume_ratio": volume_ratio,
                }

        # Opening Range Breakout: LONG on upside breakout, SHORT on downside
        if strategy == "OPENING_RANGE_BREAKOUT":
            orb_high = tick_data.get("orb_high")
            orb_low = tick_data.get("orb_low")
            if orb_high is None or orb_low is None:
                return None
            needs_volume = entry_conditions.get("volume_confirmation", True)
            vol_threshold = entry_conditions.get("volume_threshold", 1.5)

            if close > orb_high and direction in ("LONG", "BOTH"):
                if needs_volume and volume_ratio and volume_ratio < vol_threshold:
                    return None
                return {
                    "symbol": symbol,
                    "direction": "LONG",
                    "signal_type": "ORB_BREAKOUT_UP",
                    "entry_price": close,
                    "rsi": rsi,
                    "orb_high": orb_high,
                    "orb_low": orb_low,
                    "volume_ratio": volume_ratio,
                }
            if close < orb_low and direction in ("SHORT", "BOTH"):
                if needs_volume and volume_ratio and volume_ratio < vol_threshold:
                    return None
                return {
                    "symbol": symbol,
                    "direction": "SHORT",
                    "signal_type": "ORB_BREAKOUT_DOWN",
                    "entry_price": close,
                    "rsi": rsi,
                    "orb_high": orb_high,
                    "orb_low": orb_low,
                    "volume_ratio": volume_ratio,
                }

        # ADX-based strategies: SWING_MOMENTUM and VOLATILITY_ADJUSTED_SWING
        # These remain LONG only — swing shorts carry overnight risk
        if strategy in ("SWING_MOMENTUM", "VOLATILITY_ADJUSTED_SWING"):
            adx = tick_data.get("adx")
            if adx is None:
                return None
            try:
                threshold = float(entry_conditions.get("entry_threshold", 25))
            except (ValueError, TypeError):
                threshold = 28 if strategy == "VOLATILITY_ADJUSTED_SWING" else 25
            needs_volume = entry_conditions.get("volume_confirmation", True)

            if adx > threshold and direction in ("LONG", "BOTH"):
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

    def _validate_signal_cost(self, signal: dict) -> tuple[bool, str]:
        """Pre-LLM cost check. Rejects signals where expected profit < 2x costs."""
        entry_price = signal.get("entry_price", 0)
        if entry_price <= 0:
            return False, "Entry price is zero or negative"

        exit_conditions = self._strategy_config.get("exit_conditions", {})
        target_pct = (
            exit_conditions.get("target_pct")
            or self._strategy_config.get("target_pct", 2.0)
        )
        stop_pct = (
            exit_conditions.get("stop_loss_pct")
            or self._strategy_config.get("stop_loss_pct", 1.5)
        )

        # Estimate position size (same logic as _submit_trade_proposal)
        capital = CAPITAL["conservative_bucket"]
        max_risk = capital * RISK_LIMITS["max_single_trade_risk_pct"]
        risk_per_share = entry_price * stop_pct / 100
        quantity = max(1, int(max_risk / risk_per_share)) if risk_per_share > 0 else 1

        strategy_name = self._strategy_config.get("strategy_name", "")
        if strategy_name == "VOLATILITY_ADJUSTED_SWING":
            quantity = max(1, int(quantity * 0.57))

        position_value = entry_price * quantity
        expected_gross = position_value * (target_pct / 100)

        cost = estimate_equity_roundtrip_cost(
            position_value_inr=position_value,
            is_intraday=strategy_name not in (
                "SWING_MOMENTUM", "VOLATILITY_ADJUSTED_SWING"
            ),
        )

        return is_trade_viable(expected_gross, cost)

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
        signal_type = signal.get("signal_type", "")
        if signal_type == "ADX_MOMENTUM":
            note = (
                f"ADX at {signal.get('adx', 0):.1f}, RSI at {signal.get('rsi', 0):.1f} "
                f"with volume {signal.get('volume_ratio', 0):.1f}x average"
            )
        elif "VWAP" in signal_type:
            note = (
                f"VWAP deviation {signal.get('vwap_deviation_pct', 0):.2f}%, "
                f"RSI at {signal.get('rsi', 0):.1f}"
            )
        elif "ORB" in signal_type:
            note = (
                f"ORB {'upside' if signal['direction'] == 'LONG' else 'downside'} breakout, "
                f"volume {signal.get('volume_ratio', 0):.1f}x average"
            )
        else:
            note = (
                f"RSI at {signal.get('rsi', 0):.1f} with "
                f"volume {signal.get('volume_ratio', 0):.1f}x average"
            )

        # Stop/target: flip for SHORT direction
        is_short = signal["direction"] == "SHORT"
        if is_short:
            stop_loss = round(entry_price * (1 + stop_pct / 100), 2)
            target = round(entry_price * (1 - target_pct / 100), 2)
        else:
            stop_loss = round(entry_price * (1 - stop_pct / 100), 2)
            target = round(entry_price * (1 + target_pct / 100), 2)

        proposal = TradeProposal(
            symbol=signal["symbol"],
            direction=signal["direction"],
            signal_type=signal["signal_type"],
            entry_price=entry_price,
            quantity_suggested=quantity,
            stop_loss=stop_loss,
            target=target,
            signal_confidence="MEDIUM",
            indicator_snapshot={
                "rsi": signal.get("rsi"),
                "adx": signal.get("adx"),
                "volume_ratio": signal.get("volume_ratio"),
            },
            bucket=self._strategy_config.get("bucket", "conservative"),
            analyst_note=note,
        )

        self._pending_signals[proposal.proposal_id] = _time.time()

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
        state["pending_signals"] = list(self._pending_signals)
        return state
