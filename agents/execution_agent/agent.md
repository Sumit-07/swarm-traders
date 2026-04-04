# Execution Agent — Technical Specification

## Trigger conditions
Only activates when Orchestrator sends an EXECUTE message to `channel:execution_agent`.
Does NOT self-trigger.

## Inputs
- Redis: `channel:execution_agent` — approved order from Orchestrator
- Redis: `state:system_mode` — PAPER | LIVE (determines real vs simulated execution)
- Fyers API (if LIVE mode): actual broker

## Outputs
- Redis: `state:positions` (updated after fill)
- Redis: `channel:orchestrator` (fill confirmation)
- Redis: `channel:compliance_agent` (trade record for audit)
- SQLite: `trades` table

## Tools available
- `broker.place_order(symbol, qty, order_type, price, transaction_type)`
- `broker.place_stoploss_order(symbol, qty, trigger_price)`
- `broker.get_order_status(order_id)`
- `broker.cancel_order(order_id)`
- `order_simulator.simulate_fill(order)` — used in PAPER mode
- `order_simulator.simulate_stoploss(order)` — used in PAPER mode

## LLM usage
- Model: GPT-4o mini
- Call LLM ONLY for: determining optimal order type when not explicitly specified
- All order placement, status checking, and reporting is pure Python

## Constraints
- NEVER place a LIVE order without first checking `state:system_mode == LIVE`
- NEVER place an order without a corresponding stop-loss order
- NEVER place the same order twice (deduplication via order_id cache)
- ALWAYS confirm fill before reporting success
- In PAPER mode: simulate 0.05% slippage on entry and exit

## State it owns
- `state:positions` Redis key
- `trades` SQLite table
- `execution_log` SQLite table
