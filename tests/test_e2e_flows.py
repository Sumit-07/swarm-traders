"""End-to-end tests for the trading system's core flows.

Tests the full pipeline from signal detection through position opening,
monitoring, and closing — mocking only LLM calls and external APIs.
"""

import time as _time
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")

# Fixed market-hours time so tests aren't affected by real wall clock
_MARKET_HOURS_TIME = datetime(2026, 4, 7, 11, 0, 0, tzinfo=IST)


@pytest.fixture(autouse=True)
def _force_market_hours(monkeypatch):
    """Force all analyst time checks to see 11:00 AM IST (within trading hours).

    Without this, tests fail when run after 3:00 PM IST due to
    the no_new_trades cutoff in _scan_watchlist().
    """
    original_now = datetime.now

    def fake_now(tz=None):
        # Only fake it when called from analyst (checking trading hours)
        # Return real time for everything else
        return _MARKET_HOURS_TIME if tz else original_now(tz)

    monkeypatch.setattr(
        "agents.analyst.analyst.datetime",
        type("FakeDT", (), {
            "now": staticmethod(fake_now),
            "fromisoformat": datetime.fromisoformat,
        }),
    )


# ---------------------------------------------------------------------------
# Shared fakes & helpers
# ---------------------------------------------------------------------------

class FakeRedis:
    """In-memory Redis stand-in that supports state and market data keys."""

    def __init__(self, state=None, market_data=None):
        self._state = state or {}
        self._market_data = market_data or {}
        self._subscribers: dict[str, list] = {}

    def get_state(self, key):
        return self._state.get(key)

    def set_state(self, key, value):
        self._state[key] = value

    def get_market_data(self, key):
        return self._market_data.get(key)

    def set_market_data(self, key, value, ttl=None):
        self._market_data[key] = value

    def subscribe(self, channel, callback):
        self._subscribers.setdefault(channel, []).append(callback)

    def publish(self, channel, data):
        for cb in self._subscribers.get(channel, []):
            cb(data)


class FakeSQLite:
    """Minimal SQLite stand-in that records calls for assertion."""

    def __init__(self, daily_pnl=None):
        self._daily_pnl = daily_pnl
        self.logged_trades: list[dict] = []
        self.logged_signals: list[dict] = []

    def get_daily_pnl(self, date=None):
        return self._daily_pnl

    def log_trade(self, data):
        self.logged_trades.append(data)

    def log_signal(self, data):
        self.logged_signals.append(data)

    def update_trade(self, trade_id, updates):
        pass

    def query(self, *args, **kwargs):
        return []


# ---- Mock LLM responses ----

LLM_SIGNAL_VALID = {
    "signal_valid": True,
    "confidence": "HIGH",
    "analyst_note": "Strong RSI reversion setup",
    "suggested_target": None,
    "suggested_stop": None,
}

LLM_SIGNAL_INVALID = {
    "signal_valid": False,
    "confidence": "LOW",
    "invalidation_reason": "Market conditions unfavorable",
}

LLM_RISK_APPROVED = {
    "decision": "APPROVED",
    "reason": "Risk within limits, strong setup",
    "approved_stop_loss": 2462.5,
    "approved_target": 2550.0,
    "flag_human": False,
}

LLM_RISK_REJECTED = {
    "decision": "REJECTED",
    "reason": "Too risky given current exposure",
    "flag_human": False,
}


# ---- Agent factories ----

def _tick_data(symbol, close, rsi=28.0, volume_ratio=1.5, vwap=None,
               atr=30.0, adx=20.0, macd=0.5, macd_signal=0.3):
    """Build a watchlist tick data dict."""
    return {
        "symbol": symbol,
        "close": close,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "vwap": vwap or close * 1.01,
        "atr": atr,
        "adx": adx,
        "macd": macd,
        "macd_signal": macd_signal,
        "volume": 500_000,
        "timestamp": datetime.now(IST).isoformat(),
    }


def _make_analyst(redis=None, sqlite=None, strategy_config=None):
    from agents.analyst.analyst import AnalystAgent

    agent = AnalystAgent.__new__(AnalystAgent)
    agent.redis = redis or FakeRedis()
    agent.sqlite = sqlite or FakeSQLite()
    agent.logger = MagicMock()
    agent._pending_signals = {}
    agent._signal_payloads = {}
    agent._strategy_config = strategy_config
    agent._extra_context = ""
    agent._llm_provider = None
    agent._llm_call_count = 0
    agent._in_graph_run = False
    agent.call_llm = MagicMock(return_value=LLM_SIGNAL_VALID)
    agent.send_message = MagicMock()
    return agent


def _make_risk_agent(redis=None, sqlite=None, positions=None, todays_pnl=0.0):
    from agents.risk_agent.risk_agent import RiskAgent

    redis = redis or FakeRedis(state={
        "state:positions": {"positions": positions or []},
    })
    agent = RiskAgent.__new__(RiskAgent)
    agent.redis = redis
    agent.sqlite = sqlite or FakeSQLite()
    agent.logger = MagicMock()
    agent._consecutive_losses = 0
    agent._in_cooldown = False
    agent._cooldown_until = None
    agent._todays_pnl = todays_pnl
    agent._review_cache = {}
    agent._processed_proposals = set()
    agent._llm_provider = None
    agent._extra_context = ""
    agent._llm_call_count = 0
    agent.call_llm = MagicMock(return_value=LLM_RISK_APPROVED)
    agent.send_message = MagicMock()
    return agent


def _make_orchestrator(redis=None, sqlite=None, system_mode="PAPER",
                       active_strategy=None):
    from agents.orchestrator.orchestrator import OrchestratorAgent

    redis = redis or FakeRedis(state={
        "state:system_mode": {"mode": system_mode},
        "state:active_strategy": active_strategy or {
            "strategy": "RSI_MEAN_REVERSION",
        },
        "state:all_agents": {},
    })
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.redis = redis
    agent.sqlite = sqlite or FakeSQLite()
    agent.logger = MagicMock()
    agent.telegram = None
    agent.broker = None
    agent.kite = None
    agent._pending_proposals = {}
    agent._human_approval_pending = {}
    agent._llm_provider = None
    agent._extra_context = ""
    agent._llm_call_count = 0
    agent.call_llm = MagicMock()
    agent.send_message = MagicMock()
    return agent


