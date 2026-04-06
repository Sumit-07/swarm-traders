# Risk Strategist — Technical Specification

## Trigger conditions
1. 8:00 AM — Morning risk strategy selection (after Data Agent publishes pre-market data)
2. On-demand: when Orchestrator requests risk strategy re-evaluation
3. When monthly allocation status changes (trade closed, new capital available)

## Inputs
- Redis: `data:market_snapshot` (Nifty, BankNifty, VIX from Data Agent)
- Redis: `data:options_chain` (current options chain from Data Agent)
- Redis: `data:economic_calendar` (upcoming events from Data Agent)
- Redis: `data:fii_flow` (FII options data from Data Agent)
- Redis: `state:positions` (current open risk bucket positions)
- SQLite: `trades` table (monthly allocation tracking, P&L history)

## Outputs
- Redis: `state:risk_strategy` (today's risk bucket strategy configuration as JSON)
- Redis: `channel:orchestrator` (risk strategy proposal for approval)
- Redis: `channel:analyst` (risk strategy config for Analyst Agent to execute)

## Tools available
- `redis_store.read(key)` / `redis_store.write(key, value)`
- `redis_store.publish(channel, message)`
- `sqlite_store.query(sql)`
- `options_chain.get_chain(symbol, expiry)`

## LLM usage
- Model: GPT-4o
- Call LLM when: selecting risk strategy, evaluating event-driven setups
- Use pure Python when: checking allocation limits, reading options chain data, 
  calculating risk/reward ratios
- Max tokens: 2500 input / 600 output

## Strategy library
1. EVENT_OPTIONS — Buy call/put 2-3 days before major event, exit same day as event
2. EXPIRY_DIRECTIONAL — ATM/OTM option on Tuesday-Thursday with momentum, same-day exit
3. MOMENTUM_EQUITY — Stock breakout, ₹2,000 per trade, 7-day hold max
4. STRADDLE_BUY — ATM call + put before high-uncertainty event
5. NO_TRADE — No clear setup, do not force a trade

## Constraints
- Monthly allocation: ₹20,000 fixed. No re-loading mid-month unless previous trades 
  closed positively.
- Max ₹5,000 per single-leg options trade, ₹8,000 per straddle (both legs).
- Only buy options — NEVER sell/write options.
- Only Nifty, BankNifty weekly options OR liquid stock options.
- Close any position down > 60% from entry mechanically — no exceptions.
- Stop allocating new trades if monthly allocation is fully deployed.
- All strategy proposals must go through Orchestrator before reaching Analyst.

## Error handling
If options chain data is unavailable or stale, default to NO_TRADE and notify 
Orchestrator. If monthly allocation tracking is inconsistent, halt and alert human.

## State it owns
- `state:risk_strategy` Redis key
- `state:risk_allocation` Redis key (tracks monthly spend)
- Risk strategy history in SQLite `risk_strategy_log` table
