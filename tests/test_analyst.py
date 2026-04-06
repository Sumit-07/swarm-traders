"""Tests for Analyst signal generation — bidirectional support.

RSI_MEAN_REVERSION, VWAP_REVERSION, and ORB support BOTH directions.
SWING_MOMENTUM remains LONG only.
"""

import pytest

from agents.analyst.analyst import AnalystAgent


def _make_analyst_with_strategy(strategy_name, direction="BOTH", **overrides):
    """Create an AnalystAgent with a fake strategy config (no Redis/SQLite needed)."""

    class FakeRedis:
        def get_market_data(self, key):
            return None
        def get_state(self, key):
            return None

    class FakeSQLite:
        pass

    agent = AnalystAgent.__new__(AnalystAgent)
    agent._strategy_config = {
        "strategy_name": strategy_name,
        "entry_conditions": {"direction": direction, **overrides},
        "exit_conditions": {},
    }
    return agent


# ── RSI Mean Reversion ───────────────────────────────────────────────────────


class TestRSIMeanReversion:

    def test_rsi_28_generates_long(self):
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION", direction="BOTH",
                                            volume_confirmation=False)
        signal = agent._check_entry_conditions(
            "RELIANCE",
            {"rsi": 28, "close": 2500, "volume_ratio": 1.5},
            "RSI_MEAN_REVERSION",
        )
        assert signal is not None
        assert signal["direction"] == "LONG"
        assert signal["signal_type"] == "RSI_OVERSOLD"

    def test_rsi_68_generates_short(self):
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION", direction="BOTH",
                                            volume_confirmation=False)
        signal = agent._check_entry_conditions(
            "RELIANCE",
            {"rsi": 72, "close": 2500, "volume_ratio": 1.5},
            "RSI_MEAN_REVERSION",
        )
        assert signal is not None
        assert signal["direction"] == "SHORT"
        assert signal["signal_type"] == "RSI_OVERBOUGHT"

    def test_rsi_50_generates_nothing(self):
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION", direction="BOTH",
                                            volume_confirmation=False)
        signal = agent._check_entry_conditions(
            "RELIANCE",
            {"rsi": 50, "close": 2500, "volume_ratio": 1.5},
            "RSI_MEAN_REVERSION",
        )
        assert signal is None

    def test_rsi_long_only_ignores_overbought(self):
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION", direction="LONG",
                                            volume_confirmation=False)
        signal = agent._check_entry_conditions(
            "RELIANCE",
            {"rsi": 75, "close": 2500, "volume_ratio": 1.5},
            "RSI_MEAN_REVERSION",
        )
        assert signal is None

    def test_rsi_short_blocked_by_low_volume(self):
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION", direction="BOTH",
                                            volume_confirmation=True)
        signal = agent._check_entry_conditions(
            "RELIANCE",
            {"rsi": 75, "close": 2500, "volume_ratio": 0.8},
            "RSI_MEAN_REVERSION",
        )
        assert signal is None


# ── VWAP Reversion ───────────────────────────────────────────────────────────


class TestVWAPReversion:

    def test_price_below_vwap_generates_long(self):
        agent = _make_analyst_with_strategy("VWAP_REVERSION", direction="BOTH",
                                            entry_threshold=-1.2)
        # close is 2% below VWAP → should trigger LONG
        signal = agent._check_entry_conditions(
            "INFY",
            {"rsi": 40, "close": 1470, "vwap": 1500, "volume_ratio": 1.0},
            "VWAP_REVERSION",
        )
        assert signal is not None
        assert signal["direction"] == "LONG"
        assert signal["signal_type"] == "VWAP_BELOW"

    def test_price_above_vwap_generates_short(self):
        agent = _make_analyst_with_strategy("VWAP_REVERSION", direction="BOTH",
                                            entry_threshold=-1.2)
        # close is 2% above VWAP → should trigger SHORT
        signal = agent._check_entry_conditions(
            "INFY",
            {"rsi": 60, "close": 1530, "vwap": 1500, "volume_ratio": 1.0},
            "VWAP_REVERSION",
        )
        assert signal is not None
        assert signal["direction"] == "SHORT"
        assert signal["signal_type"] == "VWAP_ABOVE"

    def test_price_near_vwap_generates_nothing(self):
        agent = _make_analyst_with_strategy("VWAP_REVERSION", direction="BOTH",
                                            entry_threshold=-1.2)
        # close is 0.5% above VWAP → not enough deviation
        signal = agent._check_entry_conditions(
            "INFY",
            {"rsi": 50, "close": 1507, "vwap": 1500, "volume_ratio": 1.0},
            "VWAP_REVERSION",
        )
        assert signal is None

    def test_vwap_long_only_ignores_above(self):
        agent = _make_analyst_with_strategy("VWAP_REVERSION", direction="LONG",
                                            entry_threshold=-1.2)
        signal = agent._check_entry_conditions(
            "INFY",
            {"rsi": 60, "close": 1530, "vwap": 1500, "volume_ratio": 1.0},
            "VWAP_REVERSION",
        )
        assert signal is None

    def test_vwap_zero_returns_none(self):
        agent = _make_analyst_with_strategy("VWAP_REVERSION", direction="BOTH")
        signal = agent._check_entry_conditions(
            "INFY",
            {"rsi": 40, "close": 1500, "vwap": 0, "volume_ratio": 1.0},
            "VWAP_REVERSION",
        )
        assert signal is None


# ── Opening Range Breakout ───────────────────────────────────────────────────


