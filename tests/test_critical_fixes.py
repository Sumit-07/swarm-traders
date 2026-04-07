"""Tests for critical bug fixes in the graph-driven code path.

Covers:
1. Analyst run() passes full signal dicts (not just IDs) in state
2. Risk agent run() processes signals through 5 risk checks
3. Orchestrator run() converts approved orders to ApprovedOrder format
4. Force-close at 3:20 PM uses PositionMonitor directly (not broken graph)
"""

import time as _time
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")


# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal Redis mock for agent construction."""
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
    """Minimal SQLite mock."""
    def log_signal(self, data):
        pass

    def query(self, *args, **kwargs):
        return []


def _make_analyst():
    """Create a minimal AnalystAgent for graph-mode testing."""
    from agents.analyst.analyst import AnalystAgent

    agent = AnalystAgent.__new__(AnalystAgent)
    agent.redis = FakeRedis()
    agent.sqlite = FakeSQLite()
    agent.logger = MagicMock()
    agent._pending_signals = {}
    agent._signal_payloads = {}
    agent._strategy_config = None
    agent._extra_context = ""
    return agent


def _make_risk_agent(positions=None):
    """Create a minimal RiskAgent for graph-mode testing."""
    from agents.risk_agent.risk_agent import RiskAgent

    redis_state = {
        "state:positions": {"positions": positions or []},
    }
    agent = RiskAgent.__new__(RiskAgent)
    agent.redis = FakeRedis(state=redis_state)
    agent.sqlite = FakeSQLite()
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


def _make_orchestrator(system_mode="PAPER", active_strategy=None):
    """Create a minimal OrchestratorAgent for graph-mode testing."""
    from agents.orchestrator.orchestrator import OrchestratorAgent

    redis_state = {
        "state:system_mode": {"mode": system_mode},
        "state:active_strategy": active_strategy or {"strategy": "RSI_MEAN_REVERSION"},
        "state:all_agents": {},
    }
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.redis = FakeRedis(state=redis_state)
    agent.sqlite = FakeSQLite()
    agent.logger = MagicMock()
    agent.telegram = None
    agent.broker = None
    agent._human_approval_pending = {}
    return agent


def _sample_proposal(symbol="RELIANCE", entry_price=2500, stop_loss=2462.5,
                     target=2550, quantity=8, bucket="conservative",
                     direction="LONG"):
    """Create a sample trade proposal dict."""
    return {
        "proposal_id": f"test-{symbol}-{_time.time()}",
        "symbol": symbol,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target": target,
        "quantity_suggested": quantity,
        "direction": direction,
        "bucket": bucket,
        "signal_type": "RSI_OVERSOLD",
        "confidence": "HIGH",
    }


# ── 1. Analyst run() passes full signal dicts ───────────────────────────────


class TestAnalystRunPassesFullSignals:

    def test_run_puts_full_dicts_in_pending_signals(self):
        """run() should put full proposal dicts in state, not IDs."""
        agent = _make_analyst()

        # Pre-populate signal payloads (as _submit_trade_proposal would)
        proposal = _sample_proposal("INFY", 1500, 1477.5, 1530, 10)
        agent._signal_payloads[proposal["proposal_id"]] = proposal
        agent._pending_signals[proposal["proposal_id"]] = _time.time()

        state = {
            "conservative_strategy": {
                "strategy": "RSI_MEAN_REVERSION",
                "watchlist": [],  # empty to skip actual scanning
                "entry_conditions": {"direction": "BOTH"},
                "exit_conditions": {},
            },
        }
        result = agent.run(state)

        pending = result["pending_signals"]
        assert len(pending) == 1
        assert isinstance(pending[0], dict)
        assert pending[0]["symbol"] == "INFY"
        assert pending[0]["entry_price"] == 1500
        assert "proposal_id" in pending[0]

    def test_run_with_no_signals_returns_empty_list(self):
        """run() with no pending signals should set empty list."""
        agent = _make_analyst()

        state = {
            "conservative_strategy": {
                "strategy": "RSI_MEAN_REVERSION",
                "watchlist": [],
                "entry_conditions": {"direction": "BOTH"},
                "exit_conditions": {},
            },
        }
        result = agent.run(state)
        assert result["pending_signals"] == []

    def test_signal_payloads_cleared_on_response(self):
        """When risk agent responds, both _pending_signals and _signal_payloads should clear."""
        from agents.message import AgentMessage, MessageType, Priority

        agent = _make_analyst()
        pid = "prop-999"
        agent._pending_signals[pid] = _time.time()
        agent._signal_payloads[pid] = _sample_proposal("TCS")

        msg = AgentMessage(
            from_agent="risk_agent",
            to_agent="analyst",
            channel="channel:analyst",
            type=MessageType.RESPONSE,
            priority=Priority.NORMAL,
            payload={"proposal_id": pid, "decision": "APPROVED"},
        )
        agent.on_message(msg)

        assert pid not in agent._pending_signals
        assert pid not in agent._signal_payloads


