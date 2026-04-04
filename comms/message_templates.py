"""Formatted message templates for Telegram communication.

All templates use plain text (no markdown) as required by the design doc.
"""


def morning_briefing(date: str, global_cues: str, nifty_open: str,
                     vix: float, fii_net: str, fii_direction: str,
                     conservative_strategy: str, conservative_rationale: str,
                     risk_strategy: str, risk_rationale: str,
                     watchlist: list[str], events: str) -> str:
    watchlist_str = ", ".join(watchlist) if watchlist else "None"
    return (
        f"MORNING BRIEFING — {date}\n"
        f"{'���' * 35}\n"
        f"\n"
        f"Global: {global_cues}\n"
        f"Expected open: {nifty_open}\n"
        f"VIX: {vix}\n"
        f"FII: {fii_direction} INR {fii_net} cr yesterday\n"
        f"\n"
        f"CONSERVATIVE: {conservative_strategy}\n"
        f"  {conservative_rationale}\n"
        f"  Watchlist: {watchlist_str}\n"
        f"\n"
        f"RISK BUCKET: {risk_strategy}\n"
        f"  {risk_rationale}\n"
        f"\n"
        f"Events today: {events}\n"
        f"\n"
        f"Reply YES to approve both, NO to halt today,\n"
        f"or tell me what to change."
    )


def trade_proposal(symbol: str, direction: str, entry_price: float,
                   stop_loss: float, target: float, quantity: int,
                   bucket: str, confidence: str, note: str) -> str:
    return (
        f"TRADE PROPOSAL\n"
        f"{'─' * 35}\n"
        f"{direction} {symbol} x{quantity}\n"
        f"Entry: INR {entry_price:.2f}\n"
        f"Stop: INR {stop_loss:.2f}\n"
        f"Target: INR {target:.2f}\n"
        f"Bucket: {bucket}\n"
        f"Confidence: {confidence}\n"
        f"Note: {note}\n"
        f"\n"
        f"Reply APPROVE or REJECT"
    )


def fill_confirmation(symbol: str, txn_type: str, quantity: int,
                      fill_price: float, mode: str) -> str:
    return (
        f"{'PAPER ' if mode == 'PAPER' else ''}"
        f"{txn_type} {symbol} {quantity}x @ INR {fill_price:.2f} FILLED"
    )


def stop_triggered(symbol: str, stop_price: float, pnl: float) -> str:
    return (
        f"STOP TRIGGERED: {symbol} @ INR {stop_price:.2f}\n"
        f"P&L: INR {pnl:+.2f}"
    )


def eod_summary(date: str, trade_count: int, wins: int, losses: int,
                conservative_pnl: float, risk_pnl: float, total_pnl: float,
                mtd_pnl: float, best_trade: str, worst_trade: str,
                strategy_tomorrow: str) -> str:
    return (
        f"END OF DAY — {date}\n"
        f"{'─' * 35}\n"
        f"\n"
        f"Trades: {trade_count} (W:{wins} L:{losses})\n"
        f"Conservative P&L: INR {conservative_pnl:+.2f}\n"
        f"Risk bucket P&L: INR {risk_pnl:+.2f}\n"
        f"Total P&L today: INR {total_pnl:+.2f}\n"
        f"Month-to-date: INR {mtd_pnl:+.2f}\n"
        f"\n"
        f"Best trade: {best_trade}\n"
        f"Worst trade: {worst_trade}\n"
        f"\n"
        f"Tomorrow: {strategy_tomorrow}"
    )


def system_status(mode: str, agent_statuses: dict,
                  open_positions: int, todays_pnl: float) -> str:
    lines = [
        f"SYSTEM STATUS",
        f"{'─' * 35}",
        f"Mode: {mode}",
        f"Positions: {open_positions}",
        f"Today P&L: INR {todays_pnl:+.2f}",
        f"",
        f"Agents:",
    ]
    for agent_id, info in agent_statuses.items():
        state = info.get("state", "UNKNOWN")
        lines.append(f"  {agent_id}: {state}")
    return "\n".join(lines)
