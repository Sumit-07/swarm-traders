"""Tests for the backtesting framework — simulator, metrics, and runner."""

import numpy as np
import pandas as pd
import pytest

from backtesting.metrics import (
    calculate_metrics,
    check_gate_criteria,
    _max_consecutive_losses,
    _sharpe_ratio,
)
from backtesting.simulator import BacktestSimulator, SimulatorConfig, Trade
from config import BACKTEST_GATE_CRITERIA


# --- Fixtures ---

@pytest.fixture
def simulator():
    return BacktestSimulator()


@pytest.fixture
def sample_trade():
    """A simple LONG trade."""
    return Trade(
        trade_id=1, symbol="RELIANCE", direction="LONG",
        strategy="RSI_MEAN_REVERSION",
        entry_bar_idx=10, entry_fill_idx=11,
        entry_price=2800.0, fill_price=2801.40,  # with slippage
        stop_loss=2758.0, target=2856.0,
        quantity=5, fees=23.50, status="OPEN",
        entry_time="2024-06-01 10:00:00",
    )


def _make_trades(pnls: list[float]) -> list[Trade]:
    """Create a list of closed trades with given P&L values."""
    trades = []
    for i, pnl in enumerate(pnls):
        t = Trade(
            trade_id=i + 1, symbol="TEST", direction="LONG",
            strategy="test", entry_bar_idx=i * 10,
            entry_fill_idx=i * 10 + 1,
            entry_price=100.0, fill_price=100.05,
            exit_price=100 + pnl / 5,
            exit_fill_price=100 + pnl / 5 - 0.05,
            exit_bar_idx=i * 10 + 5,
            stop_loss=98.0, target=104.0,
            quantity=5, pnl=pnl, pnl_pct=pnl / 500 * 100,
            fees=40.0, status="CLOSED_TARGET" if pnl > 0 else "CLOSED_STOP",
            entry_time=f"2024-06-{i + 1:02d} 10:00:00",
            exit_time=f"2024-06-{i + 1:02d} 14:00:00",
            hold_bars=5,
        )
        trades.append(t)
    return trades


# --- Simulator Tests ---

class TestSimulator:
    def test_can_signal_during_market_hours(self, simulator):
        assert simulator.can_signal(pd.Timestamp("2024-06-01 10:00:00"))
        assert simulator.can_signal(pd.Timestamp("2024-06-01 15:15:00"))

    def test_cannot_signal_before_market(self, simulator):
        assert not simulator.can_signal(pd.Timestamp("2024-06-01 09:10:00"))

    def test_cannot_signal_after_cutoff(self, simulator):
        assert not simulator.can_signal(pd.Timestamp("2024-06-01 15:25:00"))

    def test_entry_slippage_long(self, simulator):
        trade = simulator.simulate_entry(
            signal_bar_idx=10, next_bar_open=100.0,
            direction="LONG", symbol="TEST", strategy="test",
            stop_loss=98.0, target=104.0, quantity=5,
        )
        assert trade.fill_price > 100.0  # slippage increases buy price
        assert trade.entry_price == 100.0
        assert trade.status == "OPEN"

    def test_entry_slippage_short(self, simulator):
        trade = simulator.simulate_entry(
            signal_bar_idx=10, next_bar_open=100.0,
            direction="SHORT", symbol="TEST", strategy="test",
            stop_loss=102.0, target=96.0, quantity=5,
        )
        assert trade.fill_price < 100.0  # slippage decreases sell price

    def test_entry_includes_fees(self, simulator):
        trade = simulator.simulate_entry(
            signal_bar_idx=10, next_bar_open=1000.0,
            direction="LONG", symbol="TEST", strategy="test",
            stop_loss=980.0, target=1040.0, quantity=1,
        )
        assert trade.fees > 0  # brokerage + STT

    def test_stop_loss_triggers_long(self, simulator):
        trade = simulator.simulate_entry(
            signal_bar_idx=10, next_bar_open=100.0,
            direction="LONG", symbol="TEST", strategy="test",
            stop_loss=98.0, target=104.0, quantity=5,
        )
        closed = simulator.check_exit(
            trade, bar_high=100.5, bar_low=97.5, bar_close=97.8,
            bar_idx=15, bar_time="2024-06-01 11:00:00",
        )
        assert closed is not None
        assert closed.status == "CLOSED_STOP"
        assert closed.pnl < 0

    def test_target_triggers_long(self, simulator):
        trade = simulator.simulate_entry(
            signal_bar_idx=10, next_bar_open=1000.0,
            direction="LONG", symbol="TEST", strategy="test",
            stop_loss=980.0, target=1040.0, quantity=10,
        )
        closed = simulator.check_exit(
            trade, bar_high=1050.0, bar_low=995.0, bar_close=1045.0,
            bar_idx=15, bar_time="2024-06-01 11:00:00",
        )
        assert closed is not None
        assert closed.status == "CLOSED_TARGET"
        assert closed.pnl > 0

    def test_no_exit_when_price_in_range(self, simulator):
        trade = simulator.simulate_entry(
            signal_bar_idx=10, next_bar_open=100.0,
            direction="LONG", symbol="TEST", strategy="test",
            stop_loss=98.0, target=104.0, quantity=5,
        )
        result = simulator.check_exit(
            trade, bar_high=101.0, bar_low=99.0, bar_close=100.5,
            bar_idx=15, bar_time="2024-06-01 11:00:00",
        )
        assert result is None

    def test_time_based_exit(self, simulator):
        trade = simulator.simulate_entry(
            signal_bar_idx=10, next_bar_open=100.0,
            direction="LONG", symbol="TEST", strategy="test",
            stop_loss=98.0, target=104.0, quantity=5,
        )
        closed = simulator.check_exit(
            trade, bar_high=101.0, bar_low=99.5, bar_close=100.5,
            bar_idx=50, bar_time="2024-06-01 15:20:00",
            is_intraday=True,
        )
        assert closed is not None
        assert closed.status == "CLOSED_TIME"

    def test_force_close(self, simulator):
        trade = simulator.simulate_entry(
            signal_bar_idx=10, next_bar_open=100.0,
            direction="LONG", symbol="TEST", strategy="test",
            stop_loss=98.0, target=104.0, quantity=5,
        )
        closed = simulator.force_close(
            trade, close_price=101.0, bar_idx=100,
            bar_time="2024-06-01 15:30:00",
        )
        assert closed.status == "CLOSED_EOD"
        assert closed.exit_price == 101.0

    def test_short_stop_loss(self, simulator):
        trade = simulator.simulate_entry(
            signal_bar_idx=10, next_bar_open=100.0,
            direction="SHORT", symbol="TEST", strategy="test",
            stop_loss=102.0, target=96.0, quantity=5,
        )
        closed = simulator.check_exit(
            trade, bar_high=103.0, bar_low=99.5, bar_close=102.5,
            bar_idx=15, bar_time="2024-06-01 11:00:00",
        )
        assert closed is not None
        assert closed.status == "CLOSED_STOP"


