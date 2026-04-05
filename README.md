# Swarm Traders

Multi-agent AI trading system for Indian markets (NSE/BSE). Ten coordinated AI agents manage two capital buckets — conservative (swing/intraday) and risk (event-driven options) — with strict risk management, human-in-the-loop approval, and full audit trails.

## How It Works

Every morning, the **Strategist** picks a trading strategy based on market regime (VIX, trend, FII flows). The **Analyst** scans a watchlist for entry signals using technical indicators. The **Risk Agent** reviews every proposal through 5 checks (position size, daily loss budget, max positions, cooldown, stop-loss logic). Only the **Orchestrator** can forward approved orders to the **Execution Agent**. The **Compliance Agent** audits everything at end of day. During market hours, the **Position Monitor** watches all open positions every 5 minutes against strategy-aware thresholds and escalates to the Orchestrator for a 3-step LLM review (Analyst thesis check → Risk Agent recommendation → final decision). After market close, the **Optimizer** runs a structured meeting to extract learnings that improve next-day decisions.

## Agent Roster

| Agent | LLM | Role |
|---|---|---|
| Orchestrator | GPT-4o | Master coordinator, conflict resolver, Telegram interface |
| Strategist | GPT-4o | Morning market regime detection, conservative strategy selection |
| Risk Strategist | GPT-4o | Risk bucket strategy selection (options-focused) |
| Data Agent | Gemini Flash | Market data ingestion, news summarization |
| Analyst | GPT-4o mini | Executes strategy config, generates trade signals |
| Risk Agent | GPT-4o mini | Position sizing, stop-loss, drawdown guard |
| Execution Agent | GPT-4o mini | Order placement (Kite Connect live, simulator for paper) |
| Compliance Agent | Gemini Flash | SEBI rules, audit trail, EOD reports |
| Optimizer | GPT-4o | Post-market learning — 3-round meeting, knowledge graph |
| Position Monitor | None | Pure Python position watchdog — threshold alerts to Orchestrator |

## Agent Hierarchy & Communication

```
                              ┌─────────────┐
                              │  Human      │
                              │  (Telegram)  │
                              └──────┬───────┘
                                     │ commands / approvals
                                     ▼
                            ┌─────────────────┐
                    ┌───────│  ORCHESTRATOR    │───────┐
                    │       │  (central hub)   │       │
                    │       └────────┬─────────┘       │
                    │                │                  │
        ┌───────────┼────────────────┼──────────────────┼───────────┐
        │           │                │                  │           │
        ▼           ▼                ▼                  ▼           ▼
  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌────────────────┐ ┌───────────┐
  │   DATA    │ │STRATEGIST │ │  ANALYST   │ │   EXECUTION    │ │COMPLIANCE │
  │   AGENT   │ │           │ │            │ │     AGENT      │ │   AGENT   │
  └─────┬─────┘ └───────────┘ └─────┬──────┘ └────────┬───────┘ └───────────┘
        │                           │                  │
        │        ┌───────────┐      │         ┌───────────┐  ┌───────────────┐
        └───────▶│   RISK    │◀─────┘         │ OPTIMIZER │  │   POSITION    │
                 │STRATEGIST │                │ (post-mkt) │  │   MONITOR    │
                 └───────────┘                └───────────┘  │ (pure Python) │
                        ▲                                    └───────────────┘
                        │                                      alerts ▲ to
                 ┌───────────┐                                 Orchestrator
                 │   RISK    │
                 │   AGENT   │
                 └───────────┘
```

### Communication Rules

All messaging goes through Redis pub/sub with strict routing validation. An agent can only send to the targets listed below — any other path is blocked at runtime.

| Agent | Can Send To |
|---|---|
| **Orchestrator** | All agents (central hub) |
| **Data Agent** | Orchestrator, Strategist, Risk Strategist |
| **Strategist** | Orchestrator |
| **Risk Strategist** | Orchestrator |
| **Analyst** | Risk Agent, Orchestrator |
| **Risk Agent** | Orchestrator |
| **Execution Agent** | Orchestrator, Compliance Agent |
| **Compliance Agent** | Orchestrator |
| **Optimizer** | Orchestrator |
| **Position Monitor** | Orchestrator |

