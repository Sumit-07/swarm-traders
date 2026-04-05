"""LangGraph subgraph for the daily Optimizer meeting.

Called by the scheduler at 3:50 PM as a separate graph invocation.

Meeting flow:
  START
    -> round1 (3 LLM calls — each agent reviews own decisions)
    -> round2 (3 LLM calls — each agent sees all Round 1, finds patterns)
    -> round3 (3 LLM calls — each agent commits to one specific change)
    -> synthesis (1 LLM call — Optimizer writes learnings + Telegram message)
    -> save_to_db (saves transcript + writes learnings to knowledge graph)
    -> notify_orchestrator (publishes synthesis to Telegram — ALWAYS runs)
  END

Two non-negotiable rules:
1. Every agent reply <= 100 words — enforced in code via enforce_word_limit()
2. notify_orchestrator ALWAYS runs and sends Telegram — no exceptions
"""

import json
from typing import TypedDict

from langgraph.graph import END, StateGraph

from tools.llm import call_llm, render_prompt
from tools.logger import get_agent_logger

logger = get_agent_logger("meeting_subgraph")

WORD_LIMIT = 100


# ── Utilities ────────────────────────────────────────────────────────────────

def enforce_word_limit(text: str, limit: int = WORD_LIMIT) -> str:
    """Truncate text to word limit. Code-level enforcement — don't rely on prompt."""
    words = text.split()
    if len(words) <= limit:
        return text
    logger.warning(
        "Agent response exceeded %d words (%d words). Truncating.",
        limit, len(words),
    )
    return " ".join(words[:limit]) + " [truncated]"


# ── State ────────────────────────────────────────────────────────────────────

class MeetingState(TypedDict, total=False):
    # Input data
    date: str
    trade_count: int
    conservative_pnl: float
    risk_pnl: float
    regime: str
    vix: float
    nifty_change_pct: float
    trades_data: list
    signals_data: list
    strategy_selected: str
    morning_rationale: str
    morning_confidence: str
    risk_strategy: str
    instrument: str

    # Meeting outputs — populated as meeting progresses
    round1_strategist: str
    round1_risk_strat: str
    round1_analyst: str
    round2_strategist: str
    round2_risk_strat: str
    round2_analyst: str
    round3_strategist: str
    round3_risk_strat: str
    round3_analyst: str
    synthesis_raw: str
    learnings: list
    telegram_message: str
    error: str


# ── LLM helper ───────────────────────────────────────────────────────────────

def _llm_call(system: str, prompt: str) -> str:
    """Make an LLM call using the optimizer's model (GPT-4o)."""
    return call_llm("optimizer", system, prompt, expect_json=False)


# ── Round nodes ──────────────────────────────────────────────────────────────

def _build_system_prompt(state: MeetingState) -> str:
    from agents.optimizer.prompts import OPTIMIZER_SYSTEM_PROMPT
    return render_prompt(OPTIMIZER_SYSTEM_PROMPT, {
        "date": state.get("date", ""),
        "trade_count": state.get("trade_count", 0),
        "conservative_pnl": state.get("conservative_pnl", 0),
        "risk_pnl": state.get("risk_pnl", 0),
        "actual_regime": state.get("regime", "unknown"),
        "vix": state.get("vix", 0),
        "nifty_change_pct": state.get("nifty_change_pct", 0),
    })


