"""Tests for the Position Monitor agent — thresholds, alerts, guards."""

import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo

import pytest

from memory.sqlite_store import SQLiteStore
from agents.position_monitor.position_monitor import PositionMonitorAgent
from agents.position_monitor.thresholds import (
    get_thresholds,
    get_all_strategy_names,
    THRESHOLDS,
    MonitorThresholds,
)

IST = ZoneInfo("Asia/Kolkata")

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = SQLiteStore(db_path)
    return store


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.get_state.return_value = {}
    redis.get_market_data.return_value = None
    redis.publish.return_value = None
    # subscribe/get_state for base_agent
    redis.subscribe.return_value = None
    return redis


@pytest.fixture
def agent(mock_redis, db):
    with patch("agents.base_agent.threading"):
        agent = PositionMonitorAgent(mock_redis, db)
    return agent


@pytest.fixture
def sample_position():
    """Position dict as it exists in Redis state:positions."""
    # Use _market_hours_now() as reference so entry is always 30 min before fake "now"
    ref_time = _market_hours_now()
    entry_time = ref_time - timedelta(minutes=30)
    return {
        "trade_id": "T001",
        "symbol": "RELIANCE",
        "direction": "LONG",
        "entry_price": 2500.0,
        "quantity": 10,
        "stop_loss": 2450.0,
        "target": 2580.0,
        "bucket": "conservative",
        "status": "OPEN",
        "entry_time": entry_time.isoformat(),
    }


@pytest.fixture
def sample_tick():
    return {"ltp": 2480.0, "last_price": 2480.0, "volume_ratio": 1.2}


def _market_hours_now():
    """Return a datetime during market hours (11:00 AM IST today)."""
    now = datetime.now(IST)
    return now.replace(hour=11, minute=0, second=0, microsecond=0)


def _insert_trade(db, trade_id="T001", strategy="RSI_MEAN_REVERSION"):
    """Insert a minimal trade row so strategy lookup works."""
    db.execute(
        "INSERT INTO trades (trade_id, symbol, direction, strategy, entry_price, "
        "quantity, stop_loss, target, status, bucket, entry_time, mode) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [trade_id, "RELIANCE", "LONG", strategy, 2500.0, 10,
         2450.0, 2580.0, "OPEN", "conservative",
         datetime.now(IST).isoformat(), "PAPER"],
    )


# ── Test 1: Grace period suppresses alert ───────────────────────────────────


def test_grace_period_suppresses_alert(agent, sample_position, sample_tick, db):
    """Position entered 3 min ago, RSI_MEAN_REVERSION grace = 10 min → no alert."""
    _insert_trade(db, "T001", "RSI_MEAN_REVERSION")

    now = _market_hours_now()
    # Override entry to be 3 min before 'now'
    sample_position["entry_time"] = (now - timedelta(minutes=3)).isoformat()

    result = agent._check_position(sample_position, now)
    assert result is None


# ── Test 2: Cooldown suppresses alert ───────────────────────────────────────


def test_cooldown_suppresses_alert(agent, sample_position, sample_tick, db):
    """Alert sent 5 min ago, cooldown = 20 min → suppressed."""
    _insert_trade(db, "T001", "RSI_MEAN_REVERSION")

    # Insert a recent alert
    recent_alert_time = (datetime.now(IST) - timedelta(minutes=5)).isoformat()
    db.execute(
        "INSERT INTO monitor_alerts "
        "(trade_id, symbol, strategy_name, trigger_type, trigger_value, "
        "trigger_description, alerted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["T001", "RELIANCE", "RSI_MEAN_REVERSION", "adverse_move", 1.0,
         "test", recent_alert_time],
    )

    now = _market_hours_now()
    result = agent._check_position(sample_position, now)
    assert result is None


# ── Test 3: Adverse move triggers alert (intraday) ─────────────────────────


