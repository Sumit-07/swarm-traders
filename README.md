# Swarm Traders

Multi-agent AI trading system for Indian markets (NSE/BSE). Eight coordinated AI agents manage two capital buckets — conservative (swing/intraday) and risk (event-driven options) — with strict risk management, human-in-the-loop approval, and full audit trails.

## How It Works

```
Data Agent → Strategist → Analyst → Risk Agent → Orchestrator → Execution Agent → Compliance Agent
```

Every morning, the **Strategist** picks a trading strategy based on market regime (VIX, trend, FII flows). The **Analyst** scans a watchlist for entry signals using technical indicators. The **Risk Agent** reviews every proposal through 5 checks (position size, daily loss budget, max positions, cooldown, stop-loss logic). Only the **Orchestrator** can forward approved orders to the **Execution Agent**. The **Compliance Agent** audits everything at end of day.

## Agent Roster

| Agent | LLM | Role |
|---|---|---|
| Orchestrator | GPT-4o | Master coordinator, conflict resolver, Telegram interface |
| Strategist | GPT-4o | Morning market regime detection, conservative strategy selection |
| Risk Strategist | GPT-4o | Risk bucket strategy selection (options-focused) |
| Data Agent | Gemini Flash | Market data ingestion, news summarization |
| Analyst | GPT-4o mini | Executes strategy config, generates trade signals |
| Risk Agent | GPT-4o mini | Position sizing, stop-loss, drawdown guard |
| Execution Agent | GPT-4o mini | Order placement (Fyers API live, simulator for paper) |
| Compliance Agent | Gemini Flash | SEBI rules, audit trail, EOD reports |

## Quick Start

### Prerequisites

- Python 3.10+
- Redis
- API keys: OpenAI, Google AI (Gemini), Fyers (broker), Telegram bot

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
pytest tests/                          # full suite (115 tests)
pytest tests/test_indicators.py        # single file
pytest tests/test_backtesting.py -v    # verbose
```

## Project Structure

```
swarm-traders/
├── agents/                  # 8 AI agents, each with soul.md, prompts.md, implementation
│   ├── base_agent.py        # Abstract base class (lifecycle, messaging, LLM calls)
│   ├── orchestrator/        # Master coordinator
│   ├── strategist/          # Conservative strategy selection
│   ├── risk_strategist/     # Risk bucket strategy selection
│   ├── data_agent/          # Market data ingestion
│   ├── analyst/             # Signal generation
│   ├── risk_agent/          # Trade proposal review (last gatekeeper)
│   ├── execution_agent/     # Order placement
│   └── compliance_agent/    # Audit and rule enforcement
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
│   ├── state.py             # Shared state TypedDict
│   └── edges.py             # Conditional routing
├── memory/                  # Storage layer
│   ├── redis_store.py       # Redis wrapper (pub/sub, shared state, market data)
│   ├── sqlite_store.py      # SQLite wrapper (trades, signals, audit trail)
│   └── schema.sql           # 7 tables with indexes
├── scheduler/               # Daily schedule
│   └── job_scheduler.py     # APScheduler IST schedule (06:55-17:15)
├── tools/                   # Shared utilities
│   ├── broker.py            # Fyers API wrapper
│   ├── market_data.py       # Two-tier data (Fyers primary, yfinance fallback)
│   ├── indicators.py        # Technical indicators (RSI, MACD, VWAP, ATR, etc.)
│   ├── llm.py               # LLM provider (OpenAI + Gemini routing)
│   └── order_simulator.py   # Paper trading engine
├── config.py                # Central config (risk limits, strategies, schedules)
├── main.py                  # Entry point
└── tests/                   # 115 tests
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
- **SQLite** — Persistent audit trail (7 tables: trades, signals, daily_pnl, agent_messages, orchestrator_log, compliance_audit, data_log)
- **LangGraph** — 4 scheduled sub-graphs: morning strategy, intraday signal loop (every 5 min), force close, EOD review

## Dashboard

Run `streamlit run dashboard/app.py` to access:

1. **Positions** — Live open positions, capital utilization, market overview
2. **Agent Status** — Heartbeat health, state, LLM call count for all 8 agents
3. **P&L** — Daily/cumulative P&L charts, drawdown visualization
4. **Trade Log** — Searchable table with date/status filters
5. **Backtest Results** — HTML report viewer, strategy comparison table

## Implementation Status

| Phase | Status | Description |
|---|---|---|
| 1. Foundation | Done | Config, logging, Redis/SQLite, market data, indicators |
| 2. Agent Scaffold | Done | 8 agents, Redis comms, LangGraph, Telegram, scheduler |
| 3. Backtesting | Done | Simulator, metrics, runner, HTML reports |
| 4. LLM Integration | Done | OpenAI/Gemini routing, prompt rendering, all agents wired |
| 5. Paper Trading | Done | Dashboard, enhanced simulator, position lifecycle |
| 6. Live Trading | Pending | Fyers live orders, cautious deployment |

## Tech Stack

Python 3.10+ | LangGraph | LangChain | Fyers API | yfinance | Redis | SQLite | SQLAlchemy | APScheduler | python-telegram-bot | Streamlit | Plotly | pandas-ta | Pydantic | Loguru

## License

Private — not open source.
