"""Backtest runner — runs strategy backtests on historical data.

Usage:
    python -m backtesting.runner --strategy RSI_MEAN_REVERSION --start 2024-06-01 --end 2024-12-31
    python -m backtesting.runner --strategy all --start 2024-06-01 --end 2024-12-31
"""

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtesting.data_loader import DataLoader
from backtesting.metrics import calculate_metrics, check_gate_criteria
from backtesting.simulator import BacktestSimulator, Trade
from config import BACKTEST_GATE_CRITERIA, CAPITAL, DEFAULT_WATCHLIST
from tools.indicators import calculate_all
from tools.logger import get_agent_logger

logger = get_agent_logger("backtest_runner")

REPORTS_DIR = Path("backtesting/reports")


# --- Strategy Definitions ---

STRATEGY_CONFIGS = {
    "RSI_MEAN_REVERSION": {
        "watchlist": DEFAULT_WATCHLIST,
        "direction": "BOTH",
        "entry_indicator": "rsi",
        "entry_threshold": 32,       # RSI < 32 to enter
        "entry_compare": "below",
        "volume_confirmation": True,
        "volume_threshold": 1.2,
        "target_pct": 2.0,
        "stop_loss_pct": 1.5,
        "is_intraday": True,
        "max_trades_per_day": 2,
    },
    "VWAP_REVERSION": {
        "watchlist": DEFAULT_WATCHLIST,
        "direction": "BOTH",
        "entry_indicator": "vwap_deviation",
        "entry_threshold": -1.2,     # price > 1.2% below VWAP
        "entry_compare": "below",
        "volume_confirmation": False,
        "target_pct": 1.0,
        "stop_loss_pct": 0.8,
        "is_intraday": True,
        "max_trades_per_day": 2,
    },
    "OPENING_RANGE_BREAKOUT": {
        "watchlist": DEFAULT_WATCHLIST,
        "direction": "BOTH",
        "entry_indicator": "orb",
        "orb_bars": 3,               # first 15 min = 3 x 5-min bars
        "volume_confirmation": True,
        "volume_threshold": 1.5,
        "target_pct": 1.0,
        "stop_loss_pct": 0.7,
        "is_intraday": True,
        "max_trades_per_day": 1,
    },
    "SWING_MOMENTUM": {
        "watchlist": DEFAULT_WATCHLIST,
        "direction": "LONG",
        "entry_indicator": "adx",
        "entry_threshold": 25,
        "entry_compare": "above",
        "volume_confirmation": True,
        "volume_threshold": 1.3,
        "target_pct": 4.0,
        "stop_loss_pct": 2.5,
        "is_intraday": False,
        "max_trades_per_day": 1,
    },
    "NIFTY_OPTIONS_BUYING": {
        "watchlist": ["NIFTY"],
        "direction": "LONG",
        "entry_indicator": "rsi",
        "entry_threshold": 35,
        "entry_compare": "below",
        "volume_confirmation": False,
        "target_pct": 5.0,
        "stop_loss_pct": 3.0,
        "is_intraday": True,
        "max_trades_per_day": 1,
    },
    "STRADDLE_BUY": {
        "strategy_type": "options",
        "bucket": "risk",
        "vix_min": 22.0,
        "vix_max": 32.0,
        "direction": "BOTH",
        "watchlist": ["NIFTY"],
        "entry_indicator": "straddle",
        "entry_time_window": ("09:20", "10:30"),
        "target_combined_multiplier": 2.0,
        "stop_loss_combined_pct": 40.0,
        "max_cost_per_trade": 8000,
        "max_combined_cost_inr": 8000,
        "lot_size": 65,
        "is_intraday": True,
        "max_trades_per_day": 1,
    },
    "VOLATILITY_ADJUSTED_SWING": {
        "strategy_type": "swing",
        "bucket": "conservative",
        "vix_min": 22.0,
        "vix_max": 32.0,
        "direction": "LONG",
        "watchlist": DEFAULT_WATCHLIST,
        "entry_indicator": "adx",
        "entry_threshold": 28,
        "entry_compare": "above",
        "volume_confirmation": True,
        "volume_threshold": 1.3,
        "target_pct": 5.5,
        "stop_loss_pct": 3.5,
        "position_size_modifier": 0.57,
        "trailing_stop": True,
        "is_intraday": False,
        "max_trades_per_day": 1,
    },
}


