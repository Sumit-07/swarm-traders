# Risk Agent — Technical Specification

## Trigger conditions
1. When Analyst publishes a trade proposal to `channel:risk_agent`
2. Every 5 minutes during market hours — monitor open positions for stop-loss breaches and drawdown checks
3. When Orchestrator requests a portfolio risk assessment
4. When daily P&L crosses a warning threshold (3% drawdown)

## Inputs
- Redis: `channel:risk_agent` — trade proposals from Analyst
- Redis: `state:positions` — current open positions
- Redis: `data:market_snapshot` — current Nifty, BankNifty, VIX
- Redis: `data:watchlist_ticks:{symbol}` — live prices for position monitoring
- SQLite: `trades` table — today's executed trades (for P&L and consecutive loss tracking)

## Outputs
- Redis: `channel:orchestrator` — approved or rejected trade decisions
- Redis: `state:risk_status` — current risk state (NORMAL | WARNING | COOLDOWN | HALTED)
- Redis: `channel:orchestrator` — drawdown alerts and cool-down notifications
- SQLite: `risk_log` table — log of all risk decisions

## Tools available
- `redis_store.read(key)` / `redis_store.write(key, value)`
- `redis_store.publish(channel, message)`
- `sqlite_store.query(sql)`
- `risk_calculator.position_size(capital, risk_pct, entry, stop)` — calculates max position size
- `risk_calculator.portfolio_exposure(positions)` — returns sector and total exposure
- `risk_calculator.daily_pnl(trades)` — returns today's realized + unrealized P&L

## LLM usage
- Model: GPT-4o mini
- Call LLM ONLY when: reviewing a trade proposal that passes basic Python checks but needs contextual judgment (e.g., stop-loss placement quality assessment via PROMPT_TRADE_REVIEW)
- Use pure Python for: all position sizing calculations, drawdown checks, cool-down enforcement, consecutive loss counting
- Max tokens: 1500 input / 300 output

## Constraints
- NEVER approve a trade that risks more than 1.5% of total capital on a single position
- NEVER allow trading when daily drawdown exceeds 3% of total capital — trigger mandatory HALT
- NEVER permit averaging down into a losing position under any circumstances
- ALWAYS enforce 1-hour cool-down after 3 consecutive losses
- ALWAYS respond to trade proposals within 30 seconds
- For risk bucket (options) trades: verify total cost does not exceed ₹5,000 per single-leg trade (₹8,000 per straddle) and monthly allocation is not exhausted

## Error handling
- If position data is unavailable, assume worst-case exposure and reject new trades until data is restored
- If a trade proposal is missing required fields, reject with reason "incomplete proposal"
- If unable to calculate current P&L, set risk status to WARNING and notify Orchestrator

## State it owns
- `state:risk_status` Redis key
- `risk_log` SQLite table
- `state:cooldown_until` Redis key (timestamp when cool-down expires)
- `state:consecutive_losses` Redis key
