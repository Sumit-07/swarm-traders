# Compliance Agent — Technical Specification

## Trigger conditions
1. At 3:45 PM (post-market) — generate end-of-day audit report
2. When Execution Agent publishes a trade record to `channel:compliance_agent`
3. When Orchestrator receives a HALT command — verify all activity has stopped
4. On-demand when Orchestrator requests a compliance check

## Inputs
- Redis: `channel:compliance_agent` — trade records from Execution Agent
- SQLite: `trades` table — all executed trades
- SQLite: `signals` table — all generated signals
- SQLite: `risk_log` table — all risk decisions
- SQLite: `orchestrator_log` table — system events
- Redis: `state:positions` — current open positions
- Redis: `state:system_mode` — current system mode

## Outputs
- SQLite: `audit_log` table — complete audit trail of every trade and decision
- SQLite: `compliance_reports` table — daily compliance reports
- Redis: `channel:orchestrator` — violation alerts (priority=CRITICAL if severity HIGH)
- Telegram (via Orchestrator): compliance violation alerts to human owner

## Tools available
- `redis_store.read(key)` / `redis_store.write(key, value)`
- `redis_store.publish(channel, message)`
- `sqlite_store.query(sql)`
- `sqlite_store.insert(table, record)`

## LLM usage
- Model: Gemini Flash
- Call LLM ONLY for: generating the end-of-day audit report (PROMPT_EOD_AUDIT) — contextual review of all trades against rules
- Use pure Python for: real-time trade logging, rule violation detection (threshold checks), kill-switch enforcement
- Max tokens: 2000 input / 500 output

## Constraints
- NEVER modify or delete any record in the audit trail
- NEVER minimize or downgrade the severity of a detected violation
- NEVER allow the system to continue operating after a kill-switch (HALT) command — verify cessation
- ALWAYS log every trade received from Execution Agent within 1 second
- ALWAYS generate a daily compliance report, even on days with zero trades

## Error handling
- If trade records are missing or incomplete, flag as a HIGH severity violation ("unlogged trade detected")
- If unable to generate EOD report, alert Orchestrator with priority=CRITICAL and write raw trade data to audit_log as fallback
- If database write fails, retry 3 times then alert Orchestrator

## State it owns
- `audit_log` SQLite table
- `compliance_reports` SQLite table
