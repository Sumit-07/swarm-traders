"""LangGraph graph definitions for the trading swarm.

Four sub-graphs triggered at different times of day:
1. Morning Strategy — 8:00 AM
2. Intraday Signal Loop — 9:30 AM to 3:00 PM, every 5 min
3. Force Close — 3:20 PM
4. EOD Review — 3:30 PM
"""

from langgraph.graph import END, StateGraph

from graph.edges import (
    has_signal,
    is_approved,
    needs_human_approval,
    should_proceed_after_approval,
)
from graph.state import SwarmState


def _make_node(agent):
    """Create a LangGraph node function from an agent instance."""
    def node_fn(state: SwarmState) -> SwarmState:
        return agent.run(state)
    return node_fn


def _human_approval_node(state: SwarmState) -> SwarmState:
    """Wait for human approval via Telegram.

    In Phase 2, this is a stub that auto-approves.
    Phase 4: will poll Telegram for response.
    """
    # Phase 2 stub: auto-approve
    state["strategy_approved"] = True
    state["human_response"] = "YES"
    state["human_approval_pending"] = False
    return state


def build_morning_graph(data_agent, strategist, risk_strategist, orchestrator):
    """Morning strategy selection flow (8:00 AM).

    Data Agent -> [Strategist + Risk Strategist] -> Orchestrator -> Human Approval
    """
    graph = StateGraph(SwarmState)

    graph.add_node("data_agent", _make_node(data_agent))
    graph.add_node("strategist", _make_node(strategist))
    graph.add_node("risk_strategist", _make_node(risk_strategist))
    graph.add_node("orchestrator", _make_node(orchestrator))
    graph.add_node("human_approval", _human_approval_node)

    graph.set_entry_point("data_agent")
    graph.add_edge("data_agent", "strategist")
    graph.add_edge("data_agent", "risk_strategist")
    graph.add_edge("strategist", "orchestrator")
    graph.add_edge("risk_strategist", "orchestrator")

    graph.add_edge("orchestrator", "human_approval")
    graph.add_conditional_edges(
        "human_approval",
        should_proceed_after_approval,
        {
            "approved": END,
            "rejected": END,
            "timeout": END,
        },
    )

    return graph.compile()


def build_signal_graph(data_agent, analyst, risk_agent, orchestrator,
                       execution_agent, compliance_agent):
    """Intraday signal detection and execution flow.

    Data refresh -> Analyst scan -> [signal?] -> Risk review -> [approved?]
    -> Orchestrator -> [needs human?] -> Execution -> Compliance
    """
    graph = StateGraph(SwarmState)

    graph.add_node("data_refresh", _make_node(data_agent))
    graph.add_node("analyst_scan", _make_node(analyst))
    graph.add_node("risk_review", _make_node(risk_agent))
    graph.add_node("orchestrator", _make_node(orchestrator))
    graph.add_node("human_approval", _human_approval_node)
    graph.add_node("execution", _make_node(execution_agent))
    graph.add_node("compliance", _make_node(compliance_agent))

    graph.set_entry_point("data_refresh")
    graph.add_edge("data_refresh", "analyst_scan")

    graph.add_conditional_edges(
        "analyst_scan",
        has_signal,
        {"signal": "risk_review", "no_signal": END},
    )

    graph.add_conditional_edges(
        "risk_review",
        is_approved,
        {"approved": "orchestrator", "rejected": END},
    )

    graph.add_conditional_edges(
        "orchestrator",
        needs_human_approval,
        {"needs_human": "human_approval", "auto_approved": "execution"},
    )

    graph.add_edge("human_approval", "execution")
    graph.add_edge("execution", "compliance")
    graph.add_edge("compliance", END)

    return graph.compile()


def build_force_close_graph(risk_agent, orchestrator, execution_agent):
    """Force close all intraday positions at 3:20 PM.

    Risk Agent check -> Orchestrator -> Execution Agent
    """
    graph = StateGraph(SwarmState)

    graph.add_node("risk_check", _make_node(risk_agent))
    graph.add_node("orchestrator", _make_node(orchestrator))
    graph.add_node("execution", _make_node(execution_agent))

    graph.set_entry_point("risk_check")
    graph.add_edge("risk_check", "orchestrator")
    graph.add_edge("orchestrator", "execution")
    graph.add_edge("execution", END)

    return graph.compile()


def build_eod_graph(compliance_agent, strategist, orchestrator):
    """End-of-day review flow (3:30 PM).

    Compliance audit -> Strategy review -> Orchestrator EOD summary
    """
    graph = StateGraph(SwarmState)

    graph.add_node("compliance_audit", _make_node(compliance_agent))
    graph.add_node("strategy_review", _make_node(strategist))
    graph.add_node("orchestrator_summary", _make_node(orchestrator))

    graph.set_entry_point("compliance_audit")
    graph.add_edge("compliance_audit", "strategy_review")
    graph.add_edge("strategy_review", "orchestrator_summary")
    graph.add_edge("orchestrator_summary", END)

    return graph.compile()
