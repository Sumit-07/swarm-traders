"""Tests for Kite Connect market data functions."""

import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tools.kite_market_data import get_instrument_token, get_vwap


class TestInstrumentCache:
    def test_raises_for_unknown_symbol(self):
        """get_instrument_token should raise KeyError for unknown symbols."""
        with pytest.raises(KeyError, match="not found in instrument cache"):
            get_instrument_token("NONEXISTENT_SYMBOL_XYZ")


class TestGetOhlcvFormat:
    def test_returns_canonical_format(self):
        """get_ohlcv should return DataFrame with canonical columns."""
        from tools.kite_market_data import get_ohlcv, _instrument_cache

        mock_kite = MagicMock()
        mock_kite.historical_data.return_value = [
            {
                "date": "2024-01-15 09:15:00",
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 103.0,
                "volume": 1000,
            },
            {
                "date": "2024-01-15 09:20:00",
                "open": 103.0,
                "high": 106.0,
                "low": 102.0,
                "close": 105.0,
                "volume": 1500,
            },
        ]

        # Inject test symbol into cache
        _instrument_cache["TESTSTOCK"] = 12345

        df = get_ohlcv(mock_kite, "TESTSTOCK", "5minute", days=1)

        assert isinstance(df, pd.DataFrame)
        expected_cols = ["timestamp", "open", "high", "low", "close", "volume", "symbol"]
        assert list(df.columns) == expected_cols
        assert len(df) == 2
        assert df["symbol"].iloc[0] == "TESTSTOCK"

        # Clean up
        del _instrument_cache["TESTSTOCK"]


class TestGetLiveQuoteFormat:
    def test_returns_canonical_format(self):
        """get_live_quote should return dict with all required keys."""
        from tools.kite_market_data import get_live_quote

        mock_kite = MagicMock()
        mock_kite.quote.return_value = {
            "NSE:RELIANCE": {
                "last_price": 2847.50,
                "ohlc": {
                    "open": 2830.0,
                    "high": 2860.0,
                    "low": 2820.0,
                    "close": 2825.0,
                },
                "volume_traded": 1234567,
            }
        }

        result = get_live_quote(mock_kite, ["RELIANCE"])

        assert "RELIANCE" in result
        q = result["RELIANCE"]
        required_keys = [
            "symbol", "last_price", "open", "high", "low",
            "close", "volume", "change_pct", "timestamp",
        ]
        for key in required_keys:
            assert key in q, f"Missing key: {key}"
        assert q["last_price"] == 2847.50


class TestGetVwap:
    def test_returns_float(self):
        """get_vwap should return a float."""
        from tools.kite_market_data import get_vwap, _instrument_cache

        mock_kite = MagicMock()
        mock_kite.historical_data.return_value = [
            {
                "date": pd.Timestamp.now().strftime("%Y-%m-%d 09:15:00"),
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 103.0,
                "volume": 1000,
            },
        ]

        _instrument_cache["TESTVWAP"] = 99999

        vwap = get_vwap(mock_kite, "TESTVWAP")
        assert isinstance(vwap, float)

        del _instrument_cache["TESTVWAP"]


class TestMarketDataRouter:
    def test_uses_yfinance_backend(self):
        """When DATA_SOURCE=yfinance, should use yfinance backend."""
        with patch.dict(os.environ, {"DATA_SOURCE": "yfinance"}):
            with patch("tools.market_data.DATA_SOURCE", "yfinance"):
                with patch("tools.yfinance_fallback.get_live_quote") as mock_yf:
                    mock_yf.return_value = {"RELIANCE": {"last_price": 100}}

                    from tools.market_data import get_live_quote
                    result = get_live_quote(["RELIANCE"])

                    mock_yf.assert_called_once_with(["RELIANCE"])