def test_adverse_move_triggers_alert_intraday(agent, sample_position, sample_tick, db):
    """RELIANCE down 1.0% with RSI_MEAN_REVERSION threshold 0.8% → alert."""
    _insert_trade(db, "T001", "RSI_MEAN_REVERSION")

    # Price dropped from 2500 to 2475 → -1.0%
    tick = {"ltp": 2475.0, "volume_ratio": 1.0}
    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:RELIANCE": tick,
        "data:last_candle:RELIANCE": None,
        "data:market_snapshot": {"nifty": {"ltp": 22000, "change_pct": -0.3}, "indiavix": {"ltp": 14}},
    }.get(key)

    now = _market_hours_now()
    result = agent._check_position(sample_position, now)
    assert result is not None
    assert result["trigger_type"] == "adverse_move"
    assert result["trigger_value"] >= 0.8


# ── Test 4: Adverse move NO alert for swing below threshold ────────────────


def test_adverse_move_no_alert_swing_below_threshold(agent, db):
    """SWING_MOMENTUM threshold 1.5%, price down 1.0% → no alert."""
    entry_time = (_market_hours_now() - timedelta(hours=2)).isoformat()
    position = {
        "trade_id": "T002", "symbol": "INFY", "direction": "LONG",
        "entry_price": 1500.0, "quantity": 5, "stop_loss": 1470.0,
        "target": 1560.0, "bucket": "conservative", "status": "OPEN",
        "entry_time": entry_time,
    }
    _insert_trade(db, "T002", "SWING_MOMENTUM")

    # Price at 1485 → -1.0% (below 1.5% threshold)
    tick = {"ltp": 1485.0, "volume_ratio": 1.0}
    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:INFY": tick,
        "data:last_candle:INFY": None,
        "data:market_snapshot": {},
    }.get(key)

    now = _market_hours_now()
    result = agent._check_position(position, now)
    assert result is None


# ── Test 5: Swing thresholds wider than intraday ──────────────────────────


def test_swing_threshold_wider_than_intraday():
    """SWING_MOMENTUM adverse threshold > RSI_MEAN_REVERSION."""
    swing = get_thresholds("SWING_MOMENTUM")
    rsi = get_thresholds("RSI_MEAN_REVERSION")
    assert swing.adverse_move_pct > rsi.adverse_move_pct
    assert swing.favorable_move_pct > rsi.favorable_move_pct
    assert swing.grace_period_minutes > rsi.grace_period_minutes
    assert swing.cooldown_minutes > rsi.cooldown_minutes


# ── Test 6: Favorable move triggers alert ──────────────────────────────────


def test_favorable_move_triggers_alert(agent, sample_position, db):
    """RELIANCE up 1.5% with RSI threshold 1.4% → favorable_move alert."""
    _insert_trade(db, "T001", "RSI_MEAN_REVERSION")

    # Price at 2537.5 → +1.5%
    tick = {"ltp": 2537.5, "volume_ratio": 1.0}
    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:RELIANCE": tick,
        "data:last_candle:RELIANCE": None,
        "data:market_snapshot": {"nifty": {}, "indiavix": {}},
    }.get(key)

    now = _market_hours_now()
    result = agent._check_position(sample_position, now)
    assert result is not None
    assert result["trigger_type"] == "favorable_move"


# ── Test 7: Options monitors premium not price ─────────────────────────────


def test_options_monitors_premium_not_price(agent, db):
    """Options strategy checks premium decay, not adverse_move_pct."""
    entry_time = (_market_hours_now() - timedelta(minutes=15)).isoformat()
    position = {
        "trade_id": "T003", "symbol": "NIFTY24500CE", "direction": "LONG",
        "entry_price": 200.0, "entry_premium": 200.0,
        "quantity": 50, "stop_loss": 0, "target": 0,
        "bucket": "risk", "status": "OPEN", "entry_time": entry_time,
    }
    _insert_trade(db, "T003", "NIFTY_OPTIONS_BUYING")

    # Premium dropped from 200 to 110 → -45% (threshold 40%)
    tick = {"ltp": 110.0, "volume_ratio": 1.0}
    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:NIFTY24500CE": tick,
        "data:market_snapshot": {"nifty": {}, "indiavix": {}},
    }.get(key)

    now = _market_hours_now()
    result = agent._check_position(position, now)
    assert result is not None
    assert result["trigger_type"] == "premium_decay"


# ── Test 8: Options no alert on underlying move alone ──────────────────────


