"""Data Agent — Market data ingestion and distribution.

The sensory system of the trading swarm. Collects, validates, and publishes
market data with machine-like precision. No opinions, no biases.
"""

from datetime import datetime

from agents.base_agent import BaseAgent
from agents.message import AgentMessage, MessageType, Priority
from tools.market_data import MarketDataProvider
from tools.indicators import calculate_all


class DataAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store,
                 market_data: MarketDataProvider):
        super().__init__("data_agent", redis_store, sqlite_store)
        self.market_data = market_data
        self._watchlist: list[str] = []

    def on_start(self):
        # Schedule recurring data pulls
        self.scheduler.add_job(
            self.pull_market_snapshot, "interval", minutes=1,
            id="market_snapshot", replace_existing=True,
        )
        self.scheduler.add_job(
            self.pull_watchlist_data, "interval", minutes=5,
            id="watchlist_data", replace_existing=True,
        )
        self.logger.info("Data Agent scheduled: snapshot every 1 min, watchlist every 5 min")

    def on_stop(self):
        pass

    def on_message(self, message: AgentMessage):
        if message.type == MessageType.REQUEST:
            request = message.payload.get("request", "")
            if request == "market_snapshot":
                self.pull_market_snapshot()
            elif request == "set_watchlist":
                self._watchlist = message.payload.get("symbols", [])
                self.logger.info(f"Watchlist updated: {self._watchlist}")
            elif request == "get_ohlcv":
                self._handle_ohlcv_request(message)
        elif message.type == MessageType.COMMAND:
            if message.payload.get("command") == "HALT":
                self.logger.info("Data Agent acknowledges HALT")

    def pull_market_snapshot(self):
        """Fetch current Nifty, BankNifty, VIX and publish to Redis."""
        snapshot = {}
        for index in ("NIFTY", "BANKNIFTY", "INDIAVIX"):
            try:
                data = self.market_data.get_index_data(index)
                snapshot[index.lower()] = data
            except Exception as e:
                self.logger.warning(f"Failed to fetch {index}: {e}")
                snapshot[index.lower()] = {"error": str(e)}

        snapshot["timestamp"] = datetime.now().isoformat()
        self.redis.set_market_data("data:market_snapshot", snapshot, ttl=120)
        self._last_action = "pulled market snapshot"

    def pull_watchlist_data(self):
        """Fetch OHLCV and calculate indicators for watchlist symbols."""
        if not self._watchlist:
            return

        for symbol in self._watchlist:
            try:
                ohlcv = self.market_data.get_ohlcv(symbol, interval="5", count=100)
                indicators = calculate_all(ohlcv)

                # Store latest indicator values
                latest = {
                    "symbol": symbol,
                    "close": float(ohlcv["close"].iloc[-1]),
                    "volume": int(ohlcv["volume"].iloc[-1]),
                    "rsi": float(indicators["rsi"].dropna().iloc[-1])
                        if not indicators["rsi"].dropna().empty else None,
                    "macd": float(indicators["macd"]["macd"].dropna().iloc[-1])
                        if not indicators["macd"]["macd"].dropna().empty else None,
                    "macd_signal": float(indicators["macd"]["signal"].dropna().iloc[-1])
                        if not indicators["macd"]["signal"].dropna().empty else None,
                    "vwap": float(indicators["vwap"].dropna().iloc[-1])
                        if not indicators["vwap"].dropna().empty else None,
                    "atr": float(indicators["atr"].dropna().iloc[-1])
                        if not indicators["atr"].dropna().empty else None,
                    "adx": float(indicators["adx"].dropna().iloc[-1])
                        if not indicators["adx"].dropna().empty else None,
                    "volume_ratio": float(indicators["volume_ratio"].dropna().iloc[-1])
                        if not indicators["volume_ratio"].dropna().empty else None,
                    "timestamp": datetime.now().isoformat(),
                }
                self.redis.set_market_data(
                    f"data:watchlist_ticks:{symbol}", latest, ttl=120,
                )
            except Exception as e:
                self.logger.warning(f"Failed to pull data for {symbol}: {e}")

        self._last_action = f"pulled watchlist data for {len(self._watchlist)} symbols"

    def _handle_ohlcv_request(self, message: AgentMessage):
        """Handle on-demand OHLCV request from another agent."""
        symbol = message.payload.get("symbol")
        interval = message.payload.get("interval", "5")
        count = message.payload.get("count", 100)

        try:
            ohlcv = self.market_data.get_ohlcv(symbol, interval, count)
            self.send_message(
                to_agent=message.from_agent,
                msg_type=MessageType.RESPONSE,
                payload={
                    "symbol": symbol,
                    "bars": len(ohlcv),
                    "latest_close": float(ohlcv["close"].iloc[-1]),
                    "status": "success",
                },
                correlation_id=message.message_id,
            )
        except Exception as e:
            self.send_message(
                to_agent=message.from_agent,
                msg_type=MessageType.RESPONSE,
                payload={"symbol": symbol, "status": "error", "error": str(e)},
                correlation_id=message.message_id,
            )

    def summarize_news(self, headlines: list[str]) -> dict:
        """Use LLM to summarize news headlines into market sentiment."""
        if not headlines:
            return {"overall_sentiment": "NEUTRAL", "key_events": [],
                    "risk_events_today": []}
        try:
            result = self.call_llm("PROMPT_NEWS_SUMMARY", {
                "current_time": datetime.now().strftime("%H:%M IST"),
                "headlines_list": "\n".join(
                    f"- {h}" for h in headlines[:15]
                ),
            })
            return result
        except Exception as e:
            self.logger.error(f"News summary LLM failed: {e}")
            return {"overall_sentiment": "UNKNOWN", "_error": str(e)}

    def run(self, state: dict) -> dict:
        """LangGraph node: refresh market data."""
        self.pull_market_snapshot()

        # Set watchlist from strategy if available
        strategy = state.get("conservative_strategy") or {}
        watchlist = strategy.get("watchlist", [])
        if watchlist:
            self._watchlist = watchlist
            self.pull_watchlist_data()

        state["market_data_ready"] = True
        state["last_data_update"] = datetime.now().isoformat()
        return state
