"""Kite Connect authentication module.

Supports two modes controlled by AUTH_MODE environment variable:
  - telegram: sends Telegram button, user taps on phone
  - auto:     fully automated TOTP login, zero human interaction

Both modes persist the token to disk and reuse it within the same day.
Token expires at midnight IST daily — re-authentication happens at 6:55 AM.
"""

import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tools.logger import get_agent_logger

logger = get_agent_logger("kite_auth")

# ── Constants ────────────────────────────────────────────────────────────────

TOKEN_FILE = Path("data/kite_token.json")
AUTH_TIMEOUT_SECONDS = 300  # 5 minutes
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
    """Returns today's access token if it exists and was generated today.

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
    """Minimal HTTP server that catches the Kite OAuth2 redirect.

    Kite sends 'request_token' in the callback, NOT 'auth_code'.
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
        pass  # Suppress HTTP server console logs


# ── Telegram mode auth flow ───────────────────────────────────────────────────

def _auth_via_telegram(kite, telegram) -> str:
    """Send Kite login URL to Telegram, wait for OAuth2 callback.

    Returns access token on success.
    """
    _KiteCallbackHandler.request_token = None
    _KiteCallbackHandler.received = False

    login_url = kite.login_url()
    logger.info("Generated Kite login URL.")

    telegram.send_auth_request(
        message=(
            "Zerodha authentication required to start the trading system.\n\n"
            "Tap the button below, log in to Zerodha, and approve access.\n"
            f"Timeout in {AUTH_TIMEOUT_SECONDS // 60} minutes."
        ),
        url=login_url,
        button_text="Authorise Zerodha \u2192",
    )

    server = HTTPServer(("0.0.0.0", CALLBACK_PORT), _KiteCallbackHandler)

    def _serve():
        while not _KiteCallbackHandler.received:
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    elapsed = 0
    while not _KiteCallbackHandler.received and elapsed < AUTH_TIMEOUT_SECONDS:
        time.sleep(1)
        elapsed += 1

    server.server_close()

    if not _KiteCallbackHandler.request_token:
        telegram.send_message(
            "Zerodha authentication timed out after "
            f"{AUTH_TIMEOUT_SECONDS // 60} minutes. "
            "System halted. Send /authenticate to retry."
        )
        raise TimeoutError(
            f"Kite auth not completed within {AUTH_TIMEOUT_SECONDS} seconds."
        )

    logger.info("Received request_token. Generating session...")
    return _exchange_token(kite, _KiteCallbackHandler.request_token, telegram)


# ── TOTP auto-login mode ──────────────────────────────────────────────────────

def _auth_via_totp(kite, telegram, config: dict) -> str:
    """Fully automated login using Zerodha credentials + TOTP.

    No human interaction required. Runs at 6:55 AM via scheduler.
    """
    import httpx
    import pyotp

    user_id = config["user_id"]
    password = config["password"]
    totp_secret = config["totp_secret"]
    api_key = config["api_key"]

    logger.info("Starting TOTP auto-login for user %s", user_id)

    with httpx.Client(timeout=15) as session:
        # Step 1 — submit user ID and password
        resp = session.post(
            "https://kite.zerodha.com/api/login",
            data={"user_id": user_id, "password": password},
        )
        resp.raise_for_status()
        login_data = resp.json()

        if login_data.get("status") != "success":
            raise ValueError(f"Login step 1 failed: {login_data.get('message')}")

        request_id = login_data["data"]["request_id"]
        logger.info("Login step 1 successful. request_id received.")

        # Step 2 — submit TOTP code
        totp = pyotp.TOTP(totp_secret)
        totp_code = totp.now()
        logger.info("Generated TOTP code.")

        resp = session.post(
            "https://kite.zerodha.com/api/twofa",
            data={
                "user_id": user_id,
                "request_id": request_id,
                "twofa_value": totp_code,
                "twofa_type": "totp",
                "skip_session": True,
            },
        )
        resp.raise_for_status()
        twofa_data = resp.json()

        if twofa_data.get("status") != "success":
            raise ValueError(f"TOTP step failed: {twofa_data.get('message')}")

        logger.info("TOTP verification successful.")

        # Step 3 — get request_token via Connect redirect
        resp = session.get(
            f"https://kite.trade/connect/login?api_key={api_key}&v=3",
            follow_redirects=True,
        )
        final_url = str(resp.url)
        params = parse_qs(urlparse(final_url).query)

        if "request_token" not in params:
            raise ValueError(
                f"request_token not found in redirect URL: {final_url}"
            )

        request_token = params["request_token"][0]
        logger.info("request_token extracted from redirect URL.")

    # Step 4 — exchange request_token for access_token
    return _exchange_token(kite, request_token, telegram)


# ── Token exchange (shared by both modes) ────────────────────────────────────

def _exchange_token(kite, request_token: str, telegram) -> str:
    """Exchange a request_token for a persistent access_token.

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
            telegram.send_message(
                f"Authentication error: {msg}. Send /authenticate to retry."
            )
        raise ValueError(msg) from e

    _save_token(access_token)

    if telegram:
        telegram.send_message(
            "Zerodha authenticated successfully. "
            "Token valid until midnight IST. System starting..."
        )

    return access_token


# ── Main entry point ──────────────────────────────────────────────────────────

def load_or_refresh_token(telegram=None):
    """Main entry point called by Orchestrator at 6:55 AM startup.

    Returns an initialised and authenticated KiteConnect client.
    """
    import os
    from kiteconnect import KiteConnect
    from config import KITE_API_KEY

    kite = KiteConnect(api_key=KITE_API_KEY)

    # Try to reuse today's token first
    existing_token = _load_todays_token()
    if existing_token:
        kite.set_access_token(existing_token)
        if telegram:
            telegram.send_message(
                "Kite token reused from earlier today. System starting..."
            )
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
        missing = [k for k, v in config.items() if not v]
        if missing:
            raise EnvironmentError(
                f"AUTH_MODE=auto requires: {missing}. Check .env file."
            )
        access_token = _auth_via_totp(kite, telegram, config)
    else:
        if not telegram:
            raise ValueError(
                "AUTH_MODE=telegram requires a TelegramBot instance. "
                "Pass telegram= to load_or_refresh_token()."
            )
        access_token = _auth_via_telegram(kite, telegram)

    kite.set_access_token(access_token)
    return kite


def force_reauthenticate(telegram=None):
    """Delete existing token and force full re-authentication.

    Called when user sends /authenticate via Telegram.
    """
    TOKEN_FILE.unlink(missing_ok=True)
    logger.info("Existing token deleted. Forcing re-authentication.")
    if telegram:
        telegram.send_message("Existing token cleared. Starting fresh authentication...")
    return load_or_refresh_token(telegram)
