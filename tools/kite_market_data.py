"""Market data fetching via Kite Connect API.

All functions return normalised pandas DataFrames or dicts.
Never called directly by agents — always called via tools/market_data.py router.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

import pandas as pd

from tools.logger import get_agent_logger

logger = get_agent_logger("kite_market_data")

# ── Index symbol mapping ─────────────────────────────────────────────────────
# Kite uses special names for indices; our system uses short names.

KITE_INDEX_MAP = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "INDIAVIX": "INDIA VIX",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MID SELECT",
}

# Reverse map for converting Kite names back
_REVERSE_INDEX_MAP = {v: k for k, v in KITE_INDEX_MAP.items()}


def _to_kite_symbol(symbol: str, prefix: str = "NSE") -> str:
    """Convert our short symbol to Kite's quote key format."""
    kite_name = KITE_INDEX_MAP.get(symbol, symbol)
    return f"{prefix}:{kite_name}"


# ── Instrument token cache ────────────────────────────────────────────────────

_instrument_cache: dict[str, int] = {}


def build_instrument_cache(kite) -> None:
    """Download NSE + NFO instrument lists and build symbol → token cache.

    Call once at startup (after authentication). The instrument list
    changes daily (new listings, expiries).
    """
    global _instrument_cache
    instruments = kite.instruments("NSE")
    _instrument_cache = {
        inst["tradingsymbol"]: inst["instrument_token"]
        for inst in instruments
    }
    # Add short aliases for indices (NIFTY → NIFTY 50's token, etc.)
    for short_name, kite_name in KITE_INDEX_MAP.items():
        if kite_name in _instrument_cache and short_name not in _instrument_cache:
            _instrument_cache[short_name] = _instrument_cache[kite_name]

    # Also add NFO instruments for F&O
    nfo_instruments = kite.instruments("NFO")
    nfo_cache = {
        inst["tradingsymbol"]: inst["instrument_token"]
        for inst in nfo_instruments
    }
    _instrument_cache.update(nfo_cache)
    logger.info("Instrument cache built: %d symbols.", len(_instrument_cache))


def get_instrument_token(symbol: str) -> int:
    """Return numeric instrument token for a symbol.

    Raises KeyError if symbol not found in cache.
    """
    token = _instrument_cache.get(symbol)
    if not token:
        raise KeyError(
            f"Symbol '{symbol}' not found in instrument cache. "
            "Check symbol name or rebuild cache."
        )
    return token