def round1_node(state: MeetingState) -> MeetingState:
    """Round 1: Each agent reviews their own decisions independently."""
    from agents.optimizer.prompts import (
        PROMPT_ROUND1_STRATEGIST,
        PROMPT_ROUND1_RISK_STRATEGIST,
        PROMPT_ROUND1_ANALYST,
    )

    system = _build_system_prompt(state)
    trades = state.get("trades_data", [])
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    losses = sum(1 for t in trades if (t.get("pnl") or 0) < 0)

    p_strat = render_prompt(PROMPT_ROUND1_STRATEGIST, {
        "strategy_selected": state.get("strategy_selected", "N/A"),
        "regime_detected": state.get("regime", "unknown"),
        "morning_rationale": state.get("morning_rationale", "N/A"),
        "morning_confidence": state.get("morning_confidence", "N/A"),
        "actual_regime": state.get("regime", "unknown"),
        "nifty_move": state.get("nifty_change_pct", 0),
        "vix": state.get("vix", 0),
        "strategy_result": "profitable" if state.get("conservative_pnl", 0) > 0 else "loss",
        "trades_taken": state.get("trade_count", 0),
        "wins": wins,
        "losses": losses,
    })

    p_risk = render_prompt(PROMPT_ROUND1_RISK_STRATEGIST, {
        "risk_strategy": state.get("risk_strategy", "N/A"),
        "instrument": state.get("instrument", "N/A"),
        "premium": abs(state.get("risk_pnl", 0)) if (state.get("risk_pnl", 0) or 0) < 0 else 0,
        "catalyst": "calendar event" if state.get("event_today") else "directional setup",
        "max_loss": 2500,
        "outcome": "profit" if (state.get("risk_pnl", 0) or 0) > 0 else "loss",
        "risk_pnl": state.get("risk_pnl", 0),
        "catalyst_result": "yes" if (state.get("risk_pnl", 0) or 0) > 0 else "no",
        "exit_premium": 0,
    })

    signals = state.get("signals_data", [])
    signals_summary = "\n".join([
        f"{s.get('symbol', '?')} | RSI:{s.get('rsi', '?')} VWAP:{s.get('vwap_dev', '?')}% "
        f"| fired:{s.get('fired', '?')} | outcome:{s.get('outcome', '?')}"
        for s in signals[:10]
    ]) or "No signals generated today."

    p_analyst = render_prompt(PROMPT_ROUND1_ANALYST, {
        "signals_list": signals_summary,
        "missed_signals": "None identified",
        "nifty_move": state.get("nifty_change_pct", 0),
        "sector_performance": "mixed",
        "volume_summary": "normal",
    })

    r1_strat = enforce_word_limit(_llm_call(system, p_strat))
    r1_risk = enforce_word_limit(_llm_call(system, p_risk))
    r1_analyst = enforce_word_limit(_llm_call(system, p_analyst))

    logger.info(
        "Round 1 complete. Words: strat=%d risk=%d analyst=%d",
        len(r1_strat.split()), len(r1_risk.split()), len(r1_analyst.split()),
    )

    return {
        **state,
        "round1_strategist": r1_strat,
        "round1_risk_strat": r1_risk,
        "round1_analyst": r1_analyst,
    }


def round2_node(state: MeetingState) -> MeetingState:
    """Round 2: Each agent sees all Round 1 responses, finds cross-agent patterns."""
    from agents.optimizer.prompts import PROMPT_ROUND2_ALL_AGENTS

    system = _build_system_prompt(state)

    def make_prompt(agent_name: str) -> str:
        return render_prompt(PROMPT_ROUND2_ALL_AGENTS, {
            "agent_name": agent_name,
            "round1_strategist": state["round1_strategist"],
            "round1_risk_strategist": state["round1_risk_strat"],
            "round1_analyst": state["round1_analyst"],
            "conservative_pnl": state.get("conservative_pnl", 0),
            "risk_pnl": state.get("risk_pnl", 0),
        })

    r2_strat = enforce_word_limit(_llm_call(system, make_prompt("Strategist")))
    r2_risk = enforce_word_limit(_llm_call(system, make_prompt("Risk Strategist")))
    r2_analyst = enforce_word_limit(_llm_call(system, make_prompt("Analyst")))

    logger.info("Round 2 complete.")

    return {
        **state,
        "round2_strategist": r2_strat,
        "round2_risk_strat": r2_risk,
        "round2_analyst": r2_analyst,
    }


