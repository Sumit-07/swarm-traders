"""SwarmState — shared state that flows through LangGraph.

This TypedDict defines all the data that agents read/write
as the graph executes different sub-flows.
"""

from typing import TypedDict


class SwarmState(TypedDict, total=False):
    # System
    system_mode: str                    # PAPER / LIVE / HALTED / REVIEW
    current_phase: str                  # PRE_MARKET / MARKET_OPEN / MARKET_CLOSE / POST_MARKET
    trading_day: str                    # ISO date (YYYY-MM-DD)

    # Data
    market_data_ready: bool
    last_data_update: str               # ISO timestamp
    market_snapshot: dict               # {nifty, banknifty, vix}
    watchlist_data: dict                # per-symbol indicator data

    # Strategy
    conservative_strategy: dict | None  # strategy config from Strategist
    risk_strategy: dict | None          # strategy config from Risk Strategist
    strategy_approved: bool             # human approved via Telegram
    strategy_approval_time: str | None

    # Signals and trades
    pending_signals: list               # signal IDs from Analyst awaiting Risk review
    approved_orders: list               # orders approved by Risk, awaiting execution
    rejected_proposals: list            # rejected proposals for logging
    active_positions: list              # currently open positions

    # Agent states
    agent_statuses: dict                # {agent_id: {state, last_action, ...}}

    # Human interaction
    human_approval_pending: bool
    human_response: str | None          # YES / NO / EDIT / specific command

    # Flow control
    error: str | None
    halt_reason: str | None
