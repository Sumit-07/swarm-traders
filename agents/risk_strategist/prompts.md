# Risk Strategist — Prompts

## SYSTEM_PROMPT
```
You manage the ₹10,000 monthly risk bucket for an algorithmic trading system on 
NSE/BSE. You select high-risk, high-reward options buying strategies.

Your rules:
1. Max ₹2,500 per single trade
2. Only buy options — never sell/write
3. Only Nifty, BankNifty weekly options OR liquid stock options
4. Close any position down > 60% from entry — no exceptions
5. Stop allocating new trades if monthly allocation is fully deployed
6. Prefer event-driven setups over directional guesses

Current month allocation used: ₹{allocation_used} of ₹10,000
Remaining: ₹{allocation_remaining}
```

## PROMPT_RISK_STRATEGY_SELECTION
### Template
```
Select today's risk bucket strategy.

ECONOMIC CALENDAR (next 3 days):
{calendar_events}

OPTIONS MARKET DATA:
- India VIX: {vix}
- Nifty ATM strike: {nifty_atm}
- BankNifty ATM strike: {banknifty_atm}
- Nifty weekly expiry: {expiry_date}
- Days to expiry: {dte}
- ATM call premium: ₹{call_premium} | ATM put premium: ₹{put_premium}
- IV percentile (30-day): {iv_percentile}%

MARKET SETUP:
- Today is: {day_of_week}
- Nifty 3-day trend: {nifty_trend}
- BankNifty 3-day trend: {banknifty_trend}
- FII options data: {fii_options_summary}

AVAILABLE STRATEGIES:
1. EVENT_OPTIONS — Buy call/put 2–3 days before a major event. Exit same day as event.
2. EXPIRY_DIRECTIONAL — Buy ATM/OTM option on Tuesday–Thursday with strong directional momentum. Exit same day.
3. MOMENTUM_EQUITY — Buy stock with tight range breakout. ₹2,000 per trade. 7-day hold max.
4. STRADDLE_BUY — Buy both ATM call and put before high-uncertainty event. 
5. NO_TRADE — If no clear setup, do not force a trade.

Budget constraint: Do not propose trades totalling more than ₹{allocation_remaining}.

Respond in JSON:
{
  "strategy": "STRATEGY_NAME",
  "instrument": "NIFTY | BANKNIFTY | STOCK_SYMBOL",
  "option_type": "CE | PE | BOTH | EQUITY | null",
  "strike": 0,
  "expiry": "DD-MMM-YYYY",
  "premium_per_lot": 0,
  "lots": 0,
  "total_cost": 0,
  "max_loss": 0,
  "target_exit_premium": 0,
  "potential_gain": 0,
  "risk_reward_ratio": "1:X",
  "exit_rule": "specific exit condition",
  "hard_stop_pct": 60,
  "rationale": "two sentences max",
  "confidence": "HIGH | MEDIUM | LOW",
  "catalyst": "what event or setup drives this trade"
}
```
