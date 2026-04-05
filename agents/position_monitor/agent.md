# Position Monitor — Technical Specification

## Trigger conditions
- APScheduler job: every 5 minutes, 9:15 AM – 3:20 PM, trading days only
- On startup: immediately checks all positions (in case system restarted
  mid-day with open positions)
- Never runs outside market hours
- Never runs when system_mode = HALTED

## Inputs
- Redis: `state:positions` — list of all open positions with entry data
- Redis: `data:watchlist_ticks:{symbol}` — latest tick per symbol
- Redis: `data:market_snapshot` — current Nifty, VIX, market direction
- SQLite: `trades` table — original entry details, strategy type, entry time
- SQLite: `monitor_alerts` table — cooldown tracking (has this position
  been alerted recently?)
- `agents/position_monitor/thresholds.py` — strategy-aware threshold definitions

## Outputs
- Redis: `channel:orchestrator` — POSITION_ALERT messages when threshold crossed
- SQLite: `monitor_alerts` table — log of every alert sent
- SQLite: `monitor_ticks` table — every 5-min check logged for audit

## LLM usage
- Model: NONE in monitor loop (pure Python)
- LLM is called by Orchestrator during its review flow, not by this agent
- This agent never makes an LLM call directly

## Active hours
- 9:15 AM – 3:20 PM IST (market hours only)
- Stops at 3:20 PM because Execution Agent force-closes intraday at 3:20
  and Risk Agent handles the close — no point monitoring after that

## Constraints
- NEVER call the broker API or place orders
- NEVER call an LLM
- NEVER alert the same position more than once within its cooldown window
- NEVER run when there are no open positions (early exit the loop)
- NEVER escalate on the first N minutes after a position opens — let the
  entry settle (grace period defined per strategy type)
- Alert cooldown per position is stored in SQLite, not Redis, so it
  survives container restarts

## State it owns
- `monitor_alerts` SQLite table
- `monitor_ticks` SQLite table

## Performance requirement
- Full loop across all open positions must complete in under 30 seconds
- Max simultaneous open positions: 5 (from risk config)
- Each position check is a handful of comparisons — this is trivially fast