def _make_execution_agent(redis=None, sqlite=None):
    from agents.execution_agent.execution_agent import ExecutionAgent

    redis = redis or FakeRedis(state={
        "state:positions": {"positions": []},
    })
    agent = ExecutionAgent.__new__(ExecutionAgent)
    agent.redis = redis
    agent.sqlite = sqlite or FakeSQLite()
    agent.logger = MagicMock()
    agent.broker = None
    agent.simulator = None  # Created below
    agent._processed_orders = set()
    agent._llm_provider = None
    agent._extra_context = ""
    agent._llm_call_count = 0
    agent.call_llm = MagicMock()
    agent.send_message = MagicMock()

    # Attach a real simulator for paper trading
    from tools.order_simulator import OrderSimulator
    agent.simulator = OrderSimulator()
    return agent


def _base_strategy():
    """Standard RSI Mean Reversion strategy config."""
    return {
        "strategy": "RSI_MEAN_REVERSION",
        "regime": "RANGE_BOUND",
        "confidence": "HIGH",
        "watchlist": ["RELIANCE", "HDFCBANK"],
        "entry_conditions": {
            "direction": "BOTH",
            "entry_threshold": 32,
            "short_threshold": 68,
            "volume_confirmation": True,
        },
        "exit_conditions": {
            "target_pct": 2.0,
            "stop_loss_pct": 1.5,
        },
    }


def _base_state(strategy=None, phase="MARKET_OPEN"):
    """Build a base SwarmState dict for graph execution."""
    return {
        "system_mode": "PAPER",
        "current_phase": phase,
        "trading_day": datetime.now(IST).strftime("%Y-%m-%d"),
        "market_data_ready": True,
        "conservative_strategy": strategy or _base_strategy(),
        "risk_strategy": None,
        "pending_signals": [],
        "approved_orders": [],
        "rejected_proposals": [],
        "active_positions": [],
        "human_approval_pending": False,
        "human_response": None,
        "error": None,
        "halt_reason": None,
    }


# ===========================================================================
# Test 1: Full signal-to-position-open flow (graph path)
# ===========================================================================

class TestSignalToPositionOpen:
    """E2E: Analyst detects signal → Risk approves → Orchestrator packages →
    Execution places paper order → Position appears in Redis."""

    def test_full_flow_position_opens_in_redis(self):
        """Happy path: RSI oversold → signal validated → risk approved →
        order executed → position in Redis + SQLite."""
        # Shared stores so all agents see each other's writes
        redis = FakeRedis(
            state={
                "state:positions": {"positions": []},
                "state:system_mode": {"mode": "PAPER"},
                "state:active_strategy": {"strategy": "RSI_MEAN_REVERSION"},
                "state:all_agents": {},
            },
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=28.0),
                "data:watchlist_ticks:HDFCBANK": _tick_data("HDFCBANK", 1600, rsi=55.0),
                "data:market_snapshot": {
                    "nifty": {"ltp": 22000, "change": "+0.5%"},
                    "timestamp": datetime.now(IST).isoformat(),
                },
            },
        )
        sqlite = FakeSQLite()

        analyst = _make_analyst(redis=redis, sqlite=sqlite)
        risk = _make_risk_agent(redis=redis, sqlite=sqlite)
        orch = _make_orchestrator(redis=redis, sqlite=sqlite)
        execution = _make_execution_agent(redis=redis, sqlite=sqlite)

        state = _base_state()

        # Step 1: Analyst scans watchlist and finds RELIANCE (RSI 28 < 32)
        state = analyst.run(state)
        assert len(state["pending_signals"]) == 1
        signal = state["pending_signals"][0]
        assert signal["symbol"] == "RELIANCE"
        assert signal["direction"] == "LONG"
        # Analyst should NOT have sent message during graph run
        analyst.send_message.assert_not_called()
        # LLM was called for signal validation
        analyst.call_llm.assert_called_once()

        # Step 2: Risk agent reviews — rules + LLM
        state = risk.run(state)
        assert len(state["approved_orders"]) == 1
        assert len(state["rejected_proposals"]) == 0
        approved = state["approved_orders"][0]
        assert approved["decision"] == "APPROVED"
        assert approved["symbol"] == "RELIANCE"
        assert approved["approved_position_size"] > 0
        # Risk LLM called exactly once
        risk.call_llm.assert_called_once()

        # Step 3: Orchestrator converts to executable order
        state = orch.run(state)
        assert len(state["approved_orders"]) == 1
        order = state["approved_orders"][0]
        assert order["symbol"] == "RELIANCE"
        assert order["transaction_type"] == "BUY"
        assert order["mode"] == "PAPER"
        assert order["approved_by"] == "risk_agent"
        assert "order_id" in order
        assert "proposal_id" in order

        # Step 4: Execution agent places paper order
        state = execution.run(state)

        # Verify: position exists in Redis
        positions = redis.get_state("state:positions")["positions"]
        assert len(positions) == 1
        pos = positions[0]
        assert pos["symbol"] == "RELIANCE"
        assert pos["status"] == "OPEN"
        assert pos["direction"] == "LONG"
        assert pos["entry_price"] > 0
        assert pos["entry_fees"] == 20  # brokerage recorded
        assert pos["strategy"] == "RSI_MEAN_REVERSION"
        assert pos["bucket"] == "conservative"

        # Verify: trade logged to SQLite
        assert len(sqlite.logged_trades) == 1
        assert sqlite.logged_trades[0]["symbol"] == "RELIANCE"
        assert sqlite.logged_trades[0]["status"] == "OPEN"

        # Verify: signal logged to SQLite
        assert len(sqlite.logged_signals) == 1

        # Simulator doesn't auto-track from _execute_paper — positions are in
        # Redis. Reload simulates what happens on next scheduler cycle.
        execution._reload_paper_positions()
        assert len(execution.simulator.open_positions) == 1

    def test_no_signal_when_rsi_normal(self):
        """No signals generated when RSI is in neutral zone."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=50.0),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        state = _base_state()
        state = analyst.run(state)
        assert len(state["pending_signals"]) == 0
        analyst.call_llm.assert_not_called()

    def test_signal_invalidated_by_llm(self):
        """Signal found by rules but rejected by LLM validation."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=28.0),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        analyst.call_llm = MagicMock(return_value=LLM_SIGNAL_INVALID)
        state = _base_state()
        state = analyst.run(state)
        assert len(state["pending_signals"]) == 0

    def test_short_signal_on_high_rsi(self):
        """RSI > 68 with volume generates SHORT signal."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=72.0),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        state = _base_state()
        state = analyst.run(state)
        assert len(state["pending_signals"]) == 1
        assert state["pending_signals"][0]["direction"] == "SHORT"

    def test_multiple_symbols_one_signal(self):
        """Only the symbol meeting entry conditions generates a signal."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=28.0),
                "data:watchlist_ticks:HDFCBANK": _tick_data("HDFCBANK", 1600, rsi=50.0),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        state = _base_state()
        state = analyst.run(state)
        assert len(state["pending_signals"]) == 1
        assert state["pending_signals"][0]["symbol"] == "RELIANCE"


