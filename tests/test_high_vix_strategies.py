"""Tests for high-VIX strategies — STRADDLE_BUY and VOLATILITY_ADJUSTED_SWING."""

import math
from datetime import time as dt_time
from unittest.mock import MagicMock, patch

import pytest


# ── Indicator tests ──────────────────────────────────────────────────────────


class TestStraddleBreakeven:
    def test_straddle_breakeven_calculation(self):
        from tools.indicators import straddle_breakeven
        result = straddle_breakeven(nifty_spot=22600, call_premium=70, put_premium=65)
        assert result["combined_premium"] == 135
        assert result["total_cost_inr"] == 135 * 25
        assert result["upper_breakeven"] == 22735
        assert result["lower_breakeven"] == 22465
        assert abs(result["move_required_pct"] - 0.597) < 0.01

    def test_straddle_breakeven_rejects_zero_spot(self):
        from tools.indicators import straddle_breakeven
        with pytest.raises(ValueError):
            straddle_breakeven(nifty_spot=0, call_premium=70, put_premium=65)

    def test_straddle_breakeven_custom_lot_size(self):
        from tools.indicators import straddle_breakeven
        result = straddle_breakeven(nifty_spot=22600, call_premium=70, put_premium=65, lot_size=50)
        assert result["total_cost_inr"] == 135 * 50


class TestVolatilityAdjustedPositionSize:
    def test_volatility_adjusted_position_size(self):
        from tools.indicators import volatility_adjusted_position_size
        result = volatility_adjusted_position_size(
            normal_position_size=5000, normal_stop_pct=2.0, adjusted_stop_pct=3.5
        )
        # 5000 * (2.0/3.5) = 2857.14 → rounded to 2900
        assert result == 2900

    def test_position_size_rounds_to_nearest_100(self):
        from tools.indicators import volatility_adjusted_position_size
        result = volatility_adjusted_position_size(
            normal_position_size=10000, normal_stop_pct=2.0, adjusted_stop_pct=3.5
        )
        assert result % 100 == 0

    def test_position_size_maintains_rupee_risk(self):
        """Rupee risk should be approximately equal: normal_size * normal_stop ≈ adjusted_size * adjusted_stop."""
        from tools.indicators import volatility_adjusted_position_size
        normal_size = 5000
        normal_stop = 2.0
        adjusted_stop = 3.5
        adjusted_size = volatility_adjusted_position_size(normal_size, normal_stop, adjusted_stop)
        normal_risk = normal_size * normal_stop / 100
        adjusted_risk = adjusted_size * adjusted_stop / 100
        # Within 10% tolerance (rounding causes small deviation)
        assert abs(normal_risk - adjusted_risk) / normal_risk < 0.10


# ── Strategy config tests ────────────────────────────────────────────────────


class TestStrategyConfigs:
    def test_straddle_in_risk_strategies(self):
        from config import RISK_STRATEGIES
        assert "STRADDLE_BUY" in RISK_STRATEGIES

    def test_volatility_adjusted_swing_in_conservative_strategies(self):
        from config import CONSERVATIVE_STRATEGIES
        assert "VOLATILITY_ADJUSTED_SWING" in CONSERVATIVE_STRATEGIES

    def test_straddle_config_has_required_fields(self):
        from backtesting.runner import STRATEGY_CONFIGS
        cfg = STRATEGY_CONFIGS["STRADDLE_BUY"]
        required = [
            "strategy_type", "bucket", "vix_min", "vix_max", "direction",
            "watchlist", "entry_indicator", "entry_time_window",
            "target_combined_multiplier", "stop_loss_combined_pct",
            "max_cost_per_trade", "is_intraday",
        ]
        for field in required:
            assert field in cfg, f"Missing field: {field}"

    def test_volatility_adjusted_swing_config_has_required_fields(self):
        from backtesting.runner import STRATEGY_CONFIGS
        cfg = STRATEGY_CONFIGS["VOLATILITY_ADJUSTED_SWING"]
        required = [
            "strategy_type", "bucket", "vix_min", "vix_max", "direction",
            "watchlist", "entry_indicator", "entry_threshold",
            "target_pct", "stop_loss_pct", "position_size_modifier",
            "trailing_stop", "is_intraday",
        ]
        for field in required:
            assert field in cfg, f"Missing field: {field}"

    def test_straddle_vix_range_correct(self):
        from backtesting.runner import STRATEGY_CONFIGS
        cfg = STRATEGY_CONFIGS["STRADDLE_BUY"]
        assert cfg["vix_min"] == 22.0
        assert cfg["vix_max"] == 32.0

    def test_volatility_adjusted_swing_stop_wider_than_normal_swing(self):
        from backtesting.runner import STRATEGY_CONFIGS
        vas = STRATEGY_CONFIGS["VOLATILITY_ADJUSTED_SWING"]
        swing = STRATEGY_CONFIGS["SWING_MOMENTUM"]
        assert vas["stop_loss_pct"] > swing["stop_loss_pct"]


# ── Backtest function tests ──────────────────────────────────────────────────


