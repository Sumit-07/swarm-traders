"""Tests for Phase 5: enhanced order simulator, position tracking, P&L, and dashboard helpers."""

from datetime import datetime, time

import pandas as pd
import pytest

from tools.order_simulator import OrderSimulator


# --- Enhanced Order Simulator Tests ---

class TestOpenPosition:
    def test_open_position_registers(self):
        sim = OrderSimulator()
        fill = sim.simulate_fill({
            "symbol": "RELIANCE", "transaction_type": "BUY",
            "quantity": 10, "price": 2800.0, "order_type": "LIMIT",
        })
        pos = sim.open_position(fill, "LONG", stop_loss=2750.0, target=2880.0)
        assert pos["status"] == "OPEN"
        assert pos["symbol"] == "RELIANCE"
        assert pos["direction"] == "LONG"
        assert len(sim.open_positions) == 1

    def test_open_position_stores_entry_fees(self):
        sim = OrderSimulator()
        fill = sim.simulate_fill({
            "symbol": "TCS", "transaction_type": "BUY",
            "quantity": 5, "price": 3500.0, "order_type": "LIMIT",
        })
        pos = sim.open_position(fill, "LONG", stop_loss=3450.0, target=3580.0)
        assert pos["entry_fees"] == fill["brokerage"]


class TestSimulateTarget:
    def test_long_target_hit(self):
        sim = OrderSimulator()
        pos = {"symbol": "INFY", "direction": "LONG", "quantity": 10,
               "target": 1600.0, "stop_loss": 1530.0}
        fill = sim.simulate_target(pos, current_price=1605.0)
        assert fill is not None
        assert fill["status"] == "FILLED"

    def test_long_target_not_hit(self):
        sim = OrderSimulator()
        pos = {"symbol": "INFY", "direction": "LONG", "quantity": 10,
               "target": 1600.0, "stop_loss": 1530.0}
        assert sim.simulate_target(pos, current_price=1590.0) is None

    def test_short_target_hit(self):
        sim = OrderSimulator()
        pos = {"symbol": "INFY", "direction": "SHORT", "quantity": 10,
               "target": 1500.0, "stop_loss": 1570.0}
        fill = sim.simulate_target(pos, current_price=1495.0)
        assert fill is not None

    def test_short_target_not_hit(self):
        sim = OrderSimulator()
        pos = {"symbol": "INFY", "direction": "SHORT", "quantity": 10,
               "target": 1500.0, "stop_loss": 1570.0}
        assert sim.simulate_target(pos, current_price=1510.0) is None


class TestCheckExits:
    def test_stop_takes_priority(self):
        sim = OrderSimulator()
        pos = {"symbol": "X", "direction": "LONG", "quantity": 5,
               "stop_loss": 100.0, "target": 110.0}
        # Price below stop
        fill, reason = sim.check_exits(pos, current_price=98.0)
        assert reason == "CLOSED_STOP"
        assert fill is not None

    def test_target_hit(self):
        sim = OrderSimulator()
        pos = {"symbol": "X", "direction": "LONG", "quantity": 5,
               "stop_loss": 100.0, "target": 110.0}
        fill, reason = sim.check_exits(pos, current_price=112.0)
        assert reason == "CLOSED_TARGET"

    def test_no_exit(self):
        sim = OrderSimulator()
        pos = {"symbol": "X", "direction": "LONG", "quantity": 5,
               "stop_loss": 100.0, "target": 110.0}
        fill, reason = sim.check_exits(pos, current_price=105.0)
        assert reason == "NONE"
        assert fill is None

    def test_time_based_exit(self):
        sim = OrderSimulator()
        pos = {"symbol": "X", "direction": "LONG", "quantity": 5,
               "stop_loss": 100.0, "target": 110.0}
        cutoff = datetime(2024, 6, 1, 15, 25)
        fill, reason = sim.check_exits(pos, current_price=105.0,
                                        current_time=cutoff)
        assert reason == "CLOSED_TIME"
        assert fill is not None

    def test_no_time_exit_before_cutoff(self):
        sim = OrderSimulator()
        pos = {"symbol": "X", "direction": "LONG", "quantity": 5,
               "stop_loss": 100.0, "target": 110.0}
        before = datetime(2024, 6, 1, 14, 30)
        fill, reason = sim.check_exits(pos, current_price=105.0,
                                        current_time=before)
        assert reason == "NONE"