# ===========================================================================
# Test 2: Risk agent rejection scenarios
# ===========================================================================

class TestRiskRejections:
    """E2E: Various scenarios where risk agent blocks a trade."""

    def _run_through_risk(self, risk_agent, signal):
        state = _base_state()
        state["pending_signals"] = [signal]
        return risk_agent.run(state)

    def test_max_positions_blocks_trade(self):
        """With 4 open positions, new trades are rejected."""
        open_positions = [
            {"symbol": f"SYM{i}", "status": "OPEN", "bucket": "conservative",
             "entry_price": 100, "quantity": 1}
            for i in range(4)
        ]
        risk = _make_risk_agent(positions=open_positions)
        signal = {
            "proposal_id": "test-max-pos",
            "symbol": "NEWSTOCK",
            "entry_price": 500,
            "stop_loss": 492.5,
            "target": 510,
            "quantity_suggested": 5,
            "direction": "LONG",
            "bucket": "conservative",
        }
        state = self._run_through_risk(risk, signal)
        assert len(state["rejected_proposals"]) == 1
        assert state["rejected_proposals"][0]["decision"] == "REJECTED"
        # LLM should NOT be called for rejected trades
        risk.call_llm.assert_not_called()

    def test_daily_loss_budget_blocks_trade(self):
        """After large daily loss, new trades are rejected."""
        risk = _make_risk_agent(todays_pnl=-1400.0)
        # With ₹50k capital, 3% max daily loss = ₹1500.
        # Already lost ₹1400, so only ₹100 budget left.
        signal = {
            "proposal_id": "test-budget",
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "stop_loss": 2462.5,  # risk = 37.5 * qty
            "target": 2550,
            "quantity_suggested": 5,  # capital_at_risk = 187.5 > 100
            "direction": "LONG",
            "bucket": "conservative",
        }
        state = self._run_through_risk(risk, signal)
        assert len(state["rejected_proposals"]) == 1
        assert "daily_loss_budget" in str(state["rejected_proposals"][0].get("reason", ""))

    def test_cooldown_blocks_trade(self):
        """3 consecutive losses trigger cooldown — new trades blocked."""
        risk = _make_risk_agent()
        risk._in_cooldown = True
        risk._cooldown_until = datetime.now(IST) + timedelta(minutes=30)
        signal = {
            "proposal_id": "test-cooldown",
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "stop_loss": 2462.5,
            "target": 2550,
            "quantity_suggested": 5,
            "direction": "LONG",
            "bucket": "conservative",
        }
        state = self._run_through_risk(risk, signal)
        assert len(state["rejected_proposals"]) == 1
        assert "not_in_cooldown" in str(state["rejected_proposals"][0].get("reason", ""))

    def test_excessive_risk_per_trade(self):
        """Trade where capital_at_risk > 1.5% of capital is rejected."""
        risk = _make_risk_agent()
        signal = {
            "proposal_id": "test-excess-risk",
            "symbol": "EXPENSIVE",
            "entry_price": 5000,
            "stop_loss": 4500,  # risk_per_share = 500
            "target": 5500,
            "quantity_suggested": 10,  # capital_at_risk = 5000 >> ₹750 limit
            "direction": "LONG",
            "bucket": "conservative",
        }
        state = self._run_through_risk(risk, signal)
        assert len(state["rejected_proposals"]) == 1

    def test_illogical_stop_loss(self):
        """Stop-loss too far from entry (risk_pct >= 5%) is rejected."""
        risk = _make_risk_agent()
        # risk_per_share = 60, qty = 50, capital_at_risk = 3000
        # risk_pct = 3000 / 50000 = 6% > 5% → stop_loss_logical fails
        signal = {
            "proposal_id": "test-bad-sl",
            "symbol": "STOCK",
            "entry_price": 100,
            "stop_loss": 40,  # 60% away — risk_pct will be >= 5%
            "target": 120,
            "quantity_suggested": 50,
            "direction": "LONG",
            "bucket": "conservative",
        }
        state = self._run_through_risk(risk, signal)
        assert len(state["rejected_proposals"]) == 1


# ===========================================================================
# Test 3: Review cache prevents duplicate LLM calls
# ===========================================================================