def get_ohlcv(kite, symbol: str, interval: str, days: int = 60) -> pd.DataFrame:
    """Fetch historical OHLCV data for a symbol.

    Args:
        kite:     Authenticated KiteConnect client
        symbol:   NSE trading symbol e.g. "RELIANCE", "HDFCBANK"
        interval: Kite interval string — "minute", "3minute", "5minute",
                  "10minute", "15minute", "30minute", "60minute", "day"
        days:     Number of calendar days of history to fetch

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume, symbol
    """
    token = get_instrument_token(symbol)
    to_date = datetime.now(IST)
    from_date = to_date - timedelta(days=days)

    try:
        records = kite.historical_data(
            instrument_token=token,
            from_date=from_date.strftime("%Y-%m-%d %H:%M:%S"),
            to_date=to_date.strftime("%Y-%m-%d %H:%M:%S"),
            interval=interval,
            continuous=False,
            oi=False,
        )
    except Exception as e:
        logger.error("Failed to fetch historical data for %s: %s", symbol, e)
        raise

    if not records:
        logger.warning("No historical data returned for %s", symbol)
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "symbol"]
        )

    df = pd.DataFrame(records)
    df.rename(columns={"date": "timestamp"}, inplace=True)
    df["symbol"] = symbol
    df = df[["timestamp", "open", "high", "low", "close", "volume", "symbol"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)

    return df


def get_live_quote(kite, symbols: list[str]) -> dict:
    """Fetch live quotes for one or more symbols.

    Args:
        kite:    Authenticated KiteConnect client
        symbols: List of NSE symbols e.g. ["RELIANCE", "HDFCBANK"]

    Returns:
        Dict keyed by symbol with standardised quote data.
    """
    kite_symbols = [_to_kite_symbol(s) for s in symbols]

    try:
        raw = kite.quote(kite_symbols)
    except Exception as e:
        logger.error("Failed to fetch live quotes for %s: %s", symbols, e)
        raise

    result = {}
    for symbol in symbols:
        key = _to_kite_symbol(symbol)
        if key not in raw:
            logger.warning("No quote data for %s", symbol)
            continue
        q = raw[key]
        result[symbol] = {
            "symbol": symbol,
            "last_price": q["last_price"],
            "open": q["ohlc"]["open"],
            "high": q["ohlc"]["high"],
            "low": q["ohlc"]["low"],
            "close": q["ohlc"]["close"],
            "volume": q.get("volume_traded", q.get("volume", 0)),
            "change_pct": round(
                ((q["last_price"] - q["ohlc"]["close"]) / q["ohlc"]["close"]) * 100, 2
            ) if q["ohlc"]["close"] else 0,
            "timestamp": datetime.now(IST),
        }

    return result


def get_options_chain(kite, underlying: str, expiry_date: str) -> pd.DataFrame:
    """Fetch the options chain for an underlying at a given expiry.

    Args:
        kite:        Authenticated KiteConnect client
        underlying:  "NIFTY" or "BANKNIFTY" or stock symbol
        expiry_date: Expiry in "YYYY-MM-DD" format
    """
    all_instruments = [
        inst for inst in kite.instruments("NFO")
        if inst["name"] == underlying
        and inst["expiry"].strftime("%Y-%m-%d") == expiry_date
    ]

    if not all_instruments:
        logger.warning(
            "No options instruments found for %s expiry %s",
            underlying, expiry_date,
        )
        return pd.DataFrame()

    calls = [i for i in all_instruments if i["instrument_type"] == "CE"]
    puts = [i for i in all_instruments if i["instrument_type"] == "PE"]

    all_tokens = [f"NFO:{i['tradingsymbol']}" for i in all_instruments]

    try:
        quotes = kite.quote(all_tokens)
    except Exception as e:
        logger.error("Options chain quote fetch failed: %s", e)
        raise

    strikes = sorted(set(i["strike"] for i in all_instruments))
    rows = []
    for strike in strikes:
        ce_sym = next(
            (f"NFO:{i['tradingsymbol']}" for i in calls if i["strike"] == strike),
            None,
        )
        pe_sym = next(
            (f"NFO:{i['tradingsymbol']}" for i in puts if i["strike"] == strike),
            None,
        )
        ce_data = quotes.get(ce_sym, {})
        pe_data = quotes.get(pe_sym, {})

        rows.append({
            "strike": strike,
            "expiry": expiry_date,
            "ce_ltp": ce_data.get("last_price", 0),
            "ce_oi": ce_data.get("oi", 0),
            "ce_volume": ce_data.get("volume_traded", 0),
            "pe_ltp": pe_data.get("last_price", 0),
            "pe_oi": pe_data.get("oi", 0),
            "pe_volume": pe_data.get("volume_traded", 0),
        })

    return pd.DataFrame(rows)


def get_vwap(kite, symbol: str) -> float:
    """Calculate current VWAP from today's 1-minute candles."""
    df = get_ohlcv(kite, symbol, interval="minute", days=1)

    today = datetime.now(IST).date()
    df = df[df["timestamp"].dt.date == today]

    if df.empty:
        return 0.0

    df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["typical_price"] * df["volume"]
    total_vol = df["volume"].sum()
    if total_vol == 0:
        return 0.0
    return round(df["tp_vol"].sum() / total_vol, 2)
