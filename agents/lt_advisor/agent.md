# LT_Advisor — Technical Specification

## Run schedule
- 08:00 AM IST daily — morning opportunity scan (runs every day)
- 12:30 PM IST weekdays — midday VIX check (only if VIX moved > 2pts since AM)
- 03:45 PM IST weekdays — EOD check (only if significant market move today)
- 10:00 AM IST Saturday — weekly summary (always sends, even if no opportunity)

Note: The 8 AM run fires every day including weekends and holidays.
The 12:30 PM and 3:45 PM runs fire on weekdays and check market_calendar.
The Saturday run fires every Saturday regardless.

## Silence conditions — checked before any LLM call
The agent sends NO message and makes NO LLM call when:
1. Quick Python score < 55/100
2. Same instrument alerted within last 7 days (check lt_advisor_log)
3. VIX threshold crossing already alerted this calendar month
4. Run type is MIDDAY and VIX moved < 2 points since morning run
5. Run type is EOD and Nifty moved < 1% today

## Inputs
- Redis: data:market_snapshot — VIX, Nifty, BankNifty current values
- Redis: data:fii_flow — today's FII/DII net flows
- tools/lt_data.py: Nifty PE ratio (fetched from NSE)
- tools/lt_data.py: 52-week high/low for Nifty
- tools/lt_data.py: FII 30-day net flow total
- SQLite: lt_advisor_log — for silence condition checks

## Outputs
- Redis: channel:orchestrator — LT_ADVISOR_ALERT messages
- SQLite: lt_advisor_log — every run logged with inputs, score, decision

## LLM model
GPT-4o mini. Maximum 2 calls per run:
  Call 1: PROMPT_OPPORTUNITY_SCAN (opportunity analysis and scoring)
  Call 2: PROMPT_DRAFT_TELEGRAM (message drafting — only if Call 1 found opportunity)

## Never does
- Never calls Analyst, Risk Agent, Execution Agent, or Position Monitor
- Never writes to Redis state:* keys (read-only except channel:orchestrator)
- Never places or cancels orders
- Never modifies trading system state
- Never sends Telegram directly — always via Orchestrator

## Error handling
If lt_data.py fetchers fail (NSE website down, etc.):
  - Use last known values from lt_advisor_log if available and < 24 hours old
  - If no cached values: skip run, log the failure, do not send any message
  - Do NOT send a message saying "data unavailable" — just stay silent

## State it owns
- lt_advisor_log SQLite table (read and write)
- Redis key: state:lt_last_run (timestamp of last successful run)
- Redis key: state:lt_vix_morning (VIX at 8 AM — used by midday check)
