"""Tests for HIGH severity bug fixes.

Covers:
1. needs_human_approval() auto-approve logic after 30 days
2. Position monitor scheduler wiring (agent + tool paper exits)
3. Risk agent PnL refresh from SQLite
4. Simulator/Redis position reconciliation on restart
5. state:active_strategy persisted to Redis in graph path
6. Force close and position monitor use PositionMonitor tool
"""

import time as _time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")


# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeRedis:
    def __init__(self, state=None, market_data=None):
        self._state = state or {}
        self._market_data = market_data or {}

    def get_state(self, key):
        return self._state.get(key)

    def set_state(self, key, value):
        self._state[key] = value

    def get_market_data(self, key):
        return self._market_data.get(key)

    def subscribe(self, channel, callback):
        pass

    def publish(self, channel, data):
        pass


class FakeSQLite:
    def __init__(self, daily_pnl=None):
        self._daily_pnl = daily_pnl

    def log_signal(self, data):
        pass

    def query(self, *args, **kwargs):
        return []

    def get_daily_pnl(self, date=None):
        return self._daily_pnl


# ── 1. needs_human_approval() ───────────────────────────────────────────────


class TestNeedsHumanApproval:

    def test_always_needs_human_within_30_days(self):
        """Within first 30 days, always require human approval."""
        from graph.edges import needs_human_approval

        today = datetime.now(IST).strftime("%Y-%m-%d")
        with patch("config.SYSTEM_START_DATE", today):
            state = {"approved_orders": [
                {"bucket": "conservative", "price": 100, "quantity": 5,
                 "confidence": "HIGH"},
            ]}
            assert needs_human_approval(state) == "needs_human"

    def test_auto_approve_after_30_days_small_trade(self):
        """After 30 days, small HIGH-confidence conservative trades auto-approve."""
        from graph.edges import needs_human_approval

        old_date = (datetime.now(IST) - timedelta(days=35)).strftime("%Y-%m-%d")
        with patch("config.SYSTEM_START_DATE", old_date):
            state = {"approved_orders": [
                {"bucket": "conservative", "price": 500, "quantity": 5,
                 "confidence": "HIGH"},
            ]}
            assert needs_human_approval(state) == "auto_approved"

    def test_needs_human_for_large_trade_after_30_days(self):
        """After 30 days, trades >= ₹6000 still need human approval."""
        from graph.edges import needs_human_approval

        old_date = (datetime.now(IST) - timedelta(days=35)).strftime("%Y-%m-%d")
        with patch("config.SYSTEM_START_DATE", old_date):
            state = {"approved_orders": [
                {"bucket": "conservative", "price": 2000, "quantity": 5,
                 "confidence": "HIGH"},  # 10000 > 6000
            ]}
            assert needs_human_approval(state) == "needs_human"

    def test_needs_human_for_low_confidence_after_30_days(self):
        """After 30 days, non-HIGH confidence still needs human."""
        from graph.edges import needs_human_approval

        old_date = (datetime.now(IST) - timedelta(days=35)).strftime("%Y-%m-%d")
        with patch("config.SYSTEM_START_DATE", old_date):
            state = {"approved_orders": [
                {"bucket": "conservative", "price": 100, "quantity": 5,
                 "confidence": "MEDIUM"},
            ]}
            assert needs_human_approval(state) == "needs_human"

    def test_risk_bucket_always_needs_human(self):
        """Risk bucket trades always need approval regardless of age."""
        from graph.edges import needs_human_approval

        old_date = (datetime.now(IST) - timedelta(days=60)).strftime("%Y-%m-%d")
        with patch("config.SYSTEM_START_DATE", old_date):
            state = {"approved_orders": [
                {"bucket": "risk", "price": 100, "quantity": 5,
                 "confidence": "HIGH"},
            ]}
            assert needs_human_approval(state) == "needs_human"

    def test_empty_orders_auto_approved(self):
        """No orders = nothing to approve."""
        from graph.edges import needs_human_approval

        old_date = (datetime.now(IST) - timedelta(days=35)).strftime("%Y-%m-%d")
        with patch("config.SYSTEM_START_DATE", old_date):
            state = {"approved_orders": []}
            assert needs_human_approval(state) == "auto_approved"


# ── 2. Position monitor scheduler wiring ─────────────────────────────────────


