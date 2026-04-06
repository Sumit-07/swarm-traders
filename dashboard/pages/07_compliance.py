"""Page 7: Compliance & Audit — daily scores, violations, clean streaks."""

import json

import streamlit as st
import pandas as pd
import plotly.express as px


def render():
    st.header("Compliance & Audit")

    try:
        from dashboard.data_helpers import get_sqlite_engine, get_audit_df
        engine = get_sqlite_engine()
    except Exception as e:
        st.warning(f"Cannot connect to database: {e}")
        return

    df = get_audit_df(engine, limit=60)

    if df.empty:
        st.info("No compliance audits recorded yet.")
        return

    # Key metrics
    latest = df.iloc[0]
    today_score = latest.get("compliance_score", 0)
    total_trades = latest.get("total_trades", 0)

    # Parse violations
    violations_raw = latest.get("violations", "[]")
    try:
        today_violations = json.loads(violations_raw) if violations_raw else []
    except (json.JSONDecodeError, TypeError):
        today_violations = []

    # Clean day streak
    clean_streak = 0
    for _, row in df.iterrows():
        try:
            v = json.loads(row.get("violations", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            v = []
        if not v:
            clean_streak += 1
        else:
            break

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Today's Score", f"{today_score:.0f}%" if today_score else "N/A")
    c2.metric("Trades Audited", total_trades)
    c3.metric("Today's Violations", len(today_violations))
    c4.metric("Clean Day Streak", clean_streak)

    st.divider()

    # Score trend chart
    if "compliance_score" in df.columns and "audit_date" in df.columns:
        chart_df = df[["audit_date", "compliance_score"]].dropna()
        if not chart_df.empty:
            chart_df = chart_df.sort_values("audit_date")
            fig = px.line(
                chart_df, x="audit_date", y="compliance_score",
                title="Compliance Score Trend",
                labels={"audit_date": "Date", "compliance_score": "Score %"},
            )
            fig.update_layout(height=300, yaxis_range=[0, 105])
            st.plotly_chart(fig, use_container_width=True)

    # Today's violations
    if today_violations:
        st.subheader("Today's Violations")
        for v in today_violations:
            if isinstance(v, dict):
                st.warning(f"**{v.get('type', 'Unknown')}**: {v.get('description', str(v))}")
            else:
                st.warning(str(v))
    else:
        st.success("No violations today.")

    # Audit history table
    st.subheader("Audit History")
    display_cols = ["audit_date", "compliance_score", "total_trades", "notes"]
    available = [c for c in display_cols if c in df.columns]
    st.dataframe(df[available], use_container_width=True, hide_index=True)
