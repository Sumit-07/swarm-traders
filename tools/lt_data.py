"""
tools/lt_data.py

Data fetchers for long-term investment analysis.
All functions used exclusively by LT_Advisor.
No trading agents use this file.

Fetches: Nifty PE ratio, 52-week high/low, FII monthly data,
         sector performance, economic calendar events.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
HEADERS  = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# Shared session with cookies — NSE blocks requests without a valid session cookie
_nse_session: Optional[requests.Session] = None
_nse_session_ts: float = 0


def _get_nse_session() -> requests.Session:
    """Return a requests session with valid NSE cookies.

    NSE requires visiting the homepage first to set cookies.
    Session is reused for 4 minutes to avoid repeated cookie fetches.
    """
    global _nse_session, _nse_session_ts
    now = time.time()
    if _nse_session and (now - _nse_session_ts) < 240:
        return _nse_session

    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        r = session.get(NSE_BASE, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logger.warning("NSE session init failed: %s", e)
        # Return session anyway — individual calls will fail gracefully
    time.sleep(0.5)
    _nse_session = session
    _nse_session_ts = now
    return session


def get_nifty_pe() -> Optional[float]:
    """
    Fetches current Nifty 50 PE ratio from NSE.
    Returns float or None if fetch fails.

    NSE publishes PE/PB/Div yield for all indices.
    Falls back to yfinance if NSE is unreachable.
    """
    # Try NSE primary endpoint
    try:
        session = _get_nse_session()
        resp = session.get(
            f"{NSE_BASE}/api/equity-stockIndices?index=NIFTY%2050",
            headers={"Referer": f"{NSE_BASE}/market-data/live-equity-market"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        pe = data.get("metadata", {}).get("pe")
        if pe:
            return round(float(pe), 2)

    except Exception as e:
        logger.warning("NSE PE fetch failed: %s. Trying fallbacks.", e)

    # Fallback 1: NSE allIndices endpoint (different URL, same session)
    try:
        session = _get_nse_session()
        resp = session.get(
            f"{NSE_BASE}/api/allIndices",
            headers={"Referer": f"{NSE_BASE}/market-data/live-equity-market"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        for idx in data.get("data", []):
            if idx.get("index") == "NIFTY 50":
                pe = idx.get("pe")
                if pe:
                    return round(float(pe), 2)
    except Exception as e:
        logger.warning("NSE allIndices PE fallback failed: %s", e)

    # Fallback 2: yfinance (often no PE for indices, but worth trying)
    try:
        nifty = yf.Ticker("^NSEI")
        info  = nifty.info
        pe    = info.get("trailingPE") or info.get("forwardPE")
        if pe:
            return round(float(pe), 2)
    except Exception as e:
        logger.warning("yfinance PE fallback failed: %s", e)

    return None


def get_nifty_52w_high_low() -> dict:
    """
    Returns Nifty 52-week high and low.
    """
    try:
        nifty = yf.download("^NSEI", period="1y", interval="1d", progress=False)
        if nifty.empty:
            return {"high": 0, "low": 0}
        high_col = nifty["High"]
        low_col = nifty["Low"]
        if hasattr(high_col, 'columns'):
            high_col = high_col.iloc[:, 0]
            low_col = low_col.iloc[:, 0]
        return {
            "high": round(float(high_col.max()), 2),
            "low":  round(float(low_col.min()), 2),
        }
    except Exception as e:
        logger.error("52-week high/low fetch failed: %s", e)
        return {"high": 0, "low": 0}


def get_fii_monthly_flow() -> dict:
    """
    Returns FII/DII net flow for today, last 5 days, and last 30 days.
    Reads from Redis first (Data Agent updates this daily).
    Falls back to NSE website if Redis value is stale.

    Returns:
        {
            "fii_today":  float (crore, negative = net selling),
            "fii_5day":   float,
            "fii_30day":  float,
            "dii_5day":   float,
            "dii_30day":  float,
            "data_date":  str
        }
    """
    try:
        session = _get_nse_session()
        resp = session.get(
            f"{NSE_BASE}/api/fiidiiTradeReact",
            headers={"Referer": f"{NSE_BASE}/report-equity/fii-dii"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data:
            return _empty_fii_dict()

        df = pd.DataFrame(data)
        df["fii_net"] = pd.to_numeric(df.get("fiiDII", [0]), errors="coerce")

        return {
            "fii_today":  float(df["fii_net"].iloc[0]) if len(df) > 0 else 0,
            "fii_5day":   float(df["fii_net"].head(5).sum()),
            "fii_30day":  float(df["fii_net"].head(30).sum()),
            "dii_5day":   0.0,
            "dii_30day":  0.0,
            "data_date":  datetime.now().strftime("%Y-%m-%d"),
        }

    except Exception as e:
        logger.warning("FII flow fetch failed: %s", e)
        return _empty_fii_dict()


def get_sector_performance_30d() -> list[dict]:
    """
    Returns 30-day performance for major NSE sectors.
    Uses yfinance sector indices as proxies.
    """
    sector_proxies = {
        "Banking":     "^NSEBANK",
        "IT":          "^CNXIT",
        "Auto":        "^CNXAUTO",
        "Pharma":      "^CNXPHARMA",
        "FMCG":        "^CNXFMCG",
        "Metal":       "^CNXMETAL",
        "Energy":      "^CNXENERGY",
        "Realty":      "^CNXREALTY",
    }

    results = []
    end   = datetime.now()
    start = end - timedelta(days=35)

    for sector, symbol in sector_proxies.items():
        try:
            data = yf.download(
                symbol,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
                progress=False,
            )
            if len(data) < 2:
                continue

            first_close = float(data["Close"].iloc[0])
            last_close  = float(data["Close"].iloc[-1])
            change_pct  = ((last_close - first_close) / first_close) * 100

            results.append({
                "sector":     sector,
                "change_pct": round(change_pct, 2),
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["change_pct"])
    return results


def get_vix_history_30d() -> dict:
    """
    Returns India VIX statistics for the last 30 days.
    """
    try:
        vix = yf.download(
            "^INDIAVIX",
            period="35d",
            interval="1d",
            progress=False,
        )
        if vix.empty:
            return {"avg": 0, "min": 0, "max": 0, "trend": "UNKNOWN"}

        closes = vix["Close"].dropna()
        if hasattr(closes, 'columns'):
            closes = closes.iloc[:, 0]
        avg    = closes.mean()

        # Determine trend: compare last 5 days vs prior 5 days
        if len(closes) >= 10:
            recent = closes.tail(5).mean()
            prior  = closes.iloc[-10:-5].mean()
            if recent > prior * 1.05:
                trend = "RISING"
            elif recent < prior * 0.95:
                trend = "FALLING"
            else:
                trend = "STABLE"
        else:
            trend = "UNKNOWN"

        return {
            "avg":   round(float(avg), 2),
            "min":   round(float(closes.min()), 2),
            "max":   round(float(closes.max()), 2),
            "trend": trend,
        }

    except Exception as e:
        logger.error("VIX history fetch failed: %s", e)
        return {"avg": 0, "min": 0, "max": 0, "trend": "UNKNOWN"}


def get_upcoming_events(days_ahead: int = 30) -> list[str]:
    """
    Returns list of upcoming significant market events.
    Hardcoded Indian market calendar — update annually.
    """
    today  = datetime.now().date()
    events = []

    annual_events = [
        (1,  1,  "New Year — markets closed"),
        (1,  26, "Republic Day — markets closed"),
        (2,  1,  "Union Budget presentation"),
        (3,  25, "Holi — markets likely closed"),
        (4,  14, "Ambedkar Jayanti — markets closed"),
        (4,  18, "Good Friday — markets closed"),
        (5,  1,  "Maharashtra Day — markets closed"),
        (8,  15, "Independence Day — markets closed"),
        (8,  27, "Ganesh Chaturthi — markets closed"),
        (10, 2,  "Gandhi Jayanti — markets closed"),
        (10, 20, "Diwali — markets closed"),
        (10, 21, "Diwali Balipratipada — markets closed"),
        (12, 25, "Christmas — markets closed"),
    ]

    year = today.year
    for month, day, desc in annual_events:
        try:
            event_date = datetime(year, month, day).date()
            if today <= event_date <= today + timedelta(days=days_ahead):
                events.append(f"{event_date.strftime('%d %b')}: {desc}")
        except ValueError:
            continue

    # RBI MPC meetings (approximately every 2 months)
    rbi_approx = [
        datetime(year, 2,  7).date(),
        datetime(year, 4,  9).date(),
        datetime(year, 6,  6).date(),
        datetime(year, 8,  8).date(),
        datetime(year, 10, 8).date(),
        datetime(year, 12, 5).date(),
    ]
    for rbi_date in rbi_approx:
        if today <= rbi_date <= today + timedelta(days=days_ahead):
            events.append(f"{rbi_date.strftime('%d %b')}: RBI MPC meeting")

    return events if events else ["No major events identified in next 30 days"]


def _empty_fii_dict() -> dict:
    return {
        "fii_today":  0.0,
        "fii_5day":   0.0,
        "fii_30day":  0.0,
        "dii_5day":   0.0,
        "dii_30day":  0.0,
        "data_date":  "unavailable",
    }
