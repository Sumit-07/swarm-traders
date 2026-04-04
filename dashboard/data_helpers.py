"""Shared data fetching helpers for the Streamlit dashboard.

Connects to Redis and SQLite to pull live state for all dashboard pages.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from config import (
    CAPITAL, RISK_LIMITS, AGENT_IDS, REDIS_HOST, REDIS_PORT, SQLITE_DB_PATH,
)


def get_redis():
    """Get a Redis connection (cached per session via st.cache_resource)."""
    import redis
    return redis.Redis(host=REDIS_HOST, port=int(REDIS_PORT), decode_responses=True)


def get_sqlite_engine():
    """Get a SQLAlchemy engine."""
    from sqlalchemy import create_engine
    db_path = SQLITE_DB_PATH
    return create_engine(f"sqlite:///{db_path}")


# --- Redis helpers ---

def _redis_get_json(r, key: str) -> dict:
    raw = r.get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


def get_system_mode(r) -> str:
    data = _redis_get_json(r, "state:system_mode")
    return data.get("mode", "UNKNOWN")


def get_positions(r) -> list[dict]:
    data = _redis_get_json(r, "state:positions")
    return data.get("positions", [])


def get_agent_statuses(r) -> dict:
    data = _redis_get_json(r, "state:all_agents")
    # Remove _updated_at key
    return {k: v for k, v in data.items() if k != "_updated_at"}


def get_agent_heartbeat(r, agent_id: str) -> dict:
    return _redis_get_json(r, f"agent:{agent_id}:heartbeat")


def get_market_snapshot(r) -> dict:
    return _redis_get_json(r, "data:market_snapshot")


# --- SQLite helpers ---

def get_trades_df(engine, date: str = None, limit: int = 200) -> pd.DataFrame:
    """Fetch trades as DataFrame."""
    query = "SELECT * FROM trades"
    params = {}
    if date:
        query += " WHERE date(entry_time) = :date"
        params["date"] = date
    query += " ORDER BY entry_time DESC LIMIT :limit"
    params["limit"] = limit

    try:
        return pd.read_sql(query, engine, params=params)
    except Exception:
        return pd.DataFrame()


def get_daily_pnl_df(engine, days: int = 30) -> pd.DataFrame:
    """Fetch daily P&L for the last N days."""
    query = """
        SELECT date, conservative_pnl, risk_pnl, total_pnl,
               conservative_trades, risk_trades, total_trades
        FROM daily_pnl
        ORDER BY date DESC LIMIT :days
    """
    try:
        df = pd.read_sql(query, engine, params={"days": days})
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
        return df
    except Exception:
        return pd.DataFrame()


def get_signals_df(engine, date: str = None, limit: int = 100) -> pd.DataFrame:
    query = "SELECT * FROM signals"
    params = {}
    if date:
        query += " WHERE date(created_at) = :date"
        params["date"] = date
    query += " ORDER BY created_at DESC LIMIT :limit"
    params["limit"] = limit
    try:
        return pd.read_sql(query, engine, params=params)
    except Exception:
        return pd.DataFrame()


def get_audit_df(engine, limit: int = 30) -> pd.DataFrame:
    query = "SELECT * FROM compliance_audit ORDER BY audit_date DESC LIMIT :limit"
    try:
        return pd.read_sql(query, engine, params={"limit": limit})
    except Exception:
        return pd.DataFrame()


def compute_trade_stats(trades_df: pd.DataFrame) -> dict:
    """Compute summary stats from a trades DataFrame."""
    if trades_df.empty:
        return {
            "total": 0, "open": 0, "closed": 0,
            "wins": 0, "losses": 0, "flat": 0,
            "total_pnl": 0, "win_rate": 0,
        }

    closed = trades_df[trades_df["status"] != "OPEN"]
    pnl_col = closed["pnl"].fillna(0) if "pnl" in closed.columns else pd.Series(dtype=float)

    wins = (pnl_col > 0).sum()
    losses = (pnl_col < 0).sum()
    total_closed = len(closed)

    return {
        "total": len(trades_df),
        "open": (trades_df["status"] == "OPEN").sum() if "status" in trades_df.columns else 0,
        "closed": total_closed,
        "wins": int(wins),
        "losses": int(losses),
        "flat": total_closed - int(wins) - int(losses),
        "total_pnl": round(float(pnl_col.sum()), 2),
        "win_rate": round(wins / total_closed, 4) if total_closed > 0 else 0,
    }