class TestReviewCache:
    """E2E: Cache prevents redundant LLM calls for same symbol+direction."""

    def test_cached_rejection_skips_full_review(self):
        """Second review of same symbol+direction uses cache, no LLM call."""
        risk = _make_risk_agent()
        signal = {
            "proposal_id": "test-cache-1",
            "symbol": "RELIANCE",
            "entry_price": 5000,
            "stop_loss": 4500,
            "target": 5500,
            "quantity_suggested": 10,
            "direction": "LONG",
            "bucket": "conservative",
        }

        # First review — gets rejected by rules (excessive risk)
        state1 = _base_state()
        state1["pending_signals"] = [signal]
        state1 = risk.run(state1)
        assert len(state1["rejected_proposals"]) == 1

        # Second review — same symbol+direction, should use cache
        signal2 = {**signal, "proposal_id": "test-cache-2"}
        state2 = _base_state()
        state2["pending_signals"] = [signal2]
        state2 = risk.run(state2)
        assert len(state2["rejected_proposals"]) == 1
        # LLM should never have been called (rejected by rules, then cached)
        risk.call_llm.assert_not_called()

    def test_approved_trade_calls_llm_once(self):
        """Approved trade calls LLM exactly once, not on repeat."""
        risk = _make_risk_agent()
        signal = {
            "proposal_id": "test-approve-1",
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "stop_loss": 2462.5,
            "target": 2550,
            "quantity_suggested": 5,
            "direction": "LONG",
            "bucket": "conservative",
        }

        state = _base_state()
        state["pending_signals"] = [signal]
        state = risk.run(state)
        assert len(state["approved_orders"]) == 1
        assert risk.call_llm.call_count == 1

    def test_proposal_dedup_across_paths(self):
        """Same proposal_id processed in run() is skipped in on_message()."""
        risk = _make_risk_agent()
        proposal_id = "test-dedup-123"
        signal = {
            "proposal_id": proposal_id,
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "stop_loss": 2462.5,
            "target": 2550,
            "quantity_suggested": 5,
            "direction": "LONG",
            "bucket": "conservative",
        }

        # Process via graph run()
        state = _base_state()
        state["pending_signals"] = [signal]
        risk.run(state)
        assert proposal_id in risk._processed_proposals

        # Now simulate message arrival with same proposal_id
        from agents.message import AgentMessage, MessageType
        msg = AgentMessage(
            from_agent="analyst",
            to_agent="risk_agent",
            type=MessageType.SIGNAL,
            channel="channel:risk_agent",
            payload=signal,
        )
        risk.on_message(msg)
        # on_message should have returned early — no extra LLM call
        # run() called it once, on_message should NOT call again
        assert risk.call_llm.call_count == 1


# ===========================================================================
# Test 4: Position close via stop-loss
# ===========================================================================

class TestPositionCloseStopLoss:
    """E2E: Open position hits stop-loss → closed by position monitor."""

    def test_stop_loss_closes_paper_position(self):
        """Price drops below stop → simulator closes position → Redis updated."""
        from tools.order_simulator import OrderSimulator
        from tools.position_monitor import PositionMonitor

        redis = FakeRedis(state={
            "state:positions": {"positions": []},
        })
        sqlite = FakeSQLite()
        simulator = OrderSimulator()

        # Open a position
        fill = simulator.simulate_fill({
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 10,
            "price": 2500.0,
            "order_type": "LIMIT",
        })
        position = simulator.open_position(
            fill, direction="LONG", stop_loss=2462.5,
            target=2550.0, bucket="conservative",
        )

        # Also record in Redis (as execution agent would)
        redis.set_state("state:positions", {
            "positions": [{
                "trade_id": fill["order_id"],
                "symbol": "RELIANCE",
                "direction": "LONG",
                "entry_price": fill["fill_price"],
                "quantity": 10,
                "stop_loss": 2462.5,
                "target": 2550.0,
                "bucket": "conservative",
                "entry_fees": 20,
                "status": "OPEN",
                "entry_time": fill["filled_at"],
            }],
        })

        monitor = PositionMonitor(redis, sqlite, simulator=simulator)

        # Price drops to 2450 — below stop of 2462.5
        closed = monitor.check_paper_exits(lambda sym: 2450.0)

        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "CLOSED_STOP"
        assert closed[0]["pnl"] < 0  # loss
        assert closed[0]["symbol"] == "RELIANCE"

        # Redis position removed
        positions = redis.get_state("state:positions")["positions"]
        open_positions = [p for p in positions if p.get("status") == "OPEN"]
        assert len(open_positions) == 0

        # Simulator position cleared
        assert len(simulator.open_positions) == 0


# ===========================================================================
# Test 5: Position close via target hit
# ===========================================================================

class TestPositionCloseTarget:
    """E2E: Open position hits target → closed by position monitor."""

    def test_target_closes_paper_position(self):
        """Price rises to target → position closed with profit."""
        from tools.order_simulator import OrderSimulator
        from tools.position_monitor import PositionMonitor

        redis = FakeRedis(state={"state:positions": {"positions": []}})
        sqlite = FakeSQLite()
        simulator = OrderSimulator()

        fill = simulator.simulate_fill({
            "symbol": "HDFCBANK",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 1600.0,
            "order_type": "LIMIT",
        })
        simulator.open_position(
            fill, direction="LONG", stop_loss=1576.0,
            target=1632.0, bucket="conservative",
        )

        redis.set_state("state:positions", {
            "positions": [{
                "trade_id": fill["order_id"],
                "symbol": "HDFCBANK",
                "direction": "LONG",
                "entry_price": fill["fill_price"],
                "quantity": 5,
                "stop_loss": 1576.0,
                "target": 1632.0,
                "bucket": "conservative",
                "entry_fees": 20,
                "status": "OPEN",
                "entry_time": fill["filled_at"],
            }],
        })

        monitor = PositionMonitor(redis, sqlite, simulator=simulator)

        # Price rises to 1640 — above target of 1632
        closed = monitor.check_paper_exits(lambda sym: 1640.0)

        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "CLOSED_TARGET"
        assert closed[0]["pnl"] > 0  # profit
        assert len(simulator.open_positions) == 0


# ===========================================================================
# Test 6: Force close at EOD (3:20 PM)
# ===========================================================================

