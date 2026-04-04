"""Page 4: Trade Log — searchable table of all trades with filters."""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta


def render():
    st.header("Trade Log")

    try:
        from dashboard.data_helpers import get_sqlite_engine, get_trades_df
        engine = get_sqlite_engine()
    except Exception as e:
        st.warning(f"Cannot connect to database: {e}")
        return

    # Filters
    col1, col2, col3 = st.columns(3)
    date_filter = col1.date_input(
        "Date", value=datetime.now().date(),
    )
    status_filter = col2.selectbox(
        "Status", ["All", "OPEN", "CLOSED_TARGET", "CLOSED_STOP",
                    "CLOSED_TIME", "CLOSED_EOD"],
    )
    limit = col3.number_input("Max rows", min_value=10, max_value=500, value=100)

    # Fetch
    date_str = date_filter.strftime("%Y-%m-%d") if date_filter else None
    trades_df = get_trades_df(engine, date=date_str, limit=limit)

    if trades_df.empty:
        st.info(f"No trades found for {date_str or 'all dates'}.")
        return

    # Apply status filter
    if status_filter != "All" and "status" in trades_df.columns:
        trades_df = trades_df[trades_df["status"] == status_filter]

    # Display count
    st.caption(f"Showing {len(trades_df)} trades")

    # Format and display
    display_cols = [
        "trade_id", "symbol", "direction", "bucket", "strategy",
        "entry_price", "exit_price", "quantity", "stop_loss", "target",
        "pnl", "pnl_pct", "fees", "status", "entry_time", "exit_time", "mode",
    ]
    available = [c for c in display_cols if c in trades_df.columns]
    df_display = trades_df[available].copy()

    # Color P&L
    def color_pnl(val):
        if pd.isna(val):
            return ""
        if val > 0:
            return "color: green"
        elif val < 0:
            return "color: red"
        return ""

    if "pnl" in df_display.columns:
        styled = df_display.style.map(color_pnl, subset=["pnl"])
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.dataframe(df_display, use_container_width=True, hide_index=True)

    # Summary for the filtered set
    if "pnl" in df_display.columns:
        total_pnl = df_display["pnl"].fillna(0).sum()
        total_fees = df_display["fees"].fillna(0).sum() if "fees" in df_display.columns else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("Trades Shown", len(df_display))
        c2.metric("Net P&L", f"INR {total_pnl:,.2f}")
        c3.metric("Total Fees", f"INR {total_fees:,.2f}")
