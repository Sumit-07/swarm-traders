# Orchestrator — Technical Specification

## Trigger conditions
1. System startup (initialises all agents)
2. Every 15 minutes during market hours (health check)
3. When any agent publishes to `channel:orchestrator` on Redis
4. When human sends a Telegram command
5. At 8:00 AM (pre-market coordination)
6. At 3:20 PM (pre-close coordination — force square-off check)
7. At 3:45 PM (post-market review initiation)

## Inputs
- Redis: `state:all_agents` (health of every agent)
- Redis: `state:positions` (current open positions)
- Redis: `state:active_strategy` (what Strategist decided this morning)
- Redis: `channel:orchestrator` (messages from any agent)
- SQLite: `trades` table (today's executed trades)
- Telegram: inbound commands from human owner

## Outputs
- Redis: `channel:[agent_id]` (instructions to specific agents)
- Redis: `state:system_mode` (values: PAPER | LIVE | HALTED | REVIEW)
- Telegram: status messages to human owner
- SQLite: `orchestrator_log` table

## Tools available
- `redis_store.read(key)` / `redis_store.write(key, value)`
- `redis_store.publish(channel, message)`
- `sqlite_store.query(sql)`
- `telegram_bot.send(message)`
- `telegram_bot.send_approval_request(proposal)`

## LLM usage
- Model: GPT-4o
- Call LLM when: agent conflict detected, unusual market event, human asks open-ended question
- Use pure Python when: health checks, routing standard messages, applying hard rules
- Max tokens: 2000 input / 600 output

## Constraints
- NEVER send an order instruction directly to Execution Agent without Risk Agent approval.
- NEVER change system_mode from LIVE to HALTED without sending Telegram alert.
- NEVER ignore a message from Risk Agent flagged priority=CRITICAL.
- ALWAYS require human approval for any trade > ₹5,000 in the first 30 days.

## Error handling
If any agent fails to respond within 60 seconds, set that agent's status to DEGRADED, 
notify human via Telegram, and route around it using fallback rules defined in config.py.

## State it owns
- `state:system_mode`
- `state:all_agents`
- `orchestrator_log` SQLite table
