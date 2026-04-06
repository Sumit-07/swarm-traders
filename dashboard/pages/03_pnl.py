"""Page 3: P&L — daily and cumulative P&L charts, drawdown, and stats."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def render():
    st.header("Profit & Loss")

    try:
        from dashboard.data_helpers import (
            get_sqlite_engine, get_daily_pnl_df, get_trades_df,
            compute_trade_stats,
        )
        engine = get_sqlite_engine()
    except Exception as e:
        st.warning(f"Cannot connect to database: {e}")
        return

    # Controls
    col1, col2 = st.columns(2)
    days = col1.selectbox("Period", [7, 14, 30, 60, 90], index=2)
    bucket = col2.selectbox("Bucket", ["All", "Conservative", "Risk"])

    # Fetch data
    pnl_df = get_daily_pnl_df(engine, days=days)
    trades_df = get_trades_df(engine, limit=500)

    if pnl_df.empty and trades_df.empty:
        st.info("No trading data yet. Start paper trading to see P&L.")
        return

    # --- Daily P&L chart ---
    if not pnl_df.empty:
        pnl_col = "total_pnl"
        if bucket == "Conservative":
            pnl_col = "conservative_pnl"
        elif bucket == "Risk":
            pnl_col = "risk_pnl"

        if pnl_col in pnl_df.columns:
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                subplot_titles=("Daily P&L", "Cumulative P&L"),
                                vertical_spacing=0.12)

            # Daily bars
            colors = ["green" if v >= 0 else "red" for v in pnl_df[pnl_col]]
            fig.add_trace(
                go.Bar(x=pnl_df["date"], y=pnl_df[pnl_col],
                        marker_color=colors, name="Daily P&L"),
                row=1, col=1,
            )

            # Cumulative line
            cumulative = pnl_df[pnl_col].cumsum()
            fig.add_trace(
                go.Scatter(x=pnl_df["date"], y=cumulative,
                           mode="lines+markers", name="Cumulative",
                           line=dict(color="royalblue", width=2)),
                row=2, col=1,
            )

            fig.update_layout(height=500, showlegend=False)
            fig.update_yaxes(title_text="INR", row=1, col=1)
            fig.update_yaxes(title_text="INR", row=2, col=1)
            st.plotly_chart(fig, use_container_width=True)

    # --- Stats cards ---
    stats = compute_trade_stats(trades_df)
    st.subheader("Trade Statistics")

    # Calculate win/loss streak
    streak_label = "N/A"
    if not trades_df.empty and "pnl" in trades_df.columns:
        closed = trades_df[trades_df["status"] == "CLOSED"].sort_values(
            "exit_time", ascending=False
        )
        if not closed.empty:
            streak = 0
            first_sign = None
            for _, row in closed.iterrows():
                pnl_val = row.get("pnl", 0) or 0
                sign = "W" if pnl_val > 0 else "L" if pnl_val < 0 else None
                if sign is None:
                    continue
                if first_sign is None:
                    first_sign = sign
                if sign == first_sign:
                    streak += 1
                else:
                    break
            if first_sign:
                streak_label = f"{streak}{first_sign}"

    # Best/worst day
    best_day = worst_day = "N/A"
    if not pnl_df.empty and pnl_col in pnl_df.columns:
        best_idx = pnl_df[pnl_col].idxmax()
        worst_idx = pnl_df[pnl_col].idxmin()
        best_val = pnl_df.loc[best_idx, pnl_col]
        worst_val = pnl_df.loc[worst_idx, pnl_col]
        best_day = f"INR {best_val:+,.0f}"
        worst_day = f"INR {worst_val:+,.0f}"

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Total Trades", stats["total"])
    c2.metric("Wins", stats["wins"])
    c3.metric("Losses", stats["losses"])
    c4.metric("Win Rate", f"{stats['win_rate']:.1%}")
    c5.metric("Net P&L", f"INR {stats['total_pnl']:,.2f}")
    c6.metric("Streak", streak_label)
    c7.metric("Best / Worst Day", best_day, worst_day)

    # --- Drawdown chart (from cumulative P&L) ---
    if not pnl_df.empty and pnl_col in pnl_df.columns:
        cumulative = pnl_df[pnl_col].cumsum()
        peak = cumulative.cummax()
        drawdown = cumulative - peak

        if drawdown.min() < 0:
            st.subheader("Drawdown")
            fig_dd = go.Figure()
            fig_dd.add_trace(
                go.Scatter(x=pnl_df["date"], y=drawdown,
                           fill="tozeroy", fillcolor="rgba(255,0,0,0.1)",
                           line=dict(color="red", width=1),
                           name="Drawdown"),
            )
            fig_dd.update_layout(height=250, yaxis_title="INR")
            st.plotly_chart(fig_dd, use_container_width=True)
