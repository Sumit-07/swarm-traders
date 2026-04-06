"""
tools/cost_estimator.py

Calculates true net P&L after all statutory costs for a proposed trade.
Called by Analyst before raising any trade proposal to Risk Agent.

Post-Budget 2026 STT rates applied throughout.
"""

from config import STATUTORY_COSTS, CONTRACT_SPECIFICATIONS
from dataclasses import dataclass


@dataclass
class TradeCost:
    stt_inr:            float
    brokerage_inr:      float
    exchange_charges_inr: float
    gst_inr:            float
    stamp_duty_inr:     float
    total_cost_inr:     float
    breakeven_pct:      float   # % move needed just to cover costs
    breakeven_pts:      float   # index points needed (for options)


def estimate_equity_roundtrip_cost(
    position_value_inr: float,
    is_intraday:        bool = True,
) -> TradeCost:
    """
    Calculates total roundtrip cost for an equity (cash market) trade.
    Roundtrip = entry + exit.
    """
    c = STATUTORY_COSTS

    # STT — on sell side only for intraday, both sides for delivery
    stt_rate = c["stt_intraday_equity_pct"] if is_intraday else c["stt_delivery_equity_pct"]
    stt = position_value_inr * stt_rate * (1 if is_intraday else 2)

    # Brokerage — ₹20 per order × 2 orders (entry + exit)
    brokerage = c["brokerage_per_order_inr"] * 2

    # Exchange charges on total turnover (entry + exit)
    turnover = position_value_inr * 2
    exchange = turnover * c["exchange_charges_nse_pct"]

    # SEBI fee
    sebi = (turnover / 1e7) * c["sebi_fee_per_crore"]

    # GST on brokerage + exchange
    gst = (brokerage + exchange) * c["gst_on_brokerage_pct"]

    # Stamp duty on buy side only
    stamp = position_value_inr * c["stamp_duty_pct"]

    total = stt + brokerage + exchange + sebi + gst + stamp
    breakeven_pct = (total / position_value_inr) * 100 if position_value_inr > 0 else 0

    return TradeCost(
        stt_inr=round(stt, 2),
        brokerage_inr=round(brokerage, 2),
        exchange_charges_inr=round(exchange + sebi, 2),
        gst_inr=round(gst, 2),
        stamp_duty_inr=round(stamp, 2),
        total_cost_inr=round(total, 2),
        breakeven_pct=round(breakeven_pct, 4),
        breakeven_pts=0.0,  # not applicable for equity
    )


def estimate_options_roundtrip_cost(
    underlying:    str,          # "NIFTY" | "BANKNIFTY"
    premium_per_unit: float,     # premium at entry
    lots:          int = 1,
    hold_to_expiry: bool = False,
) -> TradeCost:
    """
    Calculates total roundtrip cost for an options trade.

    Post-Budget 2026:
    - STT on sell: 0.15% of premium (when you exit by selling back)
    - STT on exercise: 0.15% of intrinsic value (if held to expiry)

    For bought options (our system only buys):
    - Exit by selling back: STT = 0.15% × exit_premium × lot_size × lots
    - If OTM at expiry: worthless, no exercise STT
    - If ITM at expiry: exercise STT = 0.15% of intrinsic value
    Always model exit by selling, not exercising.
    """
    c = STATUTORY_COSTS
    spec = CONTRACT_SPECIFICATIONS.get(underlying, CONTRACT_SPECIFICATIONS["NIFTY"])
    lot_size = spec["lot_size"]
    total_units = lot_size * lots

    # Entry premium value
    entry_value = premium_per_unit * total_units

    # Assume exit by selling (not exercising) — model at entry premium for breakeven
    exit_value = entry_value  # symmetric assumption for breakeven calc

    # STT on sell (exit)
    stt_exit = exit_value * c["stt_options_sell_pct"]

    # Brokerage: ₹20 × 2 (entry and exit)
    brokerage = c["brokerage_per_order_inr"] * 2

    # Exchange charges on premium turnover (entry + exit)
    premium_turnover = entry_value + exit_value
    exchange = premium_turnover * c["exchange_charges_nse_pct"]

    # SEBI fee on premium turnover
    sebi = (premium_turnover / 1e7) * c["sebi_fee_per_crore"]

    # GST
    gst = (brokerage + exchange) * c["gst_on_brokerage_pct"]

    # Stamp duty on buy (entry)
    stamp = entry_value * c["stamp_duty_pct"]

    total = stt_exit + brokerage + exchange + sebi + gst + stamp

    # Breakeven: how much must premium rise just to cover costs?
    breakeven_pct = (total / entry_value) * 100 if entry_value > 0 else 0

    # Convert to index points: breakeven_pts = total_cost / (lot_size × lots × delta)
    # Assume delta ~0.5 for ATM options
    delta = 0.5
    breakeven_pts = total / (lot_size * lots * delta)

    return TradeCost(
        stt_inr=round(stt_exit, 2),
        brokerage_inr=round(brokerage, 2),
        exchange_charges_inr=round(exchange + sebi, 2),
        gst_inr=round(gst, 2),
        stamp_duty_inr=round(stamp, 2),
        total_cost_inr=round(total, 2),
        breakeven_pct=round(breakeven_pct, 4),
        breakeven_pts=round(breakeven_pts, 1),
    )