class BacktestResult:
    """Holds the results of a backtest run."""

    def __init__(self, strategy: str, trades: list[Trade],
                 metrics: dict, gate_checks: dict,
                 equity_curve: pd.Series, config: dict):
        self.strategy = strategy
        self.trades = trades
        self.metrics = metrics
        self.gate_checks = gate_checks
        self.equity_curve = equity_curve
        self.config = config
        self.passed_gate = all(c["passed"] for c in gate_checks.values())

    def summary(self) -> str:
        """Return a printable summary."""
        m = self.metrics
        lines = [
            f"\n{'=' * 60}",
            f"BACKTEST RESULTS: {self.strategy}",
            f"{'=' * 60}",
            f"Trades: {m['total_trades']} (W:{m['wins']} L:{m['losses']})",
            f"Win Rate: {m['win_rate']:.1%}",
            f"Profit Factor: {m['profit_factor']:.2f}",
            f"Total Return: INR {m['total_return']:+,.2f} ({m['total_return_pct']:+.2f}%)",
            f"Sharpe Ratio: {m['sharpe_ratio']:.2f}",
            f"Sortino Ratio: {m['sortino_ratio']:.2f}",
            f"Max Drawdown: INR {m['max_drawdown']:,.2f} ({m['max_drawdown_pct']:.2f}%)",
            f"Calmar Ratio: {m['calmar_ratio']:.2f}",
            f"Max Consecutive Losses: {m['consecutive_losses_max']}",
            f"Avg Hold: {m['avg_hold_bars']:.0f} bars",
            f"Best Trade: INR {m['best_trade']:+,.2f}",
            f"Worst Trade: INR {m['worst_trade']:+,.2f}",
            f"Total Fees: INR {m['total_fees']:,.2f}",
            f"",
            f"GATE CRITERIA: {'PASSED' if self.passed_gate else 'FAILED'}",
        ]
        for name, check in self.gate_checks.items():
            status = "PASS" if check["passed"] else "FAIL"
            lines.append(
                f"  [{status}] {name}: {check['actual']} "
                f"(required: {check['required']})"
            )
        lines.append("=" * 60)
        return "\n".join(lines)

    def to_html(self, output_dir: str = None) -> str:
        """Generate an HTML report.

        Returns the file path of the generated report.
        """
        output_dir = Path(output_dir or REPORTS_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.strategy}_{timestamp}.html"
        filepath = output_dir / filename

        m = self.metrics
        gate_status = "PASSED" if self.passed_gate else "FAILED"

        # Build trade table rows
        trade_rows = ""
        for t in self.trades:
            pnl_color = "green" if t.pnl > 0 else "red" if t.pnl < 0 else "gray"
            trade_rows += f"""
            <tr>
                <td>{t.trade_id}</td>
                <td>{t.symbol}</td>
                <td>{t.direction}</td>
                <td>{t.fill_price:.2f}</td>
                <td>{t.exit_fill_price:.2f}</td>
                <td>{t.quantity}</td>
                <td style="color:{pnl_color}">{t.pnl:+.2f}</td>
                <td>{t.status}</td>
                <td>{t.hold_bars}</td>
            </tr>"""

        # Gate criteria rows
        gate_rows = ""
        for name, check in self.gate_checks.items():
            color = "green" if check["passed"] else "red"
            gate_rows += f"""
            <tr>
                <td>{name}</td>
                <td>{check['required']}</td>
                <td style="color:{color}">{check['actual']}</td>
                <td style="color:{color}">{"PASS" if check["passed"] else "FAIL"}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Backtest: {self.strategy}</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; margin: 40px; background: #f5f5f5; }}
        h1 {{ color: #333; }}
        .metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }}
        .metric {{ background: white; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .metric .value {{ font-size: 24px; font-weight: bold; }}
        .metric .label {{ color: #666; font-size: 12px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        th, td {{ padding: 10px 15px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #333; color: white; }}
        .gate {{ font-size: 20px; font-weight: bold; color: {"green" if self.passed_gate else "red"}; }}
    </style>
</head>
<body>
    <h1>Backtest Report: {self.strategy}</h1>
    <p>Generated: {datetime.now().isoformat()}</p>
    <p class="gate">Gate Criteria: {gate_status}</p>

    <div class="metrics">
        <div class="metric"><div class="value">{m['total_trades']}</div><div class="label">Total Trades</div></div>
        <div class="metric"><div class="value">{m['win_rate']:.1%}</div><div class="label">Win Rate</div></div>
        <div class="metric"><div class="value">{m['profit_factor']:.2f}</div><div class="label">Profit Factor</div></div>
        <div class="metric"><div class="value">{m['sharpe_ratio']:.2f}</div><div class="label">Sharpe Ratio</div></div>
        <div class="metric"><div class="value">INR {m['total_return']:+,.0f}</div><div class="label">Total Return</div></div>
        <div class="metric"><div class="value">{m['max_drawdown_pct']:.1f}%</div><div class="label">Max Drawdown</div></div>
        <div class="metric"><div class="value">{m['consecutive_losses_max']}</div><div class="label">Max Consec. Losses</div></div>
        <div class="metric"><div class="value">INR {m['total_fees']:,.0f}</div><div class="label">Total Fees</div></div>
    </div>

    <h2>Gate Criteria</h2>
    <table>
        <tr><th>Criterion</th><th>Required</th><th>Actual</th><th>Status</th></tr>
        {gate_rows}
    </table>

    <h2>Trade Log ({len(self.trades)} trades)</h2>
    <table>
        <tr><th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Qty</th><th>P&L</th><th>Status</th><th>Bars</th></tr>
        {trade_rows}
    </table>
</body>
</html>"""

        filepath.write_text(html)
        logger.info(f"Report saved: {filepath}")
        return str(filepath)