class TestForceCloseEOD:
    """E2E: All open positions force-closed at intraday cutoff."""

    def test_force_close_all_positions(self):
        """All open positions closed at EOD with valid prices."""
        from tools.order_simulator import OrderSimulator
        from tools.position_monitor import PositionMonitor

        redis = FakeRedis(state={"state:positions": {"positions": []}})
        sqlite = FakeSQLite()
        simulator = OrderSimulator()

        # Open two positions
        for symbol, price in [("RELIANCE", 2500.0), ("HDFCBANK", 1600.0)]:
            fill = simulator.simulate_fill({
                "symbol": symbol,
                "transaction_type": "BUY",
                "quantity": 5,
                "price": price,
                "order_type": "LIMIT",
            })
            simulator.open_position(
                fill, direction="LONG", stop_loss=price * 0.985,
                target=price * 1.02, bucket="conservative",
            )

        assert len(simulator.open_positions) == 2

        monitor = PositionMonitor(redis, sqlite, simulator=simulator)
        prices = {"RELIANCE": 2510.0, "HDFCBANK": 1590.0}
        closed = monitor.force_close_all(lambda sym: prices.get(sym, 0))

        assert len(closed) == 2
        assert all(c.get("exit_reason") == "CLOSED_EOD" for c in closed)
        assert len(simulator.open_positions) == 0

        # Redis cleared
        positions = redis.get_state("state:positions")
        assert len(positions["positions"]) == 0

    def test_force_close_skips_zero_price(self):
        """Positions with no valid price are NOT force-closed at 0."""
        from tools.order_simulator import OrderSimulator
        from tools.position_monitor import PositionMonitor

        redis = FakeRedis(state={"state:positions": {"positions": []}})
        sqlite = FakeSQLite()
        simulator = OrderSimulator()

        fill = simulator.simulate_fill({
            "symbol": "OBSCURE",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 300.0,
            "order_type": "LIMIT",
        })
        simulator.open_position(
            fill, direction="LONG", stop_loss=290,
            target=310, bucket="conservative",
        )

        # Price function returns 0 for this symbol
        closed = simulator.force_close_all(lambda sym: 0)
        assert len(closed) == 0
        # Position should still be open
        assert len(simulator.open_positions) == 1


# ===========================================================================
# Test 7: Execution deduplication
# ===========================================================================

class TestExecutionDedup:
    """E2E: Same order arriving via both paths is executed only once."""

    def test_duplicate_order_id_skipped(self):
        """Second order with same order_id is skipped."""
        redis = FakeRedis(state={"state:positions": {"positions": []}})
        execution = _make_execution_agent(redis=redis)

        order = {
            "order_id": "dup-order-1",
            "proposal_id": "prop-1",
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 2500.0,
            "mode": "PAPER",
            "bucket": "conservative",
            "stop_loss_price": 2462.5,
            "target_price": 2550.0,
            "strategy": "RSI_MEAN_REVERSION",
        }

        state = _base_state()
        state["approved_orders"] = [order, order]  # same order twice
        execution.run(state)

        positions = redis.get_state("state:positions")["positions"]
        assert len(positions) == 1

    def test_duplicate_proposal_id_skipped(self):
        """Two orders with different order_ids but same proposal_id — only one executes."""
        redis = FakeRedis(state={"state:positions": {"positions": []}})
        execution = _make_execution_agent(redis=redis)

        order1 = {
            "order_id": "order-graph",
            "proposal_id": "shared-prop",
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 2500.0,
            "mode": "PAPER",
            "bucket": "conservative",
            "stop_loss_price": 2462.5,
            "target_price": 2550.0,
        }
        order2 = {
            "order_id": "order-message",
            "proposal_id": "shared-prop",
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 2500.0,
            "mode": "PAPER",
            "bucket": "conservative",
            "stop_loss_price": 2462.5,
            "target_price": 2550.0,
        }

        state = _base_state()
        state["approved_orders"] = [order1, order2]
        execution.run(state)

        positions = redis.get_state("state:positions")["positions"]
        assert len(positions) == 1


# ===========================================================================
# Test 8: Graph path suppresses messages (no dual-path firing)
# ===========================================================================

class TestGraphMessageSuppression:
    """E2E: During graph run(), agents don't send Redis messages."""

    def test_analyst_no_messages_in_graph_run(self):
        """Analyst.run() should NOT send messages — graph routes via state."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=28.0),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        state = _base_state()
        state = analyst.run(state)

        assert len(state["pending_signals"]) == 1
        # Key assertion: no messages sent during graph run
        analyst.send_message.assert_not_called()

    def test_analyst_sends_message_outside_graph(self):
        """When _scan_watchlist() is called outside run(), messages ARE sent."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=28.0),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        analyst._strategy_config = {
            "strategy_name": "RSI_MEAN_REVERSION",
            "watchlist": ["RELIANCE"],
            "entry_conditions": {
                "direction": "BOTH",
                "entry_threshold": 32,
                "volume_confirmation": True,
            },
            "exit_conditions": {"target_pct": 2.0, "stop_loss_pct": 1.5},
            "bucket": "conservative",
        }
        # Directly call _scan_watchlist (not via run())
        analyst._scan_watchlist()
        # Messages should be sent when not in graph run
        analyst.send_message.assert_called()


# ===========================================================================
# Test 9: Full round-trip — open and close via graph + monitor
# ===========================================================================

class TestFullRoundTrip:
    """E2E: Signal → open position → price hits target → position closed."""

    def test_open_then_close_via_target(self):
        """Full lifecycle: detect signal, open position, close on target hit."""
        redis = FakeRedis(
            state={
                "state:positions": {"positions": []},
                "state:system_mode": {"mode": "PAPER"},
                "state:active_strategy": {"strategy": "RSI_MEAN_REVERSION"},
                "state:all_agents": {},
            },
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=28.0),
                "data:market_snapshot": {"nifty": {"ltp": 22000}},
            },
        )
        sqlite = FakeSQLite()

        analyst = _make_analyst(redis=redis, sqlite=sqlite)
        risk = _make_risk_agent(redis=redis, sqlite=sqlite)
        orch = _make_orchestrator(redis=redis, sqlite=sqlite)
        execution = _make_execution_agent(redis=redis, sqlite=sqlite)

        # --- Phase 1: Open position ---
        state = _base_state()
        state = analyst.run(state)
        state = risk.run(state)
        state = orch.run(state)
        state = execution.run(state)

        positions = redis.get_state("state:positions")["positions"]
        assert len(positions) == 1
        assert positions[0]["status"] == "OPEN"
        target = positions[0]["target"]

        # Reload positions into simulator (simulates next scheduler cycle)
        execution._reload_paper_positions()

        # --- Phase 2: Monitor closes on target ---
        from tools.position_monitor import PositionMonitor
        monitor = PositionMonitor(redis, sqlite, simulator=execution.simulator)

        # Price above target
        closed = monitor.check_paper_exits(lambda sym: target + 10)
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "CLOSED_TARGET"
        assert closed[0]["pnl"] > 0

        # Position removed from Redis and simulator
        open_pos = [p for p in redis.get_state("state:positions")["positions"]
                    if p.get("status") == "OPEN"]
        assert len(open_pos) == 0
        assert len(execution.simulator.open_positions) == 0

    def test_open_then_close_via_stop_loss(self):
        """Full lifecycle: detect signal, open position, close on stop-loss."""
        redis = FakeRedis(
            state={
                "state:positions": {"positions": []},
                "state:system_mode": {"mode": "PAPER"},
                "state:active_strategy": {"strategy": "RSI_MEAN_REVERSION"},
                "state:all_agents": {},
            },
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=28.0),
                "data:market_snapshot": {"nifty": {"ltp": 22000}},
            },
        )
        sqlite = FakeSQLite()

        analyst = _make_analyst(redis=redis, sqlite=sqlite)
        risk = _make_risk_agent(redis=redis, sqlite=sqlite)
        orch = _make_orchestrator(redis=redis, sqlite=sqlite)
        execution = _make_execution_agent(redis=redis, sqlite=sqlite)

        state = _base_state()
        state = analyst.run(state)
        state = risk.run(state)
        state = orch.run(state)
        state = execution.run(state)

        positions = redis.get_state("state:positions")["positions"]
        stop_loss = positions[0]["stop_loss"]

        # Reload positions into simulator (simulates next scheduler cycle)
        execution._reload_paper_positions()

        from tools.position_monitor import PositionMonitor
        monitor = PositionMonitor(redis, sqlite, simulator=execution.simulator)

        # Price drops below stop
        closed = monitor.check_paper_exits(lambda sym: stop_loss - 10)
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "CLOSED_STOP"
        assert closed[0]["pnl"] < 0


