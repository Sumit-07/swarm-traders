"""Page 6: Signals & Quality — signal generation, validity, and confidence."""

import streamlit as st
import pandas as pd
import plotly.express as px


def render():
    st.header("Signals & Quality")

    try:
        from dashboard.data_helpers import get_sqlite_engine, get_signals_df
        engine = get_sqlite_engine()
    except Exception as e:
        st.warning(f"Cannot connect to database: {e}")
        return

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        date_filter = st.date_input("Date", value=None)
    with col2:
        limit = st.selectbox("Limit", [50, 100, 200, 500], index=1)
    with col3:
        strategy_filter = st.text_input("Strategy filter", "")

    date_str = date_filter.isoformat() if date_filter else None
    df = get_signals_df(engine, date=date_str, limit=limit)

    if df.empty:
        st.info("No signals recorded yet.")
        return

    # Apply strategy filter
    if strategy_filter:
        df = df[df["strategy"].str.contains(strategy_filter, case=False, na=False)]

    # Key metrics
    total = len(df)
    valid = df["valid"].sum() if "valid" in df.columns else 0
    invalid = total - valid
    valid_pct = (valid / total * 100) if total > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Signals", total)
    c2.metric("Valid", int(valid))
    c3.metric("Invalid", int(invalid))
    c4.metric("Valid %", f"{valid_pct:.1f}%")

    st.divider()

    # Charts
    chart1, chart2 = st.columns(2)

    with chart1:
        if "valid" in df.columns:
            valid_counts = df["valid"].map({1: "Valid", 0: "Invalid"}).value_counts()
            fig = px.pie(
                values=valid_counts.values,
                names=valid_counts.index,
                title="Signal Validity",
                color_discrete_map={"Valid": "#2ecc71", "Invalid": "#e74c3c"},
            )
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)

    with chart2:
        if "strategy" in df.columns:
            strat_counts = df["strategy"].value_counts().head(10)
            fig = px.bar(
                x=strat_counts.index,
                y=strat_counts.values,
                title="Signals by Strategy",
                labels={"x": "Strategy", "y": "Count"},
            )
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)

    # Signals table
    st.subheader("Recent Signals")
    display_cols = [
        "symbol", "strategy", "signal_type", "confidence",
        "valid", "invalidation_reason", "created_at",
    ]
    available = [c for c in display_cols if c in df.columns]
    st.dataframe(df[available], use_container_width=True, hide_index=True)
