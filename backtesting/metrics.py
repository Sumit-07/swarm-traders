"""Backtest performance metrics.

Calculates all metrics defined in the design doc Section 9:
Sharpe, Sortino, max drawdown, win rate, profit factor, etc.
Uses 252 trading days for annualization.
"""

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def calculate_metrics(trades: list, initial_capital: float,
                      equity_curve: pd.Series = None) -> dict:
    """Calculate all performance metrics from a list of closed trades.

    Args:
        trades: List of Trade objects (must be closed, with pnl filled)
        initial_capital: Starting capital in INR
        equity_curve: Optional pre-built equity curve Series

    Returns: dict of all metrics
    """
    if not trades:
        return _empty_metrics()

    pnls = [t.pnl for t in trades]
    pnl_pcts = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_trades = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    flat_count = total_trades - win_count - loss_count

    # Win rate
    win_rate = win_count / total_trades if total_trades > 0 else 0

    # Average win / loss
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0

    # Profit factor = gross profit / gross loss
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Total return
    total_return = sum(pnls)
    total_return_pct = (total_return / initial_capital) * 100

    # Build equity curve if not provided
    if equity_curve is None:
        equity_curve = pd.Series(
            np.cumsum([initial_capital] + pnls)
        )

    # Max drawdown
    peak = equity_curve.cummax()
    drawdown = equity_curve - peak
    max_drawdown = drawdown.min()
    max_drawdown_pct = (max_drawdown / peak[drawdown.idxmin()]) * 100 if max_drawdown < 0 else 0

    # Daily returns for Sharpe/Sortino
    # Group trades by day to get daily returns
    daily_pnls = _daily_pnl_series(trades)

    # Sharpe ratio (annualized)
    sharpe = _sharpe_ratio(daily_pnls)

    # Sortino ratio (annualized)
    sortino = _sortino_ratio(daily_pnls)

    # CAGR
    trading_days = len(daily_pnls) if len(daily_pnls) > 0 else 1
    years = trading_days / TRADING_DAYS_PER_YEAR
    final_value = initial_capital + total_return
    cagr = ((final_value / initial_capital) ** (1 / years) - 1) * 100 if years > 0 and final_value > 0 else 0

    # Calmar ratio = CAGR / max drawdown %
    calmar = abs(cagr / max_drawdown_pct) if max_drawdown_pct < 0 else float("inf")

    # Consecutive losses
    consecutive_losses_max = _max_consecutive_losses(pnls)

    # Average trade duration
    hold_bars = [t.hold_bars for t in trades if t.hold_bars > 0]
    avg_hold_bars = np.mean(hold_bars) if hold_bars else 0

    # Best / worst trade
    best_trade = max(pnls) if pnls else 0
    worst_trade = min(pnls) if pnls else 0

    # Total fees
    total_fees = sum(t.fees for t in trades)

    return {
        "total_trades": total_trades,
        "wins": win_count,
        "losses": loss_count,
        "flat": flat_count,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 4),
        "total_return": round(total_return, 2),
        "total_return_pct": round(total_return_pct, 4),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "calmar_ratio": round(calmar, 4),
        "cagr": round(cagr, 4),
        "consecutive_losses_max": consecutive_losses_max,
        "avg_hold_bars": round(avg_hold_bars, 1),
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "total_fees": round(total_fees, 2),
        "initial_capital": initial_capital,
        "final_capital": round(initial_capital + total_return, 2),
    }


def check_gate_criteria(metrics: dict, criteria: dict) -> dict:
    """Check if metrics pass the backtest gate criteria.

    Args:
        metrics: Output of calculate_metrics()
        criteria: BACKTEST_GATE_CRITERIA from config

    Returns: {criterion: {"required": x, "actual": y, "passed": bool}}
    """
    checks = {
        "win_rate": {
            "required": criteria["min_win_rate"],
            "actual": metrics["win_rate"],
            "passed": metrics["win_rate"] >= criteria["min_win_rate"],
        },
        "profit_factor": {
            "required": criteria["min_profit_factor"],
            "actual": metrics["profit_factor"],
            "passed": metrics["profit_factor"] >= criteria["min_profit_factor"],
        },
        "sharpe_ratio": {
            "required": criteria["min_sharpe_ratio"],
            "actual": metrics["sharpe_ratio"],
            "passed": metrics["sharpe_ratio"] >= criteria["min_sharpe_ratio"],
        },
        "max_drawdown": {
            "required": f"<= {criteria['max_drawdown_pct'] * 100}%",
            "actual": abs(metrics["max_drawdown_pct"]),
            "passed": abs(metrics["max_drawdown_pct"]) <= criteria["max_drawdown_pct"] * 100,
        },
        "consecutive_losses": {
            "required": f"<= {criteria['max_consecutive_losses']}",
            "actual": metrics["consecutive_losses_max"],
            "passed": metrics["consecutive_losses_max"] <= criteria["max_consecutive_losses"],
        },
        "total_trades": {
            "required": f">= {criteria['min_total_trades']}",
            "actual": metrics["total_trades"],
            "passed": metrics["total_trades"] >= criteria["min_total_trades"],
        },
    }

    return checks


def _daily_pnl_series(trades: list) -> pd.Series:
    """Group trades by exit day and sum P&L per day."""
    if not trades:
        return pd.Series(dtype=float)

    daily = {}
    for t in trades:
        if t.exit_time:
            try:
                day = pd.Timestamp(t.exit_time).strftime("%Y-%m-%d")
            except Exception:
                day = f"day_{t.exit_bar_idx}"
        else:
            day = f"day_{t.exit_bar_idx}"
        daily[day] = daily.get(day, 0) + t.pnl

    return pd.Series(daily).sort_index()


def _sharpe_ratio(daily_pnls: pd.Series, risk_free_rate: float = 0.06) -> float:
    """Annualized Sharpe ratio. Risk-free rate default: 6% (India)."""
    if daily_pnls.empty or daily_pnls.std() == 0:
        return 0.0
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = daily_pnls - daily_rf
    return float((excess.mean() / excess.std()) * np.sqrt(TRADING_DAYS_PER_YEAR))


def _sortino_ratio(daily_pnls: pd.Series, risk_free_rate: float = 0.06) -> float:
    """Annualized Sortino ratio — only penalizes downside volatility."""
    if daily_pnls.empty:
        return 0.0
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = daily_pnls - daily_rf
    downside = excess[excess < 0]
    downside_std = downside.std() if len(downside) > 1 else 0
    if downside_std == 0:
        return float("inf") if excess.mean() > 0 else 0.0
    return float((excess.mean() / downside_std) * np.sqrt(TRADING_DAYS_PER_YEAR))


def _max_consecutive_losses(pnls: list) -> int:
    """Find maximum streak of consecutive losing trades."""
    max_streak = 0
    current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _empty_metrics() -> dict:
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "flat": 0,
        "win_rate": 0, "avg_win": 0, "avg_loss": 0, "profit_factor": 0,
        "total_return": 0, "total_return_pct": 0, "sharpe_ratio": 0,
        "sortino_ratio": 0, "max_drawdown": 0, "max_drawdown_pct": 0,
        "calmar_ratio": 0, "cagr": 0, "consecutive_losses_max": 0,
        "avg_hold_bars": 0, "best_trade": 0, "worst_trade": 0,
        "total_fees": 0, "initial_capital": 0, "final_capital": 0,
    }
