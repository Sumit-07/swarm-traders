"""Page 1: Live Positions — shows open positions and quick P&L."""

import streamlit as st
import pandas as pd


def render():
    st.header("Open Positions")

    try:
        from dashboard.data_helpers import (
            get_redis, get_positions, get_market_snapshot, get_system_mode,
            get_position_ltp,
        )
        r = get_redis()
    except Exception as e:
        st.warning(f"Cannot connect to Redis: {e}")
        st.info("Start Redis and the trading system to see live data.")
        return

    mode = get_system_mode(r)
    positions = get_positions(r)
    snapshot = get_market_snapshot(r)

    # Market overview
    col1, col2, col3, col4 = st.columns(4)
    nifty = snapshot.get("nifty", {})
    banknifty = snapshot.get("banknifty", {})
    vix = snapshot.get("indiavix", snapshot.get("vix", {}))

    col1.metric("Nifty 50", f"{nifty.get('ltp', 'N/A')}")
    col2.metric("BankNifty", f"{banknifty.get('ltp', 'N/A')}")
    col3.metric("India VIX", f"{vix.get('ltp', 'N/A')}")
    col4.metric("Open Positions", len(positions))

    st.divider()

    if not positions:
        st.info("No open positions.")
        return

    # Build positions table with unrealized P&L
    total_unrealized = 0
    for p in positions:
        symbol = p.get("symbol", "")
        ltp = get_position_ltp(r, symbol)
        if ltp:
            entry = p.get("entry_price", 0)
            qty = p.get("quantity", 0)
            direction = p.get("direction", "LONG")
            if direction == "SHORT":
                pnl = (entry - ltp) * qty
            else:
                pnl = (ltp - entry) * qty
            p["ltp"] = round(ltp, 2)
            p["unrealized_pnl"] = round(pnl, 2)
            total_unrealized += pnl
        else:
            p["ltp"] = None
            p["unrealized_pnl"] = None

    df = pd.DataFrame(positions)
    display_cols = [
        "symbol", "direction", "entry_price", "ltp", "quantity",
        "unrealized_pnl", "stop_loss", "target", "strategy",
        "bucket", "entry_time",
    ]
    available = [c for c in display_cols if c in df.columns]

    # Color-code P&L column
    if "unrealized_pnl" in df.columns:
        def color_pnl(val):
            if pd.isna(val):
                return ""
            return "color: #2ecc71" if val >= 0 else "color: #e74c3c"

        styled = df[available].style.applymap(color_pnl, subset=["unrealized_pnl"])
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.dataframe(df[available], use_container_width=True, hide_index=True)

    # Position summary
    st.subheader("Summary")
    from config import CAPITAL
    total_deployed = sum(
        p.get("entry_price", 0) * p.get("quantity", 0) for p in positions
    )
    conservative_capital = CAPITAL["conservative_bucket"]
    utilization = (total_deployed / conservative_capital * 100) if conservative_capital > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Capital Deployed", f"INR {total_deployed:,.0f}")
    c2.metric("Utilization", f"{utilization:.1f}%")
    c3.metric("Available", f"INR {conservative_capital - total_deployed:,.0f}")
    pnl_color = "normal" if total_unrealized >= 0 else "inverse"
    c4.metric("Unrealized P&L", f"INR {total_unrealized:,.0f}", delta_color=pnl_color)