# ===========================================================================
# Test 10: Position monitor — price 0 guard
# ===========================================================================

class TestPositionMonitorSafety:
    """E2E: Position monitor handles bad prices gracefully."""

    def test_zero_price_skips_exit_check(self):
        """Position with zero price is NOT closed — wait for valid price."""
        from tools.order_simulator import OrderSimulator
        from tools.position_monitor import PositionMonitor

        redis = FakeRedis(state={"state:positions": {"positions": []}})
        sqlite = FakeSQLite()
        simulator = OrderSimulator()

        fill = simulator.simulate_fill({
            "symbol": "STOCK",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 500.0,
            "order_type": "LIMIT",
        })
        simulator.open_position(
            fill, direction="LONG", stop_loss=490,
            target=510, bucket="conservative",
        )
        redis.set_state("state:positions", {
            "positions": [{
                "trade_id": fill["order_id"],
                "symbol": "STOCK",
                "direction": "LONG",
                "entry_price": fill["fill_price"],
                "quantity": 5,
                "stop_loss": 490,
                "target": 510,
                "bucket": "conservative",
                "entry_fees": 20,
                "status": "OPEN",
                "entry_time": fill["filled_at"],
            }],
        })

        monitor = PositionMonitor(redis, sqlite, simulator=simulator)
        closed = monitor.check_paper_exits(lambda sym: 0)
        assert len(closed) == 0
        assert len(simulator.open_positions) == 1

    def test_negative_price_skips_exit_check(self):
        """Negative price (error case) should not trigger exit."""
        from tools.order_simulator import OrderSimulator
        from tools.position_monitor import PositionMonitor

        redis = FakeRedis(state={"state:positions": {"positions": []}})
        simulator = OrderSimulator()

        fill = simulator.simulate_fill({
            "symbol": "STOCK",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 500.0,
            "order_type": "LIMIT",
        })
        simulator.open_position(
            fill, direction="LONG", stop_loss=490,
            target=510, bucket="conservative",
        )

        monitor = PositionMonitor(redis, FakeSQLite(), simulator=simulator)
        closed = monitor.check_paper_exits(lambda sym: -100)
        assert len(closed) == 0


# ===========================================================================
# Test 11: Orchestrator packages orders correctly
# ===========================================================================

class TestOrchestratorOrderPackaging:
    """E2E: Orchestrator converts risk decisions to executable orders."""

    def test_conservative_order_auto_packaged(self):
        """Conservative bucket order packaged for execution immediately."""
        redis = FakeRedis(state={
            "state:system_mode": {"mode": "PAPER"},
            "state:active_strategy": {"strategy": "RSI_MEAN_REVERSION"},
            "state:all_agents": {},
        })
        orch = _make_orchestrator(redis=redis)

        risk_approval = {
            "proposal_id": "prop-123",
            "decision": "APPROVED",
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "transaction_type": "BUY",
            "approved_position_size": 5,
            "approved_stop_loss": 2462.5,
            "approved_target": 2550.0,
            "bucket": "conservative",
            "risk_pct_final": 0.01,
            "flag_human": False,
            "checks": {},
        }

        state = _base_state()
        state["approved_orders"] = [risk_approval]
        state = orch.run(state)

        assert len(state["approved_orders"]) == 1
        order = state["approved_orders"][0]
        assert order["symbol"] == "RELIANCE"
        assert order["transaction_type"] == "BUY"
        assert order["quantity"] == 5
        assert order["mode"] == "PAPER"
        assert order["stop_loss_price"] == 2462.5
        assert order["target_price"] == 2550.0
        assert order["approved_by"] == "risk_agent"

    def test_risk_bucket_queued_for_human(self):
        """Risk bucket trades are queued for human approval, not executed."""
        orch = _make_orchestrator()

        risk_approval = {
            "proposal_id": "risk-prop-1",
            "decision": "APPROVED",
            "symbol": "NIFTY-CE",
            "entry_price": 100,
            "transaction_type": "BUY",
            "approved_position_size": 25,
            "approved_stop_loss": 60,
            "approved_target": 150,
            "bucket": "risk",
            "risk_pct_final": 0.02,
            "flag_human": True,
            "checks": {},
        }

        state = _base_state()
        state["approved_orders"] = [risk_approval]
        state = orch.run(state)

        # Risk trades get removed from approved_orders (queued for human)
        assert len(state["approved_orders"]) == 0
        assert "risk-prop-1" in orch._human_approval_pending


# ===========================================================================
# Test 12: Execution agent — position data integrity
# ===========================================================================

