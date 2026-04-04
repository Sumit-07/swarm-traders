"""Tests for the paper trading order simulator."""

import pytest

from tools.order_simulator import OrderSimulator


@pytest.fixture
def simulator():
    return OrderSimulator()


class TestSimulateFill:
    def test_buy_slippage_increases_price(self, simulator):
        order = {
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 2800.00,
            "order_type": "LIMIT",
        }
        result = simulator.simulate_fill(order)
        assert result["fill_price"] > order["price"]
        assert result["status"] == "FILLED"

    def test_sell_slippage_decreases_price(self, simulator):
        order = {
            "symbol": "RELIANCE",
            "transaction_type": "SELL",
            "quantity": 5,
            "price": 2800.00,
            "order_type": "LIMIT",
        }
        result = simulator.simulate_fill(order)
        assert result["fill_price"] < order["price"]

    def test_slippage_amount(self, simulator):
        price = 1000.00
        order = {
            "symbol": "TEST",
            "transaction_type": "BUY",
            "quantity": 1,
            "price": price,
            "order_type": "LIMIT",
        }
        result = simulator.simulate_fill(order)
        expected_fill = price * (1 + simulator.SLIPPAGE_PCT)
        assert result["fill_price"] == round(expected_fill, 2)

    def test_brokerage_included(self, simulator):
        order = {
            "symbol": "TEST",
            "transaction_type": "BUY",
            "quantity": 1,
            "price": 100.00,
            "order_type": "LIMIT",
        }
        result = simulator.simulate_fill(order)
        assert result["brokerage"] == simulator.BROKERAGE_PER_ORDER

    def test_fill_has_order_id(self, simulator):
        order = {
            "symbol": "TEST",
            "transaction_type": "BUY",
            "quantity": 1,
            "price": 100.00,
            "order_type": "LIMIT",
        }
        result = simulator.simulate_fill(order)
        assert "order_id" in result
        assert len(result["order_id"]) > 0

    def test_fill_has_timestamp(self, simulator):
        order = {
            "symbol": "TEST",
            "transaction_type": "BUY",
            "quantity": 1,
            "price": 100.00,
            "order_type": "LIMIT",
        }
        result = simulator.simulate_fill(order)
        assert "filled_at" in result


class TestSimulateStoploss:
    def test_long_stop_triggered_below(self, simulator):
        position = {
            "symbol": "RELIANCE",
            "direction": "LONG",
            "entry_price": 2800.00,
            "stop_loss": 2750.00,
            "quantity": 5,
        }
        result = simulator.simulate_stoploss(position, current_price=2740.00)
        assert result is not None
        assert result["transaction_type"] == "SELL"
        assert result["status"] == "FILLED"

    def test_long_stop_not_triggered_above(self, simulator):
        position = {
            "symbol": "RELIANCE",
            "direction": "LONG",
            "entry_price": 2800.00,
            "stop_loss": 2750.00,
            "quantity": 5,
        }
        result = simulator.simulate_stoploss(position, current_price=2810.00)
        assert result is None

    def test_short_stop_triggered_above(self, simulator):
        position = {
            "symbol": "RELIANCE",
            "direction": "SHORT",
            "entry_price": 2800.00,
            "stop_loss": 2850.00,
            "quantity": 5,
        }
        result = simulator.simulate_stoploss(position, current_price=2860.00)
        assert result is not None
        assert result["transaction_type"] == "BUY"

    def test_short_stop_not_triggered_below(self, simulator):
        position = {
            "symbol": "RELIANCE",
            "direction": "SHORT",
            "entry_price": 2800.00,
            "stop_loss": 2850.00,
            "quantity": 5,
        }
        result = simulator.simulate_stoploss(position, current_price=2790.00)
        assert result is None

    def test_stop_triggered_at_exact_price(self, simulator):
        position = {
            "symbol": "TEST",
            "direction": "LONG",
            "entry_price": 100.00,
            "stop_loss": 95.00,
            "quantity": 10,
        }
        result = simulator.simulate_stoploss(position, current_price=95.00)
        assert result is not None
