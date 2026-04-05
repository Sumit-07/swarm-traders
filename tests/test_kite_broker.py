"""Tests for Kite Connect order placement."""

import os
from unittest.mock import MagicMock, patch

import pytest

from tools.kite_broker import _map_order_type, place_order, place_stoploss_order


class TestLiveModeGuard:
    def test_place_order_raises_in_paper_mode(self):
        """place_order should raise RuntimeError in PAPER mode."""
        with patch.dict(os.environ, {"TRADING_MODE": "PAPER"}):
            with pytest.raises(RuntimeError, match="PAPER mode"):
                place_order(
                    kite=MagicMock(),
                    symbol="RELIANCE",
                    transaction_type="BUY",
                    quantity=1,
                )


class TestOrderTypeMapping:
    def test_raises_for_unknown_type(self):
        """_map_order_type should raise ValueError for unknown types."""
        mock_kite = MagicMock()
        with pytest.raises(ValueError, match="Unknown order_type"):
            _map_order_type(mock_kite, "INVALID")


class TestStoplossOrder:
    def test_uses_slm_order_type(self):
        """place_stoploss_order should use SL-M order type."""
        mock_kite = MagicMock()
        mock_kite.VARIETY_REGULAR = "regular"
        mock_kite.EXCHANGE_NSE = "NSE"
        mock_kite.TRANSACTION_TYPE_SELL = "SELL"
        mock_kite.PRODUCT_MIS = "MIS"
        mock_kite.ORDER_TYPE_SLM = "SL-M"
        mock_kite.ORDER_TYPE_LIMIT = "LIMIT"
        mock_kite.place_order.return_value = "12345"

        with patch.dict(os.environ, {"TRADING_MODE": "LIVE"}):
            order_id = place_stoploss_order(
                kite=mock_kite,
                symbol="RELIANCE",
                transaction_type="SELL",
                quantity=10,
                trigger_price=2800.0,
            )

        assert order_id == "12345"
        call_kwargs = mock_kite.place_order.call_args[1]
        assert call_kwargs["order_type"] == "SL-M"