class TestPositionDataIntegrity:
    """E2E: Position written to Redis has all required fields."""

    def test_redis_position_has_all_fields(self):
        """Position in Redis includes entry_fees, strategy, and all required data."""
        redis = FakeRedis(state={
            "state:positions": {"positions": []},
        })
        sqlite = FakeSQLite()
        execution = _make_execution_agent(redis=redis, sqlite=sqlite)

        order = {
            "order_id": "test-order",
            "proposal_id": "test-prop",
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 5,
            "price": 2500.0,
            "mode": "PAPER",
            "bucket": "conservative",
            "stop_loss_price": 2462.5,
            "target_price": 2550.0,
            "strategy": "RSI_MEAN_REVERSION",
        }

        state = _base_state()
        state["approved_orders"] = [order]
        execution.run(state)

        positions = redis.get_state("state:positions")["positions"]
        assert len(positions) == 1
        pos = positions[0]

        # All required fields present
        required_fields = [
            "trade_id", "symbol", "direction", "entry_price", "quantity",
            "stop_loss", "target", "bucket", "strategy", "entry_fees",
            "status", "entry_time",
        ]
        for field in required_fields:
            assert field in pos, f"Missing field: {field}"

        assert pos["entry_fees"] == 20
        assert pos["strategy"] == "RSI_MEAN_REVERSION"
        assert pos["status"] == "OPEN"
        assert pos["direction"] == "LONG"

    def test_execution_reloads_positions_on_startup(self):
        """Execution agent reloads open positions from Redis into simulator."""
        redis = FakeRedis(state={
            "state:positions": {
                "positions": [
                    {
                        "trade_id": "existing-1",
                        "order_id": "existing-1",
                        "symbol": "RELIANCE",
                        "direction": "LONG",
                        "entry_price": 2500.0,
                        "quantity": 5,
                        "stop_loss": 2462.5,
                        "target": 2550.0,
                        "bucket": "conservative",
                        "entry_fees": 20,
                        "status": "OPEN",
                        "entry_time": datetime.now(IST).isoformat(),
                    },
                    {
                        "trade_id": "closed-1",
                        "symbol": "HDFCBANK",
                        "status": "CLOSED_TARGET",
                    },
                ],
            },
        })
        execution = _make_execution_agent(redis=redis)
        execution._reload_paper_positions()

        # Only OPEN positions loaded
        assert len(execution.simulator.open_positions) == 1
        assert execution.simulator.open_positions[0]["symbol"] == "RELIANCE"
        assert execution.simulator.open_positions[0]["entry_fees"] == 20


# ===========================================================================
# Test 13: SHORT direction flow
# ===========================================================================