class TestPositionMonitorScheduler:

    def test_runs_agent_and_tool(self):
        """_run_position_monitor should call both agent monitor and tool paper exits."""
        from scheduler.job_scheduler import SwarmScheduler

        mock_agent = MagicMock()
        mock_agent.monitor_positions.return_value = 1

        mock_execution = MagicMock()
        mock_execution.simulator = MagicMock()
        mock_execution.simulator.open_positions = [
            {"symbol": "INFY", "order_id": "o1"},
        ]

        mock_orchestrator = MagicMock()
        mock_orchestrator.redis = FakeRedis()
        mock_orchestrator.sqlite = FakeSQLite()
        mock_orchestrator.broker = None

        agents = {
            "position_monitor": mock_agent,
            "orchestrator": mock_orchestrator,
            "execution_agent": mock_execution,
        }
        scheduler = SwarmScheduler(agents, graphs={})

        with patch("tools.position_monitor.PositionMonitor.check_paper_exits", return_value=[]):
            with patch("tools.market_data.get_live_quote", return_value={}):
                scheduler._run_position_monitor()

        mock_agent.monitor_positions.assert_called_once()

    def test_skips_tool_when_no_open_positions(self):
        """Should skip paper exit check when simulator has no positions."""
        from scheduler.job_scheduler import SwarmScheduler

        mock_agent = MagicMock()
        mock_agent.monitor_positions.return_value = 0

        mock_execution = MagicMock()
        mock_execution.simulator = MagicMock()
        mock_execution.simulator.open_positions = []

        agents = {
            "position_monitor": mock_agent,
            "orchestrator": MagicMock(redis=FakeRedis(), sqlite=FakeSQLite(), broker=None),
            "execution_agent": mock_execution,
        }
        scheduler = SwarmScheduler(agents, graphs={})

        with patch("tools.position_monitor.PositionMonitor.check_paper_exits") as mock_check:
            with patch("tools.market_data.get_live_quote"):
                scheduler._run_position_monitor()

        mock_check.assert_not_called()


# ── 3. Risk agent PnL refresh ───────────────────────────────────────────────


class TestRiskAgentPnlRefresh:

    def _make_risk_agent(self, daily_pnl=None):
        from agents.risk_agent.risk_agent import RiskAgent
        agent = RiskAgent.__new__(RiskAgent)
        agent.redis = FakeRedis(state={
            "state:positions": {"positions": []},
        })
        agent.sqlite = FakeSQLite(daily_pnl=daily_pnl)
        agent.logger = MagicMock()
        agent._consecutive_losses = 0
        agent._in_cooldown = False
        agent._cooldown_until = None
        agent._todays_pnl = 0.0
        agent._review_cache = {}
        agent._processed_proposals = set()
        agent._llm_provider = None
        agent._extra_context = ""
        return agent

    def test_run_refreshes_pnl_from_sqlite(self):
        """run() should load today's PnL from SQLite before checking signals."""
        agent = self._make_risk_agent(daily_pnl={"total_pnl": -800})

        state = {"pending_signals": []}
        agent.run(state)

        assert agent._todays_pnl == -800

    def test_pnl_affects_daily_budget_check(self):
        """With high losses, daily budget check should reject new trades."""
        agent = self._make_risk_agent(daily_pnl={"total_pnl": -1400})

        proposal = {
            "proposal_id": "test-1",
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "stop_loss": 2462.5,
            "target": 2550,
            "quantity_suggested": 8,
            "direction": "LONG",
            "bucket": "conservative",
        }
        state = {"pending_signals": [proposal]}
        result = agent.run(state)

        # Risk per trade = 37.5 * 8 = 300, but only 100 budget left (1500 - 1400)
        assert len(result["rejected_proposals"]) == 1
        assert "daily_loss_budget" in str(result["rejected_proposals"][0].get("reason", ""))

    def test_zero_pnl_when_no_sqlite_data(self):
        """PnL stays 0 when SQLite has no data for today."""
        agent = self._make_risk_agent(daily_pnl=None)
        agent.run({"pending_signals": []})
        assert agent._todays_pnl == 0.0


# ── 4. Simulator/Redis position reconciliation ──────────────────────────────


