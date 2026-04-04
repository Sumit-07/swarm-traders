# Strategist (Conservative) — Technical Specification

## Trigger conditions
1. 8:00 AM — Morning strategy selection (after Data Agent publishes pre-market data)
2. 3:45 PM — End-of-day strategy review
3. On-demand: when Orchestrator requests strategy re-evaluation due to regime change

## Inputs
- Redis: `data:market_snapshot` (Nifty, BankNifty, VIX from Data Agent)
- Redis: `data:fii_flow` (FII/DII data from Data Agent)
- Redis: `data:news_summary` (market news summary from Data Agent)
- Redis: `data:economic_calendar` (upcoming events from Data Agent)
- Redis: `state:positions` (current open positions)
- SQLite: `trades` table (yesterday's P&L, historical performance)

## Outputs
- Redis: `state:active_strategy` (today's selected strategy configuration as JSON)
- Redis: `channel:orchestrator` (strategy proposal for approval)
- Redis: `channel:analyst` (strategy config for Analyst Agent to execute)

## Tools available
- `redis_store.read(key)` / `redis_store.write(key, value)`
- `redis_store.publish(channel, message)`
- `sqlite_store.query(sql)`
- `indicators.calculate_all(ohlcv_df)` — returns RSI, MACD, VWAP, ATR, ADX

## LLM usage
- Model: GPT-4o
- Call LLM when: selecting morning strategy, reviewing end-of-day performance
- Use pure Python when: reading data inputs, formatting strategy JSON output
- Max tokens: 3000 input / 800 output

## Strategy library
1. RSI_MEAN_REVERSION — sideways/ranging markets, VIX 12-18
2. VWAP_REVERSION — low-volatility intraday, VIX < 16
3. OPENING_RANGE_BREAKOUT — trending days, strong global cues
4. SWING_MOMENTUM — strong uptrend, ADX > 25, VIX < 16
5. NIFTY_OPTIONS_BUYING — pre-event, high VIX, directional bias
6. NO_TRADE — unclear regime, major event risk, VIX > 22

## Constraints
- Manages the conservative capital bucket (₹20,000-30,000).
- NEVER recommend strategies that require selling/writing options or futures.
- Maximum capital allocation per strategy is 60% of available capital.
- Maximum 5 symbols on the watchlist per day.
- Defers to Risk Agent on position sizing — never specifies exact lot sizes.
- All strategy proposals must go through Orchestrator before reaching Analyst.

## Error handling
If Data Agent data is unavailable or stale (>10 minutes old at 8:00 AM), 
default to NO_TRADE and notify Orchestrator with reason.

## State it owns
- `state:active_strategy` Redis key
- Strategy review history in SQLite `strategy_log` table
