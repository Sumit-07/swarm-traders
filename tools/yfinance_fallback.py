"""Fallback data source using Yahoo Finance.

Used when Kite Connect is unavailable or DATA_SOURCE=yfinance.
Returns data in the canonical normalised format.
"""

from datetime import datetime

import pandas as pd

from tools.logger import get_agent_logger

logger = get_agent_logger("yfinance_fallback")

# Symbol mappings for yfinance
YFINANCE_INDEX_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "INDIAVIX": "^INDIAVIX",
}


def _to_yfinance_symbol(symbol: str) -> str:
    if symbol in YFINANCE_INDEX_MAP:
        return YFINANCE_INDEX_MAP[symbol]
    return f"{symbol}.NS"


def get_ohlcv(symbol: str, interval: str = "5m", days: int = 60) -> pd.DataFrame:
    """Fetch OHLCV data from Yahoo Finance.

    Args:
        symbol:   Clean symbol (e.g. "RELIANCE", "NIFTY")
        interval: Standard interval ("1m", "5m", "15m", "1h", "1d")
        days:     Number of days of history

    Returns:
        Canonical DataFrame: timestamp, open, high, low, close, volume, symbol
    """
    import yfinance as yf

    yf_symbol = _to_yfinance_symbol(symbol)

    # yfinance period heuristic based on interval
    if interval in ("1m", "5m"):
        period = "5d"
    elif interval in ("15m", "1h"):
        period = "1mo"
    else:
        period = f"{min(days, 730)}d"

    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval)
    except Exception as e:
        logger.error("yfinance OHLCV failed for %s: %s", symbol, e)
        raise

    if df.empty:
        logger.warning("No yfinance data for %s", symbol)
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "symbol"]
        )

    df = df.reset_index()
    rename_map = {
        "Date": "timestamp", "Datetime": "timestamp",
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    }
    df = df.rename(columns=rename_map)

    # Handle MultiIndex columns from yfinance
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=rename_map)

    df["symbol"] = symbol
    df = df[["timestamp", "open", "high", "low", "close", "volume", "symbol"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)

    return df


def get_live_quote(symbols: list[str]) -> dict:
    """Fetch live quotes from Yahoo Finance.

    Args:
        symbols: List of clean symbols e.g. ["RELIANCE", "HDFCBANK"]

    Returns:
        Canonical quote dict keyed by symbol.
    """
    import yfinance as yf

    result = {}
    for symbol in symbols:
        yf_symbol = _to_yfinance_symbol(symbol)
        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.fast_info
            result[symbol] = {
                "symbol": symbol,
                "last_price": info.last_price,
                "open": info.open,
                "high": info.day_high,
                "low": info.day_low,
                "close": info.previous_close,
                "volume": info.last_volume,
                "change_pct": round(
                    ((info.last_price - info.previous_close)
                     / info.previous_close) * 100, 2
                ) if info.previous_close else 0,
                "timestamp": datetime.now(),
            }
        except Exception as e:
            logger.warning("yfinance quote failed for %s: %s", symbol, e)

    return result
