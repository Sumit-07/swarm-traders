"""Technical indicators — pure Python with pandas/pandas-ta.

All functions take a standard OHLCV DataFrame with columns:
    datetime, open, high, low, close, volume
and return pandas Series or dicts of Series.
"""

import pandas as pd
import numpy as np


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                   signal: int = 9) -> dict:
    """MACD (Moving Average Convergence Divergence).

    Returns: {'macd': Series, 'signal': Series, 'histogram': Series}
    """
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price.

    Resets daily — groups by date for intraday data.
    """
    df = df.copy()
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    tp_volume = typical_price * df["volume"]

    # Check if intraday data (has time component)
    if "datetime" in df.columns:
        dt = pd.to_datetime(df["datetime"])
        dates = dt.dt.date
    else:
        # Single day, no reset needed
        cumulative_tp_vol = tp_volume.cumsum()
        cumulative_vol = df["volume"].cumsum()
        return cumulative_tp_vol / cumulative_vol.replace(0, np.nan)

    vwap = pd.Series(np.nan, index=df.index)
    for date in dates.unique():
        mask = dates == date
        cum_tp_vol = tp_volume[mask].cumsum()
        cum_vol = df["volume"][mask].cumsum()
        vwap[mask] = cum_tp_vol / cum_vol.replace(0, np.nan)

    return vwap


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return atr


def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20,
                              std_dev: float = 2.0) -> dict:
    """Bollinger Bands.

    Returns: {'upper': Series, 'middle': Series, 'lower': Series}
    """
    middle = df["close"].rolling(window=period).mean()
    std = df["close"].rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return {"upper": upper, "middle": middle, "lower": lower}


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr = calculate_atr(df, period)

    plus_di = 100 * (
        plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() /
        atr.replace(0, np.nan)
    )
    minus_di = 100 * (
        minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() /
        atr.replace(0, np.nan)
    )

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return adx


def calculate_ema(df: pd.DataFrame, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return df["close"].ewm(span=period, adjust=False).mean()


def calculate_volume_ratio(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """Current volume / N-day average volume."""
    avg_volume = df["volume"].rolling(window=period).mean()
    return df["volume"] / avg_volume.replace(0, np.nan)


def calculate_all(df: pd.DataFrame) -> dict:
    """Calculate all indicators on a standard OHLCV DataFrame.

    Returns dict with keys: rsi, macd, vwap, atr, bollinger, adx,
    ema_20, volume_ratio
    """
    return {
        "rsi": calculate_rsi(df),
        "macd": calculate_macd(df),
        "vwap": calculate_vwap(df),
        "atr": calculate_atr(df),
        "bollinger": calculate_bollinger_bands(df),
        "adx": calculate_adx(df),
        "ema_20": calculate_ema(df, 20),
        "volume_ratio": calculate_volume_ratio(df),
    }
