"""SwarmState — shared state that flows through LangGraph.

This TypedDict defines all the data that agents read/write
as the graph executes different sub-flows.

Scalar keys use a "last writer wins" reducer so that parallel
branches (e.g. Strategist + Risk Strategist) can fan-in without
LangGraph raising INVALID_CONCURRENT_GRAPH_UPDATE.
"""

from typing import Annotated, TypedDict


def _last_value(current, new):
    """Reducer: last writer wins (for scalar keys in parallel branches)."""
    return new


def _merge_dict(current, new):
    """Reducer: shallow-merge dicts from parallel branches."""
    if current is None:
        return new
    if new is None:
        return current
    return {**current, **new}


def _concat_list(current, new):
    """Reducer: concatenate lists from parallel branches."""
    return (current or []) + (new or [])


class SwarmState(TypedDict, total=False):
    # System
    system_mode: Annotated[str, _last_value]
    current_phase: Annotated[str, _last_value]
    trading_day: Annotated[str, _last_value]

    # Data
    market_data_ready: Annotated[bool, _last_value]
    last_data_update: Annotated[str, _last_value]
    market_snapshot: Annotated[dict, _merge_dict]
    watchlist_data: Annotated[dict, _merge_dict]

    # Strategy
    conservative_strategy: Annotated[dict | None, _last_value]
    risk_strategy: Annotated[dict | None, _last_value]
    strategy_approved: Annotated[bool, _last_value]
    strategy_approval_time: Annotated[str | None, _last_value]

    # Signals and trades
    pending_signals: Annotated[list, _concat_list]
    approved_orders: Annotated[list, _concat_list]
    rejected_proposals: Annotated[list, _concat_list]
    active_positions: Annotated[list, _concat_list]

    # Agent states
    agent_statuses: Annotated[dict, _merge_dict]

    # Human interaction
    human_approval_pending: Annotated[bool, _last_value]
    human_response: Annotated[str | None, _last_value]

    # Flow control
    error: Annotated[str | None, _last_value]
    halt_reason: Annotated[str | None, _last_value]
