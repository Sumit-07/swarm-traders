"""Trading Agent Swarm — Entry Point.

Phase 2: Starts all 8 agents, verifies communication, and can run
the morning strategy graph.

Usage:
    python main.py              # Start swarm (interactive)
    python main.py --verify     # Run Phase 1+2 verification only
"""

import argparse
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from config import (
    AGENT_IDS,
    CAPITAL,
    DATA_DIR,
    KITE_API_KEY,
    KITE_API_SECRET,
    KITE_REDIRECT_URI,
    LOGS_DIR,
    REDIS_HOST,
    REDIS_PORT,
    RISK_LIMITS,
    SQLITE_DB_PATH,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TRADING_MODE,
)


def create_stores():
    """Initialize Redis and SQLite stores."""
    from memory.redis_store import RedisStore
    from memory.sqlite_store import SQLiteStore

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for subdir in ["agent_logs", "trade_logs", "error_logs"]:
        (LOGS_DIR / subdir).mkdir(parents=True, exist_ok=True)

    redis_store = RedisStore(host=REDIS_HOST, port=REDIS_PORT)
    if not redis_store.ping():
        print("ERROR: Redis not available. Run: brew services start redis")
        sys.exit(1)

    sqlite_store = SQLiteStore(SQLITE_DB_PATH)
    return redis_store, sqlite_store


def create_agents(redis_store, sqlite_store):
    """Create all 10 agent instances."""
    from tools.broker import KiteBroker
    from tools.market_data import MarketDataProvider
    from agents.orchestrator.orchestrator import OrchestratorAgent
    from agents.strategist.strategist import StrategistAgent
    from agents.risk_strategist.risk_strategist import RiskStrategistAgent
    from agents.data_agent.data_agent import DataAgent
    from agents.analyst.analyst import AnalystAgent
    from agents.risk_agent.risk_agent import RiskAgent
    from agents.execution_agent.execution_agent import ExecutionAgent
    from agents.compliance_agent.compliance_agent import ComplianceAgent
    from agents.optimizer.optimizer import OptimizerAgent
    from agents.position_monitor.position_monitor import PositionMonitorAgent
    from comms.telegram_bot import TelegramBot

    # Telegram bot
    telegram = TelegramBot(
        token=TELEGRAM_BOT_TOKEN,
        chat_id=TELEGRAM_CHAT_ID,
        redis_store=redis_store,
    )

    # Broker (optional — only if Kite credentials are configured)
    broker = None
    if KITE_API_KEY and KITE_API_SECRET:
        broker = KiteBroker(KITE_API_KEY, KITE_API_SECRET, KITE_REDIRECT_URI)

    # Market data provider (backward compat wrapper)
    market_data = MarketDataProvider(sqlite_store=sqlite_store)

    # Create agents
    agents = {
        "orchestrator": OrchestratorAgent(
            redis_store, sqlite_store, telegram, broker=broker,
        ),
        "strategist": StrategistAgent(redis_store, sqlite_store),
        "risk_strategist": RiskStrategistAgent(redis_store, sqlite_store),
        "data_agent": DataAgent(redis_store, sqlite_store, market_data),
        "analyst": AnalystAgent(redis_store, sqlite_store),
        "risk_agent": RiskAgent(redis_store, sqlite_store),
        "execution_agent": ExecutionAgent(
            redis_store, sqlite_store, broker=broker,
        ),
        "compliance_agent": ComplianceAgent(redis_store, sqlite_store),
        "optimizer": OptimizerAgent(redis_store, sqlite_store),
        "position_monitor": PositionMonitorAgent(redis_store, sqlite_store),
    }

    return agents, telegram


def create_graphs(agents, redis_store=None, sqlite_store=None):
    """Build all LangGraph sub-graphs."""
    from graph.swarm_graph import (
        build_morning_graph,
        build_signal_graph,
        build_force_close_graph,
        build_eod_graph,
    )
    from graph.meeting_subgraph import build_meeting_graph

    graphs = {
        "morning": build_morning_graph(
            agents["data_agent"], agents["strategist"],
            agents["risk_strategist"], agents["orchestrator"],
        ),
        "signal": build_signal_graph(
            agents["data_agent"], agents["analyst"],
            agents["risk_agent"], agents["orchestrator"],
            agents["execution_agent"], agents["compliance_agent"],
        ),
        "force_close": build_force_close_graph(
            agents["risk_agent"], agents["orchestrator"],
            agents["execution_agent"],
        ),
        "eod": build_eod_graph(
            agents["compliance_agent"], agents["strategist"],
            agents["orchestrator"],
        ),
    }
    if sqlite_store and redis_store:
        graphs["meeting"] = build_meeting_graph(sqlite_store, redis_store)
    return graphs


