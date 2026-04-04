"""Conditional edge functions for LangGraph routing.

These functions examine SwarmState and return the next node to visit.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from config import RISK_LIMITS

IST = ZoneInfo("Asia/Kolkata")


def should_proceed_after_approval(state: dict) -> str:
    """After human approval step: route based on response."""
    response = state.get("human_response")
    if response == "YES":
        return "approved"
    elif response == "NO":
        return "rejected"
    return "timeout"


def has_signal(state: dict) -> str:
    """After analyst scan: check if any signals were generated."""
    signals = state.get("pending_signals", [])
    if signals and len(signals) > 0:
        return "signal"
    return "no_signal"


def is_approved(state: dict) -> str:
    """After risk review: check if any orders were approved."""
    approved = state.get("approved_orders", [])
    if approved and len(approved) > 0:
        return "approved"
    return "rejected"


def needs_human_approval(state: dict) -> str:
    """Check if a trade requires human approval before execution."""
    # For first 30 days, always require approval
    # After that, auto-approve < threshold with HIGH confidence
    # Phase 2: always require human approval
    return "needs_human"


def is_market_open(state: dict) -> str:
    """Check if we're within market trading hours."""
    now = datetime.now(IST).time()
    if time(9, 15) <= now <= time(15, 30):
        return "open"
    return "closed"


def is_intraday_cutoff(state: dict) -> str:
    """Check if we've hit the intraday force-close time."""
    now = datetime.now(IST).time()
    cutoff = time(15, 20)
    if now >= cutoff:
        return "cutoff"
    return "continue"


def should_generate_signals(state: dict) -> str:
    """Check if conditions allow new signal generation."""
    now = datetime.now(IST).time()

    # No new trades after 15:00
    if now >= time(15, 0):
        return "no_new_trades"

    # No trades in first 15 minutes (except ORB)
    if now < time(9, 30):
        strategy = state.get("conservative_strategy", {})
        if strategy.get("strategy") == "OPENING_RANGE_BREAKOUT":
            return "scan"
        return "wait"

    # Check if strategy is approved
    if not state.get("strategy_approved", False):
        return "not_approved"

    # Check system mode
    mode = state.get("system_mode", "PAPER")
    if mode == "HALTED":
        return "halted"

    return "scan"
