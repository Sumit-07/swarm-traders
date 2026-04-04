"""Tests for Phase 6: Fyers broker methods, position monitoring, and mode switching."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from tools.broker import FyersBroker
from tools.position_monitor import PositionMonitor
from tools.order_simulator import OrderSimulator


# --- Broker Order Tests (mocked Fyers API) ---

class TestBrokerPlaceOrder:
    def _make_broker(self):
        broker = FyersBroker("client", "secret", "http://localhost")
        broker.fyers = MagicMock()
        broker._authenticated = True
        return broker

    def test_place_order_success(self):
        broker = self._make_broker()
        broker.fyers.place_order.return_value = {
            "s": "ok", "id": "ORD123", "message": "Order placed"
        }
        result = broker.place_order(
            "NSE:RELIANCE-EQ", 10, "LIMIT", 2800.0, "BUY",
        )
        assert result["status"] == "PLACED"
        assert result["order_id"] == "ORD123"

    def test_place_order_failure(self):
        broker = self._make_broker()
        broker.fyers.place_order.return_value = {
            "s": "error", "message": "Insufficient funds"
        }
        result = broker.place_order(
            "NSE:RELIANCE-EQ", 10, "LIMIT", 2800.0, "BUY",
        )
        assert result["status"] == "FAILED"

    def test_place_order_exception(self):
        broker = self._make_broker()
        broker.fyers.place_order.side_effect = Exception("Network error")
        result = broker.place_order(
            "NSE:RELIANCE-EQ", 10, "LIMIT", 2800.0, "BUY",
        )
        assert result["status"] == "ERROR"

    def test_place_order_unauthenticated(self):
        broker = FyersBroker("client", "secret", "http://localhost")
        with pytest.raises(RuntimeError, match="not authenticated"):
            broker.place_order("NSE:X-EQ", 1, "LIMIT", 100, "BUY")


class TestBrokerStopLoss:
    def _make_broker(self):
        broker = FyersBroker("client", "secret", "http://localhost")
        broker.fyers = MagicMock()
        broker._authenticated = True
        return broker

    def test_stoploss_order_success(self):
        broker = self._make_broker()
        broker.fyers.place_order.return_value = {
            "s": "ok", "id": "SL456", "message": "SL placed"
        }
        result = broker.place_stoploss_order(
            "NSE:RELIANCE-EQ", 10, 2750.0,
        )
        assert result["status"] == "PLACED"
        assert result["order_id"] == "SL456"

    def test_stoploss_uses_slm_type(self):
        broker = self._make_broker()
        broker.fyers.place_order.return_value = {"s": "ok", "id": "SL1"}
        broker.place_stoploss_order("NSE:X-EQ", 5, 100.0)
        call_data = broker.fyers.place_order.call_args[1]["data"]
        assert call_data["type"] == 4  # SL-M order type


class TestBrokerPositions:
    def test_get_positions_success(self):
        broker = FyersBroker("c", "s", "http://localhost")
        broker.fyers = MagicMock()
        broker._authenticated = True
        broker.fyers.positions.return_value = {
            "s": "ok",
            "netPositions": [
                {"symbol": "NSE:RELIANCE-EQ", "netQty": 10,
                 "avgPrice": 2800, "ltp": 2850, "pl": 500,
                 "productType": "INTRADAY"},
                {"symbol": "NSE:TCS-EQ", "netQty": 0,
                 "avgPrice": 3500, "ltp": 3500, "pl": 0,
                 "productType": "INTRADAY"},
            ],
        }
        positions = broker.get_positions()
        assert len(positions) == 1  # Only non-zero qty
        assert positions[0]["symbol"] == "NSE:RELIANCE-EQ"
        assert positions[0]["direction"] == "LONG"

    def test_get_positions_short(self):
        broker = FyersBroker("c", "s", "http://localhost")
        broker.fyers = MagicMock()
        broker._authenticated = True
        broker.fyers.positions.return_value = {
            "s": "ok",
            "netPositions": [
                {"symbol": "NSE:INFY-EQ", "netQty": -5,
                 "avgPrice": 1600, "ltp": 1580, "pl": 100,
                 "productType": "INTRADAY"},
            ],
        }
        positions = broker.get_positions()
        assert positions[0]["direction"] == "SHORT"


class TestBrokerFunds:
    def test_get_funds(self):
        broker = FyersBroker("c", "s", "http://localhost")
        broker.fyers = MagicMock()
        broker._authenticated = True
        broker.fyers.funds.return_value = {
            "s": "ok",
            "fund_limit": [
                {"title": "Total Balance", "equityAmount": 50000},
                {"title": "Available Balance", "equityAmount": 42000},
                {"title": "Used Margin", "equityAmount": 8000},
            ],
        }
        funds = broker.get_funds()
        assert funds["total_balance"] == 50000
        assert funds["available_balance"] == 42000
        assert funds["used_margin"] == 8000


class TestBrokerModifyCancel:
    def _make_broker(self):
        broker = FyersBroker("c", "s", "http://localhost")
        broker.fyers = MagicMock()
        broker._authenticated = True
        return broker

    def test_modify_order(self):
        broker = self._make_broker()
        broker.fyers.modify_order.return_value = {"s": "ok", "message": "Modified"}
        result = broker.modify_order("ORD1", price=2850.0)
        assert result["status"] == "MODIFIED"

    def test_cancel_order(self):
        broker = self._make_broker()
        broker.fyers.cancel_order.return_value = {"s": "ok", "message": "Cancelled"}
        result = broker.cancel_order("ORD1")
        assert result["status"] == "CANCELLED"

    def test_exit_position(self):
        broker = self._make_broker()
        broker.fyers.place_order.return_value = {"s": "ok", "id": "EXIT1"}
        result = broker.exit_position("NSE:RELIANCE-EQ", 10, "LONG")
        assert result["status"] == "PLACED"
        # Should be a SELL market order
        call_data = broker.fyers.place_order.call_args[1]["data"]
        assert call_data["side"] == -1  # SELL
        assert call_data["type"] == 2   # MARKET


# --- Position Monitor Tests ---

class TestPositionMonitor:
    def test_sync_no_broker(self):
        redis = MagicMock()
        sqlite = MagicMock()
        monitor = PositionMonitor(redis, sqlite)
        result = monitor.sync_positions()
        assert result["mode"] == "PAPER"
        assert result["synced"] == 0

    def test_check_paper_exits_stop_hit(self):
        redis = MagicMock()
        redis.get_state.return_value = {"positions": []}
        sqlite = MagicMock()
        sim = OrderSimulator()

        # Open a position
        fill = sim.simulate_fill({
            "symbol": "TEST", "transaction_type": "BUY",
            "quantity": 10, "price": 100.0, "order_type": "LIMIT",
        })
        sim.open_position(fill, "LONG", stop_loss=95.0, target=110.0)

        monitor = PositionMonitor(redis, sqlite, simulator=sim)
        closed = monitor.check_paper_exits(lambda s: 93.0)  # Below stop

        assert len(closed) == 1
        assert closed[0]["status"] == "CLOSED_STOP"
        assert len(sim.open_positions) == 0

    @patch("tools.position_monitor.datetime")
    def test_check_paper_exits_no_trigger(self, mock_dt):
        # Mock time to be during market hours (before cutoff)
        mock_now = datetime(2024, 6, 1, 11, 0, 0)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        redis = MagicMock()
        sqlite = MagicMock()
        sim = OrderSimulator()

        fill = sim.simulate_fill({
            "symbol": "TEST", "transaction_type": "BUY",
            "quantity": 10, "price": 100.0, "order_type": "LIMIT",
        })
        sim.open_position(fill, "LONG", stop_loss=95.0, target=110.0)

        monitor = PositionMonitor(redis, sqlite, simulator=sim)
        closed = monitor.check_paper_exits(lambda s: 102.0)  # In range

        assert len(closed) == 0
        assert len(sim.open_positions) == 1

    def test_portfolio_summary(self):
        redis = MagicMock()
        redis.get_state.return_value = {
            "positions": [
                {"symbol": "A", "entry_price": 100, "quantity": 10,
                 "status": "OPEN", "unrealized_pnl": 50},
                {"symbol": "B", "entry_price": 200, "quantity": 5,
                 "status": "OPEN", "unrealized_pnl": -30},
                {"symbol": "C", "entry_price": 300, "quantity": 3,
                 "status": "CLOSED_STOP"},
            ],
        }
        sqlite = MagicMock()
        monitor = PositionMonitor(redis, sqlite)
        summary = monitor.get_portfolio_summary()

        assert summary["open_count"] == 2
        assert summary["total_deployed"] == 2000  # 100*10 + 200*5
        assert summary["unrealized_pnl"] == 20    # 50 + (-30)


# --- Mode Switching Tests ---

class TestModeSwitching:
    def test_live_switch_requires_confirmation(self):
        """Switching to LIVE without confirmed=True should not switch."""
        from agents.orchestrator.orchestrator import OrchestratorAgent

        redis = MagicMock()
        redis.get_state.return_value = {"mode": "PAPER"}
        sqlite = MagicMock()
        telegram = MagicMock()

        orch = OrchestratorAgent(redis, sqlite, telegram_bot=telegram)
        orch._running = False  # Don't start listener

        orch._switch_to_live({"confirmed": False})

        # Should NOT have set mode to LIVE
        redis.set_state.assert_not_called()
        # Should have sent warning via Telegram
        telegram.send_message.assert_called_once()
        assert "WARNING" in telegram.send_message.call_args[0][0]

    def test_live_switch_with_confirmation(self):
        from agents.orchestrator.orchestrator import OrchestratorAgent

        redis = MagicMock()
        redis.get_state.return_value = {"mode": "PAPER"}
        sqlite = MagicMock()
        telegram = MagicMock()

        orch = OrchestratorAgent(redis, sqlite, telegram_bot=telegram)
        orch._running = False

        orch._switch_to_live({"confirmed": True})

        # Should have set mode to LIVE in Redis
        redis.set_state.assert_called()
        set_calls = [c for c in redis.set_state.call_args_list
                     if c[0][0] == "state:system_mode"]
        assert len(set_calls) >= 1
        mode_data = set_calls[-1][0][1]
        assert mode_data["mode"] == "LIVE"
        assert mode_data["live_cap"] == 8000

    def test_paper_switch(self):
        from agents.orchestrator.orchestrator import OrchestratorAgent

        redis = MagicMock()
        redis.get_state.return_value = {"mode": "LIVE"}
        sqlite = MagicMock()
        telegram = MagicMock()

        orch = OrchestratorAgent(redis, sqlite, telegram_bot=telegram)
        orch._running = False

        orch._switch_to_paper()

        set_calls = [c for c in redis.set_state.call_args_list
                     if c[0][0] == "state:system_mode"]
        assert len(set_calls) >= 1
        assert set_calls[-1][0][1]["mode"] == "PAPER"