class TestClosePosition:
    def test_close_long_profit(self):
        sim = OrderSimulator()
        fill = sim.simulate_fill({
            "symbol": "RELIANCE", "transaction_type": "BUY",
            "quantity": 10, "price": 1000.0, "order_type": "LIMIT",
        })
        sim.open_position(fill, "LONG", stop_loss=980.0, target=1040.0)

        exit_fill = sim.simulate_fill({
            "symbol": "RELIANCE", "transaction_type": "SELL",
            "quantity": 10, "price": 1040.0, "order_type": "TARGET",
        })
        result = sim.close_position(fill["order_id"], exit_fill, "CLOSED_TARGET")

        assert result["status"] == "CLOSED_TARGET"
        assert result["pnl"] > 0
        assert result["total_fees"] > 0
        assert len(sim.open_positions) == 0

    def test_close_long_loss(self):
        sim = OrderSimulator()
        fill = sim.simulate_fill({
            "symbol": "RELIANCE", "transaction_type": "BUY",
            "quantity": 10, "price": 1000.0, "order_type": "LIMIT",
        })
        sim.open_position(fill, "LONG", stop_loss=980.0, target=1040.0)

        exit_fill = sim.simulate_fill({
            "symbol": "RELIANCE", "transaction_type": "SELL",
            "quantity": 10, "price": 980.0, "order_type": "STOPLOSS",
        })
        result = sim.close_position(fill["order_id"], exit_fill, "CLOSED_STOP")

        assert result["status"] == "CLOSED_STOP"
        assert result["pnl"] < 0

    def test_close_nonexistent_position(self):
        sim = OrderSimulator()
        result = sim.close_position("fake_id", {}, "CLOSED_STOP")
        assert "error" in result

    def test_pnl_pct_calculated(self):
        sim = OrderSimulator()
        fill = sim.simulate_fill({
            "symbol": "TCS", "transaction_type": "BUY",
            "quantity": 5, "price": 3500.0, "order_type": "LIMIT",
        })
        sim.open_position(fill, "LONG", stop_loss=3400.0, target=3600.0)

        exit_fill = sim.simulate_fill({
            "symbol": "TCS", "transaction_type": "SELL",
            "quantity": 5, "price": 3600.0, "order_type": "TARGET",
        })
        result = sim.close_position(fill["order_id"], exit_fill, "CLOSED_TARGET")
        assert "pnl_pct" in result


class TestForceCloseAll:
    def test_force_close_all_positions(self):
        sim = OrderSimulator()

        # Open two positions
        for symbol, price in [("INFY", 1500.0), ("WIPRO", 400.0)]:
            fill = sim.simulate_fill({
                "symbol": symbol, "transaction_type": "BUY",
                "quantity": 10, "price": price, "order_type": "LIMIT",
            })
            sim.open_position(fill, "LONG", stop_loss=price * 0.98,
                              target=price * 1.03)

        assert len(sim.open_positions) == 2

        closed = sim.force_close_all(lambda s: {"INFY": 1510, "WIPRO": 395}[s])
        assert len(closed) == 2
        assert len(sim.open_positions) == 0
        assert all(c["status"] == "CLOSED_EOD" for c in closed)


# --- Dashboard Data Helpers Tests ---

class TestComputeTradeStats:
    def test_empty_dataframe(self):
        from dashboard.data_helpers import compute_trade_stats
        stats = compute_trade_stats(pd.DataFrame())
        assert stats["total"] == 0
        assert stats["win_rate"] == 0

    def test_mixed_trades(self):
        from dashboard.data_helpers import compute_trade_stats
        df = pd.DataFrame([
            {"status": "CLOSED_TARGET", "pnl": 150},
            {"status": "CLOSED_STOP", "pnl": -80},
            {"status": "CLOSED_TARGET", "pnl": 200},
            {"status": "OPEN", "pnl": None},
        ])
        stats = compute_trade_stats(df)
        assert stats["total"] == 4
        assert stats["open"] == 1
        assert stats["closed"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["total_pnl"] == 270
        assert stats["win_rate"] == round(2 / 3, 4)