class BacktestRunner:
    """Runs strategy backtests on historical data."""

    def __init__(self, initial_capital: float = None):
        self.capital = initial_capital or CAPITAL["conservative_bucket"]
        self.data_loader = DataLoader(cache_dir="data/backtest_cache")
        self.simulator = BacktestSimulator()

    def run(self, strategy: str, start: str, end: str,
            interval: str = "5") -> BacktestResult:
        """Run a backtest for a strategy.

        Args:
            strategy: Strategy name (from STRATEGY_CONFIGS)
            start: "YYYY-MM-DD"
            end: "YYYY-MM-DD"
            interval: Bar interval ("5" for 5-min)

        Returns: BacktestResult
        """
        config = STRATEGY_CONFIGS.get(strategy)
        if not config:
            raise ValueError(f"Unknown strategy: {strategy}. "
                           f"Available: {list(STRATEGY_CONFIGS.keys())}")

        logger.info(f"Starting backtest: {strategy} ({start} to {end})")

        # Load data
        data = self.data_loader.load_multiple(
            config["watchlist"], start, end, interval
        )

        if not data:
            logger.error("No data loaded — cannot run backtest")
            return BacktestResult(
                strategy, [], calculate_metrics([], self.capital),
                check_gate_criteria(calculate_metrics([], self.capital),
                                    BACKTEST_GATE_CRITERIA),
                pd.Series([self.capital]), config,
            )

        # Run strategy-specific logic
        all_trades = []
        for symbol, df in data.items():
            indicators = calculate_all(df)
            trades = self._run_strategy(symbol, df, indicators, config)
            all_trades.extend(trades)

        # Sort by entry time
        all_trades.sort(key=lambda t: t.entry_fill_idx)

        # Calculate metrics
        metrics = calculate_metrics(all_trades, self.capital)

        # Build equity curve
        equity = [self.capital]
        for t in all_trades:
            equity.append(equity[-1] + t.pnl)
        equity_curve = pd.Series(equity)

        # Check gate criteria
        gate_checks = check_gate_criteria(metrics, BACKTEST_GATE_CRITERIA)

        return BacktestResult(
            strategy, all_trades, metrics, gate_checks,
            equity_curve, config,
        )

    def _run_strategy(self, symbol: str, df: pd.DataFrame,
                      indicators: dict, config: dict) -> list[Trade]:
        """Run a strategy on a single symbol's data."""
        trades = []
        open_trades: list[Trade] = []
        daily_trade_count = {}

        for i in range(1, len(df) - 1):
            bar = df.iloc[i]
            next_bar = df.iloc[i + 1]

            bar_time = str(bar.get("datetime", ""))
            bar_date = bar_time[:10] if bar_time else ""

            # Check and close open trades first
            for trade in open_trades[:]:
                closed = self.simulator.check_exit(
                    trade, bar["high"], bar["low"], bar["close"],
                    i, bar_time, config.get("is_intraday", True),
                )
                if closed:
                    trades.append(closed)
                    open_trades.remove(trade)

            # Check for new entry signals
            if not self.simulator.can_signal(bar.get("datetime", "")):
                continue

            # Limit trades per day
            day_count = daily_trade_count.get(bar_date, 0)
            max_per_day = config.get("max_trades_per_day", 2)
            if day_count >= max_per_day:
                continue

            # Check entry condition
            signal = self._check_signal(i, df, indicators, config)
            if not signal:
                continue

            # Don't open if we already have max open trades
            if len(open_trades) >= max_per_day:
                continue

            # Calculate stop and target
            entry_price = next_bar["open"]
            target_pct = config.get("target_pct", 2.0)
            stop_pct = config.get("stop_loss_pct", 1.5)

            if config.get("direction") == "LONG":
                stop_loss = round(entry_price * (1 - stop_pct / 100), 2)
                target = round(entry_price * (1 + target_pct / 100), 2)
            else:
                stop_loss = round(entry_price * (1 + stop_pct / 100), 2)
                target = round(entry_price * (1 - target_pct / 100), 2)

            # Simulate entry
            trade = self.simulator.simulate_entry(
                signal_bar_idx=i,
                next_bar_open=entry_price,
                direction=config.get("direction", "LONG"),
                symbol=symbol,
                strategy=config.get("entry_indicator", ""),
                stop_loss=stop_loss,
                target=target,
                quantity=self._position_size(entry_price, stop_loss),
                signal_time=bar_time,
            )
            open_trades.append(trade)
            daily_trade_count[bar_date] = day_count + 1

        # Force close any remaining open trades at last bar
        last_bar = df.iloc[-1]
        for trade in open_trades:
            closed = self.simulator.force_close(
                trade, last_bar["close"], len(df) - 1,
                str(last_bar.get("datetime", "")),
                config.get("is_intraday", True),
            )
            trades.append(closed)

        return trades

    def _check_signal(self, idx: int, df: pd.DataFrame,
                      indicators: dict, config: dict) -> bool:
        """Check if entry conditions are met at bar index."""
        indicator_name = config.get("entry_indicator", "")

        if indicator_name == "rsi":
            rsi = indicators["rsi"]
            if idx >= len(rsi) or pd.isna(rsi.iloc[idx]):
                return False
            threshold = config.get("entry_threshold", 32)
            compare = config.get("entry_compare", "below")
            if compare == "below" and rsi.iloc[idx] >= threshold:
                return False
            if compare == "above" and rsi.iloc[idx] <= threshold:
                return False

        elif indicator_name == "vwap_deviation":
            vwap = indicators["vwap"]
            if idx >= len(vwap) or pd.isna(vwap.iloc[idx]):
                return False
            close = df["close"].iloc[idx]
            deviation_pct = ((close - vwap.iloc[idx]) / vwap.iloc[idx]) * 100
            threshold = config.get("entry_threshold", -1.2)
            if deviation_pct > threshold:
                return False

        elif indicator_name == "orb":
            orb_bars = config.get("orb_bars", 3)
            # Need at least orb_bars to establish the range
            # Check if current bar is within same day and past ORB period
            dt = df["datetime"].iloc[idx] if "datetime" in df.columns else None
            if dt is None:
                return False
            day_mask = df["datetime"].dt.date == pd.Timestamp(dt).date()
            day_data = df[day_mask]
            if len(day_data) < orb_bars + 1:
                return False
            day_start_idx = day_data.index[0]
            if idx - day_start_idx < orb_bars:
                return False  # still within ORB formation period
            orb_high = day_data.iloc[:orb_bars]["high"].max()
            if df["close"].iloc[idx] <= orb_high:
                return False  # no breakout

        elif indicator_name == "adx":
            adx = indicators["adx"]
            if idx >= len(adx) or pd.isna(adx.iloc[idx]):
                return False
            threshold = config.get("entry_threshold", 25)
            if adx.iloc[idx] < threshold:
                return False
            # Also check RSI is in 55-70 range for momentum
            rsi = indicators["rsi"]
            if idx >= len(rsi) or pd.isna(rsi.iloc[idx]):
                return False
            if not (55 <= rsi.iloc[idx] <= 70):
                return False

        else:
            return False

        # Volume confirmation
        if config.get("volume_confirmation"):
            vr = indicators["volume_ratio"]
            if idx >= len(vr) or pd.isna(vr.iloc[idx]):
                return False
            vol_threshold = config.get("volume_threshold", 1.2)
            if vr.iloc[idx] < vol_threshold:
                return False

        return True

    def _position_size(self, entry_price: float, stop_loss: float) -> int:
        """Calculate position size based on 2% risk rule."""
        from config import RISK_LIMITS
        max_risk = self.capital * RISK_LIMITS["max_single_trade_risk_pct"]
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share <= 0:
            return 1
        size = int(max_risk / risk_per_share)
        return max(1, size)

    def run_all(self, start: str, end: str,
                interval: str = "5") -> dict[str, BacktestResult]:
        """Run backtests for all strategies.

        Returns: {strategy_name: BacktestResult}
        """
        results = {}
        for strategy in STRATEGY_CONFIGS:
            try:
                result = self.run(strategy, start, end, interval)
                results[strategy] = result
                print(result.summary())
            except Exception as e:
                logger.error(f"Backtest failed for {strategy}: {e}")
        return results


