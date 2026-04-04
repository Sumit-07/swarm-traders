"""Redis wrapper for shared state and pub/sub messaging."""

import json
from datetime import datetime
from typing import Callable

import redis

from tools.logger import get_agent_logger

logger = get_agent_logger("redis_store")


class RedisStore:
    def __init__(self, host: str = "localhost", port: int = 6379):
        self.client = redis.Redis(host=host, port=port, decode_responses=True)
        self._pubsub = None

    # --- Health ---

    def ping(self) -> bool:
        try:
            return self.client.ping()
        except redis.ConnectionError:
            return False

    # --- Shared State (key-value) ---

    def set_state(self, key: str, value: dict, ttl: int = None):
        """Store a dict as JSON. Auto-adds _updated_at timestamp."""
        value["_updated_at"] = datetime.now().isoformat()
        self.client.set(key, json.dumps(value))
        if ttl:
            self.client.expire(key, ttl)

    def get_state(self, key: str) -> dict | None:
        raw = self.client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    def delete_state(self, key: str):
        self.client.delete(key)

    # --- Market Data (with default TTL for stale protection) ---

    def set_market_data(self, key: str, data: dict, ttl: int = 120):
        """Store market data with a default 120s TTL (stale data protection)."""
        data["_updated_at"] = datetime.now().isoformat()
        self.client.set(key, json.dumps(data), ex=ttl)

    def get_market_data(self, key: str) -> dict | None:
        raw = self.client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    # --- Pub/Sub ---

    def publish(self, channel: str, message: dict) -> int:
        """Publish a message dict to a Redis channel. Returns subscriber count."""
        return self.client.publish(channel, json.dumps(message))

    def get_pubsub(self) -> redis.client.PubSub:
        """Get a new PubSub instance for subscribing."""
        return self.client.pubsub()

    def subscribe(self, pubsub: redis.client.PubSub, channel: str,
                  callback: Callable = None):
        """Subscribe to a channel on the given PubSub instance."""
        if callback:
            pubsub.subscribe(**{channel: callback})
        else:
            pubsub.subscribe(channel)

    # --- Utilities ---

    def get_all_keys(self, pattern: str = "*") -> list[str]:
        return [k for k in self.client.scan_iter(match=pattern)]

    def flush_pattern(self, pattern: str):
        """Delete all keys matching a pattern. Use with caution."""
        keys = self.get_all_keys(pattern)
        if keys:
            self.client.delete(*keys)
