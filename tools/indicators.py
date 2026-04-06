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


# --- High-VIX strategy helpers ---


def straddle_breakeven(
    nifty_spot: float,
    call_premium: float,
    put_premium: float,
    lot_size: int = 65,
) -> dict:
    """Calculate straddle break-even points.

    Args:
        nifty_spot:    Current Nifty price
        call_premium:  ATM call premium in points
        put_premium:   ATM put premium in points
        lot_size:      Nifty lot size (65 as of January 2026)

    Returns:
        Dict with combined_premium, total_cost_inr, upper/lower breakeven,
        move_required_pct.
    """
    if nifty_spot <= 0:
        raise ValueError("nifty_spot must be positive")
    combined = call_premium + put_premium
    total_inr = combined * lot_size
    upper = nifty_spot + combined
    lower = nifty_spot - combined
    move_pct = round((combined / nifty_spot) * 100, 3)

    return {
        "combined_premium": combined,
        "total_cost_inr": total_inr,
        "upper_breakeven": upper,
        "lower_breakeven": lower,
        "move_required_pct": move_pct,
    }


def volatility_adjusted_position_size(
    normal_position_size: float,
    normal_stop_pct: float,
    adjusted_stop_pct: float,
) -> float:
    """Calculate reduced position size for high-VIX trades to keep rupee risk constant.

    Logic: (normal_size × normal_stop) = (adjusted_size × adjusted_stop)
    So: adjusted_size = normal_size × (normal_stop / adjusted_stop)

    Returns adjusted position size rounded to nearest ₹100.
    """
    adjusted = normal_position_size * (normal_stop_pct / adjusted_stop_pct)
    return round(adjusted / 100) * 100
