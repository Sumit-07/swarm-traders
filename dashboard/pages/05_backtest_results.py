"""Page 5: Backtest Results — view and compare strategy backtest reports."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path


REPORTS_DIR = Path("backtesting/reports")


def render():
    st.header("Backtest Results")

    # Find available reports
    if not REPORTS_DIR.exists():
        st.info("No backtest reports found. Run a backtest first.")
        st.code("python -m backtesting.runner --strategy RSI_MEAN_REVERSION "
                "--start 2024-06-01 --end 2024-12-31 --report")
        return

    html_reports = sorted(REPORTS_DIR.glob("*.html"), reverse=True)
    json_reports = sorted(REPORTS_DIR.glob("*.json"), reverse=True)

    if not html_reports and not json_reports:
        st.info("No backtest reports found. Run a backtest to generate reports.")
        return

    # Report selector
    report_names = [r.stem for r in html_reports]
    if report_names:
        selected = st.selectbox("Select Report", report_names)
        report_path = REPORTS_DIR / f"{selected}.html"

        if report_path.exists():
            html_content = report_path.read_text()
            st.components.v1.html(html_content, height=800, scrolling=True)

    # If JSON results exist, show comparison table
    if json_reports:
        st.subheader("Strategy Comparison")

        import json
        rows = []
        for jf in json_reports:
            try:
                data = json.loads(jf.read_text())
                metrics = data.get("metrics", data)
                rows.append({
                    "Strategy": data.get("strategy", jf.stem),
                    "Total Trades": metrics.get("total_trades", 0),
                    "Win Rate": f"{metrics.get('win_rate', 0):.1%}",
                    "Profit Factor": metrics.get("profit_factor", 0),
                    "Sharpe": metrics.get("sharpe_ratio", 0),
                    "Max DD %": f"{abs(metrics.get('max_drawdown_pct', 0)):.1f}%",
                    "Total Return": f"INR {metrics.get('total_return', 0):,.0f}",
                    "CAGR %": f"{metrics.get('cagr', 0):.1f}%",
                })
            except Exception:
                continue

        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Gate criteria check
            st.subheader("Gate Criteria")
            from config import BACKTEST_GATE_CRITERIA as gc
            st.markdown(f"""
            | Criterion | Required |
            |---|---|
            | Win Rate | >= {gc['min_win_rate']:.0%} |
            | Profit Factor | >= {gc['min_profit_factor']} |
            | Sharpe Ratio | >= {gc['min_sharpe_ratio']} |
            | Max Drawdown | <= {gc['max_drawdown_pct']:.0%} |
            | Max Consecutive Losses | <= {gc['max_consecutive_losses']} |
            | Min Total Trades | >= {gc['min_total_trades']} |
            """)