# ── 2. Risk agent run() processes signals ────────────────────────────────────


class TestRiskAgentRunProcessesSignals:

    def test_approved_signal_goes_to_approved_orders(self):
        """A valid proposal should land in approved_orders."""
        agent = _make_risk_agent()

        # Small risk: 37.5 per share * 8 shares = 300 < 750 (1.5% of 50k)
        proposal = _sample_proposal("RELIANCE", 2500, 2462.5, 2550, 8)
        state = {"pending_signals": [proposal]}

        result = agent.run(state)

        assert len(result["approved_orders"]) == 1
        assert len(result["rejected_proposals"]) == 0
        order = result["approved_orders"][0]
        assert order["symbol"] == "RELIANCE"
        assert order["decision"] == "APPROVED"
        assert order["approved_position_size"] > 0

    def test_excessive_risk_gets_rejected(self):
        """A proposal exceeding single-trade risk should be rejected."""
        agent = _make_risk_agent()

        # Huge risk: 500 per share * 100 shares = 50000 > 750
        proposal = _sample_proposal("TCS", 3500, 3000, 4000, 100)
        state = {"pending_signals": [proposal]}

        result = agent.run(state)

        assert len(result["approved_orders"]) == 0
        assert len(result["rejected_proposals"]) == 1
        assert result["rejected_proposals"][0]["decision"] == "REJECTED"

    def test_max_positions_check(self):
        """Should reject when max positions already open."""
        open_positions = [
            {"symbol": f"SYM{i}", "status": "OPEN", "bucket": "conservative"}
            for i in range(4)  # max_simultaneous_positions = 4
        ]
        agent = _make_risk_agent(positions=open_positions)

        proposal = _sample_proposal("INFY", 1500, 1477.5, 1530, 5)
        state = {"pending_signals": [proposal]}

        result = agent.run(state)

        assert len(result["approved_orders"]) == 0
        assert len(result["rejected_proposals"]) == 1
        assert "max_positions" in str(result["rejected_proposals"][0].get("reason", ""))

    def test_cooldown_blocks_trades(self):
        """Should reject when in cooldown from consecutive losses."""
        agent = _make_risk_agent()
        agent._in_cooldown = True
        agent._cooldown_until = datetime(2099, 12, 31, tzinfo=IST)

        proposal = _sample_proposal("SBIN", 800, 788, 816, 10)
        state = {"pending_signals": [proposal]}

        result = agent.run(state)

        assert len(result["approved_orders"]) == 0
        assert len(result["rejected_proposals"]) == 1

    def test_multiple_signals_processed(self):
        """run() should process all pending signals, not just the first."""
        agent = _make_risk_agent()

        proposals = [
            _sample_proposal("RELIANCE", 2500, 2462.5, 2550, 8),
            _sample_proposal("INFY", 1500, 1477.5, 1530, 5),
        ]
        state = {"pending_signals": proposals}

        result = agent.run(state)

        total = len(result["approved_orders"]) + len(result["rejected_proposals"])
        assert total == 2

    def test_non_dict_signals_skipped(self):
        """run() should skip non-dict items without crashing."""
        agent = _make_risk_agent()

        state = {"pending_signals": ["not-a-dict", 42, None]}

        result = agent.run(state)

        assert result["approved_orders"] == []
        assert result["rejected_proposals"] == []

    def test_daily_loss_budget_check(self):
        """Should reject when daily loss budget is exhausted."""
        agent = _make_risk_agent()
        # Set today's PnL to -1400 (budget is 1500 = 3% of 50k, only 100 left)
        agent._todays_pnl = -1400

        # Risk = 37.5 * 8 = 300 > 100 remaining budget
        proposal = _sample_proposal("RELIANCE", 2500, 2462.5, 2550, 8)
        state = {"pending_signals": [proposal]}

        result = agent.run(state)

        assert len(result["rejected_proposals"]) == 1
        assert "daily_loss_budget" in str(result["rejected_proposals"][0].get("reason", ""))


# ── 3. Orchestrator run() converts approved orders ──────────────────────────


