"""Data Agent — Market data ingestion and distribution.

The sensory system of the trading swarm. Collects, validates, and publishes
market data with machine-like precision. No opinions, no biases.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from agents.base_agent import BaseAgent
from agents.message import AgentMessage, MessageType, Priority
from config import DEFAULT_WATCHLIST
from tools.market_data import MarketDataProvider
from tools.indicators import calculate_all
from tools.news_fetcher import fetch_market_news


class DataAgent(BaseAgent):
    def __init__(self, redis_store, sqlite_store,
                 market_data: MarketDataProvider):
        super().__init__("data_agent", redis_store, sqlite_store)
        self.market_data = market_data
        self._watchlist: list[str] = []

    def on_start(self):
        # Seed watchlist so data flows immediately (strategist can override later)
        if not self._watchlist:
            self._watchlist = DEFAULT_WATCHLIST.copy()
            self.logger.info(f"Watchlist seeded with {len(self._watchlist)} default symbols")

        # Schedule recurring data pulls
        self.scheduler.add_job(
            self.pull_market_snapshot, "interval", minutes=1,
            id="market_snapshot", replace_existing=True,
        )
        self.scheduler.add_job(
            self.pull_watchlist_data, "interval", minutes=5,
            id="watchlist_data", replace_existing=True,
        )
        self.scheduler.add_job(
            self.pull_news, "cron", hour="8-15", minute=0,
            timezone=IST, id="news_pull", replace_existing=True,
        )
        self.logger.info("Data Agent scheduled: snapshot 1m, watchlist 5m, news hourly 8-15 IST")

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
            elif request == "news_summary":
                self.pull_news()
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
                ltp = data.get("ltp", data.get("last_price", "?"))
                self.logger.debug(f"{index}: LTP={ltp}")
            except Exception as e:
                self.logger.warning(f"Failed to fetch {index}: {e}")
                snapshot[index.lower()] = {"error": str(e)}

        snapshot["timestamp"] = datetime.now(IST).isoformat()
        self.redis.set_market_data("data:market_snapshot", snapshot, ttl=360)
        self._last_action = "pulled market snapshot"
        self.logger.info("Market snapshot saved to Redis.")

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
                    "timestamp": datetime.now(IST).isoformat(),
                }
                self.redis.set_market_data(
                    f"data:watchlist_ticks:{symbol}", latest, ttl=360,
                )
            except Exception as e:
                self.logger.warning(f"Failed to pull data for {symbol}: {e}")

        self._last_action = f"pulled watchlist data for {len(self._watchlist)} symbols"
        self.logger.info(f"Watchlist data saved to Redis for {len(self._watchlist)} symbols")

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

    def pull_news(self, force: bool = False):
        """Fetch market news via Gemini search grounding and store in Redis.

        Skips if last fetch was < 55 minutes ago (unless force=True).
        """
        now = datetime.now(IST)

        if not force:
            existing = self.redis.get_market_data("data:news_summary")
            if existing and existing.get("fetched_at"):
                try:
                    last = datetime.fromisoformat(existing["fetched_at"])
                    minutes_ago = (now - last).total_seconds() / 60
                    if minutes_ago < 55:
                        return
                except (ValueError, TypeError):
                    pass

        result = fetch_market_news(
            current_time=now.strftime("%H:%M"),
            current_date=now.strftime("%Y-%m-%d"),
        )
        result["fetched_at"] = now.isoformat()
        self.redis.set_market_data("data:news_summary", result, ttl=7200)
        sentiment = result.get("overall_sentiment", "UNKNOWN")
        headline_count = len(result.get("headlines", []))
        self.logger.info(
            f"News saved to Redis: {headline_count} headlines, sentiment={sentiment}"
        )
        self._last_action = f"pulled news: {sentiment}"

    def run(self, state: dict) -> dict:
        """LangGraph node: refresh market data."""
        self.pull_market_snapshot()
        self.pull_news()

        # Set watchlist from strategy if available, fall back to defaults
        strategy = state.get("conservative_strategy") or {}
        watchlist = strategy.get("watchlist", [])
        if watchlist:
            self._watchlist = watchlist
        elif not self._watchlist:
            self._watchlist = DEFAULT_WATCHLIST.copy()
        self.pull_watchlist_data()

        state["market_data_ready"] = True
        state["last_data_update"] = datetime.now(IST).isoformat()
        return state