class TestSimulatorReconciliation:

    def test_reload_positions_on_start(self):
        """Execution agent should reload open positions from Redis into simulator."""
        from agents.execution_agent.execution_agent import ExecutionAgent

        redis = FakeRedis(state={
            "state:positions": {"positions": [
                {"trade_id": "t1", "symbol": "INFY", "direction": "LONG",
                 "entry_price": 1500, "quantity": 10, "stop_loss": 1477,
                 "target": 1530, "bucket": "conservative", "entry_time": "",
                 "status": "OPEN"},
                {"trade_id": "t2", "symbol": "TCS", "direction": "SHORT",
                 "entry_price": 3500, "quantity": 5, "stop_loss": 3535,
                 "target": 3430, "bucket": "conservative", "entry_time": "",
                 "status": "OPEN"},
                {"trade_id": "t3", "symbol": "SBIN", "status": "CLOSED"},
            ]},
        })

        agent = ExecutionAgent.__new__(ExecutionAgent)
        agent.redis = redis
        agent.sqlite = FakeSQLite()
        agent.logger = MagicMock()
        agent.broker = None
        agent._processed_orders = set()

        from tools.order_simulator import OrderSimulator
        agent.simulator = OrderSimulator()

        agent._reload_paper_positions()

        # Should have loaded 2 OPEN positions (not the CLOSED one)
        assert len(agent.simulator.open_positions) == 2
        symbols = {p["symbol"] for p in agent.simulator.open_positions}
        assert symbols == {"INFY", "TCS"}

    def test_reload_skips_closed_positions(self):
        """Should not load CLOSED positions into simulator."""
        from agents.execution_agent.execution_agent import ExecutionAgent
        from tools.order_simulator import OrderSimulator

        redis = FakeRedis(state={
            "state:positions": {"positions": [
                {"trade_id": "t1", "symbol": "SBIN", "status": "CLOSED"},
            ]},
        })

        agent = ExecutionAgent.__new__(ExecutionAgent)
        agent.redis = redis
        agent.sqlite = FakeSQLite()
        agent.logger = MagicMock()
        agent.broker = None
        agent._processed_orders = set()
        agent.simulator = OrderSimulator()

        agent._reload_paper_positions()
        assert len(agent.simulator.open_positions) == 0

    def test_reload_handles_empty_positions(self):
        """Should handle empty Redis positions gracefully."""
        from agents.execution_agent.execution_agent import ExecutionAgent
        from tools.order_simulator import OrderSimulator

        agent = ExecutionAgent.__new__(ExecutionAgent)
        agent.redis = FakeRedis(state={})
        agent.sqlite = FakeSQLite()
        agent.logger = MagicMock()
        agent.broker = None
        agent._processed_orders = set()
        agent.simulator = OrderSimulator()

        agent._reload_paper_positions()
        assert len(agent.simulator.open_positions) == 0


# ── 5. state:active_strategy persisted in graph path ────────────────────────


class TestActiveStrategyPersistence:

    def _make_orchestrator(self):
        from agents.orchestrator.orchestrator import OrchestratorAgent
        redis = FakeRedis(state={
            "state:system_mode": {"mode": "PAPER"},
            "state:all_agents": {},
        })
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        agent.redis = redis
        agent.sqlite = FakeSQLite()
        agent.logger = MagicMock()
        agent.telegram = None
        agent.broker = None
        agent._human_approval_pending = {}
        return agent

    def test_run_writes_strategy_to_redis(self):
        """Orchestrator run() should persist conservative_strategy to Redis."""
        agent = self._make_orchestrator()

        state = {
            "current_phase": "PRE_MARKET",
            "conservative_strategy": {
                "strategy": "RSI_MEAN_REVERSION",
                "regime": "RANGING",
                "confidence": "HIGH",
                "rationale": "Stable VIX",
            },
            "approved_orders": [],
        }
        agent.run(state)

        stored = agent.redis.get_state("state:active_strategy")
        assert stored is not None
        assert stored["strategy"] == "RSI_MEAN_REVERSION"
        assert stored["regime"] == "RANGING"
        assert stored["confidence"] == "HIGH"

    def test_run_without_strategy_does_not_overwrite(self):
        """If no strategy in state, should not write to Redis."""
        agent = self._make_orchestrator()
        agent.redis.set_state("state:active_strategy", {"strategy": "EXISTING"})

        state = {
            "current_phase": "MARKET_OPEN",
            "approved_orders": [],
        }
        agent.run(state)

        stored = agent.redis.get_state("state:active_strategy")
        assert stored["strategy"] == "EXISTING"

    def test_strategy_available_for_order_creation(self):
        """Strategy written to Redis should be used when creating orders."""
        agent = self._make_orchestrator()

        # First, run with strategy in state
        state = {
            "current_phase": "PRE_MARKET",
            "conservative_strategy": {
                "strategy": "VWAP_REVERSION",
                "regime": "RANGING",
                "confidence": "MEDIUM",
            },
            "approved_orders": [],
        }
        agent.run(state)

        # Now run with approved orders — should pick up strategy from Redis
        state2 = {
            "current_phase": "MARKET_OPEN",
            "conservative_strategy": {
                "strategy": "VWAP_REVERSION",
                "regime": "RANGING",
                "confidence": "MEDIUM",
            },
            "approved_orders": [{
                "proposal_id": "p1",
                "symbol": "INFY",
                "entry_price": 1500,
                "transaction_type": "BUY",
                "approved_position_size": 5,
                "approved_stop_loss": 1477,
                "approved_target": 1530,
                "bucket": "conservative",
                "decision": "APPROVED",
            }],
        }
        result = agent.run(state2)

        order = result["approved_orders"][0]
        assert order["strategy"] == "VWAP_REVERSION"


