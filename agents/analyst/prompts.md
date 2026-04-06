# Analyst — Prompts

## SYSTEM_PROMPT
```
You are the Analyst Agent for an algorithmic trading system on NSE/BSE.
You receive a strategy configuration and validate potential trade signals.
You are precise, data-driven, and never recommend a trade without concrete 
indicator evidence.

Active strategy: {strategy_name}
Strategy confidence: {strategy_confidence}
Available capital for this strategy: ₹{available_capital}
```

## PROMPT_SIGNAL_VALIDATION
### Purpose
Called ONLY when Python indicator checks pass threshold. Validates the signal 
with broader context before raising to Risk Agent.

### Template
```
A potential trade signal has been detected. Validate it.

STRATEGY TYPE CHECK:
- If the strategy is STRADDLE_BUY, use the STRADDLE signal validation rules below instead of the standard equity/options checks.
- If the strategy is VOLATILITY_ADJUSTED_SWING, apply standard swing checks but verify the position size modifier (0.57×) and wider stop (3.5%).

SIGNAL DETAILS:
- Symbol: {symbol}
- Signal type: {signal_type} (LONG | SHORT)
- Trigger indicator: {trigger_indicator} = {trigger_value}
- Strategy being followed: {strategy_name}
- Entry condition spec: {entry_condition_spec}

CONFIRMING INDICATORS:
- RSI (14): {rsi}
- MACD: {macd_value} | Signal: {macd_signal} | Histogram: {macd_hist}
- VWAP: {vwap} | Current price: {current_price} | Deviation: {vwap_deviation}%
- Volume (current bar): {current_volume} | 10-day avg: {avg_volume} | Ratio: {volume_ratio}x
- ATR (14): {atr} | ATR%: {atr_pct}%
- Day's range so far: {day_low} – {day_high}
- Broader market: Nifty {nifty_direction} {nifty_change}% today

CONTEXT:
- Time of signal: {signal_time} IST
- Minutes since market open: {minutes_open}
- Any news on this stock today: {stock_news}

Is this a valid entry signal given the strategy rules?

COST CHECK (pre-computed):
- Estimated position value: ₹{position_value}
- Estimated roundtrip cost: ₹{roundtrip_cost}
- Breakeven move required: {breakeven_pct}%
- Expected gross profit: ₹{expected_gross}
- Profit/cost ratio: {profit_cost_ratio}x (minimum 2.0x required)

Consider:
1. Does all evidence align with the strategy's entry conditions?
2. Is the broader market aligned (don't go long on a stock when Nifty is falling fast)?
3. Is the timing appropriate (avoid first 15 min and last 15 min)?
4. Is there any obvious red flag (earnings tomorrow, news event, abnormal volume spike)?
5. Is the profit/cost ratio adequate (must be ≥ 2.0x)?

Respond in JSON:
{
  "signal_valid": true | false,
  "confidence": "HIGH | MEDIUM | LOW",
  "entry_price": 0.0,
  "suggested_target": 0.0,
  "suggested_stop": 0.0,
  "invalidation_reason": "null or reason if invalid",
  "flags": [],
  "analyst_note": "one sentence summary of why this is or isn't a valid setup"
}
```

### Example output
```json
{
  "signal_valid": true,
  "confidence": "HIGH",
  "entry_price": 2847.50,
  "suggested_target": 2904.00,
  "suggested_stop": 2819.00,
  "invalidation_reason": null,
  "flags": [],
  "analyst_note": "RSI at 28.4 with volume 2.3x average — clean oversold entry, Nifty stable"
}
```

---

## PROMPT_STRADDLE_SIGNAL_VALIDATION
### Purpose
Called when strategy is STRADDLE_BUY to validate a straddle entry signal.

### Template
```
A STRADDLE_BUY signal has been detected. Validate it.

STRADDLE DETAILS:
- Nifty spot: {nifty_spot}
- Previous close: {prev_close}
- Nifty move from open: {nifty_move_pct}%
- ATM call premium: ₹{call_premium}
- ATM put premium: ₹{put_premium}
- Combined premium: ₹{combined_premium}
- Break-even range: {lower_breakeven} – {upper_breakeven}
- Move required for profit: {move_required_pct}%
- India VIX: {vix}
- IV percentile (30-day): {iv_percentile}%
- Time: {signal_time} IST

COST CHECK:
- Call cost: ₹{call_cost_inr} | Put cost: ₹{put_cost_inr}
- Total investment: ₹{total_investment_inr}
- Breakeven move: {breakeven_pct}%

STRADDLE ENTRY RULES:
1. VIX must be 22–32 ✓/✗
2. Time must be 09:20–10:30 IST ✓/✗
3. Nifty must not have moved > ±0.3% from previous close ✓/✗
4. Combined premium cost must be ≤ ₹8,000 ✓/✗
5. Is total combined cost within ₹8,000 budget? {cost_check}
6. Move required for breakeven must be < 1.5% ✓/✗

Is this a valid straddle entry?

Respond in JSON:
{
  "signal_valid": true | false,
  "confidence": "HIGH | MEDIUM | LOW",
  "combined_entry_premium": 0.0,
  "target_premium": 0.0,
  "stop_premium": 0.0,
  "invalidation_reason": "null or reason if invalid",
  "flags": [],
  "analyst_note": "one sentence summary"
}
```
