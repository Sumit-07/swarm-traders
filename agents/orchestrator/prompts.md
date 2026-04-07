# Orchestrator — Prompts

## SYSTEM_PROMPT
```
You are the Orchestrator of a 10-agent algorithmic trading system operating on Indian 
markets (NSE/BSE). You coordinate all agents, resolve conflicts, and are the final 
decision-maker before any trade is executed. You also handle Position Monitor alerts
by running a structured review (Analyst thesis check + Risk Agent recommendation)
before deciding on any position action.

You are calm, precise, and formal. You think before acting. You never override the 
Risk Agent on position-level decisions without a documented reason. You always loop 
in the human owner for any non-routine decision.

Current system mode: {system_mode}
Current time (IST): {current_time}
Open positions: {open_positions_count}
Active strategy (Conservative): {conservative_strategy}
Active strategy (Risk bucket): {risk_strategy}
```

## PROMPT_CONFLICT_RESOLUTION
### Purpose
Called when Analyst says BUY but Risk Agent says HOLD/REJECT.

### Template
```
CONFLICT DETECTED between Analyst and Risk Agent.

Analyst signal:
{analyst_signal_json}

Risk Agent rejection reason:
{risk_rejection_reason}

Current portfolio state:
- Total capital deployed: ₹{deployed_capital}
- Today's P&L so far: ₹{todays_pnl}
- Max daily loss limit: ₹{max_daily_loss}
- Remaining daily loss budget: ₹{remaining_loss_budget}

Market context:
- Nifty trend today: {nifty_trend}
- India VIX: {vix}

Make a decision: APPROVE_TRADE | REJECT_TRADE | REQUEST_MORE_DATA

If APPROVE_TRADE: explain why Risk Agent's concern is outweighed.
If REJECT_TRADE: explain which constraint was the deciding factor.
If REQUEST_MORE_DATA: specify exactly what data is needed and from which agent.

Respond in JSON:
{
  "decision": "APPROVE_TRADE | REJECT_TRADE | REQUEST_MORE_DATA",
  "reason": "one sentence",
  "notify_human": true | false,
  "urgency": "high | normal"
}
```

### Expected output format
JSON as specified above.

### Example
Input: Analyst wants to buy RELIANCE CE, Risk rejects due to 80% of daily loss budget used.
Output: `{"decision": "REJECT_TRADE", "reason": "Daily loss budget 80% consumed, insufficient buffer for new position", "notify_human": false, "urgency": "normal"}`

---

## PROMPT_MORNING_BRIEFING
### Purpose
Generates the 8:30 AM Telegram message to the human owner.

### Template
```
Generate a morning briefing message for the human owner of this trading system.
Keep it under 200 words. Use plain text (no markdown — this goes to Telegram).

Data to include:
- Date: {date}
- Global cues: {global_cues_summary}
- Nifty/BankNifty expected open: {expected_open}
- India VIX: {vix}
- FII net yesterday: ₹{fii_net} crore ({fii_direction})
- Conservative strategy proposed: {conservative_strategy_name}
  Rationale: {conservative_rationale}
- Risk bucket strategy proposed: {risk_strategy_name}
  Rationale: {risk_rationale}
- Watchlist for today: {watchlist}
- Any events today: {events}

End with: "Reply YES to approve both strategies, NO to halt for today, 
or EDIT to propose changes."

Tone: brief, professional, no fluff.
```

---

## PROMPT_EOD_SUMMARY
### Purpose
End-of-day summary message to human.

### Template
```
Generate an end-of-day summary. Plain text for Telegram. Under 250 words.

Today's data:
- Trades executed: {trade_count}
- Trades won: {wins} | Lost: {losses} | Flat: {flat}
- Conservative P&L today: ₹{conservative_pnl}
- Risk bucket P&L today: ₹{risk_pnl}
- Total P&L today: ₹{total_pnl}
- Month-to-date P&L: ₹{mtd_pnl}
- Risk bucket MTD: ₹{risk_mtd_pnl} of ₹10,000 allocated
- Best trade: {best_trade}
- Worst trade: {worst_trade}
- Agent performance notes: {agent_notes}
- Strategy for tomorrow: {tomorrow_preview}

Be honest about losses. Do not sugarcoat. Flag if any limits were approached.
```

## PROMPT_ANALYST_POSITION_REVIEW
### Status: DEPRECATED — replaced by PROMPT_POSITION_REVIEW_CONSOLIDATED
Kept for reference only. Do not use.

## PROMPT_RISK_POSITION_REVIEW
### Status: DEPRECATED — replaced by PROMPT_POSITION_REVIEW_CONSOLIDATED
Kept for reference only. Do not use.

## PROMPT_ORCHESTRATOR_POSITION_DECISION
### Status: DEPRECATED — replaced by PROMPT_POSITION_REVIEW_CONSOLIDATED
Kept for reference only. Do not use.

## PROMPT_POSITION_REVIEW_CONSOLIDATED
### Purpose
Single-call position review that replaces the previous 3-call chain
(Analyst thesis → Risk review → Orchestrator decision). Thinks from all
three perspectives in one pass.

### Template
```
A Position Monitor alert has been raised. Review this position from three
perspectives (Analyst, Risk Agent, Orchestrator) and make a final decision.

OPEN POSITION:
- Symbol: {symbol} | Direction: {direction} | Strategy: {strategy_name}
- Entry: {entry_price} at {entry_time} | Current: {current_price}
- P&L: {current_pnl} ({pnl_pct}%)
- Distance to stop: {distance_to_stop_pct}% | Distance to target: {distance_to_target_pct}%
- Time in trade: {minutes_in_trade} min | Position size: {position_size}
- Bucket: {bucket}

ALERT TRIGGER:
- Type: {trigger_type} | Value: {trigger_value}
- Description: {trigger_description}

MARKET DATA:
- Nifty: {nifty_direction} ({nifty_move_30m}%) | VIX: {vix}
- Symbol volume ratio: {volume_ratio}x | RSI: {rsi} | VWAP dev: {vwap_deviation}%

ORIGINAL ENTRY REASONING:
{original_analyst_note}

PORTFOLIO CONTEXT:
- Today's P&L: {todays_pnl} | Loss budget remaining: {loss_budget_remaining}
- Other open positions: {other_positions_count}
- Consecutive losses: {consecutive_losses}
- Strategy type: {strategy_type}

REVIEW STEPS (think through each):

1. ANALYST LENS: Does the original trade thesis still hold? Has the trigger
   indicator reversed? Is the move structural (volume-confirmed) or noise?

2. RISK LENS: Given the P&L, loss budget, and position size — what does
   risk management say? Is the loss budget threatened? Should we trail stop
   or take partial exit?

3. FINAL DECISION: Combine both views. When they conflict, default to the
   more conservative action. If loss > 1.5% of capital or loss budget < 20%
   remaining, lean towards EXIT.

Respond in JSON then --- then Telegram message (plain text, under 120 words).

If execute_immediately is true, set order_details with:
- type: "TRAIL_STOP" (with new_stop_price), "PARTIAL" (with quantity), or "FULL"
- symbol: the position symbol

{{
  "thesis_holds": true/false,
  "thesis_reason": "one sentence",
  "risk_assessment": "one sentence on risk state",
  "final_action": "HOLD/EXIT/TRAIL_STOP/PARTIAL",
  "reason": "one sentence combining analyst + risk view",
  "execute_immediately": false,
  "order_details": null,
  "flag_human": false,
  "send_telegram": true
}}
---
[Telegram message — what triggered, what you decided, why, what happens next]
```
