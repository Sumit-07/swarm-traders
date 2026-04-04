"""Page 1: Live Positions — shows open positions and quick P&L."""

import streamlit as st
import pandas as pd


def render():
    st.header("Open Positions")

    try:
        from dashboard.data_helpers import (
            get_redis, get_positions, get_market_snapshot, get_system_mode,
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

    # Build positions table
    df = pd.DataFrame(positions)
    display_cols = [
        "symbol", "direction", "entry_price", "quantity",
        "stop_loss", "target", "bucket", "entry_time",
    ]
    available = [c for c in display_cols if c in df.columns]
    st.dataframe(df[available], use_container_width=True, hide_index=True)

    # Position summary
    st.subheader("Summary")
    from config import CAPITAL
    total_deployed = sum(
        p.get("entry_price", 0) * p.get("quantity", 0) for p in positions
    )
    conservative_capital = CAPITAL["conservative_bucket"]
    utilization = (total_deployed / conservative_capital * 100) if conservative_capital > 0 else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Capital Deployed", f"INR {total_deployed:,.0f}")
    c2.metric("Utilization", f"{utilization:.1f}%")
    c3.metric("Available", f"INR {conservative_capital - total_deployed:,.0f}")
