"""Page 10: Watchlist Live — real-time indicators for all watchlist symbols."""

import streamlit as st
import pandas as pd

from config import DEFAULT_WATCHLIST


def render():
    st.header("Watchlist Live")

    try:
        from dashboard.data_helpers import get_redis, get_watchlist_ticks
        r = get_redis()
    except Exception as e:
        st.warning(f"Cannot connect to Redis: {e}")
        return

    # Auto-refresh toggle
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)
    if auto_refresh:
        import time
        st.empty()
        time.sleep(0)  # Streamlit will rerun via experimental_rerun below

    ticks = get_watchlist_ticks(r, DEFAULT_WATCHLIST)

    if not ticks:
        st.info("No watchlist data available. Data agent may not be running.")
        return

    # Key metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("Symbols Tracked", len(ticks))

    oversold = sum(1 for t in ticks if t.get("rsi", 50) < 30)
    overbought = sum(1 for t in ticks if t.get("rsi", 50) > 70)
    c2.metric("RSI Oversold (<30)", oversold)
    c3.metric("RSI Overbought (>70)", overbought)

    st.divider()

    # Build DataFrame
    rows = []
    for t in ticks:
        close = t.get("close", 0) or t.get("ltp", 0)
        vwap = t.get("vwap", 0)
        vwap_dev = ((close - vwap) / vwap * 100) if vwap else 0

        rows.append({
            "Symbol": t.get("symbol", ""),
            "LTP": close,
            "RSI": round(t.get("rsi", 0), 1),
            "VWAP Dev %": round(vwap_dev, 2),
            "ADX": round(t.get("adx", 0), 1),
            "Vol Ratio": round(t.get("volume_ratio", 0), 2),
            "ATR": round(t.get("atr", 0), 2),
            "MACD": round(t.get("macd", 0), 2),
        })

    df = pd.DataFrame(rows)

    # Color-code RSI
    def highlight_rsi(val):
        if val < 30:
            return "background-color: #27ae60; color: white"
        elif val > 70:
            return "background-color: #e74c3c; color: white"
        elif val < 40:
            return "background-color: #2ecc7144"
        elif val > 60:
            return "background-color: #e74c3c44"
        return ""

    def highlight_vwap(val):
        if val < -1.2:
            return "background-color: #27ae6044"
        elif val > 1.2:
            return "background-color: #e74c3c44"
        return ""

    styled = df.style.applymap(highlight_rsi, subset=["RSI"])
    styled = styled.applymap(highlight_vwap, subset=["VWAP Dev %"])

    st.dataframe(styled, use_container_width=True, hide_index=True, height=600)

    if auto_refresh:
        import time as _t
        _t.sleep(30)
        st.rerun()
