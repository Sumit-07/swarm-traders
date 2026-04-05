"""Knowledge graph read/write operations for the Optimizer agent.

Called by Optimizer (write) and all agents at startup (read).
Never import from agent modules here — this is a shared utility.
All writes must include a regime tag. Reject any learning without one.
"""

from datetime import date, datetime
from math import log

from tools.logger import get_agent_logger

logger = get_agent_logger("knowledge_graph")

REQUIRED_FIELDS = {
    "agent_target", "category", "regime",
    "applies_to", "learning", "confidence",
}


def write_learnings(
    db,
    learnings: list[dict],
    meeting_date,
    outcome_pnl: float,
) -> int:
    """Write new learnings from Optimizer synthesis to the knowledge graph.

    Returns number of learnings successfully written.
    Reinforces existing learnings if a similar one already exists.
    """
    if isinstance(meeting_date, str):
        meeting_date_str = meeting_date
    else:
        meeting_date_str = meeting_date.isoformat()

    written = 0
    for learning in learnings:
        missing = REQUIRED_FIELDS - set(learning.keys())
        if missing:
            logger.warning("Skipping learning — missing fields: %s", missing)
            continue

        if not learning.get("learning") or len(learning["learning"]) < 20:
            logger.warning("Skipping learning — too vague or empty.")
            continue

        existing = _find_similar_learning(db, learning)
        if existing:
            reinforce_learning(db, existing["id"], "confirmed")
            logger.info("Reinforced existing learning id=%d", existing["id"])
            written += 1
            continue

        db.execute("""
            INSERT INTO learnings (
                created_date, agent_target, category, regime,
                applies_to, learning, confidence, times_reinforced,
                last_reinforced, outcome_pnl, source_meeting_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, [
            meeting_date_str,
            learning["agent_target"],
            learning["category"],
            learning["regime"],
            learning["applies_to"],
            learning["learning"],
            learning["confidence"],
            meeting_date_str,
            outcome_pnl,
            meeting_date_str,
        ])
        written += 1
        logger.info("New learning written: %s", learning["learning"][:60])

    return written


def load_memories(
    db,
    agent_id: str,
    current_regime: str,
    strategy_type: str,
    limit: int = 5,
) -> str:
    """Load relevant learnings for an agent at startup.

    Returns formatted string for injection into agent system prompt.
    Returns empty string if no relevant learnings exist.

    Scoring: confidence * log(times_reinforced + 1) * recency_weight
    Recency: <=7d: 1.0, 8-30d: 0.8, 31-90d: 0.5, >90d: 0.3
    """
    rows = db.query("""
        SELECT
            id, learning, confidence, times_reinforced,
            regime, category, last_reinforced,
            julianday('now') - julianday(last_reinforced) AS days_since
        FROM learnings
        WHERE archived = FALSE
          AND (agent_target = :agent_id OR agent_target = 'all')
          AND (regime = :regime OR regime = 'all')
          AND (applies_to = :strategy_type OR applies_to = 'all')
    """, {
        "agent_id": agent_id,
        "regime": current_regime,
        "strategy_type": strategy_type,
    })

    if not rows:
        return ""

    # Score in Python (SQLite lacks LOG function)
    scored = []
    for row in rows:
        days = row.get("days_since", 0) or 0
        if days <= 7:
            recency = 1.0
        elif days <= 30:
            recency = 0.8
        elif days <= 90:
            recency = 0.5
        else:
            recency = 0.3

        score = (
            row["confidence"]
            * log(row["times_reinforced"] + 1)
            * recency
        )
        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]

    lines = []
    for _, row in top:
        reinforced_note = (
            f"reinforced {row['times_reinforced']}x"
            if row["times_reinforced"] > 1
            else "new"
        )
        lines.append(
            f"- [{row['category'].replace('_', ' ')}] "
            f"{row['learning']} "
            f"({reinforced_note}, confidence {row['confidence']:.0%})"
        )

    return (
        "\n\nLEARNINGS FROM PAST TRADING — apply where relevant today:\n"
        + "\n".join(lines)
        + "\n"
    )


def reinforce_learning(db, learning_id: int, outcome: str) -> None:
    """Update a learning's confidence based on new evidence.

    Args:
        outcome: 'confirmed' | 'contradicted' | 'neutral'
    """
    if outcome == "confirmed":
        db.execute("""
            UPDATE learnings
            SET times_reinforced = times_reinforced + 1,
                confidence = MIN(0.97, confidence * 1.1),
                last_reinforced = date('now')
            WHERE id = ?
        """, [learning_id])
        logger.info("Learning %d reinforced — confidence increased.", learning_id)

    elif outcome == "contradicted":
        db.execute("""
            UPDATE learnings
            SET confidence = MAX(0.20, confidence * 0.8),
                last_reinforced = date('now')
            WHERE id = ?
        """, [learning_id])
        logger.info("Learning %d contradicted — confidence decreased.", learning_id)


def archive_stale_learnings(db) -> int:
    """Archive learnings not reinforced in 90+ days with <3 reinforcements.

    Run weekly (Sunday evening). Returns count of archived learnings.
    """
    result = db.execute("""
        UPDATE learnings
        SET archived = TRUE
        WHERE archived = FALSE
          AND julianday('now') - julianday(last_reinforced) > 90
          AND times_reinforced < 3
    """)
    count = result.rowcount if result else 0
    if count > 0:
        logger.info("Archived %d stale learnings.", count)
    return count


def _find_similar_learning(db, new_learning: dict) -> dict | None:
    """Check if a very similar learning already exists.

    Similarity: same agent_target + category + regime, word overlap > 60%.
    """
    existing = db.query("""
        SELECT id, learning FROM learnings
        WHERE archived = FALSE
          AND agent_target = :agent_target
          AND category = :category
          AND regime = :regime
    """, {
        "agent_target": new_learning["agent_target"],
        "category": new_learning["category"],
        "regime": new_learning["regime"],
    })

    new_words = set(new_learning["learning"].lower().split())
    for row in existing:
        existing_words = set(row["learning"].lower().split())
        if len(new_words) == 0:
            continue
        overlap = len(new_words & existing_words) / len(new_words)
        if overlap > 0.60:
            return row

    return None