def test_options_no_alert_on_underlying_move_alone(agent, db):
    """Options premium stable at -10%, no alert despite big underlying move."""
    entry_time = (_market_hours_now() - timedelta(minutes=15)).isoformat()
    position = {
        "trade_id": "T004", "symbol": "NIFTY24500CE", "direction": "LONG",
        "entry_price": 200.0, "entry_premium": 200.0,
        "quantity": 50, "stop_loss": 0, "target": 0,
        "bucket": "risk", "status": "OPEN", "entry_time": entry_time,
    }
    _insert_trade(db, "T004", "NIFTY_OPTIONS_BUYING")

    # Premium only down 10% (well below 40% threshold)
    tick = {"ltp": 180.0, "volume_ratio": 1.0}
    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:NIFTY24500CE": tick,
        "data:market_snapshot": {"nifty": {"ltp": 24000, "change_pct": -2.0}, "indiavix": {}},
    }.get(key)

    now = _market_hours_now()
    result = agent._check_position(position, now)
    assert result is None


# ── Test 9: Time warning fires at threshold ────────────────────────────────


def test_time_warning_fires_at_threshold(agent, sample_position, db):
    """Intraday position at 14:40 → 40 min to close, within 45 min threshold."""
    _insert_trade(db, "T001", "RSI_MEAN_REVERSION")

    # Price is flat (no adverse/favorable triggers)
    tick = {"ltp": 2500.0, "volume_ratio": 1.0}
    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:RELIANCE": tick,
        "data:last_candle:RELIANCE": None,
        "data:market_snapshot": {"nifty": {}, "indiavix": {}},
    }.get(key)

    # 14:40 IST → 40 min to 15:20 forced close
    now = datetime.now(IST).replace(hour=14, minute=40, second=0, microsecond=0)
    result = agent._check_position(sample_position, now)
    assert result is not None
    assert result["trigger_type"] == "time_warning"


# ── Test 10: Time warning does NOT fire for swing ──────────────────────────


def test_time_warning_does_not_fire_for_swing(agent, db):
    """SWING_MOMENTUM has time_warning_minutes=0, so no time warning."""
    # Use 14:40 as "now" for this test, entry 4 hours before that
    ref_time = datetime.now(IST).replace(hour=14, minute=40, second=0, microsecond=0)
    entry_time = (ref_time - timedelta(hours=4)).isoformat()
    position = {
        "trade_id": "T005", "symbol": "TATASTEEL", "direction": "LONG",
        "entry_price": 150.0, "quantity": 50, "stop_loss": 145.0,
        "target": 165.0, "bucket": "conservative", "status": "OPEN",
        "entry_time": entry_time,
    }
    _insert_trade(db, "T005", "SWING_MOMENTUM")

    tick = {"ltp": 150.0, "volume_ratio": 1.0}
    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:TATASTEEL": tick,
        "data:last_candle:TATASTEEL": None,
        "data:market_snapshot": {},
    }.get(key)

    # Close to forced close time
    now = datetime.now(IST).replace(hour=14, minute=40, second=0, microsecond=0)
    result = agent._check_position(position, now)
    assert result is None


# ── Test 11: Velocity check uses direction ─────────────────────────────────


def test_velocity_check_uses_direction(agent, sample_position, db):
    """For LONG, a big bearish candle is adverse; a big bullish candle isn't."""
    _insert_trade(db, "T001", "RSI_MEAN_REVERSION")

    tick = {"ltp": 2500.0, "volume_ratio": 1.0}  # flat price (no other triggers)
    bearish_candle = {"open": 2500.0, "close": 2488.0}  # -0.48% (threshold 0.4%)

    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:RELIANCE": tick,
        "data:last_candle:RELIANCE": bearish_candle,
        "data:market_snapshot": {"nifty": {}, "indiavix": {}},
    }.get(key)

    now = _market_hours_now()
    result = agent._check_position(sample_position, now)
    assert result is not None
    assert result["trigger_type"] == "adverse_velocity"

    # Now flip direction to SHORT — same candle should NOT be adverse
    sample_position["direction"] = "SHORT"
    result2 = agent._check_position(sample_position, now)
    # For SHORT, a bearish candle is favorable (positive move), so adverse_velocity won't fire
    # (It might fire favorable_velocity instead if above threshold)
    if result2:
        assert result2["trigger_type"] != "adverse_velocity"


# ── Test 12: Single trigger per cycle ──────────────────────────────────────