def round3_node(state: MeetingState) -> MeetingState:
    """Round 3: Each agent commits to ONE specific measurable change."""
    from agents.optimizer.prompts import PROMPT_ROUND3_ALL_AGENTS

    system = _build_system_prompt(state)

    summary = (
        f"Round 1 findings: {state['round1_strategist'][:80]}... "
        f"{state['round1_risk_strat'][:80]}... {state['round1_analyst'][:80]}...\n"
        f"Round 2 findings: {state['round2_strategist'][:80]}... "
        f"{state['round2_risk_strat'][:80]}... {state['round2_analyst'][:80]}..."
    )

    def make_prompt(agent_name: str) -> str:
        return render_prompt(PROMPT_ROUND3_ALL_AGENTS, {
            "agent_name": agent_name,
            "optimizer_summary_of_rounds_1_and_2": summary,
        })

    r3_strat = enforce_word_limit(_llm_call(system, make_prompt("Strategist")))
    r3_risk = enforce_word_limit(_llm_call(system, make_prompt("Risk Strategist")))
    r3_analyst = enforce_word_limit(_llm_call(system, make_prompt("Analyst")))

    logger.info("Round 3 complete.")

    return {
        **state,
        "round3_strategist": r3_strat,
        "round3_risk_strat": r3_risk,
        "round3_analyst": r3_analyst,
    }


def synthesis_node(state: MeetingState) -> MeetingState:
    """Optimizer synthesises all Round 3 outputs into learnings + Telegram message."""
    from agents.optimizer.prompts import PROMPT_OPTIMIZER_SYNTHESIS

    prompt = render_prompt(PROMPT_OPTIMIZER_SYNTHESIS, {
        "date": state["date"],
        "regime": state.get("regime", "unknown"),
        "vix": state.get("vix", 0),
        "conservative_pnl": state.get("conservative_pnl", 0),
        "risk_pnl": state.get("risk_pnl", 0),
        "trade_count": state.get("trade_count", 0),
        "round3_strategist": state["round3_strategist"],
        "round3_risk_strategist": state["round3_risk_strat"],
        "round3_analyst": state["round3_analyst"],
    })

    system = (
        "You are the Optimizer. Output structured JSON learnings then a --- separator "
        "then a plain-text Telegram message. No markdown in the Telegram section."
    )

    raw = _llm_call(system, prompt)

    learnings = []
    telegram_message = ""

    try:
        if "---" in raw:
            json_part, telegram_part = raw.split("---", 1)
            # Strip markdown code fences if present
            json_str = json_part.strip()
            if json_str.startswith("```"):
                json_str = json_str.split("\n", 1)[1] if "\n" in json_str else json_str[3:]
            if json_str.endswith("```"):
                json_str = json_str[:-3]
            learnings = json.loads(json_str.strip())
            telegram_message = telegram_part.strip()
        else:
            learnings = json.loads(raw.strip())
            telegram_message = (
                f"OPTIMIZER REPORT — {state['date']}\n"
                f"Conservative: {state.get('conservative_pnl', 0):.0f} | "
                f"Risk: {state.get('risk_pnl', 0):.0f}\n"
                f"{len(learnings)} learning(s) written to knowledge graph."
            )
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Synthesis parsing failed: %s", e)
        learnings = []
        telegram_message = (
            f"OPTIMIZER REPORT — {state['date']}\n"
            f"Meeting completed but synthesis parsing failed.\n"
            f"Conservative: {state.get('conservative_pnl', 0):.0f} | "
            f"Risk: {state.get('risk_pnl', 0):.0f}\n"
            f"Raw output saved to optimizer_meetings table for manual review."
        )

    logger.info("Synthesis complete. %d learnings extracted.", len(learnings))

    return {
        **state,
        "synthesis_raw": raw,
        "learnings": learnings,
        "telegram_message": telegram_message,
    }


# ── DB and notification nodes (bound via closure) ────────────────────────────

