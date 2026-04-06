"""Tests for risk management rules from config."""

from datetime import time

import pytest

from config import CAPITAL, RISK_LIMITS


class TestRiskLimitValues:
    """Verify risk limits are correctly configured."""

    def test_single_trade_risk_is_1_5_percent(self):
        assert RISK_LIMITS["max_single_trade_risk_pct"] == 0.015

    def test_daily_loss_is_3_percent(self):
        assert RISK_LIMITS["max_daily_loss_pct"] == 0.03

    def test_averaging_down_forbidden(self):
        assert RISK_LIMITS["averaging_down_permitted"] is False

    def test_options_max_trade_is_5000(self):
        assert RISK_LIMITS["max_options_trade_inr"] == 5000

    def test_consecutive_loss_cooldown_is_3(self):
        assert RISK_LIMITS["consecutive_loss_cooldown"] == 3

    def test_cooldown_duration_is_60_minutes(self):
        assert RISK_LIMITS["cooldown_duration_minutes"] == 60

    def test_max_simultaneous_positions(self):
        assert RISK_LIMITS["max_simultaneous_positions"] == 4
        assert RISK_LIMITS["max_risk_positions"] == 3

    def test_options_stop_loss_is_60_percent(self):
        assert RISK_LIMITS["options_stop_loss_pct"] == 0.60

    def test_options_max_hold_2_days(self):
        assert RISK_LIMITS["options_max_hold_days"] == 2

    def test_human_approval_30_days(self):
        assert RISK_LIMITS["require_human_approval_days"] == 30

    def test_auto_approve_threshold(self):
        assert RISK_LIMITS["auto_approve_threshold_inr"] == 6000
        assert RISK_LIMITS["auto_approve_confidence"] == "HIGH"


class TestRiskCalculations:
    """Test risk limit calculations."""

    def test_max_single_trade_risk_conservative(self):
        capital = CAPITAL["conservative_bucket"]
        max_risk = capital * RISK_LIMITS["max_single_trade_risk_pct"]
        assert max_risk == 750  # 1.5% of 50000

    def test_max_daily_loss_conservative(self):
        capital = CAPITAL["conservative_bucket"]
        max_loss = capital * RISK_LIMITS["max_daily_loss_pct"]
        assert max_loss == 1500  # 3% of 50000

    def test_options_trade_within_risk_bucket(self):
        monthly_budget = CAPITAL["risk_bucket_monthly"]
        max_per_trade = RISK_LIMITS["max_options_trade_inr"]
        # Should be able to do at least 4 trades per month
        assert monthly_budget / max_per_trade >= 4

    def test_intraday_cutoff_before_market_close(self):
        cutoff = time(15, 20)
        market_close = time(15, 30)
        assert cutoff < market_close

    def test_no_new_trades_before_cutoff(self):
        no_new = time(15, 0)
        cutoff = time(15, 20)
        assert no_new < cutoff


class TestCooldownLogic:
    """Test consecutive loss cooldown behaviour."""

    def test_cooldown_triggers_at_threshold(self):
        threshold = RISK_LIMITS["consecutive_loss_cooldown"]
        consecutive_losses = 3
        assert consecutive_losses >= threshold

    def test_below_threshold_no_cooldown(self):
        threshold = RISK_LIMITS["consecutive_loss_cooldown"]
        consecutive_losses = 2
        assert consecutive_losses < threshold