**Key constraints:** Only the Orchestrator can message the Execution Agent. No agent can place trades directly. The Position Monitor makes zero LLM calls — it only detects threshold breaches and escalates to Orchestrator.

### Daily Schedule (IST)

```
06:55  System startup — all agents initialized
07:00  Data Agent wakes — fetches market data, news, FII/DII flows
08:00  Strategists wake — morning strategy selection graph runs
09:00  Trading agents wake — Analyst, Risk Agent, Execution Agent
09:15  ── Market opens ──────────────────────────────────────────
09:15  Signal loop begins (every 5 minutes)
       Data Agent → Analyst → Risk Agent → Orchestrator → Execution
09:15  Position Monitor begins (every 5 minutes until 15:20)
       Checks open positions against strategy-aware thresholds
       Alerts → Orchestrator 3-step review (Analyst + Risk + Decision)
15:00  Last signal check
15:20  Force close all intraday positions
15:30  ── Market closes ─────────────────────────────────────────
15:30  EOD review — Compliance Agent audit
15:45  Strategy review — Strategist evaluates day's performance
15:50  Optimizer meeting — 3-round structured review, knowledge graph
17:15  System sleep — all agents enter idle mode
```

### Optimizer Meeting Flow

The Optimizer runs a post-market meeting at 3:50 PM with the Strategist, Risk Strategist, and Analyst (3 rounds, 10 LLM calls total):

```
Round 1 — Self-review: each agent reviews their own decisions independently
Round 2 — Cross-agent patterns: each agent sees all Round 1 responses
Round 3 — Commitments: each agent commits to ONE specific measurable change
Synthesis — Optimizer extracts learnings → knowledge graph + Telegram report
```

Learnings are scored by confidence, reinforcement count, and recency. Each morning, agents load relevant learnings into their system prompts via `load_memories()`.

## Quick Start

### Prerequisites

- Python 3.10+
- Redis
- API keys: OpenAI, Google AI (Gemini), Kite Connect (Zerodha broker), Telegram bot

### Setup

```bash
# Install Redis
brew install redis && brew services start redis

# Create virtual environment
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### Run

```bash
# Start the trading system (paper mode by default)
python main.py

# Verify setup (Phase 1+2 checks)
python main.py --verify

# Launch dashboard (separate terminal)
streamlit run dashboard/app.py
```

### Backtesting

```bash
# Run a single strategy backtest
python -m backtesting.runner --strategy RSI_MEAN_REVERSION \
    --start 2024-06-01 --end 2024-12-31 --report