def main():
    parser = argparse.ArgumentParser(description="Run strategy backtests")
    parser.add_argument("--strategy", default="RSI_MEAN_REVERSION",
                        help="Strategy name or 'all'")
    parser.add_argument("--start", default="2024-06-01",
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-12-31",
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--interval", default="5",
                        help="Bar interval (1, 5, 15, 60, D)")
    parser.add_argument("--capital", type=float, default=None,
                        help="Initial capital (INR)")
    parser.add_argument("--report", action="store_true",
                        help="Generate HTML report")
    args = parser.parse_args()

    runner = BacktestRunner(initial_capital=args.capital)

    if args.strategy.lower() == "all":
        results = runner.run_all(args.start, args.end, args.interval)
        if args.report:
            for name, result in results.items():
                result.to_html()
        # Print comparison table
        if results:
            print(f"\n{'=' * 100}")
            print(f"STRATEGY COMPARISON ({args.start} to {args.end})")
            print(f"{'=' * 100}")
            header = (f"{'Strategy':<28} {'Trades':>6} {'Win%':>6} "
                      f"{'PF':>6} {'Return%':>9} {'Sharpe':>7} "
                      f"{'MaxDD%':>7} {'CL':>4} {'Gate':>6}")
            print(header)
            print("-" * 100)
            for name, r in results.items():
                m = r.metrics
                gate_pass = sum(1 for c in r.gate_checks.values() if c["passed"])
                gate_total = len(r.gate_checks)
                gate_str = f"{gate_pass}/{gate_total}"
                if r.passed_gate:
                    gate_str += " ✓"
                print(f"{name:<28} {m['total_trades']:>6} "
                      f"{m['win_rate']:>5.1%} {m['profit_factor']:>6.2f} "
                      f"{m['total_return_pct']:>+8.2f}% "
                      f"{m['sharpe_ratio']:>7.2f} "
                      f"{m['max_drawdown_pct']:>6.1f}% "
                      f"{m['consecutive_losses_max']:>4} {gate_str:>6}")
            print("=" * 100)
            passed = [n for n, r in results.items() if r.passed_gate]
            if passed:
                print(f"\nStrategies PASSING all gate criteria: {', '.join(passed)}")
            else:
                print(f"\nNo strategies passed all gate criteria.")
    else:
        result = runner.run(args.strategy, args.start, args.end, args.interval)
        print(result.summary())
        if args.report:
            path = result.to_html()
            print(f"\nReport saved: {path}")


if __name__ == "__main__":
    main()