def estimate_straddle_cost(
    underlying:    str,
    call_premium:  float,
    put_premium:   float,
    lots:          int = 1,
) -> dict:
    """
    Calculates combined cost and breakeven for a straddle (both legs).
    Returns dict with cost breakdown and profitability assessment.
    """
    spec = CONTRACT_SPECIFICATIONS.get(underlying, CONTRACT_SPECIFICATIONS["NIFTY"])
    lot_size = spec["lot_size"]

    call_cost = estimate_options_roundtrip_cost(underlying, call_premium, lots)
    put_cost  = estimate_options_roundtrip_cost(underlying, put_premium,  lots)

    combined_premium_value = (call_premium + put_premium) * lot_size * lots
    total_cost = call_cost.total_cost_inr + put_cost.total_cost_inr

    # Straddle breakeven: Nifty must move by (combined premium + total costs) / lot_size
    # in either direction to profit
    combined_premium_pts = call_premium + put_premium
    cost_pts = total_cost / (lot_size * lots)
    breakeven_pts_each_direction = combined_premium_pts + cost_pts

    return {
        "call_cost":                call_cost,
        "put_cost":                 put_cost,
        "total_cost_inr":           round(total_cost, 2),
        "combined_premium_value":   round(combined_premium_value, 2),
        "total_investment_inr":     round(combined_premium_value + total_cost, 2),
        "breakeven_pts_up":         round(breakeven_pts_each_direction, 1),
        "breakeven_pts_down":       round(breakeven_pts_each_direction, 1),
        "breakeven_pct":            round((breakeven_pts_each_direction / 22600) * 100, 3),
        "is_cost_viable":           combined_premium_value <= 8000,  # within budget
        "recommendation": (
            "VIABLE" if combined_premium_value <= 8000
            else f"TOO_EXPENSIVE — cost ₹{combined_premium_value:.0f} exceeds ₹8,000 limit"
        ),
    }


def is_trade_viable(
    gross_profit_expected_inr: float,
    trade_cost:                TradeCost,
    minimum_margin:            float = 2.0,
) -> tuple[bool, str]:
    """
    Checks if a trade is worth taking after costs.

    Args:
        gross_profit_expected_inr: expected gross P&L if trade works
        trade_cost:                TradeCost from estimate_* functions
        minimum_margin:            minimum ratio of gross profit to cost
                                   (default 2.0 = profit must be 2x costs)

    Returns:
        (is_viable, reason_string)
    """
    if gross_profit_expected_inr <= 0:
        return False, "Expected profit is negative or zero."

    profit_to_cost_ratio = gross_profit_expected_inr / trade_cost.total_cost_inr

    if profit_to_cost_ratio < minimum_margin:
        return False, (
            f"Expected profit ₹{gross_profit_expected_inr:.0f} is only "
            f"{profit_to_cost_ratio:.1f}x the cost ₹{trade_cost.total_cost_inr:.0f}. "
            f"Minimum required: {minimum_margin}x."
        )

    return True, (
        f"Viable — profit/cost ratio: {profit_to_cost_ratio:.1f}x. "
        f"Total cost: ₹{trade_cost.total_cost_inr:.0f}."
    )
