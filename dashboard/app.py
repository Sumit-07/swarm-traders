"""Swarm Traders Dashboard — Main entry point.

Run: streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path (needed when Streamlit runs inside Docker)
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import streamlit as st

st.set_page_config(
    page_title="Swarm Traders",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Sidebar navigation ---
pages = {
    "Positions": "dashboard/pages/01_positions.py",
    "Agent Status": "dashboard/pages/02_agent_status.py",
    "P&L": "dashboard/pages/03_pnl.py",
    "Trade Log": "dashboard/pages/04_trade_log.py",
    "Backtest Results": "dashboard/pages/05_backtest_results.py",
}

st.sidebar.title("Swarm Traders")

# Show system mode in sidebar
try:
    from dashboard.data_helpers import get_redis, get_system_mode
    r = get_redis()
    mode = get_system_mode(r)
    mode_color = {
        "PAPER": "blue", "LIVE": "green", "HALTED": "red", "REVIEW": "orange",
    }.get(mode, "gray")
    st.sidebar.markdown(f"**Mode:** :{mode_color}[{mode}]")
except Exception:
    st.sidebar.markdown("**Mode:** :gray[OFFLINE]")

st.sidebar.divider()
selection = st.sidebar.radio("Navigate", list(pages.keys()), label_visibility="collapsed")

# --- Load selected page ---
page_file = pages[selection]

# Use exec to load page (Streamlit multipage via sidebar radio)
import importlib
page_modules = {
    "Positions": "dashboard.pages.01_positions",
    "Agent Status": "dashboard.pages.02_agent_status",
    "P&L": "dashboard.pages.03_pnl",
    "Trade Log": "dashboard.pages.04_trade_log",
    "Backtest Results": "dashboard.pages.05_backtest_results",
}

module_name = page_modules[selection]
try:
    mod = importlib.import_module(module_name)
    mod.render()
except Exception as e:
    st.error(f"Failed to load page: {e}")