def verify():
    """Run Phase 1 + Phase 2 verification."""
    print("=" * 60)
    print("Trading Agent Swarm — Verification")
    print(f"Time: {datetime.now(IST).isoformat()}")
    print(f"Mode: {TRADING_MODE}")
    print("=" * 60)

    # Phase 1 checks
    redis_store, sqlite_store = create_stores()
    print("\n[1/5] Redis: CONNECTED")
    print(f"[2/5] SQLite: INITIALIZED at {SQLITE_DB_PATH}")

    # Phase 2: Create agents
    print("\n[3/5] Creating agents...")
    agents, telegram = create_agents(redis_store, sqlite_store)
    for agent_id in AGENT_IDS:
        print(f"  {agent_id}: CREATED")

    # Start all agents
    print("\n[4/5] Starting agents...")
    for agent_id, agent in agents.items():
        try:
            agent.start()
            print(f"  {agent_id}: STARTED")
        except Exception as e:
            print(f"  {agent_id}: FAILED ({e})")

    # Trigger immediate heartbeats (scheduled ones run every 60s)
    for agent in agents.values():
        agent._send_heartbeat()
    time.sleep(0.5)

    # Check heartbeats in Redis
    print("\n[5/5] Checking heartbeats...")
    all_ok = True
    for agent_id in AGENT_IDS:
        hb = redis_store.get_state(f"agent:{agent_id}:heartbeat")
        if hb:
            print(f"  {agent_id}: {hb.get('state', '?')} "
                  f"(last: {hb.get('last_action', '?')})")
        else:
            print(f"  {agent_id}: NO HEARTBEAT")
            all_ok = False

    # Check agent registry
    all_agents = redis_store.get_state("state:all_agents") or {}
    print(f"\n  Registered agents: {len(all_agents)}/{len(AGENT_IDS)}")

    # Test message routing
    print("\n  Testing message routing...")
    from agents.message import MessageType
    try:
        agents["data_agent"].send_message(
            to_agent="orchestrator",
            msg_type=MessageType.HEARTBEAT,
            payload={"test": True},
        )
        print("  data_agent -> orchestrator: OK")
    except Exception as e:
        print(f"  data_agent -> orchestrator: FAILED ({e})")

    # Test routing validation (should fail)
    try:
        agents["analyst"].send_message(
            to_agent="execution_agent",
            msg_type=MessageType.COMMAND,
            payload={"test": True},
        )
        print("  analyst -> execution_agent: SHOULD HAVE FAILED!")
        all_ok = False
    except Exception:
        print("  analyst -> execution_agent: CORRECTLY BLOCKED")

    # Stop agents
    for agent in agents.values():
        agent.stop()

    print("\n" + "=" * 60)
    if all_ok:
        print("Phase 2 Verification COMPLETE — All checks passed")
    else:
        print("Phase 2 Verification COMPLETE — Some checks failed")
    print("=" * 60)


def run_swarm():
    """Start the full trading swarm with scheduler."""
    print("=" * 60)
    print("Trading Agent Swarm — Starting")
    print(f"Time: {datetime.now(IST).isoformat()}")
    print(f"Mode: {TRADING_MODE}")
    print("=" * 60)

    redis_store, sqlite_store = create_stores()
    agents, telegram = create_agents(redis_store, sqlite_store)
    graphs = create_graphs(agents, redis_store, sqlite_store)

    # Start all agents
    print("\nStarting agents...")
    for agent_id, agent in agents.items():
        agent.start()
        print(f"  {agent_id}: STARTED")

    # Start Telegram bot
    telegram.start()

    # Start scheduler
    from scheduler.job_scheduler import SwarmScheduler
    scheduler = SwarmScheduler(agents, graphs, telegram)
    scheduler.start()

    # Give orchestrator a reference so /catchup command works
    agents["orchestrator"].swarm_scheduler = scheduler

    telegram.send_message(
        f"Trading system online.\n"
        f"Mode: {TRADING_MODE}\n"
        f"Agents: {len(agents)} active\n"
        f"Time: {datetime.now(IST).isoformat()}"
    )

    print("\nSwarm is running. Press Ctrl+C to stop.")

    # Graceful shutdown
    def shutdown(signum, frame):
        print("\nShutting down...")
        scheduler.stop()
        for agent in agents.values():
            agent.stop()
        telegram.send_message("Trading system shutting down.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep main thread alive
    while True:
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Trading Agent Swarm")
    parser.add_argument("--verify", action="store_true",
                        help="Run verification checks only")
    args = parser.parse_args()

    if args.verify:
        verify()
    else:
        run_swarm()


if __name__ == "__main__":
    main()
