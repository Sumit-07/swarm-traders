"""Tests for Analyst signal generation — bidirectional support,
pending signal staleness, and intraday time guards.

RSI_MEAN_REVERSION, VWAP_REVERSION, and ORB support BOTH directions.
SWING_MOMENTUM remains LONG only.
"""

import time as _time
from datetime import datetime
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest

from agents.analyst.analyst import AnalystAgent

IST = ZoneInfo("Asia/Kolkata")


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
    agent._signal_payloads = {}
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


# ── Pending signal staleness ───────────────────────────────────────────────


class TestPendingSignalStaleness:

    def test_stale_signals_cleared_after_2_minutes(self):
        """Pending signals older than 120s should be auto-cleared."""
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION")
        agent.logger = MagicMock()
        agent.redis = type("FakeRedis", (), {
            "get_market_data": lambda self, k: None,
            "get_state": lambda self, k: None,
        })()

        # Add a signal timestamped 3 minutes ago
        agent._pending_signals = {"old-proposal-1": _time.time() - 200}

        agent._scan_watchlist()

        assert "old-proposal-1" not in agent._pending_signals

    def test_fresh_signals_not_cleared(self):
        """Pending signals under 120s should be kept."""
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION")
        agent.logger = MagicMock()
        agent.redis = type("FakeRedis", (), {
            "get_market_data": lambda self, k: None,
            "get_state": lambda self, k: None,
        })()

        agent._pending_signals = {"fresh-proposal": _time.time() - 30}

        agent._scan_watchlist()

        assert "fresh-proposal" in agent._pending_signals

    def test_max_2_pending_blocks_new_proposals(self):
        """With 2 pending signals, new proposals should be skipped."""
        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION",
                                            direction="BOTH",
                                            volume_confirmation=False)
        agent._strategy_config["exit_conditions"] = {
            "stop_loss_pct": 1.5,
            "target_pct": 2.0,
        }
        now = _time.time()
        agent._pending_signals = {
            "pending-1": now,
            "pending-2": now,
        }
        agent.logger = MagicMock()
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

        # Should still be 2, not 3
        assert len(agent._pending_signals) == 2

    def test_response_clears_pending_signal(self):
        """Risk agent RESPONSE should remove proposal from pending."""
        from agents.message import AgentMessage, MessageType, Priority

        agent = _make_analyst_with_strategy("RSI_MEAN_REVERSION")
        agent.logger = MagicMock()
        agent._pending_signals = {"prop-123": _time.time()}

        msg = AgentMessage(
            from_agent="risk_agent",
            to_agent="analyst",
            channel="channel:analyst",
            type=MessageType.RESPONSE,
            priority=Priority.NORMAL,
            payload={"proposal_id": "prop-123", "decision": "APPROVED"},
        )
        agent.on_message(msg)

        assert "prop-123" not in agent._pending_signals


# ── Intraday time guard ────────────────────────────────────────────────────


class TestIntradayTimeGuard:

    def _make_scannable_agent(self, strategy_name, **overrides):
        """Create an analyst agent ready for _scan_watchlist() calls."""
        agent = _make_analyst_with_strategy(strategy_name, **overrides)
        agent._pending_signals = {}
        agent.logger = MagicMock()
        agent.redis = type("FakeRedis", (), {
            "get_market_data": lambda self, k: None,
            "get_state": lambda self, k: None,
        })()
        return agent

    @patch("agents.analyst.analyst.datetime")
    def test_intraday_blocked_after_cutoff(self, mock_dt):
        """Intraday strategy should not scan after 15:00."""
        mock_dt.now.return_value = datetime(2026, 4, 7, 15, 5, tzinfo=IST)

        agent = self._make_scannable_agent("RSI_MEAN_REVERSION")
        agent._scan_watchlist()

        agent.logger.info.assert_any_call("Past intraday cutoff — no new signals")

    @patch("agents.analyst.analyst.datetime")
    def test_swing_allowed_after_cutoff(self, mock_dt):
        """Swing strategy should still scan after 15:00."""
        mock_dt.now.return_value = datetime(2026, 4, 7, 15, 5, tzinfo=IST)

        agent = self._make_scannable_agent("SWING_MOMENTUM", direction="LONG",
                                           entry_threshold=25,
                                           volume_confirmation=False)
        agent._strategy_config["watchlist"] = []
        agent._scan_watchlist()

        cutoff_calls = [
            c for c in agent.logger.info.call_args_list
            if "intraday cutoff" in str(c)
        ]
        assert len(cutoff_calls) == 0

    @patch("agents.analyst.analyst.datetime")
    def test_intraday_allowed_before_cutoff(self, mock_dt):
        """Intraday strategy should scan normally before 15:00."""
        mock_dt.now.return_value = datetime(2026, 4, 7, 14, 30, tzinfo=IST)

        agent = self._make_scannable_agent("RSI_MEAN_REVERSION")
        agent._strategy_config["watchlist"] = []
        agent._scan_watchlist()

        cutoff_calls = [
            c for c in agent.logger.info.call_args_list
            if "intraday cutoff" in str(c)
        ]
        assert len(cutoff_calls) == 0


# ── Execution agent time guard ─────────────────────────────────────────────


class TestExecutionTimeGuard:

    @patch("agents.execution_agent.execution_agent.datetime")
    def test_intraday_blocked_after_cutoff(self, mock_dt):
        """Intraday order should be blocked after 15:20."""
        mock_dt.now.return_value = datetime(2026, 4, 7, 15, 25, tzinfo=IST)

        from agents.execution_agent.execution_agent import ExecutionAgent

        agent = ExecutionAgent.__new__(ExecutionAgent)
        agent.logger = MagicMock()
        agent._processed_orders = set()
        agent.simulator = MagicMock()
        agent.broker = None

        order = {
            "order_id": "test-order-1",
            "symbol": "ICICIBANK",
            "transaction_type": "BUY",
            "quantity": 40,
            "price": 1224.6,
            "mode": "PAPER",
            "strategy": "RSI_MEAN_REVERSION",
        }
        agent._execute_order(order)

        agent.logger.warning.assert_any_call(
            "Blocked ICICIBANK after intraday cutoff — too late to open"
        )
        agent.simulator.simulate_fill.assert_not_called()

    @patch("agents.execution_agent.execution_agent.datetime")
    def test_swing_allowed_after_cutoff(self, mock_dt):
        """Swing order should proceed even after 15:20."""
        mock_dt.now.return_value = datetime(2026, 4, 7, 15, 25, tzinfo=IST)

        from agents.execution_agent.execution_agent import ExecutionAgent

        agent = ExecutionAgent.__new__(ExecutionAgent)
        agent.logger = MagicMock()
        agent._processed_orders = set()
        agent.simulator = MagicMock()
        agent.simulator.simulate_fill.return_value = {
            "order_id": "fill-1", "symbol": "SBIN",
            "fill_price": 800, "quantity": 10,
            "transaction_type": "BUY", "status": "FILLED",
            "filled_at": "2026-04-07T15:25:00+05:30",
            "slippage": 0.4, "brokerage": 20,
        }
        agent.broker = None
        agent._report_fill = MagicMock()
        agent._place_stop_loss = MagicMock()

        order = {
            "order_id": "test-order-2",
            "symbol": "SBIN",
            "transaction_type": "BUY",
            "quantity": 10,
            "price": 800,
            "mode": "PAPER",
            "strategy": "SWING_MOMENTUM",
        }
        agent._execute_order(order)

        agent.simulator.simulate_fill.assert_called_once()