class TestOrchestratorRunConvertsOrders:

    def test_converts_risk_approval_to_approved_order(self):
        """Orchestrator should convert risk agent output to ApprovedOrder format."""
        agent = _make_orchestrator()

        risk_approval = {
            "proposal_id": "test-prop-1",
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "transaction_type": "BUY",
            "approved_position_size": 8,
            "approved_stop_loss": 2462.5,
            "approved_target": 2550,
            "bucket": "conservative",
            "decision": "APPROVED",
            "risk_pct_final": 0.006,
        }

        state = {
            "current_phase": "MARKET_OPEN",
            "approved_orders": [risk_approval],
        }

        result = agent.run(state)

        orders = result["approved_orders"]
        assert len(orders) == 1
        order = orders[0]
        # Must have ApprovedOrder fields
        assert "order_id" in order
        assert order["symbol"] == "RELIANCE"
        assert order["transaction_type"] == "BUY"
        assert order["quantity"] == 8
        assert order["price"] == 2500
        assert order["stop_loss_price"] == 2462.5
        assert order["target_price"] == 2550
        assert order["mode"] == "PAPER"
        assert order["approved_by"] == "risk_agent"

    def test_risk_bucket_queued_for_human_approval(self):
        """Risk bucket trades should be queued, not forwarded to execution."""
        agent = _make_orchestrator()

        risk_approval = {
            "proposal_id": "risk-prop-1",
            "symbol": "NIFTY24APR25000CE",
            "entry_price": 150,
            "transaction_type": "BUY",
            "approved_position_size": 50,
            "approved_stop_loss": 60,
            "approved_target": 250,
            "bucket": "risk",
            "decision": "APPROVED",
        }

        state = {
            "current_phase": "MARKET_OPEN",
            "approved_orders": [risk_approval],
        }

        result = agent.run(state)

        # Risk trade should NOT be in executable orders
        assert len(result["approved_orders"]) == 0
        # Should be in human approval pending
        assert "risk-prop-1" in agent._human_approval_pending

    def test_non_market_phase_skips_conversion(self):
        """PRE_MARKET and other phases should not convert orders."""
        agent = _make_orchestrator()

        state = {
            "current_phase": "PRE_MARKET",
            "approved_orders": [{"proposal_id": "x", "symbol": "Y"}],
        }

        result = agent.run(state)

        # Should pass through unchanged (no conversion)
        assert result["approved_orders"] == [{"proposal_id": "x", "symbol": "Y"}]

    def test_mixed_buckets_handled_correctly(self):
        """Conservative orders execute, risk orders queue for approval."""
        agent = _make_orchestrator()

        conservative = {
            "proposal_id": "cons-1",
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "transaction_type": "BUY",
            "approved_position_size": 8,
            "approved_stop_loss": 2462.5,
            "approved_target": 2550,
            "bucket": "conservative",
            "decision": "APPROVED",
        }
        risk = {
            "proposal_id": "risk-1",
            "symbol": "NIFTY_OPT",
            "entry_price": 100,
            "transaction_type": "BUY",
            "approved_position_size": 25,
            "approved_stop_loss": 40,
            "approved_target": 180,
            "bucket": "risk",
            "decision": "APPROVED",
        }

        state = {
            "current_phase": "MARKET_OPEN",
            "approved_orders": [conservative, risk],
        }

        result = agent.run(state)

        assert len(result["approved_orders"]) == 1
        assert result["approved_orders"][0]["symbol"] == "RELIANCE"
        assert "risk-1" in agent._human_approval_pending


# ── 4. Force-close uses PositionMonitor directly ────────────────────────────


