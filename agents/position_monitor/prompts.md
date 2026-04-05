# Position Monitor — Prompts (used by Orchestrator during review)

These prompts are NOT called by the Position Monitor itself.
They are called by Orchestrator during its position review flow,
triggered by a Position Monitor alert. The actual prompt templates
are in agents/orchestrator/prompts.md.

---

## PROMPT_ANALYST_POSITION_REVIEW
### Purpose
Orchestrator calls Analyst to check if the original trade thesis still holds
after a Position Monitor alert.

### Template
```
You are the Analyst. A Position Monitor alert has been raised for an open
position. Review whether the original trade thesis still holds.

OPEN POSITION:
- Symbol: {symbol}
- Direction: {direction}
- Strategy: {strategy_name}
- Entry price: {entry_price}
- Entry time: {entry_time}
- Current price: {current_price}
- Current P&L: {current_pnl} ({pnl_pct}%)
- Distance to stop: {distance_to_stop_pct}%
- Distance to target: {distance_to_target_pct}%
- Time in trade: {minutes_in_trade} minutes

ALERT DETAILS:
- Trigger type: {trigger_type}
- Trigger value: {trigger_value}
- Threshold that was crossed: {threshold_description}

CURRENT MARKET DATA:
- Nifty direction last 30 min: {nifty_direction} ({nifty_move_30m}%)
- India VIX: {vix}
- Symbol volume ratio (current vs avg): {volume_ratio}x
- RSI current: {rsi}
- VWAP deviation: {vwap_deviation}%

ORIGINAL ENTRY REASONING:
{original_analyst_note}

Answer ONE question: Does the original trade thesis still hold?

Consider:
1. Has the indicator that triggered entry reversed or weakened significantly?
2. Is the broader market working against this position?
3. Is the move accompanied by confirming volume (structural) or low volume (noise)?
4. For the strategy type ({strategy_name}), is this move within normal
   expected variance or is it a genuine thesis break?

Respond in JSON:
{
  "thesis_holds": true | false,
  "confidence": "HIGH | MEDIUM | LOW",
  "key_reason": "one specific sentence",
  "market_alignment": "WITH | AGAINST | NEUTRAL",
  "indicator_status": "INTACT | WEAKENING | BROKEN",
  "analyst_recommendation": "HOLD | WATCH_CLOSELY | EXIT",
  "note": "optional additional context, max 20 words"
}
```

---

## PROMPT_RISK_POSITION_REVIEW
### Purpose
Orchestrator calls Risk Agent with Analyst's assessment to get a
risk-based recommendation on the position.

### Template
```
You are the Risk Agent. Review an open position following a Position Monitor
alert. The Analyst has reviewed the trade thesis. Make a risk-based decision.

POSITION STATE:
- Symbol: {symbol}
- Strategy: {strategy_name}
- Current P&L: {current_pnl} ({pnl_pct}%)
- Distance to stop: {distance_to_stop_pct}%
- Distance to target: {distance_to_target_pct}%
- Time in trade: {minutes_in_trade} min
- Position size: {position_size}
- Bucket: {bucket}

ALERT TRIGGER:
- Type: {trigger_type}
- Value: {trigger_value}

ANALYST ASSESSMENT:
- Thesis holds: {thesis_holds}
- Analyst confidence: {analyst_confidence}
- Indicator status: {indicator_status}
- Analyst recommendation: {analyst_recommendation}

PORTFOLIO CONTEXT:
- Today's P&L so far: {todays_pnl}
- Loss budget remaining: {loss_budget_remaining}
- Other open positions: {other_positions_count}
- Consecutive losses today: {consecutive_losses}

STRATEGY-SPECIFIC CONTEXT:
- Strategy type: {strategy_type}
- Time remaining before forced close: {time_to_forced_close} min

Respond in JSON:
{
  "action": "HOLD | TRAIL_STOP | PARTIAL_EXIT | FULL_EXIT",
  "reason": "one specific sentence citing the key factor",
  "urgency": "IMMEDIATE | NEXT_CANDLE | MONITOR",
  "if_trail_stop": {
    "new_stop_price": 0.0,
    "rationale": "why this level"
  },
  "if_partial_exit": {
    "exit_quantity": 0,
    "remaining_quantity": 0,
    "rationale": "why partial"
  },
  "flag_human": true | false,
  "flag_reason": "why human should know | null"
}
```

---

## PROMPT_ORCHESTRATOR_POSITION_DECISION
### Purpose
Orchestrator synthesises Analyst + Risk Agent inputs and makes the
final decision. Also generates the Telegram message.

### Template
```
You are the Orchestrator. A Position Monitor alert has triggered a full
review. You have received assessments from Analyst and Risk Agent.
Make the final decision and draft the Telegram message.

POSITION: {symbol} {direction} | Entry {entry_price} | Now {current_price}
P&L: {current_pnl} ({pnl_pct}%) | Strategy: {strategy_name}

ALERT TRIGGER: {trigger_type} -- {trigger_description}

ANALYST SAYS:
- Thesis holds: {thesis_holds} (confidence: {analyst_confidence})
- Key reason: {analyst_key_reason}
- Recommendation: {analyst_recommendation}

RISK AGENT SAYS:
- Action: {risk_action}
- Reason: {risk_reason}
- Urgency: {risk_urgency}
- Flag human: {flag_human}

Make the final decision. When both agents agree, follow their lead.
When they disagree, default to the more conservative action.
Always flag human if Risk Agent says to.

Then write the Telegram message. Plain text. Under 120 words.
Include: what triggered, what you decided, why, what happens next.

Respond in JSON then --- then Telegram message:
{
  "final_action": "HOLD | TRAIL_STOP | PARTIAL_EXIT | FULL_EXIT",
  "reason": "one sentence",
  "execute_immediately": true | false,
  "order_details": {
    "type": "TRAIL | PARTIAL | FULL | null",
    "symbol": "{symbol}",
    "quantity": 0,
    "price_type": "MARKET | LIMIT",
    "new_stop_price": 0.0
  } | null,
  "send_telegram": true
}
---
[Telegram message -- plain text, under 120 words]
```
