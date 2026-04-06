"""Page 9: Optimizer Learnings — knowledge graph and meeting history."""

import streamlit as st
import pandas as pd
import plotly.express as px


def render():
    st.header("Optimizer Learnings")

    try:
        from dashboard.data_helpers import (
            get_sqlite_engine, get_learnings_df, get_meetings_df,
        )
        engine = get_sqlite_engine()
    except Exception as e:
        st.warning(f"Cannot connect to database: {e}")
        return

    learnings_df = get_learnings_df(engine)
    meetings_df = get_meetings_df(engine)

    # Key metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active Learnings", len(learnings_df))
    c2.metric("Total Meetings", len(meetings_df))

    most_reinforced = ""
    if not learnings_df.empty:
        top = learnings_df.iloc[0]
        most_reinforced = f"{top.get('category', '')} ({top.get('times_reinforced', 0)}x)"
        avg_confidence = learnings_df["confidence"].mean() if "confidence" in learnings_df.columns else 0
    else:
        avg_confidence = 0

    c3.metric("Avg Confidence", f"{avg_confidence:.0%}")
    c4.metric("Most Reinforced", most_reinforced or "N/A")

    st.divider()

    # Learnings table with filters
    st.subheader("Active Learnings")
    if learnings_df.empty:
        st.info("No learnings recorded yet. The optimizer writes learnings after post-market meetings.")
    else:
        # Filters
        f1, f2, f3 = st.columns(3)
        with f1:
            agents = ["All"] + sorted(learnings_df["agent_target"].unique().tolist())
            agent_filter = st.selectbox("Agent", agents)
        with f2:
            categories = ["All"] + sorted(learnings_df["category"].unique().tolist())
            cat_filter = st.selectbox("Category", categories)
        with f3:
            regimes = ["All"] + sorted(learnings_df["regime"].unique().tolist())
            regime_filter = st.selectbox("Regime", regimes)

        filtered = learnings_df.copy()
        if agent_filter != "All":
            filtered = filtered[filtered["agent_target"] == agent_filter]
        if cat_filter != "All":
            filtered = filtered[filtered["category"] == cat_filter]
        if regime_filter != "All":
            filtered = filtered[filtered["regime"] == regime_filter]

        st.dataframe(filtered, use_container_width=True, hide_index=True)

        # Category breakdown chart
        if "category" in learnings_df.columns:
            cat_counts = learnings_df["category"].value_counts()
            fig = px.pie(
                values=cat_counts.values, names=cat_counts.index,
                title="Learnings by Category",
            )
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # Meeting history
    st.subheader("Meeting History")
    if meetings_df.empty:
        st.info("No optimizer meetings recorded yet.")
    else:
        st.dataframe(meetings_df, use_container_width=True, hide_index=True)
