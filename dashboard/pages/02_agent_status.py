"""Page 2: Agent Status — heartbeat, state, and last action for all 8 agents."""

import streamlit as st
import pandas as pd
from datetime import datetime


def render():
    st.header("Agent Status")

    try:
        from dashboard.data_helpers import (
            get_redis, get_agent_statuses, get_agent_heartbeat,
        )
        from config import AGENT_IDS
        r = get_redis()
    except Exception as e:
        st.warning(f"Cannot connect to Redis: {e}")
        return

    statuses = get_agent_statuses(r)

    # Build status table
    rows = []
    for agent_id in AGENT_IDS:
        heartbeat = get_agent_heartbeat(r, agent_id)
        info = statuses.get(agent_id, {})

        state = heartbeat.get("state", info.get("state", "OFFLINE"))
        last_action = heartbeat.get("last_action", info.get("last_action", "N/A"))
        llm_calls = heartbeat.get("llm_calls_today", 0)
        last_beat = heartbeat.get("timestamp", "")

        # Calculate seconds since last heartbeat
        if last_beat:
            try:
                dt = datetime.fromisoformat(last_beat)
                age = (datetime.now() - dt).total_seconds()
                health = "Healthy" if age < 120 else "Stale" if age < 300 else "Down"
            except Exception:
                health = "Unknown"
                age = -1
        else:
            health = "No heartbeat"
            age = -1

        rows.append({
            "Agent": agent_id,
            "State": state,
            "Health": health,
            "Last Action": last_action,
            "LLM Calls": llm_calls,
            "Last Heartbeat": last_beat[:19] if last_beat else "N/A",
        })

    df = pd.DataFrame(rows)

    # Color-code states
    def color_state(val):
        colors = {
            "ACTIVE": "background-color: #d4edda",
            "IDLE": "background-color: #fff3cd",
            "DEGRADED": "background-color: #f8d7da",
            "OFFLINE": "background-color: #e2e3e5",
        }
        return colors.get(val, "")

    def color_health(val):
        colors = {
            "Healthy": "background-color: #d4edda",
            "Stale": "background-color: #fff3cd",
            "Down": "background-color: #f8d7da",
        }
        return colors.get(val, "")

    styled = df.style.map(color_state, subset=["State"]).map(
        color_health, subset=["Health"]
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # Summary metrics
    active = sum(1 for r in rows if r["State"] == "ACTIVE")
    total_llm = sum(r["LLM Calls"] for r in rows)

    c1, c2, c3 = st.columns(3)
    c1.metric("Active Agents", f"{active} / {len(AGENT_IDS)}")
    c2.metric("Total LLM Calls Today", total_llm)
    c3.metric("Agents Online", sum(1 for r in rows if r["Health"] == "Healthy"))