class TestORB:

    def test_breakout_up_generates_long(self):
        agent = _make_analyst_with_strategy("OPENING_RANGE_BREAKOUT", direction="BOTH",
                                            volume_confirmation=False)
        signal = agent._check_entry_conditions(
            "TCS",
            {"rsi": 55, "close": 3520, "orb_high": 3500, "orb_low": 3450,
             "volume_ratio": 2.0},
            "OPENING_RANGE_BREAKOUT",
        )
        assert signal is not None
        assert signal["direction"] == "LONG"
        assert signal["signal_type"] == "ORB_BREAKOUT_UP"

    def test_breakout_down_generates_short(self):
        agent = _make_analyst_with_strategy("OPENING_RANGE_BREAKOUT", direction="BOTH",
                                            volume_confirmation=False)
        signal = agent._check_entry_conditions(
            "TCS",
            {"rsi": 45, "close": 3440, "orb_high": 3500, "orb_low": 3450,
             "volume_ratio": 2.0},
            "OPENING_RANGE_BREAKOUT",
        )
        assert signal is not None
        assert signal["direction"] == "SHORT"
        assert signal["signal_type"] == "ORB_BREAKOUT_DOWN"

    def test_price_inside_range_generates_nothing(self):
        agent = _make_analyst_with_strategy("OPENING_RANGE_BREAKOUT", direction="BOTH",
                                            volume_confirmation=False)
        signal = agent._check_entry_conditions(
            "TCS",
            {"rsi": 50, "close": 3475, "orb_high": 3500, "orb_low": 3450,
             "volume_ratio": 2.0},
            "OPENING_RANGE_BREAKOUT",
        )
        assert signal is None

    def test_orb_short_blocked_by_low_volume(self):
        agent = _make_analyst_with_strategy("OPENING_RANGE_BREAKOUT", direction="BOTH",
                                            volume_confirmation=True,
                                            volume_threshold=1.5)
        signal = agent._check_entry_conditions(
            "TCS",
            {"rsi": 45, "close": 3440, "orb_high": 3500, "orb_low": 3450,
             "volume_ratio": 1.0},
            "OPENING_RANGE_BREAKOUT",
        )
        assert signal is None


# ── SWING_MOMENTUM stays LONG only ──────────────────────────────────────────


class TestSwingMomentumLongOnly:

    def test_swing_generates_long(self):
        agent = _make_analyst_with_strategy("SWING_MOMENTUM", direction="LONG",
                                            entry_threshold=25,
                                            volume_confirmation=False)
        signal = agent._check_entry_conditions(
            "SBIN",
            {"rsi": 60, "close": 800, "adx": 30, "volume_ratio": 1.5},
            "SWING_MOMENTUM",
        )
        assert signal is not None
        assert signal["direction"] == "LONG"

    def test_swing_never_generates_short(self):
        """Even with direction=BOTH, swing only produces LONG signals."""
        agent = _make_analyst_with_strategy("SWING_MOMENTUM", direction="BOTH",
                                            entry_threshold=25,
                                            volume_confirmation=False)
        signal = agent._check_entry_conditions(
            "SBIN",
            {"rsi": 60, "close": 800, "adx": 30, "volume_ratio": 1.5},
            "SWING_MOMENTUM",
        )
        # Should produce LONG (not SHORT)
        assert signal is not None
        assert signal["direction"] == "LONG"


# ── Stop/target calculation for SHORT ────────────────────────────────────────


class TestShortStopTarget:

    def test_short_stop_above_entry(self):
        """For SHORT, stop loss should be above entry price."""
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION", direction="BOTH",
                                            volume_confirmation=False)
        agent._strategy_config["exit_conditions"] = {
            "stop_loss_pct": 1.5,
            "target_pct": 2.0,
        }
        agent._pending_signals = {}

        # Mock send_message to capture proposal
        captured = {}

        def mock_send(to_agent, msg_type, payload, priority, requires_response=False):
            captured.update(payload)

        agent.send_message = mock_send
        agent.logger = __import__("logging").getLogger("test")

        # Mock sqlite.log_signal
        agent.sqlite = type("FakeSQLite", (), {"log_signal": lambda self, x: None})()

        signal = {
            "symbol": "RELIANCE",
            "direction": "SHORT",
            "signal_type": "RSI_OVERBOUGHT",
            "entry_price": 2500,
            "rsi": 75,
            "volume_ratio": 1.5,
        }
        agent._submit_trade_proposal(signal)

        assert captured["stop_loss"] > captured["entry_price"]
        assert captured["target"] < captured["entry_price"]

    def test_long_stop_below_entry(self):
        """For LONG, stop loss should be below entry price."""
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION", direction="BOTH",
                                            volume_confirmation=False)
        agent._strategy_config["exit_conditions"] = {
            "stop_loss_pct": 1.5,
            "target_pct": 2.0,
        }
        agent._pending_signals = {}

        captured = {}

        def mock_send(to_agent, msg_type, payload, priority, requires_response=False):
            captured.update(payload)

        agent.send_message = mock_send
        agent.logger = __import__("logging").getLogger("test")
        agent.sqlite = type("FakeSQLite", (), {"log_signal": lambda self, x: None})()

        signal = {
            "symbol": "RELIANCE",
            "direction": "LONG",
            "signal_type": "RSI_OVERSOLD",
            "entry_price": 2500,
            "rsi": 28,
            "volume_ratio": 1.5,
        }
        agent._submit_trade_proposal(signal)

        assert captured["stop_loss"] < captured["entry_price"]
        assert captured["target"] > captured["entry_price"]