class TestShortDirection:
    """E2E: SHORT signal correctly flows through the pipeline."""

    def test_short_signal_stop_above_entry(self):
        """SHORT signal has stop-loss above entry and target below."""
        redis = FakeRedis(
            state={
                "state:positions": {"positions": []},
                "state:system_mode": {"mode": "PAPER"},
                "state:active_strategy": {"strategy": "RSI_MEAN_REVERSION"},
                "state:all_agents": {},
            },
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=72.0),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        sqlite = FakeSQLite()

        analyst = _make_analyst(redis=redis, sqlite=sqlite)
        state = _base_state()
        state = analyst.run(state)

        assert len(state["pending_signals"]) == 1
        signal = state["pending_signals"][0]
        assert signal["direction"] == "SHORT"
        # For SHORT: stop_loss > entry_price, target < entry_price
        assert signal["stop_loss"] > signal["entry_price"]
        assert signal["target"] < signal["entry_price"]

    def test_short_position_transaction_type_sell(self):
        """SHORT direction → transaction_type = SELL in orchestrator output."""
        redis = FakeRedis(
            state={
                "state:positions": {"positions": []},
                "state:system_mode": {"mode": "PAPER"},
                "state:active_strategy": {"strategy": "RSI_MEAN_REVERSION"},
                "state:all_agents": {},
            },
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data("RELIANCE", 2500, rsi=72.0),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        sqlite = FakeSQLite()

        analyst = _make_analyst(redis=redis, sqlite=sqlite)
        risk = _make_risk_agent(redis=redis, sqlite=sqlite)
        orch = _make_orchestrator(redis=redis, sqlite=sqlite)

        state = _base_state()
        state = analyst.run(state)
        state = risk.run(state)
        state = orch.run(state)

        if state["approved_orders"]:
            order = state["approved_orders"][0]
            assert order["transaction_type"] == "SELL"


# ===========================================================================
# Test 14: P&L calculation correctness
# ===========================================================================

class TestPnLCalculation:
    """E2E: P&L calculations include all fees (brokerage + STT)."""

    def test_stop_loss_pnl_includes_fees(self):
        """P&L on stop-loss exit includes entry brokerage + exit brokerage + STT."""
        from tools.order_simulator import OrderSimulator

        sim = OrderSimulator()

        # Entry
        fill = sim.simulate_fill({
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 10,
            "price": 2500.0,
            "order_type": "LIMIT",
        })
        sim.open_position(
            fill, direction="LONG", stop_loss=2462.5,
            target=2550.0, bucket="conservative",
        )

        # Exit at stop
        exit_fill, reason = sim.check_exits(
            sim.open_positions[0],
            current_price=2450.0,
        )
        assert exit_fill is not None
        result = sim.close_position(fill["order_id"], exit_fill, reason)

        # Verify fees are included
        entry_fee = 20  # brokerage
        exit_fee = 20   # brokerage
        exit_value = exit_fill["fill_price"] * 10
        stt = exit_value * 0.001  # 0.1% STT
        total_fees = entry_fee + exit_fee + stt

        assert result["total_fees"] == pytest.approx(total_fees, abs=0.5)
        # P&L should be negative (loss + fees)
        raw_pnl = (exit_fill["fill_price"] - fill["fill_price"]) * 10
        assert result["pnl"] == pytest.approx(raw_pnl - total_fees, abs=0.5)

    def test_target_hit_pnl_is_positive(self):
        """P&L on target hit is positive even after fees."""
        from tools.order_simulator import OrderSimulator

        sim = OrderSimulator()

        fill = sim.simulate_fill({
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 10,
            "price": 2500.0,
            "order_type": "LIMIT",
        })
        sim.open_position(
            fill, direction="LONG", stop_loss=2462.5,
            target=2550.0, bucket="conservative",
        )

        exit_fill, reason = sim.check_exits(
            sim.open_positions[0],
            current_price=2560.0,
        )
        result = sim.close_position(fill["order_id"], exit_fill, reason)

        assert result["pnl"] > 0
        assert result["exit_reason"] == "CLOSED_TARGET"


# ===========================================================================
# Test 15: Cooldown trigger and recovery
# ===========================================================================

class TestCooldownLifecycle:
    """E2E: Consecutive losses trigger cooldown, which expires after duration."""

    def test_three_losses_trigger_cooldown(self):
        """3 RECORD_LOSS commands → cooldown active → trades blocked."""
        risk = _make_risk_agent()

        # Simulate 3 consecutive losses
        risk._record_loss()
        assert risk._in_cooldown is False
        risk._record_loss()
        assert risk._in_cooldown is False
        risk._record_loss()
        assert risk._in_cooldown is True
        assert risk._cooldown_until is not None

        # New trade should be rejected
        signal = {
            "proposal_id": "test-during-cooldown",
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "stop_loss": 2462.5,
            "target": 2550,
            "quantity_suggested": 5,
            "direction": "LONG",
            "bucket": "conservative",
        }
        state = _base_state()
        state["pending_signals"] = [signal]
        state = risk.run(state)
        assert len(state["rejected_proposals"]) == 1

    def test_cooldown_expires(self):
        """After cooldown duration, trading resumes."""
        risk = _make_risk_agent()

        # Set cooldown to already expired
        risk._in_cooldown = True
        risk._cooldown_until = datetime.now(IST) - timedelta(minutes=1)

        signal = {
            "proposal_id": "test-after-cooldown",
            "symbol": "RELIANCE",
            "entry_price": 2500,
            "stop_loss": 2462.5,
            "target": 2550,
            "quantity_suggested": 5,
            "direction": "LONG",
            "bucket": "conservative",
        }
        state = _base_state()
        state["pending_signals"] = [signal]
        state = risk.run(state)
        assert len(state["approved_orders"]) == 1
        assert risk._in_cooldown is False

    def test_win_resets_consecutive_losses(self):
        """RECORD_WIN resets loss counter, preventing cooldown."""
        risk = _make_risk_agent()

        risk._record_loss()
        risk._record_loss()
        assert risk._consecutive_losses == 2

        # A win resets the counter
        risk._consecutive_losses = 0  # simulates RECORD_WIN command
        risk._record_loss()
        assert risk._in_cooldown is False


# ===========================================================================
# Test 16: Strong signal skips LLM validation
# ===========================================================================

class TestStrongSignalSkipsLLM:
    """E2E: Very strong rule-based signals skip LLM validation to save tokens."""

    def test_extreme_rsi_oversold_skips_llm(self):
        """RSI < 25 with volume > 1.5x → LLM skipped, confidence set to HIGH."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data(
                    "RELIANCE", 2500, rsi=22.0, volume_ratio=2.0,
                ),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        state = _base_state()
        state = analyst.run(state)

        assert len(state["pending_signals"]) == 1
        signal = state["pending_signals"][0]
        assert signal["signal_confidence"] == "HIGH"
        assert "Strong rule-based signal" in signal["analyst_note"]
        # LLM should NOT have been called
        analyst.call_llm.assert_not_called()

    def test_extreme_rsi_overbought_skips_llm(self):
        """RSI > 75 with volume > 1.5x → SHORT signal, LLM skipped."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data(
                    "RELIANCE", 2500, rsi=78.0, volume_ratio=1.8,
                ),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        state = _base_state()
        state = analyst.run(state)

        assert len(state["pending_signals"]) == 1
        assert state["pending_signals"][0]["direction"] == "SHORT"
        analyst.call_llm.assert_not_called()

    def test_moderate_rsi_still_calls_llm(self):
        """RSI 28 (oversold but not extreme) → LLM still called."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data(
                    "RELIANCE", 2500, rsi=28.0, volume_ratio=1.5,
                ),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        state = _base_state()
        state = analyst.run(state)

        assert len(state["pending_signals"]) == 1
        analyst.call_llm.assert_called_once()

    def test_strong_rsi_but_low_volume_still_calls_llm(self):
        """RSI < 25 but volume < 1.5x → not strong enough, LLM called."""
        redis = FakeRedis(
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data(
                    "RELIANCE", 2500, rsi=22.0, volume_ratio=1.3,
                ),
                "data:market_snapshot": {"nifty": {}},
            },
        )
        analyst = _make_analyst(redis=redis)
        state = _base_state()
        state = analyst.run(state)

        assert len(state["pending_signals"]) == 1
        analyst.call_llm.assert_called_once()

    def test_strong_signal_full_flow_to_execution(self):
        """Strong signal → skip LLM → risk approved → position opened."""
        redis = FakeRedis(
            state={
                "state:positions": {"positions": []},
                "state:system_mode": {"mode": "PAPER"},
                "state:active_strategy": {"strategy": "RSI_MEAN_REVERSION"},
                "state:all_agents": {},
            },
            market_data={
                "data:watchlist_ticks:RELIANCE": _tick_data(
                    "RELIANCE", 2500, rsi=20.0, volume_ratio=2.5,
                ),
                "data:market_snapshot": {"nifty": {"ltp": 22000}},
            },
        )
        sqlite = FakeSQLite()

        analyst = _make_analyst(redis=redis, sqlite=sqlite)
        risk = _make_risk_agent(redis=redis, sqlite=sqlite)
        orch = _make_orchestrator(redis=redis, sqlite=sqlite)
        execution = _make_execution_agent(redis=redis, sqlite=sqlite)

        state = _base_state()
        state = analyst.run(state)
        state = risk.run(state)
        state = orch.run(state)
        state = execution.run(state)

        # Analyst skipped LLM, risk still called LLM (1 call total, not 2)
        analyst.call_llm.assert_not_called()
        risk.call_llm.assert_called_once()

        # Position opened
        positions = redis.get_state("state:positions")["positions"]
        assert len(positions) == 1
        assert positions[0]["symbol"] == "RELIANCE"
