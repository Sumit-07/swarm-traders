# Data Agent — Technical Specification

## Trigger conditions
1. 7:00 AM — Full pre-market data pull (global cues, FII data, overnight news)
2. 8:00 AM — Feed Strategist agents with fresh market snapshot
3. 9:00 AM — Pre-open data refresh
4. Every 1 minute during market hours (9:15 AM – 3:30 PM) — Tick data update
5. Every 5 minutes during market hours — Options chain update
6. On-demand: when Analyst or Strategist requests specific data

## Inputs
- Fyers API: Live tick data, OHLCV
- yfinance (fallback): Historical data
- nsepython: Options chain, F&O data, index data
- Investing.com / NewsAPI: Economic calendar, news headlines
- NSE website: FII/DII provisional data

## Outputs
- Redis: `data:market_snapshot` — current Nifty, BankNifty, VIX
- Redis: `data:watchlist_ticks:{symbol}` — live OHLCV per watched symbol
- Redis: `data:options_chain` — current options chain
- Redis: `data:news_summary` — summarised news (LLM call)
- Redis: `data:fii_flow` — today's FII/DII data
- Redis: `data:economic_calendar` — events for next 3 days

## Tools available
- `market_data.get_quote(symbol)`
- `market_data.get_ohlcv(symbol, interval, count)`
- `options_chain.get_chain(symbol, expiry)`
- `news_fetcher.get_headlines()`
- `economic_calendar.get_events(days_ahead=3)`
- `indicators.calculate_all(ohlcv_df)` — returns RSI, MACD, VWAP, ATR

## LLM usage
- Model: Gemini Flash
- Call LLM ONLY for: summarising news headlines into 3-sentence market sentiment 
  (once per hour during market hours)
- All data fetching, validation, and storage is pure Python

## Constraints
- Never publish data older than 2 minutes as "live" during market hours.
- If Fyers API fails, fall back to yfinance. Log the fallback.
- Never make more than 3 API calls per second to any data source (rate limit).

## State it owns
- All `data:*` Redis keys
- `data_log` SQLite table
