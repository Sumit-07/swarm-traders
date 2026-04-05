"""Tests for the Optimizer agent — knowledge graph, meeting subgraph, guards."""

import json
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from memory.sqlite_store import SQLiteStore
from memory.knowledge_graph import (
    archive_stale_learnings,
    load_memories,
    reinforce_learning,
    write_learnings,
)
from graph.meeting_subgraph import enforce_word_limit


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite store with schema initialized."""
    db_path = str(tmp_path / "test.db")
    store = SQLiteStore(db_path)
    return store


@pytest.fixture
def sample_learning():
    return {
        "agent_target": "strategist",
        "category": "regime_detection",
        "regime": "trending",
        "applies_to": "all",
        "learning": "VIX above 20 combined with flat ADX signals false breakouts more often than trends",
        "confidence": 0.70,
    }


@pytest.fixture
def sample_learnings(sample_learning):
    return [
        sample_learning,
        {
            "agent_target": "analyst",
            "category": "signal_quality",
            "regime": "ranging",
            "applies_to": "intraday",
            "learning": "RSI signals below 28 in ranging markets produce better risk-reward than threshold 32",
            "confidence": 0.65,
        },
    ]


# ── enforce_word_limit ──────────────────────────────────────────────────────


def test_enforce_word_limit_truncates_over_limit():
    text = " ".join(["word"] * 150)
    result = enforce_word_limit(text, 100)
    words = result.split()
    assert len(words) == 101  # 100 words + "[truncated]"
    assert words[-1] == "[truncated]"


def test_enforce_word_limit_passes_under_limit():
    text = "This is a short sentence."
    result = enforce_word_limit(text, 100)
    assert result == text
    assert "[truncated]" not in result


# ── write_learnings ─────────────────────────────────────────────────────────


def test_write_learnings_rejects_missing_regime(db):
    bad_learning = {
        "agent_target": "strategist",
        "category": "test",
        # missing: regime, applies_to, learning, confidence
    }
    count = write_learnings(db, [bad_learning], "2025-01-15", 500.0)
    assert count == 0


def test_write_learnings_reinforces_similar_existing(db, sample_learning):
    """If a similar learning exists (>60% word overlap), reinforce it instead of creating new."""
    write_learnings(db, [sample_learning], "2025-01-15", 500.0)

    # Write a similar learning (same words, slight reorder)
    similar = {
        **sample_learning,
        "learning": "VIX above 20 combined with flat ADX often signals false breakouts rather than real trends",
    }
    count = write_learnings(db, [similar], "2025-01-16", 300.0)
    assert count == 1

    # Should have reinforced the existing one, not created a new row
    rows = db.query("SELECT * FROM learnings WHERE archived = FALSE")
    assert len(rows) == 1
    assert rows[0]["times_reinforced"] >= 2


# ── load_memories ───────────────────────────────────────────────────────────


def test_load_memories_returns_empty_for_no_learnings(db):
    result = load_memories(db, "strategist", "trending", "all")
    assert result == ""


def test_load_memories_filters_by_regime(db, sample_learnings):
    write_learnings(db, sample_learnings, "2025-01-15", 500.0)

    # Load for TRENDING regime — should get strategist learning
    result = load_memories(db, "strategist", "trending", "all")
    assert "VIX above 20" in result

    # Load for VOLATILE regime — should not match TRENDING learning
    result = load_memories(db, "strategist", "high_volatility", "all")
    assert "VIX above 20" not in result


def test_load_memories_scores_by_confidence_and_reinforcement(db):
    """Higher confidence and more reinforcements should rank higher."""
    learnings = [
        {
            "agent_target": "strategist",
            "category": "regime_detection",
            "regime": "all",
            "applies_to": "all",
            "learning": "Low confidence learning that should rank lower in the results list",
            "confidence": 0.30,
        },
        {
            "agent_target": "strategist",
            "category": "timing",
            "regime": "all",
            "applies_to": "all",
            "learning": "High confidence learning that should rank first in the results list",
            "confidence": 0.90,
        },
    ]
    write_learnings(db, learnings, "2025-01-15", 500.0)

    # Reinforce the high-confidence one multiple times
    rows = db.query("SELECT id, learning FROM learnings ORDER BY confidence DESC")
    high_id = rows[0]["id"]
    reinforce_learning(db, high_id, "confirmed")
    reinforce_learning(db, high_id, "confirmed")

    result = load_memories(db, "strategist", "all", "all")
    lines = [l for l in result.strip().split("\n") if l.startswith("- ")]
    assert len(lines) == 2
    # High confidence + reinforced should be first
    assert "High confidence" in lines[0]
    assert "Low confidence" in lines[1]


# ── reinforce_learning ──────────────────────────────────────────────────────


def test_reinforce_learning_confirmed_increases_confidence(db, sample_learning):
    write_learnings(db, [sample_learning], "2025-01-15", 500.0)
    rows = db.query("SELECT id, confidence FROM learnings")
    learning_id = rows[0]["id"]
    original_confidence = rows[0]["confidence"]

    reinforce_learning(db, learning_id, "confirmed")

    updated = db.query("SELECT confidence, times_reinforced FROM learnings WHERE id = :id", {"id": learning_id})
    assert updated[0]["confidence"] > original_confidence
    assert updated[0]["times_reinforced"] == 2  # 1 initial + 1 reinforcement


def test_reinforce_learning_contradicted_decreases_confidence(db, sample_learning):
    write_learnings(db, [sample_learning], "2025-01-15", 500.0)
    rows = db.query("SELECT id, confidence FROM learnings")
    learning_id = rows[0]["id"]
    original_confidence = rows[0]["confidence"]

    reinforce_learning(db, learning_id, "contradicted")

    updated = db.query("SELECT confidence FROM learnings WHERE id = :id", {"id": learning_id})
    assert updated[0]["confidence"] < original_confidence


# ── archive_stale_learnings ─────────────────────────────────────────────────


def test_archive_stale_learnings_archives_old_unreinforced(db):
    """Learnings >90 days old with <3 reinforcements should be archived."""
    old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
    db.execute("""
        INSERT INTO learnings (
            created_date, agent_target, category, regime,
            applies_to, learning, confidence, times_reinforced,
            last_reinforced, outcome_pnl, source_meeting_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        old_date, "strategist", "regime_detection", "trending", "all",
        "Old stale learning that should be archived after ninety days",
        0.50, 1, old_date, 0, old_date,
    ])

    count = archive_stale_learnings(db)
    assert count == 1

    rows = db.query("SELECT archived FROM learnings")
    assert rows[0]["archived"] == 1