class TestStraddleBacktest:
    def test_straddle_entry_valid_at_correct_time(self):
        from backtesting.strategies.straddle_backtest import straddle_entry_valid
        bar = {"timestamp": "2024-06-10 09:25:00", "close": 22600}
        assert straddle_entry_valid(bar, vix=25, prev_close=22600) is True

    def test_straddle_entry_invalid_after_1030(self):
        from backtesting.strategies.straddle_backtest import straddle_entry_valid
        bar = {"timestamp": "2024-06-10 10:45:00", "close": 22600}
        assert straddle_entry_valid(bar, vix=25, prev_close=22600) is False

    def test_straddle_entry_invalid_low_vix(self):
        from backtesting.strategies.straddle_backtest import straddle_entry_valid
        bar = {"timestamp": "2024-06-10 09:25:00", "close": 22600}
        assert straddle_entry_valid(bar, vix=18, prev_close=22600) is False

    def test_straddle_entry_invalid_high_vix(self):
        from backtesting.strategies.straddle_backtest import straddle_entry_valid
        bar = {"timestamp": "2024-06-10 09:25:00", "close": 22600}
        assert straddle_entry_valid(bar, vix=35, prev_close=22600) is False

    def test_straddle_entry_invalid_nifty_already_moved(self):
        from backtesting.strategies.straddle_backtest import straddle_entry_valid
        # Nifty 1.2% above prev close
        bar = {"timestamp": "2024-06-10 09:25:00", "close": 22871}
        assert straddle_entry_valid(bar, vix=25, prev_close=22600) is False

    def test_straddle_pnl_profit_on_surge(self):
        from backtesting.strategies.straddle_backtest import straddle_pnl
        result = straddle_pnl(entry_combined_premium=100, exit_combined_premium=220)
        assert result == 120 * 25  # ₹3,000

    def test_straddle_pnl_loss_on_flat(self):
        from backtesting.strategies.straddle_backtest import straddle_pnl
        result = straddle_pnl(entry_combined_premium=100, exit_combined_premium=60)
        assert result == -40 * 25  # -₹1,000

    def test_compute_atm_premium(self):
        from backtesting.strategies.straddle_backtest import compute_atm_premium
        # 0.4 * 22600 * (25/100) * sqrt(3/365)
        result = compute_atm_premium(nifty_price=22600, vix=25, dte=3)
        expected = 0.4 * 22600 * 0.25 * math.sqrt(3 / 365)
        assert abs(result - expected) < 0.01


# ── Monitor threshold tests ──────────────────────────────────────────────────


class TestMonitorThresholds:
    def test_straddle_thresholds_exist_in_monitor(self):
        from agents.position_monitor.thresholds import get_thresholds
        t = get_thresholds("STRADDLE_BUY")
        assert t is not None

    def test_volatility_adjusted_swing_thresholds_exist(self):
        from agents.position_monitor.thresholds import get_thresholds
        t = get_thresholds("VOLATILITY_ADJUSTED_SWING")
        assert t is not None

    def test_straddle_monitor_uses_premium_not_price(self):
        from agents.position_monitor.thresholds import get_thresholds
        t = get_thresholds("STRADDLE_BUY")
        assert t.strategy_type == "options"
        assert t.adverse_move_pct == 0.0  # price-based thresholds disabled

    def test_straddle_adverse_velocity_disabled(self):
        from agents.position_monitor.thresholds import get_thresholds
        t = get_thresholds("STRADDLE_BUY")
        assert t.adverse_velocity_pct == 0.0

    def test_vas_thresholds_wider_than_swing_momentum(self):
        from agents.position_monitor.thresholds import get_thresholds
        vas = get_thresholds("VOLATILITY_ADJUSTED_SWING")
        swing = get_thresholds("SWING_MOMENTUM")
        assert vas.adverse_move_pct > swing.adverse_move_pct
        assert vas.cooldown_minutes >= swing.cooldown_minutes


# ── Execution tests ──────────────────────────────────────────────────────────


class TestStraddleExecution:
    def test_paper_straddle_returns_two_order_ids(self):
        from agents.execution_agent.execution_agent import ExecutionAgent
        with patch("agents.base_agent.threading"):
            agent = ExecutionAgent(MagicMock(), MagicMock())
        order = {"call_premium": 70, "put_premium": 65, "lots": 1}
        result = agent._simulate_straddle_fill(order)
        assert "call_order_id" in result
        assert "put_order_id" in result
        assert result["status"] == "FILLED"

    def test_paper_straddle_applies_slippage(self):
        from agents.execution_agent.execution_agent import ExecutionAgent
        with patch("agents.base_agent.threading"):
            agent = ExecutionAgent(MagicMock(), MagicMock())
        order = {"call_premium": 70, "put_premium": 65, "lots": 1}
        result = agent._simulate_straddle_fill(order)
        assert result["call_fill_price"] > 70  # slippage applied
        assert result["put_fill_price"] > 65


# ── VIX framework logic tests ───────────────────────────────────────────────


class TestVIXFramework:
    def test_strategist_fallback_vas_at_high_vix(self):
        """VIX 26 should produce VOLATILITY_ADJUSTED_SWING, not NO_TRADE."""
        from agents.strategist.strategist import StrategistAgent
        with patch("agents.base_agent.threading"):
            agent = StrategistAgent(MagicMock(), MagicMock())
        result = agent._fallback_strategy(26)
        assert result["strategy"] == "VOLATILITY_ADJUSTED_SWING"

    def test_strategist_fallback_no_trade_above_32(self):
        """VIX 35 must produce NO_TRADE."""
        from agents.strategist.strategist import StrategistAgent
        with patch("agents.base_agent.threading"):
            agent = StrategistAgent(MagicMock(), MagicMock())
        result = agent._fallback_strategy(35)
        assert result["strategy"] == "NO_TRADE"

    def test_strategist_fallback_normal_below_18(self):
        """VIX 14 should still produce RSI_MEAN_REVERSION."""
        from agents.strategist.strategist import StrategistAgent
        with patch("agents.base_agent.threading"):
            agent = StrategistAgent(MagicMock(), MagicMock())
        result = agent._fallback_strategy(14)
        assert result["strategy"] == "RSI_MEAN_REVERSION"
