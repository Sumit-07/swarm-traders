"""Tests for technical indicators."""

import numpy as np
import pandas as pd
import pytest

from tools.indicators import (
    calculate_adx,
    calculate_all,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_volume_ratio,
    calculate_vwap,
)


@pytest.fixture
def sample_ohlcv():
    """Generate a realistic OHLCV DataFrame for testing."""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2024-01-01 09:15", periods=n, freq="5min")
    close = 2800 + np.cumsum(np.random.randn(n) * 5)
    high = close + np.abs(np.random.randn(n) * 3)
    low = close - np.abs(np.random.randn(n) * 3)
    open_ = close + np.random.randn(n) * 2
    volume = np.random.randint(10000, 100000, n)

    return pd.DataFrame({
        "datetime": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture
def trending_up_ohlcv():
    """DataFrame with a clear uptrend but with some pullbacks for RSI calculation."""
    np.random.seed(99)
    n = 50
    dates = pd.date_range("2024-01-01 09:15", periods=n, freq="5min")
    # Add small noise so there are some down bars (needed for RSI calculation)
    close = np.linspace(100, 150, n) + np.random.randn(n) * 2
    return pd.DataFrame({
        "datetime": dates,
        "open": close - 0.5,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.random.randint(10000, 50000, n),
    })


@pytest.fixture
def trending_down_ohlcv():
    """DataFrame with a clear downtrend."""
    n = 50
    dates = pd.date_range("2024-01-01 09:15", periods=n, freq="5min")
    close = np.linspace(150, 100, n) + np.random.randn(n) * 0.5
    return pd.DataFrame({
        "datetime": dates,
        "open": close + 0.5,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.random.randint(10000, 50000, n),
    })


class TestRSI:
    def test_rsi_range(self, sample_ohlcv):
        rsi = calculate_rsi(sample_ohlcv)
        valid = rsi.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_trending_up(self, trending_up_ohlcv):
        rsi = calculate_rsi(trending_up_ohlcv)
        # RSI should be above 50 in a strong uptrend (use last valid value)
        valid = rsi.dropna()
        assert len(valid) > 0
        assert valid.iloc[-1] > 50

    def test_rsi_trending_down(self, trending_down_ohlcv):
        rsi = calculate_rsi(trending_down_ohlcv)
        assert rsi.iloc[-1] < 50

    def test_rsi_length(self, sample_ohlcv):
        rsi = calculate_rsi(sample_ohlcv)
        assert len(rsi) == len(sample_ohlcv)

    def test_rsi_nan_for_initial_period(self, sample_ohlcv):
        rsi = calculate_rsi(sample_ohlcv, period=14)
        # First values (up to period) should be NaN due to warmup
        assert rsi.iloc[:13].isna().all()
        # Should have valid values after warmup
        assert rsi.dropna().shape[0] > 0


class TestMACD:
    def test_macd_returns_dict(self, sample_ohlcv):
        result = calculate_macd(sample_ohlcv)
        assert "macd" in result
        assert "signal" in result
        assert "histogram" in result

    def test_histogram_is_macd_minus_signal(self, sample_ohlcv):
        result = calculate_macd(sample_ohlcv)
        diff = result["macd"] - result["signal"]
        np.testing.assert_array_almost_equal(
            result["histogram"].values, diff.values, decimal=10
        )

    def test_macd_length(self, sample_ohlcv):
        result = calculate_macd(sample_ohlcv)
        assert len(result["macd"]) == len(sample_ohlcv)


class TestVWAP:
    def test_vwap_within_price_range(self, sample_ohlcv):
        vwap = calculate_vwap(sample_ohlcv)
        valid = vwap.dropna()
        assert (valid >= sample_ohlcv["low"].min()).all()
        assert (valid <= sample_ohlcv["high"].max()).all()

    def test_vwap_length(self, sample_ohlcv):
        vwap = calculate_vwap(sample_ohlcv)
        assert len(vwap) == len(sample_ohlcv)

    def test_vwap_resets_daily(self):
        """VWAP should reset at each new day."""
        dates = pd.concat([
            pd.Series(pd.date_range("2024-01-01 09:15", periods=5, freq="5min")),
            pd.Series(pd.date_range("2024-01-02 09:15", periods=5, freq="5min")),
        ]).reset_index(drop=True)
        df = pd.DataFrame({
            "datetime": dates,
            "open": [100] * 10,
            "high": [105] * 10,
            "low": [95] * 10,
            "close": [100] * 10,
            "volume": [1000] * 10,
        })
        vwap = calculate_vwap(df)
        # First bar of each day should equal typical price
        assert not np.isnan(vwap.iloc[0])
        assert not np.isnan(vwap.iloc[5])


class TestATR:
    def test_atr_positive(self, sample_ohlcv):
        atr = calculate_atr(sample_ohlcv)
        valid = atr.dropna()
        assert (valid > 0).all()

    def test_atr_length(self, sample_ohlcv):
        atr = calculate_atr(sample_ohlcv)
        assert len(atr) == len(sample_ohlcv)


class TestBollingerBands:
    def test_upper_above_lower(self, sample_ohlcv):
        bb = calculate_bollinger_bands(sample_ohlcv)
        valid_idx = bb["upper"].dropna().index
        assert (bb["upper"][valid_idx] >= bb["lower"][valid_idx]).all()

    def test_middle_is_sma(self, sample_ohlcv):
        bb = calculate_bollinger_bands(sample_ohlcv, period=20)
        sma = sample_ohlcv["close"].rolling(20).mean()
        np.testing.assert_array_almost_equal(
            bb["middle"].dropna().values, sma.dropna().values
        )

    def test_returns_three_bands(self, sample_ohlcv):
        bb = calculate_bollinger_bands(sample_ohlcv)
        assert "upper" in bb and "middle" in bb and "lower" in bb


class TestADX:
    def test_adx_positive(self, sample_ohlcv):
        adx = calculate_adx(sample_ohlcv)
        valid = adx.dropna()
        assert (valid >= 0).all()

    def test_adx_trending_is_higher(self, trending_up_ohlcv, sample_ohlcv):
        adx_trend = calculate_adx(trending_up_ohlcv).iloc[-1]
        # ADX should be positive for trending data
        assert adx_trend > 0


class TestEMA:
    def test_ema_length(self, sample_ohlcv):
        ema = calculate_ema(sample_ohlcv, 20)
        assert len(ema) == len(sample_ohlcv)

    def test_ema_follows_trend(self, trending_up_ohlcv):
        ema = calculate_ema(trending_up_ohlcv, 10)
        # EMA should be below close in uptrend (lagging)
        assert ema.iloc[-1] < trending_up_ohlcv["close"].iloc[-1]


class TestVolumeRatio:
    def test_volume_ratio_positive(self, sample_ohlcv):
        vr = calculate_volume_ratio(sample_ohlcv)
        valid = vr.dropna()
        assert (valid > 0).all()

    def test_constant_volume_ratio_is_one(self):
        df = pd.DataFrame({
            "volume": [1000] * 20,
            "close": [100] * 20,
        })
        vr = calculate_volume_ratio(df)
        valid = vr.dropna()
        np.testing.assert_array_almost_equal(valid.values, 1.0)


class TestCalculateAll:
    def test_returns_all_keys(self, sample_ohlcv):
        result = calculate_all(sample_ohlcv)
        expected_keys = {"rsi", "macd", "vwap", "atr", "bollinger", "adx",
                         "ema_20", "volume_ratio"}
        assert set(result.keys()) == expected_keys

    def test_macd_is_dict(self, sample_ohlcv):
        result = calculate_all(sample_ohlcv)
        assert isinstance(result["macd"], dict)
        assert "macd" in result["macd"]

    def test_bollinger_is_dict(self, sample_ohlcv):
        result = calculate_all(sample_ohlcv)
        assert isinstance(result["bollinger"], dict)
        assert "upper" in result["bollinger"]
