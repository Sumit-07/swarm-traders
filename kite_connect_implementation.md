# Kite Connect (Zerodha) — Complete Broker Integration Plan

> **For Claude Code:** This document fully replaces all Fyers API references in the
> codebase. Read every section before writing a single line of code. The document
> is ordered by implementation sequence — do not skip ahead. When you see a
> ⚠️ symbol, that is a common mistake — read it carefully before proceeding.

---

## Table of Contents

1. [What is changing and why](#1-what-is-changing-and-why)
2. [Files to create, modify, and delete](#2-files-to-create-modify-and-delete)
3. [Environment variables](#3-environment-variables)
4. [Dependencies](#4-dependencies)
5. [Authentication — two modes](#5-authentication--two-modes)
6. [Market data implementation](#6-market-data-implementation)
7. [Order placement implementation](#7-order-placement-implementation)
8. [WebSocket live tick data](#8-websocket-live-tick-data)
9. [Data normalisation layer](#9-data-normalisation-layer)
10. [Swappable data source abstraction](#10-swappable-data-source-abstraction)
11. [Static IP registration](#11-static-ip-registration)
12. [Config changes](#12-config-changes)
13. [Agent changes](#13-agent-changes)
14. [Docker changes](#14-docker-changes)
15. [Tests to write](#15-tests-to-write)
16. [Implementation sequence](#16-implementation-sequence)

---

## 1. What is Changing and Why

### What was used before
- `fyers-apiv3` Python SDK
- Fyers OAuth2 with Telegram button callback
- Fyers historical data and live quote endpoints
- Fyers WebSocket for tick data

### What replaces it
- `kiteconnect` Python SDK (Zerodha)
- Two auth modes: Telegram button (paper trading) and TOTP auto-login (live trading)
- Kite historical data and live quote endpoints
- `KiteTicker` WebSocket for tick data

### Why Kite over Fyers
- Instant API approval — no waiting period
- Same broker where trading capital lives — end-to-end integration testing
- ₹500/month covers all data (live + 10 years historical)
- Order placement API is free
- Larger developer community, better documentation
- Static IP already solved by GoDaddy VPS fixed IP

### What does NOT change
- Agent architecture and LangGraph graph
- Redis message bus and all channel names
- SQLite schema and all table structures
- Telegram bot for human communication
- Streamlit dashboard
- Backtesting framework
- Risk management rules
- Docker and docker-compose setup (minor port addition only)
- Scheduler and daily runtime schedule
- Market calendar

---

## 2. Files to Create, Modify, and Delete

### Create (new files)
```
tools/kite_auth.py          # All authentication logic
tools/kite_broker.py        # Order placement, positions, holdings
tools/kite_market_data.py   # Historical OHLCV, live quotes, options chain
tools/kite_ticker.py        # WebSocket live tick data stream
tools/yfinance_fallback.py  # Fallback data source if Kite is down
```

### Modify (existing files — specific changes documented per file below)
```
tools/market_data.py        # Becomes the abstraction layer router
tools/broker.py             # Becomes thin wrapper calling kite_broker.py
config.py                   # Replace Fyers vars with Kite vars
requirements.txt            # Replace fyers-apiv3 with kiteconnect + pyotp
.env.example                # Replace Fyers keys with Kite keys
.gitignore                  # Add token files
docker-compose.yml          # Add port 8080 exposure
agents/orchestrator/orchestrator.py  # Update startup auth call
agents/data_agent/data_agent.py      # Update data fetch calls
agents/execution_agent/execution_agent.py  # Update order placement calls
```

### Delete (remove entirely)
```
tools/fyers_auth.py         # If it exists — replaced by kite_auth.py
```

### Do NOT touch
```
agents/strategist/          # No changes needed
agents/risk_strategist/     # No changes needed
agents/analyst/             # No changes needed
agents/risk_agent/          # No changes needed
agents/compliance_agent/    # No changes needed
graph/                      # No changes needed
memory/                     # No changes needed
comms/                      # Minor addition to telegram_bot.py only
backtesting/                # Uses market_data.py abstraction — no changes
dashboard/                  # No changes needed
scheduler/                  # No changes needed
```

---

## 3. Environment Variables

### `.env.example` — replace entire Fyers block with this

```bash
# ============================================================
# KITE CONNECT (ZERODHA) — BROKER CONFIGURATION
# ============================================================

# Kite Connect API credentials
# Get from: developers.kite.trade → My Apps → Create App
KITE_API_KEY=your_api_key_here
KITE_API_SECRET=your_api_secret_here

# Zerodha account credentials
# Required for AUTH_MODE=auto only (TOTP auto-login)
ZERODHA_USER_ID=your_client_id       # e.g. AB1234
ZERODHA_PASSWORD=your_login_password
ZERODHA_TOTP_SECRET=your_totp_base32_secret
# How to get TOTP secret:
# 1. Go to kite.zerodha.com → Profile → Security → Enable TOTP
# 2. When QR code appears, ALSO copy the text secret below it
# 3. Paste that secret here (looks like: JBSWY3DPEHPK3PXP)

# Authentication mode
# telegram = sends Telegram button, user taps on phone (paper trading phase)
# auto     = fully automated TOTP login, no human needed (live trading phase)
AUTH_MODE=telegram

# OAuth2 redirect URI
# Must match EXACTLY what you registered in developers.kite.trade
# Use your VPS public IP — not localhost
KITE_REDIRECT_URI=http://YOUR_VPS_PUBLIC_IP:8080

# ============================================================
# DATA SOURCE CONFIGURATION
# ============================================================

# Primary data source
# kite      = Kite Connect API (₹500/month, most reliable)
# nsepython = NSE scraper (free, fallback)
# yfinance  = Yahoo Finance (free, 15-min delay, last resort)
DATA_SOURCE=kite

# ============================================================
# SYSTEM CONFIGURATION (unchanged)
# ============================================================
TRADING_MODE=PAPER
REDIS_HOST=redis
REDIS_PORT=6379
SQLITE_DB_PATH=./data/trading_swarm.db
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_personal_chat_id
LOG_LEVEL=INFO
```

---

## 4. Dependencies

### `requirements.txt` — replace Fyers lines with these

Remove:
```
fyers-apiv3
```

Add:
```
kiteconnect>=5.0.0      # Zerodha Kite Connect SDK
pyotp>=2.9.0            # TOTP code generation for auto-login
pyotp requires no system dependencies — pure Python
```

Keep everything else unchanged.

Install verification command (run after updating requirements.txt):
```bash
pip install kiteconnect pyotp
python -c "from kiteconnect import KiteConnect, KiteTicker; import pyotp; print('OK')"
```

---

## 5. Authentication — Two Modes

### Create `tools/kite_auth.py`

This file contains all authentication logic. Both modes (telegram and auto)
share the same token persistence layer. The mode is controlled by `AUTH_MODE`
in `.env`.

```python
"""
tools/kite_auth.py

Kite Connect authentication module.
Supports two modes controlled by AUTH_MODE environment variable:
  - telegram: sends Telegram button, user taps on phone
  - auto:     fully automated TOTP login, zero human interaction

Both modes persist the token to disk and reuse it within the same day.
Token expires at midnight IST daily — re-authentication happens at 6:55 AM.
"""

import json
import logging
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

TOKEN_FILE = Path("data/kite_token.json")
AUTH_TIMEOUT_SECONDS = 300    # 5 minutes before giving up on Telegram mode
CALLBACK_PORT = 8080


# ── Token persistence ─────────────────────────────────────────────────────────

def _save_token(access_token: str) -> None:
    """Persist token to disk with today's date for reuse check."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token_data = {
        "access_token": access_token,
        "generated_at": datetime.now().isoformat(),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    logger.info("Kite access token saved to disk.")


def _load_todays_token() -> str | None:
    """
    Returns today's access token if it exists and was generated today.
    Returns None if missing, stale, or corrupt.
    """
    if not TOKEN_FILE.exists():
        return None
    try:
        with open(TOKEN_FILE) as f:
            token_data = json.load(f)
        generated_date = token_data.get("generated_date", "")
        today = datetime.now().strftime("%Y-%m-%d")
        if generated_date == today:
            logger.info("Reusing Kite token generated earlier today.")
            return token_data["access_token"]
        logger.info("Kite token is stale (from %s). Re-authenticating.", generated_date)
        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Corrupt token file: %s. Deleting and re-authenticating.", e)
        TOKEN_FILE.unlink(missing_ok=True)
        return None


# ── OAuth2 callback server (used by telegram mode) ───────────────────────────

class _KiteCallbackHandler(BaseHTTPRequestHandler):
    """
    Minimal HTTP server that catches the Kite OAuth2 redirect.

    ⚠️ Kite sends 'request_token' in the callback, NOT 'auth_code'.
    This is different from Fyers. Do not rename the parameter.
    """
    request_token: str | None = None
    received: bool = False

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)

        if "request_token" in params:
            _KiteCallbackHandler.request_token = params["request_token"][0]
            _KiteCallbackHandler.received = True

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;padding:40px;"
                b"max-width:400px;margin:auto'>"
                b"<h2>Zerodha authentication successful.</h2>"
                b"<p>You can close this tab. The trading system is starting.</p>"
                b"</body></html>"
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress HTTP server console logs — use Python logger instead
        pass


# ── Telegram mode auth flow ───────────────────────────────────────────────────

def _auth_via_telegram(kite: KiteConnect, telegram) -> str:
    """
    Sends Kite login URL to Telegram as a tap-able button.
    Waits for OAuth2 callback on port CALLBACK_PORT.
    Returns access token on success.

    Flow:
    1. Generate Kite login URL
    2. Send Telegram message with [Authorise Zerodha →] button
    3. Start HTTP server on :8080
    4. Wait for callback (up to AUTH_TIMEOUT_SECONDS)
    5. Exchange request_token for access_token
    6. Save token to disk and return it
    """

    # Reset handler state before each attempt
    _KiteCallbackHandler.request_token = None
    _KiteCallbackHandler.received = False

    login_url = kite.login_url()
    logger.info("Generated Kite login URL.")

    # Send to Telegram
    telegram.send_auth_request(
        message=(
            "Zerodha authentication required to start the trading system.\n\n"
            "Tap the button below, log in to Zerodha, and approve access.\n"
            f"Timeout in {AUTH_TIMEOUT_SECONDS // 60} minutes."
        ),
        url=login_url,
        button_text="Authorise Zerodha →",
    )

    # Start callback server in background thread
    server = HTTPServer(("0.0.0.0", CALLBACK_PORT), _KiteCallbackHandler)

    def _serve():
        while not _KiteCallbackHandler.received:
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    # Poll for request_token with timeout
    elapsed = 0
    while not _KiteCallbackHandler.received and elapsed < AUTH_TIMEOUT_SECONDS:
        time.sleep(1)
        elapsed += 1

    server.server_close()

    if not _KiteCallbackHandler.request_token:
        telegram.send(
            "Zerodha authentication timed out after "
            f"{AUTH_TIMEOUT_SECONDS // 60} minutes. "
            "System halted. Send /authenticate to retry."
        )
        raise TimeoutError(
            f"Kite auth not completed within {AUTH_TIMEOUT_SECONDS} seconds."
        )

    # Exchange request_token for access_token
    logger.info("Received request_token. Generating session...")
    return _exchange_token(kite, _KiteCallbackHandler.request_token, telegram)


# ── TOTP auto-login mode ──────────────────────────────────────────────────────

def _auth_via_totp(kite: KiteConnect, telegram, config: dict) -> str:
    """
    Fully automated login using Zerodha credentials + TOTP.
    No human interaction required. Runs at 6:55 AM via scheduler.

    ⚠️ This logs into Zerodha's web interface programmatically.
    This works because Zerodha uses standard RFC 6238 TOTP,
    the same standard used by Google Authenticator.
    pyotp generates the identical 6-digit code the app would show.

    ⚠️ If Zerodha changes their login page structure, this will break.
    Monitor the kite.trade developer forum for announcements.
    If it breaks, fall back to telegram mode by setting AUTH_MODE=telegram.
    """
    user_id = config["user_id"]
    password = config["password"]
    totp_secret = config["totp_secret"]
    api_key = config["api_key"]

    logger.info("Starting TOTP auto-login for user %s", user_id)
    session = requests.Session()

    # Step 1 — submit user ID and password
    try:
        resp = session.post(
            "https://kite.zerodha.com/api/login",
            data={"user_id": user_id, "password": password},
            timeout=15,
        )
        resp.raise_for_status()
        login_data = resp.json()

        if login_data.get("status") != "success":
            raise ValueError(f"Login step 1 failed: {login_data.get('message')}")

        request_id = login_data["data"]["request_id"]
        logger.info("Login step 1 successful. request_id received.")

    except requests.RequestException as e:
        raise ConnectionError(f"Kite login step 1 failed: {e}") from e

    # Step 2 — submit TOTP code (generated programmatically)
    totp = pyotp.TOTP(totp_secret)
    totp_code = totp.now()
    logger.info("Generated TOTP code.")

    try:
        resp = session.post(
            "https://kite.zerodha.com/api/twofa",
            data={
                "user_id": user_id,
                "request_id": request_id,
                "twofa_value": totp_code,
                "twofa_type": "totp",
                "skip_session": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        twofa_data = resp.json()

        if twofa_data.get("status") != "success":
            raise ValueError(f"TOTP step failed: {twofa_data.get('message')}")

        logger.info("TOTP verification successful.")

    except requests.RequestException as e:
        raise ConnectionError(f"Kite TOTP step failed: {e}") from e

    # Step 3 — get request_token via Connect redirect
    try:
        resp = session.get(
            f"https://kite.trade/connect/login?api_key={api_key}&v=3",
            allow_redirects=True,
            timeout=15,
        )
        final_url = resp.url
        params = parse_qs(urlparse(final_url).query)

        if "request_token" not in params:
            raise ValueError(
                f"request_token not found in redirect URL: {final_url}"
            )

        request_token = params["request_token"][0]
        logger.info("request_token extracted from redirect URL.")

    except requests.RequestException as e:
        raise ConnectionError(f"Kite Connect redirect failed: {e}") from e

    # Step 4 — exchange request_token for access_token
    return _exchange_token(kite, request_token, telegram)


# ── Token exchange (shared by both modes) ────────────────────────────────────

def _exchange_token(kite: KiteConnect, request_token: str, telegram) -> str:
    """
    Exchanges a request_token for a persistent access_token.
    Saves token to disk. Returns access_token string.
    """
    from config import KITE_API_SECRET

    try:
        data = kite.generate_session(request_token, api_secret=KITE_API_SECRET)
        access_token = data["access_token"]
        logger.info("Access token generated successfully.")
    except Exception as e:
        msg = f"Kite token exchange failed: {e}"
        logger.error(msg)
        if telegram:
            telegram.send(f"Authentication error: {msg}. Send /authenticate to retry.")
        raise ValueError(msg) from e

    _save_token(access_token)

    if telegram:
        telegram.send(
            "Zerodha authenticated successfully. "
            "Token valid until midnight IST. System starting..."
        )

    return access_token


# ── Main entry point ──────────────────────────────────────────────────────────

def load_or_refresh_token(telegram=None) -> KiteConnect:
    """
    Main entry point called by Orchestrator at 6:55 AM startup.

    Logic:
    1. Check if today's token exists on disk → reuse it
    2. If not, run auth flow based on AUTH_MODE:
       - AUTH_MODE=telegram → send Telegram button, wait for tap
       - AUTH_MODE=auto     → run TOTP auto-login

    Returns an initialised and authenticated KiteConnect client.

    Args:
        telegram: TelegramBot instance (required for telegram mode,
                  optional for auto mode — used for error notifications)
    """
    import os
    from config import KITE_API_KEY

    kite = KiteConnect(api_key=KITE_API_KEY)

    # Try to reuse today's token first
    existing_token = _load_todays_token()
    if existing_token:
        kite.set_access_token(existing_token)
        if telegram:
            telegram.send("Kite token reused from earlier today. System starting...")
        return kite

    # Need fresh authentication
    auth_mode = os.getenv("AUTH_MODE", "telegram").lower()
    logger.info("No valid token found. Starting %s auth flow.", auth_mode)

    if auth_mode == "auto":
        config = {
            "api_key": KITE_API_KEY,
            "user_id": os.getenv("ZERODHA_USER_ID"),
            "password": os.getenv("ZERODHA_PASSWORD"),
            "totp_secret": os.getenv("ZERODHA_TOTP_SECRET"),
        }
        # Validate all required vars are present
        missing = [k for k, v in config.items() if not v]
        if missing:
            raise EnvironmentError(
                f"AUTH_MODE=auto requires: {missing}. "
                "Check .env file."
            )
        access_token = _auth_via_totp(kite, telegram, config)

    else:
        # Default: telegram mode
        if not telegram:
            raise ValueError(
                "AUTH_MODE=telegram requires a TelegramBot instance. "
                "Pass telegram= to load_or_refresh_token()."
            )
        access_token = _auth_via_telegram(kite, telegram)

    kite.set_access_token(access_token)
    return kite


def force_reauthenticate(telegram=None) -> KiteConnect:
    """
    Deletes existing token and forces full re-authentication.
    Called when user sends /authenticate via Telegram.
    """
    TOKEN_FILE.unlink(missing_ok=True)
    logger.info("Existing token deleted. Forcing re-authentication.")
    if telegram:
        telegram.send("Existing token cleared. Starting fresh authentication...")
    return load_or_refresh_token(telegram)
```

---

## 6. Market Data Implementation

### Create `tools/kite_market_data.py`

```python
"""
tools/kite_market_data.py

Market data fetching via Kite Connect API.
All functions return normalised pandas DataFrames or dicts.
Never called directly by agents — always called via tools/market_data.py router.
"""

import logging
from datetime import datetime, timedelta

import pandas as pd
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

# ── Instrument token cache ────────────────────────────────────────────────────
# Kite uses numeric instrument tokens, not string symbols.
# We maintain a cache so we don't fetch the full instrument list every call.
# The cache is populated once at startup and refreshed daily.

_instrument_cache: dict[str, int] = {}


def build_instrument_cache(kite: KiteConnect) -> None:
    """
    Downloads the full NSE instrument list and builds a symbol → token cache.
    Call this once at startup (7:00 AM) after authentication.

    ⚠️ This downloads ~5MB of data. Call once per day, not per request.
    The instrument list changes daily (new listings, expiries, etc.)
    """
    global _instrument_cache
    instruments = kite.instruments("NSE")
    _instrument_cache = {
        inst["tradingsymbol"]: inst["instrument_token"]
        for inst in instruments
        if inst["segment"] == "NSE"
    }
    # Also add NFO instruments for F&O
    nfo_instruments = kite.instruments("NFO")
    nfo_cache = {
        inst["tradingsymbol"]: inst["instrument_token"]
        for inst in nfo_instruments
    }
    _instrument_cache.update(nfo_cache)
    logger.info("Instrument cache built: %d symbols.", len(_instrument_cache))


def get_instrument_token(symbol: str) -> int:
    """
    Returns numeric instrument token for a symbol.
    Raises KeyError if symbol not found in cache.

    ⚠️ Always call build_instrument_cache() at startup before using this.
    """
    token = _instrument_cache.get(symbol)
    if not token:
        raise KeyError(
            f"Symbol '{symbol}' not found in instrument cache. "
            "Check symbol name or rebuild cache."
        )
    return token


def get_ohlcv(
    kite: KiteConnect,
    symbol: str,
    interval: str,
    days: int = 60,
) -> pd.DataFrame:
    """
    Fetches historical OHLCV data for a symbol.

    Args:
        kite:     Authenticated KiteConnect client
        symbol:   NSE trading symbol e.g. "RELIANCE", "HDFCBANK"
        interval: Kite interval string — one of:
                  "minute", "3minute", "5minute", "10minute", "15minute",
                  "30minute", "60minute", "day"
        days:     Number of calendar days of history to fetch

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume
        timestamp is timezone-aware (Asia/Kolkata)

    ⚠️ Kite rate limit: 3 historical data requests per second.
        Add time.sleep(0.4) between calls when fetching multiple symbols.
    ⚠️ Kite maximum date range per request:
        minute intervals: 60 days
        day interval: 2000 days
        If days > 60 for intraday, split into multiple requests.
    """
    token = get_instrument_token(symbol)
    to_date = datetime.now()
    from_date = to_date - timedelta(days=days)

    try:
        records = kite.historical_data(
            instrument_token=token,
            from_date=from_date.strftime("%Y-%m-%d %H:%M:%S"),
            to_date=to_date.strftime("%Y-%m-%d %H:%M:%S"),
            interval=interval,
            continuous=False,
            oi=False,
        )
    except Exception as e:
        logger.error("Failed to fetch historical data for %s: %s", symbol, e)
        raise

    if not records:
        logger.warning("No historical data returned for %s", symbol)
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(records)
    df.rename(columns={"date": "timestamp"}, inplace=True)
    df["symbol"] = symbol
    df = df[["timestamp", "open", "high", "low", "close", "volume", "symbol"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)

    return df


def get_live_quote(kite: KiteConnect, symbols: list[str]) -> dict:
    """
    Fetches live quotes for one or more symbols.

    Args:
        kite:    Authenticated KiteConnect client
        symbols: List of NSE symbols e.g. ["RELIANCE", "HDFCBANK"]

    Returns:
        Dict keyed by symbol with standardised quote data:
        {
            "RELIANCE": {
                "symbol": "RELIANCE",
                "last_price": 2847.50,
                "open": 2830.00,
                "high": 2860.00,
                "low": 2820.00,
                "close": 2825.00,   # previous day close
                "volume": 1234567,
                "change_pct": 0.79,
                "timestamp": datetime
            }
        }

    ⚠️ Kite quote API uses "NSE:SYMBOL" format. This function adds the prefix.
    ⚠️ Max 500 symbols per call.
    """
    kite_symbols = [f"NSE:{s}" for s in symbols]

    try:
        raw = kite.quote(kite_symbols)
    except Exception as e:
        logger.error("Failed to fetch live quotes for %s: %s", symbols, e)
        raise

    result = {}
    for symbol in symbols:
        key = f"NSE:{symbol}"
        if key not in raw:
            logger.warning("No quote data for %s", symbol)
            continue
        q = raw[key]
        result[symbol] = {
            "symbol": symbol,
            "last_price": q["last_price"],
            "open": q["ohlc"]["open"],
            "high": q["ohlc"]["high"],
            "low": q["ohlc"]["low"],
            "close": q["ohlc"]["close"],
            "volume": q["volume_traded"],
            "change_pct": round(
                ((q["last_price"] - q["ohlc"]["close"]) / q["ohlc"]["close"]) * 100, 2
            ),
            "timestamp": datetime.now(),
        }

    return result


def get_options_chain(
    kite: KiteConnect,
    underlying: str,
    expiry_date: str,
) -> pd.DataFrame:
    """
    Fetches the options chain for an underlying at a given expiry.

    Args:
        kite:        Authenticated KiteConnect client
        underlying:  "NIFTY" or "BANKNIFTY" or stock symbol
        expiry_date: Expiry in "YYYY-MM-DD" format

    Returns:
        DataFrame with columns:
        strike, ce_ltp, ce_oi, ce_volume, ce_iv,
        pe_ltp, pe_oi, pe_volume, pe_iv, expiry

    ⚠️ Getting options chain from Kite requires fetching all NFO instruments
       filtered by underlying and expiry, then quoting them in batch.
       This is more complex than Fyers' direct options chain endpoint.
       See implementation below.
    """
    # Get all NFO instruments for this underlying
    all_instruments = [
        inst for inst in kite.instruments("NFO")
        if inst["name"] == underlying
        and inst["expiry"].strftime("%Y-%m-%d") == expiry_date
    ]

    if not all_instruments:
        logger.warning(
            "No options instruments found for %s expiry %s",
            underlying, expiry_date
        )
        return pd.DataFrame()

    # Separate calls and puts
    calls = [i for i in all_instruments if i["instrument_type"] == "CE"]
    puts  = [i for i in all_instruments if i["instrument_type"] == "PE"]

    # Fetch quotes in batch (max 500 per call)
    all_tokens = [
        f"NFO:{i['tradingsymbol']}"
        for i in all_instruments
    ]

    try:
        quotes = kite.quote(all_tokens)
    except Exception as e:
        logger.error("Options chain quote fetch failed: %s", e)
        raise

    # Build structured chain
    strikes = sorted(set(i["strike"] for i in all_instruments))
    rows = []
    for strike in strikes:
        ce_sym = next(
            (f"NFO:{i['tradingsymbol']}" for i in calls if i["strike"] == strike),
            None
        )
        pe_sym = next(
            (f"NFO:{i['tradingsymbol']}" for i in puts if i["strike"] == strike),
            None
        )
        ce_data = quotes.get(ce_sym, {})
        pe_data = quotes.get(pe_sym, {})

        rows.append({
            "strike": strike,
            "expiry": expiry_date,
            "ce_ltp":    ce_data.get("last_price", 0),
            "ce_oi":     ce_data.get("oi", 0),
            "ce_volume": ce_data.get("volume_traded", 0),
            "pe_ltp":    pe_data.get("last_price", 0),
            "pe_oi":     pe_data.get("oi", 0),
            "pe_volume": pe_data.get("volume_traded", 0),
        })

    return pd.DataFrame(rows)


def get_vwap(kite: KiteConnect, symbol: str) -> float:
    """
    Calculates current VWAP from today's 1-minute candles.
    VWAP = sum(typical_price * volume) / sum(volume)
    typical_price = (high + low + close) / 3
    """
    df = get_ohlcv(kite, symbol, interval="minute", days=1)

    # Filter to today's session only
    today = datetime.now().date()
    df = df[df["timestamp"].dt.date == today]

    if df.empty:
        return 0.0

    df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["typical_price"] * df["volume"]
    vwap = df["tp_vol"].sum() / df["volume"].sum()
    return round(vwap, 2)
```

---

## 7. Order Placement Implementation

### Create `tools/kite_broker.py`

```python
"""
tools/kite_broker.py

Order placement and account management via Kite Connect.
Only used in LIVE trading mode. In PAPER mode, all calls
are routed to order_simulator.py instead.

⚠️ Every function in this file touches real money in LIVE mode.
   Add a mode check at the top of every public function.
"""

import logging
import os
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


def _assert_live_mode():
    """
    Guard function — raises if called in PAPER mode.
    Every order placement function calls this first.
    """
    if os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE":
        raise RuntimeError(
            "Order placement called in PAPER mode. "
            "This is a bug — route to order_simulator.py instead."
        )


def place_order(
    kite: KiteConnect,
    symbol: str,
    transaction_type: str,
    quantity: int,
    order_type: str = "LIMIT",
    price: float = 0.0,
    trigger_price: float = 0.0,
    product: str = "MIS",
    tag: str = "",
) -> str:
    """
    Places an order on NSE via Kite Connect.

    Args:
        kite:             Authenticated KiteConnect client
        symbol:           NSE trading symbol e.g. "RELIANCE"
        transaction_type: "BUY" or "SELL"
        quantity:         Number of shares
        order_type:       "LIMIT", "MARKET", "SL", "SL-M"
        price:            Limit price (required for LIMIT and SL orders)
        trigger_price:    Stop-loss trigger (required for SL and SL-M)
        product:          "MIS" (intraday), "CNC" (delivery), "NRML" (F&O)
        tag:              Optional tag for order identification (max 20 chars)

    Returns:
        order_id string from Kite

    ⚠️ SEBI mandate: all orders must include market_protection for MARKET
       and SL-M order types. Kite enforces this automatically.
    ⚠️ Static IP must be registered in Kite developer dashboard before
       this function will work in production.
    """
    _assert_live_mode()

    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=(
                kite.TRANSACTION_TYPE_BUY
                if transaction_type == "BUY"
                else kite.TRANSACTION_TYPE_SELL
            ),
            quantity=quantity,
            product=kite.PRODUCT_MIS if product == "MIS" else kite.PRODUCT_CNC,
            order_type=_map_order_type(kite, order_type),
            price=price if order_type in ("LIMIT", "SL") else None,
            trigger_price=trigger_price if order_type in ("SL", "SL-M") else None,
            tag=tag[:20] if tag else None,
        )
        logger.info(
            "Order placed: %s %s %s x%d @ %.2f → order_id=%s",
            transaction_type, symbol, order_type, quantity, price, order_id
        )
        return str(order_id)

    except Exception as e:
        logger.error("Order placement failed for %s: %s", symbol, e)
        raise


def place_stoploss_order(
    kite: KiteConnect,
    symbol: str,
    transaction_type: str,
    quantity: int,
    trigger_price: float,
    price: float = 0.0,
    product: str = "MIS",
) -> str:
    """
    Places a stop-loss order. transaction_type is the exit side:
    If you're long (bought), transaction_type = "SELL"
    If you're short (sold), transaction_type = "BUY"
    """
    return place_order(
        kite=kite,
        symbol=symbol,
        transaction_type=transaction_type,
        quantity=quantity,
        order_type="SL-M",
        trigger_price=trigger_price,
        price=price or trigger_price * 0.99,
        product=product,
    )


def get_order_status(kite: KiteConnect, order_id: str) -> dict:
    """Returns current status of an order."""
    _assert_live_mode()
    orders = kite.orders()
    for order in orders:
        if str(order["order_id"]) == str(order_id):
            return {
                "order_id": order_id,
                "status": order["status"],
                "filled_quantity": order["filled_quantity"],
                "average_price": order["average_price"],
                "symbol": order["tradingsymbol"],
            }
    raise ValueError(f"Order {order_id} not found.")


def cancel_order(kite: KiteConnect, order_id: str) -> bool:
    """Cancels a pending order. Returns True on success."""
    _assert_live_mode()
    try:
        kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
        logger.info("Order %s cancelled.", order_id)
        return True
    except Exception as e:
        logger.error("Failed to cancel order %s: %s", order_id, e)
        return False


def get_positions(kite: KiteConnect) -> list[dict]:
    """Returns all open intraday (MIS) positions."""
    _assert_live_mode()
    positions = kite.positions()
    return [
        {
            "symbol": p["tradingsymbol"],
            "quantity": p["quantity"],
            "average_price": p["average_price"],
            "last_price": p["last_price"],
            "pnl": p["pnl"],
            "product": p["product"],
        }
        for p in positions["day"]
        if p["quantity"] != 0
    ]


def get_holdings(kite: KiteConnect) -> list[dict]:
    """Returns all delivery (CNC) holdings."""
    _assert_live_mode()
    return kite.holdings()


def get_margins(kite: KiteConnect) -> dict:
    """Returns available margin for equity segment."""
    _assert_live_mode()
    margins = kite.margins(segment="equity")
    return {
        "available": margins["available"]["live_balance"],
        "used": margins["utilised"]["debits"],
        "total": margins["available"]["live_balance"] + margins["utilised"]["debits"],
    }


def _map_order_type(kite: KiteConnect, order_type: str) -> str:
    mapping = {
        "LIMIT":  kite.ORDER_TYPE_LIMIT,
        "MARKET": kite.ORDER_TYPE_MARKET,
        "SL":     kite.ORDER_TYPE_SL,
        "SL-M":   kite.ORDER_TYPE_SLM,
    }
    if order_type not in mapping:
        raise ValueError(f"Unknown order_type: {order_type}. Use LIMIT, MARKET, SL, or SL-M.")
    return mapping[order_type]
```

---

## 8. WebSocket Live Tick Data

### Create `tools/kite_ticker.py`

```python
"""
tools/kite_ticker.py

KiteTicker WebSocket wrapper for live tick data streaming.
Subscribes to symbols in the watchlist and publishes ticks to Redis.
Used by Data Agent during market hours.

⚠️ KiteTicker runs in a background thread. Do not block the main thread.
⚠️ KiteTicker automatically reconnects on disconnect — this is built in.
"""

import logging
from kiteconnect import KiteTicker
from memory.redis_store import RedisStore
import json
from datetime import datetime

logger = logging.getLogger(__name__)


class KiteTickerManager:
    """
    Manages the KiteTicker WebSocket connection.
    Subscribes to instrument tokens and publishes tick data to Redis.
    """

    def __init__(self, api_key: str, access_token: str, redis: RedisStore):
        self.ticker = KiteTicker(api_key, access_token)
        self.redis = redis
        self.subscribed_tokens: list[int] = []
        self._setup_callbacks()

    def _setup_callbacks(self):
        self.ticker.on_ticks = self._on_ticks
        self.ticker.on_connect = self._on_connect
        self.ticker.on_close = self._on_close
        self.ticker.on_error = self._on_error
        self.ticker.on_reconnect = self._on_reconnect

    def _on_connect(self, ws, response):
        logger.info("KiteTicker connected.")
        if self.subscribed_tokens:
            self.ticker.subscribe(self.subscribed_tokens)
            self.ticker.set_mode(self.ticker.MODE_FULL, self.subscribed_tokens)

    def _on_ticks(self, ws, ticks):
        """
        Called on every tick. Publishes to Redis.
        Tick format (MODE_FULL):
        {
            "instrument_token": 738561,
            "last_price": 2847.5,
            "volume_traded": 1234567,
            "ohlc": {"open": ..., "high": ..., "low": ..., "close": ...},
            "timestamp": datetime,
            ...
        }
        """
        for tick in ticks:
            token = tick["instrument_token"]
            # Convert datetime to string for JSON serialisation
            tick_serialisable = {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in tick.items()
            }
            self.redis.write(
                f"tick:{token}",
                json.dumps(tick_serialisable),
                ttl=60,
            )

    def _on_close(self, ws, code, reason):
        logger.warning("KiteTicker closed: %s %s", code, reason)

    def _on_error(self, ws, code, reason):
        logger.error("KiteTicker error: %s %s", code, reason)

    def _on_reconnect(self, ws, attempts_count):
        logger.info("KiteTicker reconnecting (attempt %d)...", attempts_count)

    def subscribe(self, instrument_tokens: list[int]):
        """Subscribe to a list of instrument tokens."""
        self.subscribed_tokens = instrument_tokens
        if self.ticker.is_connected():
            self.ticker.subscribe(instrument_tokens)
            self.ticker.set_mode(self.ticker.MODE_FULL, instrument_tokens)

    def start(self):
        """Start the WebSocket in a background thread."""
        self.ticker.connect(threaded=True)
        logger.info("KiteTicker started in background thread.")

    def stop(self):
        """Stop the WebSocket connection."""
        self.ticker.close()
        logger.info("KiteTicker stopped.")
```

---

## 9. Data Normalisation Layer

All three data sources (Kite, nsepython, yfinance) must return data
in this exact standardised format. Any agent or backtest code
that consumes data expects this format.

### Canonical OHLCV DataFrame format

```python
# Every get_ohlcv() function across all backends must return this:
pd.DataFrame({
    "timestamp": pd.Series(dtype="datetime64[ns, Asia/Kolkata]"),
    "open":      pd.Series(dtype="float64"),
    "high":      pd.Series(dtype="float64"),
    "low":       pd.Series(dtype="float64"),
    "close":     pd.Series(dtype="float64"),
    "volume":    pd.Series(dtype="int64"),
    "symbol":    pd.Series(dtype="str"),
})
# Sorted by timestamp ascending. No NaN values. No duplicate timestamps.
```

### Canonical live quote format

```python
# Every get_live_quote() function must return this dict structure:
{
    "SYMBOL": {
        "symbol":     str,
        "last_price": float,
        "open":       float,
        "high":       float,
        "low":        float,
        "close":      float,   # previous day close
        "volume":     int,
        "change_pct": float,   # % change from previous close
        "timestamp":  datetime,
    }
}
```

---

## 10. Swappable Data Source Abstraction

### Modify `tools/market_data.py` — replace entire file

```python
"""
tools/market_data.py

Unified data access layer. Routes all data requests to the configured
backend based on DATA_SOURCE environment variable.

Agents and backtest code ALWAYS call this module, never the backend
modules directly. This enables seamless switching between data sources
without changing any agent code.

DATA_SOURCE options:
  kite      — Kite Connect API (primary, ₹500/month)
  nsepython — NSE website scraper (free fallback)
  yfinance  — Yahoo Finance (free, 15-min delay, last resort)
"""

import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)
DATA_SOURCE = os.getenv("DATA_SOURCE", "kite")

# ── Shared state ──────────────────────────────────────────────────────────────
# The Kite client is injected at startup by the Orchestrator.
# Other backends need no client — they're stateless HTTP.

_kite_client = None


def set_kite_client(kite):
    """Called by Orchestrator after authentication to inject the Kite client."""
    global _kite_client
    _kite_client = kite
    logger.info("Kite client injected into market_data module.")


# ── Interval mapping ─────────────────────────────────────────────────────────
# Different backends use different interval strings. This map normalises them.

INTERVAL_MAP = {
    # standard → kite format
    "1m":  "minute",
    "3m":  "3minute",
    "5m":  "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h":  "60minute",
    "1d":  "day",
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_ohlcv(symbol: str, interval: str = "5m", days: int = 60) -> pd.DataFrame:
    """
    Fetch historical OHLCV data for a symbol.
    Returns canonical DataFrame format (see data normalisation spec).
    """
    if DATA_SOURCE == "kite":
        if not _kite_client:
            raise RuntimeError("Kite client not set. Call set_kite_client() first.")
        from tools.kite_market_data import get_ohlcv as kite_ohlcv
        kite_interval = INTERVAL_MAP.get(interval, interval)
        return kite_ohlcv(_kite_client, symbol, kite_interval, days)

    elif DATA_SOURCE == "nsepython":
        from tools.nsepython_data import get_ohlcv as nse_ohlcv
        return nse_ohlcv(symbol, interval, days)

    else:  # yfinance
        from tools.yfinance_fallback import get_ohlcv as yf_ohlcv
        return yf_ohlcv(symbol, interval, days)


def get_live_quote(symbols: list[str]) -> dict:
    """
    Fetch live quotes for a list of symbols.
    Returns canonical quote dict format.
    """
    if DATA_SOURCE == "kite":
        if not _kite_client:
            raise RuntimeError("Kite client not set.")
        from tools.kite_market_data import get_live_quote as kite_quote
        return kite_quote(_kite_client, symbols)

    elif DATA_SOURCE == "nsepython":
        from tools.nsepython_data import get_live_quote as nse_quote
        return nse_quote(symbols)

    else:
        from tools.yfinance_fallback import get_live_quote as yf_quote
        return yf_quote(symbols)


def get_options_chain(underlying: str, expiry_date: str) -> pd.DataFrame:
    """Fetch options chain. Only supported on Kite and nsepython backends."""
    if DATA_SOURCE == "kite":
        if not _kite_client:
            raise RuntimeError("Kite client not set.")
        from tools.kite_market_data import get_options_chain as kite_chain
        return kite_chain(_kite_client, underlying, expiry_date)

    elif DATA_SOURCE == "nsepython":
        from tools.nsepython_data import get_options_chain as nse_chain
        return nse_chain(underlying, expiry_date)

    else:
        raise NotImplementedError("Options chain not available via yfinance.")


def get_vwap(symbol: str) -> float:
    """Calculate current VWAP from today's 1-min data."""
    if DATA_SOURCE == "kite":
        if not _kite_client:
            raise RuntimeError("Kite client not set.")
        from tools.kite_market_data import get_vwap as kite_vwap
        return kite_vwap(_kite_client, symbol)
    else:
        # Calculate from OHLCV for other backends
        df = get_ohlcv(symbol, "1m", days=1)
        import datetime
        today = datetime.datetime.now().date()
        df = df[df["timestamp"].dt.date == today]
        if df.empty:
            return 0.0
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_vol"] = df["typical_price"] * df["volume"]
        return round(df["tp_vol"].sum() / df["volume"].sum(), 2)
```

---

## 11. Static IP Registration

This is a one-time manual step required before going LIVE.
Not needed for paper trading.

### Steps

1. Log in to `developers.kite.trade`
2. Go to "My Apps" → select your app
3. Find "IP Whitelist" section
4. Enter your GoDaddy VPS public IP address
   - Find it by running on the server: `curl ifconfig.me`
5. Save. Takes effect immediately.
6. Kite allows one primary and one backup IP per app.
   - Primary: your GoDaddy VPS IP
   - Backup: optional, can be your home IP for testing

**When to do this:** Set it up during paper trading so it's ready when you go live. Do not wait until day 1 of live trading to discover it's needed.

---

## 12. Config Changes

### Modify `config.py`

Remove all Fyers variables. Add:

```python
import os

# ── Kite Connect ──────────────────────────────────────────────────────────────
KITE_API_KEY    = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
KITE_REDIRECT_URI = os.getenv("KITE_REDIRECT_URI", "http://localhost:8080")
AUTH_MODE       = os.getenv("AUTH_MODE", "telegram")

# Validate at startup
if not KITE_API_KEY or not KITE_API_SECRET:
    raise EnvironmentError(
        "KITE_API_KEY and KITE_API_SECRET must be set in .env"
    )

if AUTH_MODE == "auto":
    required_auto = ["ZERODHA_USER_ID", "ZERODHA_PASSWORD", "ZERODHA_TOTP_SECRET"]
    missing = [v for v in required_auto if not os.getenv(v)]
    if missing:
        raise EnvironmentError(
            f"AUTH_MODE=auto requires these env vars: {missing}"
        )
```

---

## 13. Agent Changes

### `agents/orchestrator/orchestrator.py`

Replace the Fyers startup call with:

```python
from tools.kite_auth import load_or_refresh_token, force_reauthenticate
from tools.market_data import set_kite_client
from tools.kite_market_data import build_instrument_cache

async def startup(self):
    self.logger.info("Orchestrator starting up...")

    # Step 1 — authenticate with Kite
    try:
        kite = load_or_refresh_token(telegram=self.telegram)
        self.kite = kite
        set_kite_client(kite)  # inject into market_data abstraction layer
        self.logger.info("Kite authentication successful.")
    except TimeoutError:
        self.logger.error("Kite auth timed out.")
        self.telegram.send("Auth timed out. System halted. Send /authenticate.")
        self.set_system_mode("HALTED")
        return
    except Exception as e:
        self.logger.error("Kite auth failed: %s", e)
        self.telegram.send(f"Auth failed: {e}. Send /authenticate to retry.")
        self.set_system_mode("HALTED")
        return

    # Step 2 — build instrument cache (required for all data calls)
    try:
        build_instrument_cache(kite)
    except Exception as e:
        self.logger.error("Instrument cache build failed: %s", e)
        self.telegram.send(f"Instrument cache failed: {e}. System halted.")
        self.set_system_mode("HALTED")
        return

    # Step 3 — initialise all agents
    await self.initialise_agents()
    self.logger.info("All agents initialised. Starting pre-market sequence.")
```

Add `/authenticate` Telegram command handler:

```python
elif command == "/authenticate":
    try:
        kite = force_reauthenticate(telegram=self.telegram)
        self.kite = kite
        set_kite_client(kite)
        build_instrument_cache(kite)
    except Exception as e:
        self.telegram.send(f"Re-authentication failed: {e}")
```

### `agents/data_agent/data_agent.py`

Replace all Fyers data calls with calls to `tools/market_data.py`.
The data agent should never import from `tools/kite_market_data.py` directly.

```python
# CORRECT — always use the abstraction layer
from tools.market_data import get_ohlcv, get_live_quote, get_options_chain, get_vwap

# WRONG — never import backend directly from agents
# from tools.kite_market_data import get_ohlcv  ← DO NOT DO THIS
```

### `agents/execution_agent/execution_agent.py`

```python
import os
from tools.kite_broker import place_order, place_stoploss_order, get_order_status
from tools.order_simulator import simulate_fill, simulate_stoploss

def execute_order(self, order: dict):
    mode = os.getenv("TRADING_MODE", "PAPER").upper()

    if mode == "LIVE":
        # Real execution
        order_id = place_order(
            kite=self.orchestrator.kite,
            symbol=order["symbol"],
            transaction_type=order["transaction_type"],
            quantity=order["quantity"],
            order_type=order["order_type"],
            price=order["price"],
        )
        # Place stop-loss immediately after entry
        sl_order_id = place_stoploss_order(
            kite=self.orchestrator.kite,
            symbol=order["symbol"],
            transaction_type="SELL" if order["transaction_type"] == "BUY" else "BUY",
            quantity=order["quantity"],
            trigger_price=order["stop_loss_price"],
        )
        return {"order_id": order_id, "sl_order_id": sl_order_id}

    else:
        # Paper mode — simulated execution
        return simulate_fill(order)
```

---

## 14. Docker Changes

### `docker-compose.yml`

Add port 8080 to the `swarm` service for OAuth2 callback:

```yaml
swarm:
  build:
    context: .
    dockerfile: Dockerfile
  container_name: trading_swarm
  restart: always
  depends_on:
    redis:
      condition: service_healthy
  env_file:
    - .env
  environment:
    - REDIS_HOST=redis
    - REDIS_PORT=6379
  volumes:
    - sqlite_data:/app/data
    - ./logs:/app/logs
  ports:
    - "0.0.0.0:8080:8080"    # Kite OAuth2 callback — needed for auth
  command: python main.py
  networks:
    - trading_net
```

No other Docker changes required.

---

## 15. Tests to Write

### `tests/test_kite_auth.py`

```python
def test_load_todays_token_returns_token_if_exists_today()
def test_load_todays_token_returns_none_for_stale_token()
def test_load_todays_token_returns_none_if_file_missing()
def test_load_todays_token_handles_corrupt_json()
def test_callback_handler_captures_request_token()
def test_callback_handler_returns_404_for_unknown_path()
def test_totp_code_matches_expected_format()
    # pyotp.TOTP(secret).now() returns 6-digit string
def test_save_token_creates_file_with_correct_date()
def test_force_reauthenticate_deletes_existing_token()
```

### `tests/test_kite_market_data.py`

```python
def test_get_ohlcv_returns_canonical_format()
    # Check columns, dtypes, no NaN, sorted by timestamp
def test_get_live_quote_returns_canonical_format()
    # Check all required keys present
def test_get_instrument_token_raises_for_unknown_symbol()
def test_get_vwap_returns_float()
def test_market_data_router_uses_correct_backend()
    # Set DATA_SOURCE=yfinance, verify yfinance backend called
```

### `tests/test_kite_broker.py`

```python
def test_place_order_raises_in_paper_mode()
    # TRADING_MODE=PAPER → RuntimeError
def test_map_order_type_raises_for_unknown_type()
def test_place_stoploss_uses_slm_order_type()
```

---

## 16. Implementation Sequence

Implement in this exact order. Do not proceed to the next step
until the current step is verified working.

### Step 1 — Dependencies and config (30 min)
- [ ] Update `requirements.txt` (add kiteconnect, pyotp; remove fyers-apiv3)
- [ ] Update `config.py` with Kite variables and validation
- [ ] Update `.env.example`
- [ ] Update `.gitignore` (add `data/kite_token.json`)
- [ ] Run: `pip install kiteconnect pyotp`
- [ ] Run: `python -c "from kiteconnect import KiteConnect; print('OK')"`

### Step 2 — Authentication (2 hours)
- [ ] Create `tools/kite_auth.py` (full file as specified above)
- [ ] Add `send_auth_request` method to `comms/telegram_bot.py`
- [ ] Write tests in `tests/test_kite_auth.py`
- [ ] Run tests: `pytest tests/test_kite_auth.py -v`
- [ ] Manual test: run `load_or_refresh_token()` with AUTH_MODE=telegram
        → Telegram message should appear with button
        → Tap button → token file should appear in data/

### Step 3 — Market data (2 hours)
- [ ] Create `tools/kite_market_data.py` (full file as specified above)
- [ ] Create `tools/yfinance_fallback.py` (implement get_ohlcv, get_live_quote)
- [ ] Modify `tools/market_data.py` (replace with abstraction layer)
- [ ] Write tests in `tests/test_kite_market_data.py`
- [ ] Manual test: fetch RELIANCE 5-min OHLCV for last 30 days
        → Should return DataFrame with correct columns
- [ ] Manual test: fetch live quote for RELIANCE, HDFCBANK
        → Should return dict with all canonical fields

### Step 4 — Instrument cache (30 min)
- [ ] Verify `build_instrument_cache()` runs without error
- [ ] Verify `get_instrument_token("RELIANCE")` returns correct token
- [ ] Verify `get_instrument_token("INVALID")` raises KeyError

### Step 5 — Order placement (1 hour)
- [ ] Create `tools/kite_broker.py` (full file as specified above)
- [ ] Write tests in `tests/test_kite_broker.py`
- [ ] Verify PAPER mode guard raises RuntimeError
- [ ] Do NOT test live order placement until Step 8

### Step 6 — WebSocket ticker (1 hour)
- [ ] Create `tools/kite_ticker.py` (full file as specified above)
- [ ] Verify KiteTickerManager starts without error
- [ ] Verify tick data appears in Redis after subscribing

### Step 7 — Orchestrator integration (1 hour)
- [ ] Update `agents/orchestrator/orchestrator.py` startup method
- [ ] Update `/authenticate` command handler
- [ ] Update Data Agent to use `tools/market_data.py` abstraction
- [ ] Update Execution Agent order routing
- [ ] Run full system in PAPER mode: `docker compose up`
        → Should authenticate, build cache, start all agents

### Step 8 — Static IP registration (10 min)
- [ ] Run `curl ifconfig.me` on GoDaddy VPS to get public IP
- [ ] Register IP at developers.kite.trade → My Apps → IP Whitelist
- [ ] Verify order placement works from VPS IP (test with paper order)

### Step 9 — End-to-end paper trading test (1 trading day)
- [ ] Run system for a full trading day in PAPER mode
- [ ] Verify: morning auth Telegram message appears at 6:55 AM
- [ ] Verify: instrument cache builds at 7:00 AM
- [ ] Verify: market data flows to agents during market hours
- [ ] Verify: signals generated and paper orders simulated
- [ ] Verify: EOD report arrives via Telegram at 4:00 PM
- [ ] Verify: system does not start on weekend (market_calendar check)

### Step 10 — Switch to TOTP auto-login (optional, before going live)
- [ ] Set AUTH_MODE=auto in .env
- [ ] Add ZERODHA_USER_ID, ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET to .env
- [ ] Restart container: `docker compose restart swarm`
- [ ] Verify: no Telegram auth message at 6:55 AM — fully automated

---

*End of Kite Connect implementation plan.*
*Version: 1.0*
*Replaces: fyers_auth_implementation.md*
