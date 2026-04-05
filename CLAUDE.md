# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-agent AI trading system for Indian markets (NSE/BSE) running on Mac M1 Pro. Two capital buckets:
- **Conservative** (₹20k-30k): Swing trades, intraday, weekly options buying — managed by 7 coordinated agents
- **Risk** (₹10k/month fixed): Event-driven options, expiry plays, momentum — managed by Risk Strategist feeding into the same Analyst/Execution pipeline

The full system design spec is in `trading_swarm_design.md` — read it before implementing anything. Each agent has a `soul.md` (identity/reasoning style), `agent.md` (technical spec), and `prompts.md` (all LLM prompts).

## Commands

```bash
# Setup
brew install redis && brew services start redis
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in API keys

# Run system (starts in PAPER mode)
python main.py

# Verify setup (Phase 1+2 checks)
python main.py --verify

# Dashboard (separate terminal)
streamlit run dashboard/app.py

# Backtesting
python -m backtesting.runner --strategy RSI_MEAN_REVERSION --start 2024-06-01 --end 2024-12-31 --report

# Tests
pytest tests/                          # full suite (148 tests)
pytest tests/test_indicators.py        # single test file
pytest tests/test_live_trading.py -v   # live trading tests
```

## Architecture

### Agent Roster (8 agents)
| Agent | LLM | Role |
|---|---|---|
| `orchestrator` | GPT-4o | Master coordinator, conflict resolver, Telegram interface |
| `strategist` | GPT-4o | Morning market regime detection, conservative strategy selection |
| `risk_strategist` | GPT-4o | Risk bucket strategy selection (options-focused) |
| `data_agent` | Gemini Flash | Market data ingestion, news summarization (no opinions) |
| `analyst` | GPT-4o mini | Executes strategy config, generates trade signals |
| `risk_agent` | GPT-4o mini | Position sizing, stop-loss, drawdown guard — last gatekeeper |
| `execution_agent` | GPT-4o mini | Order placement (Fyers API live, simulator for paper) |
| `compliance_agent` | Gemini Flash | SEBI rules, audit trail, EOD reports |

### Data Flow
```
Data Agent → Strategist/Risk Strategist → Analyst → Risk Agent → Orchestrator → Execution Agent → Compliance Agent
```
No agent may send directly to Execution Agent except Orchestrator. All trade proposals must pass through Risk Agent before execution.

### Communication Backbone
- **Redis pub/sub**: Real-time agent-to-agent messaging via `channel:<agent_id>` channels
- **Redis hash**: Shared state (`state:positions`, `state:system_mode`, `data:*` keys)
- **SQLite**: Persistent trade log, signal log, daily P&L, audit trail
- **LangGraph**: Defines agent graph topology (nodes, edges, conditional routing) in `graph/`

### Key Directories
- `agents/`: Each agent has its own directory with `soul.md`, `agent.md`, `prompts.md`, and `.py` implementation. All inherit from `base_agent.py`.
- `tools/`: Broker API wrapper (`broker.py` — Kite Connect live orders), market data router (`market_data.py`), Kite auth (`kite_auth.py` — telegram + TOTP modes), Kite market data (`kite_market_data.py`), Kite order placement (`kite_broker.py`), WebSocket ticker (`kite_ticker.py`), yfinance fallback, indicators (pure Python, NO TA-Lib), LLM provider (`llm.py` — OpenAI/Gemini routing), order simulator, position monitor
- `graph/`: LangGraph graph definition (`swarm_graph.py`), shared state TypedDict (`state.py`), conditional edges (`edges.py`)
- `memory/`: Redis and SQLite wrappers, DB schema
- `comms/`: Telegram bot for human interface
- `dashboard/`: Streamlit app with 5 pages (positions, agent status, P&L, trade log, backtest results)

## Critical Rules

### Risk Management (hardcoded in `config.py`, non-negotiable)
- Max 2% of capital risk per trade
- Max 5% daily portfolio drawdown → mandatory halt
- Max ₹2,500 per single options trade
- 3 consecutive losses → 1 hour cooldown
- Averaging down is NEVER permitted
- Intraday positions force-close at 3:20 PM IST
- No new trades after 3:00 PM IST
- Options down 60% from entry → mechanical close
- System always starts in PAPER mode

### Human Approval
- First 30 days: ALL trades require human Telegram approval
- After day 30: only auto-approve trades < ₹3,000 with HIGH confidence and no active violations

### Backtest Gate Criteria (must pass ALL before paper trading)
Win rate ≥ 42%, Profit factor ≥ 1.3, Sharpe ≥ 0.8, Max drawdown ≤ 18%, Max consecutive losses ≤ 6, Min 30 trades

### Simulation Rules (prevent look-ahead bias)
- Entry on next bar open (not current bar close)
- 0.05% slippage on entry/exit
- ₹20 flat brokerage per order
- No signals before 9:15 AM or after 3:20 PM

### Mode Switching
- System always starts in PAPER mode (hardcoded default)
- Switch to LIVE via Telegram `/live confirm` — requires explicit confirmation
- Initial live cap: ₹8,000 (enforced in orchestrator)
- Switch back via `/paper` at any time
- All mode transitions logged to SQLite audit trail

### Position Monitoring
- `tools/position_monitor.py` reconciles broker positions with Redis state
- Detects discrepancies: quantity mismatch, broker-only positions, local-only positions
- Paper positions checked for stop/target/time exits every cycle
- Force-close-all at 3:20 PM for both paper and live positions

## Implementation Phases

All 6 phases are complete:
1. **Foundation** — Data pipeline, indicators, Redis/SQLite (no LLMs)
2. **Agent scaffold** — All 8 agents as classes, Redis comms, LangGraph, Telegram (no LLM calls)
3. **Backtesting** — Backtest all strategies, identify 2-3 that pass gate criteria
4. **LLM integration** — Wire up prompts, full morning-to-close flow in paper mode
5. **Paper trading** — Full trading days, dashboard, 7 consecutive days stable
6. **Live trading** — Fyers live orders, position monitoring, mode switching with safety guards

## Tech Stack

Python 3.10+, LangGraph/LangChain, langchain-openai, langchain-google-genai, Kite Connect (Zerodha broker), yfinance (fallback data), Redis, SQLite/SQLAlchemy, APScheduler, python-telegram-bot, Streamlit/Plotly (dashboard), pyotp (TOTP auto-login), Pydantic, Loguru
