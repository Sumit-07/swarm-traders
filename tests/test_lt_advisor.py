"""Tests for LT_Advisor scoring, silence conditions, VIX thresholds, and universe."""

import tempfile
import os

import pytest

from agents.lt_advisor.lt_advisor import compute_quick_score, LTAdvisor
from memory.sqlite_store import SQLiteStore


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db():
    """Creates a temporary SQLite database with the full schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SQLiteStore(path)
    yield db
    os.unlink(path)


def make_test_advisor(db):
    """Creates an LTAdvisor with a real DB and no Redis (not needed for these tests)."""

    class FakeRedis:
        def get_state(self, key):
            return None

        def set_state(self, key, value, ttl=None):
            pass

        def publish(self, channel, message):
            return 0

    return LTAdvisor(redis=FakeRedis(), db=db)


# ── Scoring model ────────────────────────────────────────────────────────────


def test_high_vix_scores_above_threshold():
    score = compute_quick_score(
        vix=26, vix_trend="STABLE", nifty_pe=19.5,
        nifty_from_high_pct=-8.0, fii_30day_crore=-7000,
    )
    assert score >= 55


def test_low_vix_scores_below_threshold():
    score = compute_quick_score(
        vix=13, vix_trend="STABLE", nifty_pe=24,
        nifty_from_high_pct=-2.0, fii_30day_crore=2000,
    )
    assert score < 55


def test_falling_vix_penalty_reduces_score():
    stable_score = compute_quick_score(26, "STABLE", 20, -10, -8000)
    falling_score = compute_quick_score(26, "FALLING", 20, -10, -8000)
    assert falling_score < stable_score


def test_score_never_exceeds_100():
    score = compute_quick_score(35, "RISING", 14, -25, -15000)
    assert score <= 100


# ── VIX threshold crossing ──────────────────────────────────────────────────


def test_vix_threshold_25_detected_first_time(tmp_db):
    advisor = make_test_advisor(tmp_db)
    result = advisor._check_vix_threshold_crossing(vix=26.0)
    assert result is not None
    assert result["threshold_crossed"] == 25
    assert result["tranche_number"] == 2


def test_vix_threshold_not_repeated_same_month(tmp_db):
    advisor = make_test_advisor(tmp_db)
    # Alert both 25 and 20 thresholds this month
    tmp_db.execute(
        """
        INSERT INTO lt_advisor_log
        (run_type, action_taken, alert_type, threshold_crossed)
        VALUES ('MORNING', 'ALERT', 'VIX_THRESHOLD', 25)
        """,
    )
    tmp_db.execute(
        """
        INSERT INTO lt_advisor_log
        (run_type, action_taken, alert_type, threshold_crossed)
        VALUES ('MORNING', 'ALERT', 'VIX_THRESHOLD', 20)
        """,
    )
    result = advisor._check_vix_threshold_crossing(vix=26.0)
    assert result is None


def test_vix_30_independent_of_25(tmp_db):
    advisor = make_test_advisor(tmp_db)
    tmp_db.execute(
        """
        INSERT INTO lt_advisor_log
        (run_type, action_taken, alert_type, threshold_crossed)
        VALUES ('MORNING', 'ALERT', 'VIX_THRESHOLD', 25)
        """,
    )
    result = advisor._check_vix_threshold_crossing(vix=31.0)
    assert result is not None
    assert result["threshold_crossed"] == 30


# ── Universe ────────────────────────────────────────────────────────────────


def test_tier1_always_has_instruments():
    from config.lt_universe import LT_UNIVERSE
    assert len(LT_UNIVERSE["TIER_1"]) >= 2


def test_tier2_included_at_high_vix():
    from config.lt_universe import LT_UNIVERSE
    assert len(LT_UNIVERSE["TIER_2"]) >= 1


def test_no_blacklisted_instruments_in_universe():
    from config.lt_universe import LT_UNIVERSE, LT_BLACKLIST
    all_names = []
    for tier in LT_UNIVERSE.values():
        for inst in tier:
            all_names.append(inst["name"].lower())
    for blacklisted in LT_BLACKLIST:
        for name in all_names:
            assert blacklisted.lower() not in name


# ── Silence conditions ──────────────────────────────────────────────────────


def test_silence_if_recently_alerted(tmp_db):
    advisor = make_test_advisor(tmp_db)
    tmp_db.execute(
        """
        INSERT INTO lt_advisor_log
        (run_type, action_taken, instrument, logged_at)
        VALUES ('MORNING', 'ALERT', 'UTI Nifty 50 Index Fund',
                datetime('now', '-3 days'))
        """,
    )
    silence = advisor._check_silence_conditions({"vix": 26})
    assert silence is not None
    assert "tier1_alerted_recently" in silence


def test_no_silence_if_alert_was_8_days_ago(tmp_db):
    advisor = make_test_advisor(tmp_db)
    tmp_db.execute(
        """
        INSERT INTO lt_advisor_log
        (run_type, action_taken, instrument, logged_at)
        VALUES ('MORNING', 'ALERT', 'UTI Nifty 50 Index Fund',
                datetime('now', '-8 days'))
        """,
    )
    silence = advisor._check_silence_conditions({"vix": 26})
    assert silence is None