# Available strategies: RSI_MEAN_REVERSION, VWAP_REVERSION,
# OPENING_RANGE_BREAKOUT, SWING_MOMENTUM, NIFTY_OPTIONS_BUYING
```

### Tests

```bash
pytest tests/                          # full suite (179 tests)
pytest tests/test_indicators.py        # single file
pytest tests/test_optimizer.py -v      # optimizer/knowledge graph tests
```

## Project Structure

```
swarm-traders/
├── agents/                  # 10 AI agents, each with soul.md, prompts.md, implementation
│   ├── base_agent.py        # Abstract base class (lifecycle, messaging, LLM calls)
│   ├── orchestrator/        # Master coordinator
│   ├── strategist/          # Conservative strategy selection
│   ├── risk_strategist/     # Risk bucket strategy selection
│   ├── data_agent/          # Market data ingestion
│   ├── analyst/             # Signal generation
│   ├── risk_agent/          # Trade proposal review (last gatekeeper)
│   ├── execution_agent/     # Order placement
│   ├── compliance_agent/    # Audit and rule enforcement
│   ├── optimizer/           # Post-market learning (3-round meeting)
│   └── position_monitor/    # Pure Python position watchdog (zero LLM calls)
├── backtesting/             # Backtest framework
│   ├── data_loader.py       # Historical data (yfinance with caching)
│   ├── simulator.py         # Order fill simulation (anti-look-ahead-bias)
│   ├── metrics.py           # 22 performance metrics + gate criteria
│   └── runner.py            # Strategy runner with HTML reports
├── comms/                   # Human interface
│   ├── telegram_bot.py      # Telegram bot (commands, approvals)
│   └── message_templates.py # Message formatting
├── dashboard/               # Streamlit dashboard
│   ├── app.py               # Main entry point
│   ├── data_helpers.py      # Redis/SQLite data fetching
│   └── pages/               # 5 pages (positions, agents, P&L, trades, backtests)
├── graph/                   # LangGraph workflow
│   ├── swarm_graph.py       # 4 sub-graphs (morning, signal loop, force close, EOD)
│   ├── meeting_subgraph.py  # Optimizer 3-round meeting graph
│   ├── state.py             # Shared state TypedDict
│   └── edges.py             # Conditional routing
├── memory/                  # Storage layer
│   ├── redis_store.py       # Redis wrapper (pub/sub, shared state, market data)
│   ├── sqlite_store.py      # SQLite wrapper (trades, signals, audit trail)
│   ├── knowledge_graph.py   # Optimizer learnings (write, load, reinforce, archive)
│   └── schema.sql           # 11 tables with indexes
├── scheduler/               # Daily schedule
│   └── job_scheduler.py     # APScheduler IST schedule (06:55-17:15)
├── tools/                   # Shared utilities
│   ├── broker.py            # Kite Connect API wrapper
│   ├── market_data.py       # Two-tier data (Kite primary, yfinance fallback)
│   ├── indicators.py        # Technical indicators (RSI, MACD, VWAP, ATR, etc.)
│   ├── llm.py               # LLM provider (OpenAI + Gemini routing)
│   └── order_simulator.py   # Paper trading engine
├── config.py                # Central config (risk limits, strategies, schedules)
├── main.py                  # Entry point
└── tests/                   # 179 tests
```

## Risk Management

All risk rules are hardcoded in `config.py` and cannot be overridden by any agent or LLM:

- **2% max risk per trade** — position sized to risk no more than 2% of capital to stop-loss
- **5% daily drawdown halt** — system automatically halts if daily loss exceeds 5%
- **3 consecutive losses** — triggers 1-hour cooldown, no new trades
- **No averaging down** — never permitted under any circumstances
- **3:20 PM force close** — all intraday positions closed before market end
- **Options 60% stop** — mechanical close if option premium drops 60%
- **Paper mode by default** — system always starts in PAPER mode

## Communication Architecture

- **Redis pub/sub** — Real-time agent-to-agent messaging with strict routing validation (only Orchestrator can message Execution Agent)
- **Redis hash** — Shared mutable state (positions, system mode, market data with 120s TTL)
- **SQLite** — Persistent audit trail (11 tables: trades, signals, daily_pnl, agent_messages, orchestrator_log, compliance_audit, data_log, learnings, optimizer_meetings, monitor_alerts, monitor_ticks)
- **LangGraph** — 5 scheduled sub-graphs: morning strategy, intraday signal loop (every 5 min), force close, EOD review, optimizer meeting

## Dashboard

Run `streamlit run dashboard/app.py` to access:

1. **Positions** — Live open positions, capital utilization, market overview
2. **Agent Status** — Heartbeat health, state, LLM call count for all 10 agents
3. **P&L** — Daily/cumulative P&L charts, drawdown visualization
4. **Trade Log** — Searchable table with date/status filters
5. **Backtest Results** — HTML report viewer, strategy comparison table

## Implementation Status

| Phase | Status | Description |
|---|---|---|
| 1. Foundation | Done | Config, logging, Redis/SQLite, market data, indicators |
| 2. Agent Scaffold | Done | 10 agents, Redis comms, LangGraph, Telegram, scheduler |
| 3. Backtesting | Done | Simulator, metrics, runner, HTML reports |
| 4. LLM Integration | Done | OpenAI/Gemini routing, prompt rendering, all agents wired |
| 5. Paper Trading | Done | Dashboard, enhanced simulator, position lifecycle |
| 6. Live Trading | Done | Fyers live orders, position monitoring, mode switching |

## Tech Stack

Python 3.10+ | LangGraph | LangChain | Kite Connect (Zerodha) | yfinance | Redis | SQLite | SQLAlchemy | APScheduler | python-telegram-bot | Streamlit | Plotly | pyotp | Pydantic | Loguru

## License

Private — not open source.