class TestForceCloseWiring:

    def test_force_close_calls_position_monitor(self):
        """Scheduler should call PositionMonitor.force_close_all(), not graph."""
        from scheduler.job_scheduler import SwarmScheduler

        # Build minimal agents dict
        mock_orchestrator = MagicMock()
        mock_orchestrator.redis = FakeRedis(state={
            "state:positions": {"positions": [
                {"symbol": "INFY", "status": "OPEN", "quantity": 10,
                 "entry_price": 1500, "bucket": "conservative"},
            ]},
        })
        mock_orchestrator.sqlite = FakeSQLite()
        mock_orchestrator.broker = None

        mock_execution = MagicMock()
        mock_execution.simulator = MagicMock()
        mock_execution.simulator.open_positions = [
            {"symbol": "INFY", "quantity": 10, "entry_price": 1500},
        ]
        mock_execution.simulator.force_close_all.return_value = [
            {"symbol": "INFY", "exit_reason": "CLOSED_EOD", "pnl": 50},
        ]

        agents = {
            "orchestrator": mock_orchestrator,
            "execution_agent": mock_execution,
        }
        scheduler = SwarmScheduler(agents, graphs={}, telegram_bot=None)
        scheduler._initial_state = {}

        with patch("tools.market_data.get_live_quote") as mock_quote:
            mock_quote.return_value = {"INFY": {"ltp": 1510}}

            with patch("tools.position_monitor.PositionMonitor.force_close_all",
                       return_value=[{"symbol": "INFY", "exit_reason": "CLOSED_EOD"}]) as mock_fc:
                scheduler._run_force_close()
                mock_fc.assert_called_once()

    def test_force_close_does_not_use_graph(self):
        """_run_force_close should NOT invoke the force_close graph."""
        from scheduler.job_scheduler import SwarmScheduler

        mock_graph = MagicMock()
        agents = {
            "orchestrator": MagicMock(
                redis=FakeRedis(state={"state:positions": {"positions": []}}),
                sqlite=FakeSQLite(),
                broker=None,
            ),
            "execution_agent": MagicMock(simulator=MagicMock()),
        }
        scheduler = SwarmScheduler(agents, graphs={"force_close": mock_graph})
        scheduler._initial_state = {}

        with patch("tools.position_monitor.PositionMonitor.force_close_all", return_value=[]):
            with patch("tools.market_data.get_live_quote", return_value={}):
                scheduler._run_force_close()

        # Graph should NOT have been invoked
        mock_graph.invoke.assert_not_called()

    def test_force_close_sends_telegram_on_close(self):
        """Should send Telegram notification when positions are closed."""
        from scheduler.job_scheduler import SwarmScheduler

        mock_telegram = MagicMock()
        agents = {
            "orchestrator": MagicMock(
                redis=FakeRedis(state={"state:positions": {"positions": []}}),
                sqlite=FakeSQLite(),
                broker=None,
            ),
            "execution_agent": MagicMock(simulator=MagicMock()),
        }
        scheduler = SwarmScheduler(agents, graphs={}, telegram_bot=mock_telegram)
        scheduler._initial_state = {}

        closed = [{"symbol": "RELIANCE", "exit_reason": "CLOSED_EOD"}]
        with patch("tools.position_monitor.PositionMonitor.force_close_all", return_value=closed):
            with patch("tools.market_data.get_live_quote", return_value={}):
                scheduler._run_force_close()

        mock_telegram.send_message.assert_called_once()
        msg = mock_telegram.send_message.call_args[0][0]
        assert "RELIANCE" in msg
        assert "1 position" in msg

    def test_force_close_handles_missing_orchestrator(self):
        """Should log error and return if orchestrator not found."""
        from scheduler.job_scheduler import SwarmScheduler

        scheduler = SwarmScheduler(agents={}, graphs={})
        # Should not raise
        scheduler._run_force_close()


# ── 5. End-to-end graph data flow ───────────────────────────────────────────


class TestGraphDataFlow:
    """Test that data flows correctly through analyst -> risk -> orchestrator."""

    def test_analyst_to_risk_to_orchestrator(self):
        """Full pipeline: analyst produces signals, risk reviews, orchestrator converts."""
        # Step 1: Analyst produces signals
        analyst = _make_analyst()
        proposal = _sample_proposal("HDFCBANK", 1650, 1625, 1700, 10)
        analyst._signal_payloads[proposal["proposal_id"]] = proposal
        analyst._pending_signals[proposal["proposal_id"]] = _time.time()

        state = {
            "conservative_strategy": {
                "strategy": "RSI_MEAN_REVERSION",
                "watchlist": [],
                "entry_conditions": {"direction": "BOTH"},
                "exit_conditions": {},
            },
        }
        state = analyst.run(state)

        # Verify analyst output
        assert len(state["pending_signals"]) == 1
        assert state["pending_signals"][0]["symbol"] == "HDFCBANK"

        # Step 2: Risk agent reviews
        risk = _make_risk_agent()
        state = risk.run(state)

        # Verify risk output
        assert len(state["approved_orders"]) + len(state["rejected_proposals"]) == 1

        if state["approved_orders"]:
            # Step 3: Orchestrator converts
            orch = _make_orchestrator()
            state["current_phase"] = "MARKET_OPEN"
            state = orch.run(state)

            # Verify orchestrator output has ApprovedOrder fields
            assert len(state["approved_orders"]) == 1
            order = state["approved_orders"][0]
            assert "order_id" in order
            assert "mode" in order
            assert order["approved_by"] == "risk_agent"
