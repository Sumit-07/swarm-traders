"""Page 8: System Log — orchestrator decisions + position monitor alerts."""

import streamlit as st
import pandas as pd


def render():
    st.header("System Log")

    try:
        from dashboard.data_helpers import (
            get_sqlite_engine, get_orchestrator_log_df, get_monitor_alerts_df,
        )
        engine = get_sqlite_engine()
    except Exception as e:
        st.warning(f"Cannot connect to database: {e}")
        return

    days = st.selectbox("Period", [1, 3, 7, 14, 30], index=2)

    orch_df = get_orchestrator_log_df(engine, days=days)
    alerts_df = get_monitor_alerts_df(engine, days=days)

    # Key metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Orchestrator Events", len(orch_df))
    c2.metric("Position Alerts", len(alerts_df))

    conflicts = 0
    if not orch_df.empty and "event_type" in orch_df.columns:
        conflicts = len(orch_df[orch_df["event_type"].str.contains("conflict", case=False, na=False)])
    c3.metric("Conflicts Resolved", conflicts)

    rejections = 0
    if not orch_df.empty and "decision" in orch_df.columns:
        rejections = len(orch_df[orch_df["decision"].str.contains("REJECT", case=False, na=False)])
    c4.metric("Rejections", rejections)

    st.divider()

    # Orchestrator log
    st.subheader("Orchestrator Decisions")
    if orch_df.empty:
        st.info("No orchestrator events recorded.")
    else:
        display_cols = [
            "created_at", "event_type", "agent_involved",
            "decision", "reason", "description",
        ]
        available = [c for c in display_cols if c in orch_df.columns]
        st.dataframe(orch_df[available], use_container_width=True, hide_index=True)

    st.divider()

    # Position monitor alerts
    st.subheader("Position Monitor Alerts")
    if alerts_df.empty:
        st.info("No position alerts recorded.")
    else:
        display_cols = [
            "alerted_at", "symbol", "strategy_name", "trigger_type",
            "trigger_value", "trigger_description", "orchestrator_action",
        ]
        available = [c for c in display_cols if c in alerts_df.columns]
        st.dataframe(alerts_df[available], use_container_width=True, hide_index=True)
