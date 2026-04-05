# Optimizer — Technical Specification

## Trigger conditions
- Triggered by Orchestrator at 3:50 PM on trading days only
- Guard: requires minimum 2 completed trades today (wins OR losses)
- Guard: does not run on days where system was in HALTED mode all day
- Guard: does not run on non-trading days (enforced by market_calendar)
- If guards fail: Orchestrator sends Telegram "No optimizer meeting today —
  [reason]." and skips.

## Inputs
- SQLite: `trades` table — today's completed trades with entry, exit, P&L
- SQLite: `signals` table — all signals generated today (fired + not fired)
- Redis: `state:active_strategy` — what Strategist selected this morning
- Redis: `data:market_snapshot` — today's Nifty/BankNifty/VIX data
- SQLite: `learnings` table — existing knowledge graph (context for meeting)

## Outputs
- SQLite: `learnings` table — new learnings written after meeting
- SQLite: `optimizer_meetings` table — full meeting transcript logged
- Redis: `channel:orchestrator` — synthesis message for Telegram forwarding
  This message MUST be sent. Orchestrator MUST forward it to Telegram.

## LLM usage
- Model: GPT-4o (no exceptions — most reasoning-intensive task in system)
- Calls per meeting: 10 total
  - Round 1: 3 calls (one per agent)
  - Round 2: 3 calls (one per agent, sees all Round 1 responses)
  - Round 3: 3 calls (one per agent, commits to one change)
  - Synthesis: 1 call (Optimizer synthesises all Round 3 outputs)
- Max tokens per agent call: 300 input / 150 output (enforces 100-word limit)
- Max tokens for synthesis call: 1500 input / 400 output

## Constraints
- NEVER trigger during market hours (before 3:30 PM)
- NEVER write a learning to the graph that does not include a regime tag
- NEVER run if fewer than 2 trades completed today
- NEVER skip sending synthesis to Orchestrator — this is unconditional
- Each agent response MUST be truncated to 100 words in code if LLM exceeds
  the limit. Do not rely solely on the prompt instruction.
- Meeting transcript must be saved to SQLite before synthesis is written

## State it owns
- `learnings` SQLite table (read + write)
- `optimizer_meetings` SQLite table (write only)
- Redis key: `state:last_optimizer_run` (date of last successful meeting)
