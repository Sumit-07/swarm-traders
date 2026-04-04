# Analyst — Technical Specification

## Trigger conditions
1. When Strategist or Risk Strategist publishes a strategy config to `channel:analyst`
2. Every 2 minutes during market hours (9:15 AM – 3:30 PM) — scan watchlist for entry signals
3. On-demand when Orchestrator requests a signal re-evaluation
4. Does NOT self-trigger outside market hours

## Inputs
- Redis: `channel:analyst` — strategy config from Strategist / Risk Strategist
- Redis: `data:watchlist_ticks:{symbol}` — live OHLCV per watched symbol (from Data Agent)
- Redis: `data:market_snapshot` — current Nifty, BankNifty, VIX
- Redis: `data:options_chain` — current options chain (for options strategies)
- Redis: `state:active_strategy` — currently active strategy configuration

## Outputs
- Redis: `channel:risk_agent` — trade proposal for Risk Agent review
- Redis: `channel:orchestrator` — status updates and signal notifications
- SQLite: `signals` table — log of all detected signals (acted on or not)

## Tools available
- `indicators.calculate_all(ohlcv_df)` — returns RSI, MACD, VWAP, ATR
- `indicators.calculate_rsi(ohlcv_df, period=14)`
- `indicators.calculate_vwap(ohlcv_df)`
- `indicators.calculate_macd(ohlcv_df)`
- `indicators.calculate_atr(ohlcv_df, period=14)`
- `redis_store.read(key)` / `redis_store.write(key, value)`
- `redis_store.publish(channel, message)`
- `sqlite_store.query(sql)`

## LLM usage
- Model: GPT-4o mini
- Call LLM ONLY when: Python indicator checks pass threshold — to validate signal with broader context (PROMPT_SIGNAL_VALIDATION)
- Use pure Python for: all indicator calculations, threshold checks, watchlist scanning
- Max tokens: 1500 input / 400 output

## Constraints
- NEVER generate a trade signal without concrete indicator evidence meeting the strategy's entry conditions
- NEVER have more than 2 trade proposals in queue simultaneously
- NEVER improvise beyond the strategy config — follow entry conditions exactly as specified
- NEVER generate signals in the first 15 minutes (9:15–9:30 AM) or last 15 minutes (3:15–3:30 PM) of market hours
- ALWAYS include exact indicator values in every trade proposal

## Error handling
- If Data Agent tick data is stale (>2 minutes old during market hours), skip signal generation and log a warning
- If indicator calculation fails for a symbol, skip that symbol and continue scanning the rest of the watchlist
- If LLM validation call fails, do not raise the signal — log and retry once after 30 seconds

## State it owns
- `signals` SQLite table
- `state:analyst_status` Redis key (SCANNING | SIGNAL_DETECTED | IDLE)