# --- Metrics Tests ---

class TestMetrics:
    def test_empty_trades(self):
        m = calculate_metrics([], 25000)
        assert m["total_trades"] == 0
        assert m["win_rate"] == 0

    def test_all_winners(self):
        trades = _make_trades([100, 200, 150, 80, 120])
        m = calculate_metrics(trades, 25000)
        assert m["wins"] == 5
        assert m["losses"] == 0
        assert m["win_rate"] == 1.0
        assert m["total_return"] == 650

    def test_all_losers(self):
        trades = _make_trades([-100, -200, -150])
        m = calculate_metrics(trades, 25000)
        assert m["wins"] == 0
        assert m["losses"] == 3
        assert m["win_rate"] == 0.0
        assert m["total_return"] == -450

    def test_mixed_trades(self):
        trades = _make_trades([200, -100, 150, -50, 300, -80])
        m = calculate_metrics(trades, 25000)
        assert m["wins"] == 3
        assert m["losses"] == 3
        assert m["win_rate"] == 0.5
        assert m["profit_factor"] > 1.0  # more profit than loss

    def test_max_drawdown(self):
        trades = _make_trades([100, -200, -150, 50, 300])
        m = calculate_metrics(trades, 25000)
        assert m["max_drawdown"] < 0

    def test_profit_factor(self):
        trades = _make_trades([200, -100])
        m = calculate_metrics(trades, 25000)
        assert m["profit_factor"] == 2.0

    def test_fees_tracked(self):
        trades = _make_trades([100, 200])
        m = calculate_metrics(trades, 25000)
        assert m["total_fees"] > 0


class TestConsecutiveLosses:
    def test_no_losses(self):
        assert _max_consecutive_losses([100, 200, 300]) == 0

    def test_single_loss(self):
        assert _max_consecutive_losses([100, -50, 200]) == 1

    def test_three_consecutive(self):
        assert _max_consecutive_losses([100, -50, -30, -20, 200]) == 3

    def test_multiple_streaks(self):
        assert _max_consecutive_losses([-10, -20, 100, -30, -40, -50]) == 3

    def test_empty(self):
        assert _max_consecutive_losses([]) == 0


class TestGateCriteria:
    def test_passing_metrics(self):
        trades = _make_trades(
            [100, -50, 200, -30, 150, -80, 120, -40, 180, -60,
             100, -50, 200, -30, 150, -80, 120, -40, 180, -60,
             100, -50, 200, -30, 150, -80, 120, -40, 180, -60]
        )
        m = calculate_metrics(trades, 25000)
        checks = check_gate_criteria(m, BACKTEST_GATE_CRITERIA)
        # Check that total_trades passes (we have 30)
        assert checks["total_trades"]["passed"] is True

    def test_insufficient_trades_fails(self):
        trades = _make_trades([100, -50, 200])
        m = calculate_metrics(trades, 25000)
        checks = check_gate_criteria(m, BACKTEST_GATE_CRITERIA)
        assert checks["total_trades"]["passed"] is False