def test_single_trigger_per_cycle(agent, sample_position, db):
    """Multiple thresholds breached → only first trigger returned."""
    _insert_trade(db, "T001", "RSI_MEAN_REVERSION")

    # Price dropped a lot (adverse_move), near stop (stop_proximity),
    # and close to forced close (time_warning). Only adverse_move fires first.
    tick = {"ltp": 2475.0, "volume_ratio": 1.0}  # -1.0%
    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:RELIANCE": tick,
        "data:last_candle:RELIANCE": None,
        "data:market_snapshot": {"nifty": {}, "indiavix": {}},
    }.get(key)

    now = datetime.now(IST).replace(hour=14, minute=40, second=0)
    result = agent._check_position(sample_position, now)
    # Should get exactly one trigger (adverse_move is checked first)
    assert result is not None
    assert result["trigger_type"] == "adverse_move"


# ── Test 13: Alert payload has required fields ─────────────────────────────


def test_alert_payload_has_required_fields(agent, sample_position, db):
    """Alert dict has all fields required by orchestrator's review flow."""
    _insert_trade(db, "T001", "RSI_MEAN_REVERSION")

    tick = {"ltp": 2475.0, "volume_ratio": 1.5}
    agent.redis.get_market_data.side_effect = lambda key: {
        "data:watchlist_ticks:RELIANCE": tick,
        "data:last_candle:RELIANCE": None,
        "data:market_snapshot": {"nifty": {"ltp": 22000, "change_pct": -0.5}, "indiavix": {"ltp": 15}},
    }.get(key)

    now = _market_hours_now()
    result = agent._check_position(sample_position, now)
    assert result is not None

    # Top-level fields
    assert "alert_type" in result
    assert "trigger_type" in result
    assert "trigger_value" in result
    assert "trigger_description" in result
    assert "strategy_type" in result
    assert "cooldown_minutes" in result
    assert "timestamp" in result

    # Position sub-dict
    pos = result["position"]
    for field in ["trade_id", "symbol", "direction", "strategy_name", "bucket",
                  "entry_price", "current_price", "entry_time", "minutes_in_trade",
                  "stop_loss_price", "target_price", "quantity"]:
        assert field in pos, f"Missing position field: {field}"

    # Market context
    ctx = result["market_context"]
    assert "nifty_price" in ctx
    assert "nifty_change" in ctx
    assert "vix" in ctx


# ── Test 14: get_thresholds raises for unknown strategy ────────────────────


def test_get_thresholds_raises_for_unknown_strategy():
    with pytest.raises(KeyError, match="No monitor thresholds"):
        get_thresholds("UNKNOWN_STRATEGY_XYZ")


# ── Test 15: Monitor inactive outside market hours ─────────────────────────


def test_monitor_inactive_outside_market_hours(agent, mock_redis):
    """monitor_positions returns 0 outside 9:15–15:20."""
    with patch(
        "agents.position_monitor.position_monitor.datetime"
    ) as mock_dt:
        # 7:00 AM — before market
        early = datetime.now(IST).replace(hour=7, minute=0, second=0)
        mock_dt.now.return_value = early
        mock_dt.fromisoformat = datetime.fromisoformat

        # _is_monitoring_active checks now.time()
        assert agent._is_monitoring_active(early) is False

        # 16:00 — after market
        late = datetime.now(IST).replace(hour=16, minute=0, second=0)
        assert agent._is_monitoring_active(late) is False

        # 11:00 — during market
        mid = datetime.now(IST).replace(hour=11, minute=0, second=0)
        assert agent._is_monitoring_active(mid) is True


# ── Test 16: No alerts when no open positions ──────────────────────────────


def test_no_alerts_when_no_open_positions(agent, mock_redis):
    """Empty positions list → 0 alerts, no errors."""
    mock_redis.get_state.side_effect = lambda key: {
        "state:system_mode": {"mode": "PAPER"},
        "state:positions": {"positions": []},
    }.get(key, {})

    with patch(
        "agents.position_monitor.position_monitor.datetime"
    ) as mock_dt:
        market_time = datetime.now(IST).replace(hour=11, minute=0, second=0)
        mock_dt.now.return_value = market_time
        mock_dt.fromisoformat = datetime.fromisoformat
        result = agent.monitor_positions()

    assert result == 0
