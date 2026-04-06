"""Straddle backtest helpers — entry validation, ATM premium estimation, P&L.

Used by BacktestRunner when strategy == STRADDLE_BUY.
"""

import math
from datetime import time as dt_time

import pandas as pd


def compute_atm_premium(nifty_price: float, vix: float, dte: int = 3) -> float:
    """Estimate a single ATM option premium using simplified Black-Scholes.

    Formula: 0.4 × S × IV × √(T/365)

    Args:
        nifty_price: Current Nifty spot price
        vix:         India VIX level (e.g. 25 means 25%)
        dte:         Days to expiry

    Returns:
        Estimated ATM premium in points.
    """
    iv = vix / 100.0
    return 0.4 * nifty_price * iv * math.sqrt(dte / 365)


def straddle_entry_valid(bar: dict, vix: float, prev_close: float) -> bool:
    """Check if a straddle entry is valid at this bar.

    Conditions:
        1. VIX between 22 and 32
        2. Time between 09:20 and 10:30 IST
        3. Nifty hasn't already moved > ±0.3% from previous close

    Args:
        bar:        Dict with 'timestamp' (str) and 'close' (float)
        vix:        Current India VIX
        prev_close: Previous day's Nifty close

    Returns:
        True if entry conditions are met.
    """
    # VIX range check
    if vix < 22 or vix > 32:
        return False

    # Time window check
    ts = pd.Timestamp(bar["timestamp"])
    bar_time = ts.time()
    if bar_time < dt_time(9, 20) or bar_time > dt_time(10, 30):
        return False

    # Nifty hasn't already moved significantly
    if prev_close > 0:
        move_pct = abs(bar["close"] - prev_close) / prev_close * 100
        if move_pct > 0.3:
            return False

    return True


def straddle_pnl(
    entry_combined_premium: float,
    exit_combined_premium: float,
    lot_size: int = 25,
) -> float:
    """Calculate straddle P&L in INR.

    Args:
        entry_combined_premium: Combined call+put premium at entry (points)
        exit_combined_premium:  Combined call+put premium at exit (points)
        lot_size:               Nifty lot size (25 as of 2025)

    Returns:
        P&L in INR (positive = profit).
    """
    return (exit_combined_premium - entry_combined_premium) * lot_size
