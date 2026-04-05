"""KiteTicker WebSocket wrapper for live tick data streaming.

Subscribes to symbols in the watchlist and publishes ticks to Redis.
Used by Data Agent during market hours.
"""

import json
from datetime import datetime

from tools.logger import get_agent_logger

logger = get_agent_logger("kite_ticker")


class KiteTickerManager:
    """Manages the KiteTicker WebSocket connection.

    Subscribes to instrument tokens and publishes tick data to Redis.
    """

    def __init__(self, api_key: str, access_token: str, redis_store):
        from kiteconnect import KiteTicker

        self.ticker = KiteTicker(api_key, access_token)
        self.redis = redis_store
        self.subscribed_tokens: list[int] = []
        self._setup_callbacks()

    def _setup_callbacks(self):
        self.ticker.on_ticks = self._on_ticks
        self.ticker.on_connect = self._on_connect
        self.ticker.on_close = self._on_close
        self.ticker.on_error = self._on_error
        self.ticker.on_reconnect = self._on_reconnect

    def _on_connect(self, ws, response):
        logger.info("KiteTicker connected.")
        if self.subscribed_tokens:
            self.ticker.subscribe(self.subscribed_tokens)
            self.ticker.set_mode(self.ticker.MODE_FULL, self.subscribed_tokens)

    def _on_ticks(self, ws, ticks):
        for tick in ticks:
            token = tick["instrument_token"]
            tick_serialisable = {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in tick.items()
            }
            self.redis.write(
                f"tick:{token}",
                json.dumps(tick_serialisable),
                ttl=60,
            )

    def _on_close(self, ws, code, reason):
        logger.warning("KiteTicker closed: %s %s", code, reason)

    def _on_error(self, ws, code, reason):
        logger.error("KiteTicker error: %s %s", code, reason)

    def _on_reconnect(self, ws, attempts_count):
        logger.info("KiteTicker reconnecting (attempt %d)...", attempts_count)

    def subscribe(self, instrument_tokens: list[int]):
        """Subscribe to a list of instrument tokens."""
        self.subscribed_tokens = instrument_tokens
        if self.ticker.is_connected():
            self.ticker.subscribe(instrument_tokens)
            self.ticker.set_mode(self.ticker.MODE_FULL, instrument_tokens)

    def start(self):
        """Start the WebSocket in a background thread."""
        self.ticker.connect(threaded=True)
        logger.info("KiteTicker started in background thread.")

    def stop(self):
        """Stop the WebSocket connection."""
        self.ticker.close()
        logger.info("KiteTicker stopped.")