# ── 6. Duplicate trade prevention ───────────────────────────────────────────


class TestDuplicateTradeDedup:

    def _make_execution_agent(self):
        from agents.execution_agent.execution_agent import ExecutionAgent
        from tools.order_simulator import OrderSimulator

        agent = ExecutionAgent.__new__(ExecutionAgent)
        agent.redis = FakeRedis(state={"state:positions": {"positions": []}})
        agent.sqlite = FakeSQLite()
        agent.logger = MagicMock()
        agent.broker = None
        agent._processed_orders = set()
        agent.simulator = OrderSimulator()
        agent._report_fill = MagicMock()
        agent._place_stop_loss = MagicMock()
        return agent

    @patch("agents.execution_agent.execution_agent.datetime")
    def test_same_proposal_different_order_ids_blocked(self, mock_dt):
        """Two orders with same proposal_id but different order_ids = duplicate."""
        mock_dt.now.return_value = datetime(2026, 4, 7, 10, 0, tzinfo=IST)

        agent = self._make_execution_agent()

        order1 = {
            "order_id": "order-aaa",
            "proposal_id": "prop-123",
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 10,
            "price": 2500,
            "mode": "PAPER",
            "strategy": "RSI_MEAN_REVERSION",
        }
        order2 = {
            "order_id": "order-bbb",  # different order_id
            "proposal_id": "prop-123",  # same proposal_id
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 10,
            "price": 2500,
            "mode": "PAPER",
            "strategy": "RSI_MEAN_REVERSION",
        }

        agent._execute_order(order1)
        agent._execute_order(order2)

        # Simulator should only have been called once
        assert agent.simulator.open_positions is not None
        assert agent.logger.warning.call_count >= 1
        dup_warnings = [
            c for c in agent.logger.warning.call_args_list
            if "Duplicate proposal" in str(c)
        ]
        assert len(dup_warnings) == 1

    @patch("agents.execution_agent.execution_agent.datetime")
    def test_different_proposals_both_execute(self, mock_dt):
        """Two orders with different proposal_ids should both execute."""
        mock_dt.now.return_value = datetime(2026, 4, 7, 10, 0, tzinfo=IST)

        agent = self._make_execution_agent()

        order1 = {
            "order_id": "order-aaa",
            "proposal_id": "prop-111",
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 10,
            "price": 2500,
            "mode": "PAPER",
            "strategy": "RSI_MEAN_REVERSION",
        }
        order2 = {
            "order_id": "order-bbb",
            "proposal_id": "prop-222",
            "symbol": "INFY",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 1500,
            "mode": "PAPER",
            "strategy": "RSI_MEAN_REVERSION",
        }

        agent._execute_order(order1)
        agent._execute_order(order2)

        # No duplicate warnings
        dup_warnings = [
            c for c in agent.logger.warning.call_args_list
            if "Duplicate" in str(c)
        ]
        assert len(dup_warnings) == 0
