"""Unified data access layer.

Routes all data requests to the configured backend based on
DATA_SOURCE environment variable.

Agents and backtest code ALWAYS call this module, never the backend
modules directly. This enables seamless switching between data sources.

DATA_SOURCE options:
  kite      — Kite Connect API (primary)
  yfinance  — Yahoo Finance (free, 15-min delay, last resort)
"""

import os

import pandas as pd

from tools.logger import get_agent_logger

logger = get_agent_logger("market_data")
DATA_SOURCE = os.getenv("DATA_SOURCE", "kite")

# ── Shared state ──────────────────────────────────────────────────────────────
# The Kite client is injected at startup by the Orchestrator.

_kite_client = None


def set_kite_client(kite):
    """Called by Orchestrator after authentication to inject the Kite client."""
    global _kite_client
    _kite_client = kite
    logger.info("Kite client injected into market_data module.")


# ── Interval mapping ─────────────────────────────────────────────────────────

INTERVAL_MAP = {
    "1m": "minute",
    "3m": "3minute",
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h": "60minute",
    "1d": "day",
    # Also accept old-style Fyers intervals for backward compat
    "1": "minute",
    "5": "5minute",
    "15": "15minute",
    "60": "60minute",
    "D": "day",
}

# yfinance interval mapping
YF_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "1d": "1d",
    "1": "1m",
    "5": "5m",
    "15": "15m",
    "60": "1h",
    "D": "1d",
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_ohlcv(symbol: str, interval: str = "5m", days: int = 60) -> pd.DataFrame:
    """Fetch historical OHLCV data for a symbol.

    Returns canonical DataFrame: timestamp, open, high, low, close, volume, symbol
    """
    if DATA_SOURCE == "kite" and _kite_client:
        from tools.kite_market_data import get_ohlcv as kite_ohlcv
        kite_interval = INTERVAL_MAP.get(interval, interval)
        return kite_ohlcv(_kite_client, symbol, kite_interval, days)

    # Fallback: yfinance
    from tools.yfinance_fallback import get_ohlcv as yf_ohlcv
    yf_interval = YF_INTERVAL_MAP.get(interval, interval)
    return yf_ohlcv(symbol, yf_interval, days)


def get_live_quote(symbols: list[str]) -> dict:
    """Fetch live quotes for a list of symbols.

    Returns canonical quote dict keyed by symbol.
    """
    if DATA_SOURCE == "kite" and _kite_client:
        from tools.kite_market_data import get_live_quote as kite_quote
        return kite_quote(_kite_client, symbols)

    from tools.yfinance_fallback import get_live_quote as yf_quote
    return yf_quote(symbols)


def get_options_chain(underlying: str, expiry_date: str) -> pd.DataFrame:
    """Fetch options chain. Only supported on Kite backend."""
    if DATA_SOURCE == "kite" and _kite_client:
        from tools.kite_market_data import get_options_chain as kite_chain
        return kite_chain(_kite_client, underlying, expiry_date)

    raise NotImplementedError("Options chain not available via yfinance.")


def get_vwap(symbol: str) -> float:
    """Calculate current VWAP from today's 1-min data."""
    if DATA_SOURCE == "kite" and _kite_client:
        from tools.kite_market_data import get_vwap as kite_vwap
        return kite_vwap(_kite_client, symbol)

    # Calculate from OHLCV for other backends
    import datetime as dt
    df = get_ohlcv(symbol, "1m", days=1)
    today = dt.datetime.now().date()
    if "timestamp" in df.columns:
        df = df[df["timestamp"].dt.date == today]
    if df.empty:
        return 0.0
    df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["typical_price"] * df["volume"]
    total_vol = df["volume"].sum()
    if total_vol == 0:
        return 0.0
    return round(df["tp_vol"].sum() / total_vol, 2)


# ── Legacy compatibility ─────────────────────────────────────────────────────
# MarketDataProvider class for backward compatibility with existing code
# that instantiates it. Delegates to the module-level functions above.


class MarketDataProvider:
    """Backward-compatible wrapper around the module-level functions."""

    def __init__(self, fyers_broker=None, sqlite_store=None):
        self.db = sqlite_store

    def get_quote(self, symbol: str) -> dict:
        quotes = get_live_quote([symbol])
        if symbol in quotes:
            q = quotes[symbol]
            return {
                "symbol": symbol,
                "ltp": q["last_price"],
                "open": q["open"],
                "high": q["high"],
                "low": q["low"],
                "close": q["close"],
                "volume": q["volume"],
                "timestamp": q["timestamp"].isoformat()
                if hasattr(q["timestamp"], "isoformat") else str(q["timestamp"]),
            }
        raise RuntimeError(f"No quote data for {symbol}")

    def get_index_data(self, index: str = "NIFTY") -> dict:
        return self.get_quote(index)

    def get_ohlcv(self, symbol: str, interval: str = "5",
                  count: int = 100) -> pd.DataFrame:
        yf_interval = YF_INTERVAL_MAP.get(interval, interval)
        df = get_ohlcv(symbol, yf_interval, days=max(count // 10, 5))
        # Rename timestamp→datetime for backward compat with old callers
        if "timestamp" in df.columns and "datetime" not in df.columns:
            df = df.rename(columns={"timestamp": "datetime"})
        if "symbol" in df.columns:
            df = df.drop(columns=["symbol"])
        return df.tail(count).reset_index(drop=True)

    def get_historical(self, symbol: str, start: str, end: str,
                       interval: str = "5") -> pd.DataFrame:
        from datetime import datetime
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        days = (end_dt - start_dt).days + 1
        yf_interval = YF_INTERVAL_MAP.get(interval, interval)
        df = get_ohlcv(symbol, yf_interval, days=days)
        if "timestamp" in df.columns and "datetime" not in df.columns:
            df = df.rename(columns={"timestamp": "datetime"})
        if "symbol" in df.columns:
            df = df.drop(columns=["symbol"])
        return df