def test_archive_stale_learnings_keeps_well_reinforced(db):
    """Learnings with >=3 reinforcements should not be archived even if old."""
    old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
    db.execute("""
        INSERT INTO learnings (
            created_date, agent_target, category, regime,
            applies_to, learning, confidence, times_reinforced,
            last_reinforced, outcome_pnl, source_meeting_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        old_date, "strategist", "regime_detection", "trending", "all",
        "Well reinforced learning that should survive archival process easily",
        0.80, 5, old_date, 0, old_date,
    ])

    count = archive_stale_learnings(db)
    assert count == 0

    rows = db.query("SELECT archived FROM learnings")
    assert rows[0]["archived"] == 0


# ── notify_orchestrator ─────────────────────────────────────────────────────


def test_notify_orchestrator_publishes_even_on_empty_synthesis(db):
    """notify_orchestrator must ALWAYS publish — even if telegram_message is empty."""
    from graph.meeting_subgraph import _make_notify_node

    redis_mock = MagicMock()
    node = _make_notify_node(redis_mock, db)

    state = {
        "date": "2025-01-15",
        "telegram_message": "",
        "learnings": [],
        "conservative_pnl": 150,
        "risk_pnl": -50,
    }

    node(state)

    # Must have published to channel:orchestrator
    redis_mock.publish.assert_called_once()
    call_args = redis_mock.publish.call_args
    assert call_args[0][0] == "channel:orchestrator"

    # The fallback message should mention the date
    payload = call_args[0][1]
    assert "2025-01-15" in payload["payload"]["telegram_message"]


# ── synthesis parsing ───────────────────────────────────────────────────────


def test_synthesis_parsing_handles_malformed_json():
    """synthesis_node should produce a fallback Telegram message on bad JSON."""
    from graph.meeting_subgraph import synthesis_node

    state = {
        "date": "2025-01-15",
        "regime": "trending",
        "vix": 15.0,
        "conservative_pnl": 200,
        "risk_pnl": -100,
        "trade_count": 3,
        "round3_strategist": "Some text",
        "round3_risk_strat": "Some text",
        "round3_analyst": "Some text",
    }

    # Mock _llm_call to return malformed output
    with patch("graph.meeting_subgraph._llm_call", return_value="NOT VALID JSON AT ALL"):
        result = synthesis_node(state)

    assert result["learnings"] == []
    assert "parsing failed" in result["telegram_message"]
    assert "2025-01-15" in result["telegram_message"]


# ── Meeting guards (scheduler) ──────────────────────────────────────────────


def test_meeting_guard_skips_if_fewer_than_2_trades(db):
    """Optimizer meeting should be skipped if fewer than 2 closed trades today."""
    from unittest.mock import MagicMock
    from scheduler.job_scheduler import SwarmScheduler

    # Create orchestrator mock with sqlite/redis
    orchestrator_mock = MagicMock()
    orchestrator_mock.sqlite = db
    orchestrator_mock.redis = MagicMock()
    orchestrator_mock.redis.get_state.return_value = {"mode": "PAPER"}

    agents = {"orchestrator": orchestrator_mock}
    telegram_mock = MagicMock()
    scheduler = SwarmScheduler(agents, {}, telegram_mock)

    # No trades exist — should skip
    scheduler._run_optimizer_meeting()

    # Should have sent a skip message via telegram
    telegram_mock.send_message.assert_called()
    msg = telegram_mock.send_message.call_args[0][0]
    assert "only" in msg.lower() or "0" in msg


def test_meeting_guard_skips_if_already_ran_today(db):
    """If optimizer_meetings already has an entry for today, skip."""
    from zoneinfo import ZoneInfo
    from scheduler.job_scheduler import SwarmScheduler

    IST = ZoneInfo("Asia/Kolkata")
    today = datetime.now(IST).strftime("%Y-%m-%d")

    # Insert a meeting record for today
    db.execute("""
        INSERT INTO optimizer_meetings (
            meeting_date, trade_count, conservative_pnl, risk_pnl, regime,
            round1_strategist, round1_risk_strat, round1_analyst,
            round2_strategist, round2_risk_strat, round2_analyst,
            round3_strategist, round3_risk_strat, round3_analyst,
            synthesis_raw, learnings_written, telegram_sent
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, [
        today, 3, 200, -50, "trending",
        "r1s", "r1r", "r1a",
        "r2s", "r2r", "r2a",
        "r3s", "r3r", "r3a",
        "raw", 2, 1,
    ])

    # Insert enough trades to pass the first guard
    for i in range(3):
        db.execute("""
            INSERT INTO trades (
                trade_id, symbol, direction, bucket, strategy,
                entry_price, quantity, status, entry_time, mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            f"test-trade-{i}", "RELIANCE", "LONG", "conservative",
            "RSI_MEAN_REVERSION", 2800, 1, "CLOSED",
            f"{today} 10:00:00", "PAPER",
        ])

    orchestrator_mock = MagicMock()
    orchestrator_mock.sqlite = db
    orchestrator_mock.redis = MagicMock()
    orchestrator_mock.redis.get_state.return_value = {"mode": "PAPER"}
    orchestrator_mock.redis.get_market_data.return_value = {}

    agents = {"orchestrator": orchestrator_mock}
    telegram_mock = MagicMock()
    scheduler = SwarmScheduler(agents, {"meeting": MagicMock()}, telegram_mock)

    scheduler._run_optimizer_meeting()

    telegram_mock.send_message.assert_called()
    msg = telegram_mock.send_message.call_args[0][0]
    assert "already" in msg.lower()