def _make_save_node(sqlite_store):
    """Create save_to_db node with bound SQLite store."""
    def save_to_db_node(state: MeetingState) -> MeetingState:
        from memory.knowledge_graph import write_learnings

        # Save full meeting transcript
        sqlite_store.execute("""
            INSERT OR REPLACE INTO optimizer_meetings (
                meeting_date, trade_count, conservative_pnl, risk_pnl, regime,
                round1_strategist, round1_risk_strat, round1_analyst,
                round2_strategist, round2_risk_strat, round2_analyst,
                round3_strategist, round3_risk_strat, round3_analyst,
                synthesis_raw, learnings_written, telegram_sent
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
        """, [
            state["date"],
            state.get("trade_count", 0),
            state.get("conservative_pnl", 0),
            state.get("risk_pnl", 0),
            state.get("regime", ""),
            state.get("round1_strategist", ""),
            state.get("round1_risk_strat", ""),
            state.get("round1_analyst", ""),
            state.get("round2_strategist", ""),
            state.get("round2_risk_strat", ""),
            state.get("round2_analyst", ""),
            state.get("round3_strategist", ""),
            state.get("round3_risk_strat", ""),
            state.get("round3_analyst", ""),
            state.get("synthesis_raw", ""),
        ])

        # Write learnings to knowledge graph
        count = write_learnings(
            db=sqlite_store,
            learnings=state.get("learnings", []),
            meeting_date=state["date"],
            outcome_pnl=(state.get("conservative_pnl", 0) or 0)
                        + (state.get("risk_pnl", 0) or 0),
        )

        # Update count
        sqlite_store.execute("""
            UPDATE optimizer_meetings
            SET learnings_written = ?
            WHERE meeting_date = ?
        """, [count, state["date"]])

        logger.info("Meeting saved. %d learnings written.", count)
        return state

    return save_to_db_node


def _make_notify_node(redis_store, sqlite_store):
    """Create notify_orchestrator node with bound stores.

    THIS FUNCTION ALWAYS RUNS — NO CONDITION CAN SKIP IT.
    If telegram_message is empty, sends a fallback error message.
    """
    def notify_orchestrator_node(state: MeetingState) -> MeetingState:
        from agents.message import AgentMessage, MessageType, Priority

        message = (state.get("telegram_message") or "").strip()

        if not message:
            message = (
                f"OPTIMIZER REPORT — {state.get('date', 'unknown')}\n"
                f"Meeting completed. Synthesis generation failed — "
                f"check optimizer_meetings table for transcript.\n"
                f"Conservative P&L: {state.get('conservative_pnl', 0):.0f} | "
                f"Risk P&L: {state.get('risk_pnl', 0):.0f}"
            )

        msg = AgentMessage(
            from_agent="optimizer",
            to_agent="orchestrator",
            channel="channel:orchestrator",
            type=MessageType.SYNTHESIS,
            priority=Priority.HIGH,
            payload={
                "telegram_message": message,
                "learnings_count": len(state.get("learnings", [])),
                "meeting_date": state.get("date", ""),
                "telegram_mandatory": True,
            },
        )
        redis_store.publish("channel:orchestrator", msg.model_dump())

        # Mark telegram as sent in DB
        sqlite_store.execute("""
            UPDATE optimizer_meetings
            SET telegram_sent = TRUE
            WHERE meeting_date = ?
        """, [state.get("date", "")])

        logger.info("Optimizer synthesis published to Orchestrator.")
        return state

    return notify_orchestrator_node


# ── Graph builder ────────────────────────────────────────────────────────────

def build_meeting_graph(sqlite_store, redis_store):
    """Build and compile the Optimizer meeting LangGraph.

    Returns a compiled graph that can be invoked with:
        graph.invoke(initial_meeting_state)
    """
    graph = StateGraph(MeetingState)

    graph.add_node("round1", round1_node)
    graph.add_node("round2", round2_node)
    graph.add_node("round3", round3_node)
    graph.add_node("synthesis", synthesis_node)
    graph.add_node("save_to_db", _make_save_node(sqlite_store))
    graph.add_node("notify_orchestrator", _make_notify_node(redis_store, sqlite_store))

    graph.set_entry_point("round1")
    graph.add_edge("round1", "round2")
    graph.add_edge("round2", "round3")
    graph.add_edge("round3", "synthesis")
    graph.add_edge("synthesis", "save_to_db")
    graph.add_edge("save_to_db", "notify_orchestrator")
    graph.add_edge("notify_orchestrator", END)

    return graph.compile()
