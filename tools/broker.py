"""Fyers API wrapper for broker operations.

Handles authentication (OAuth2 with callback server), market data,
and order placement via Fyers v3 API.
All order methods log extensively for audit trail.
"""

import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
from tools.logger import get_agent_logger

from config import FYERS_CLIENT_ID, FYERS_SECRET_KEY, FYERS_REDIRECT_URI

logger = get_agent_logger("broker")

# --- OAuth2 Authentication Module ---

TOKEN_FILE = Path("data/fyers_token.json")
AUTH_TIMEOUT_SECONDS = 300  # 5 minutes
CALLBACK_PORT = 8080


class _FyersCallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP server that catches the Fyers OAuth2 redirect.

    Listens on CALLBACK_PORT for a single GET request containing
    the auth_code query parameter.
    """
    auth_code: str | None = None
    received: bool = False

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)

        if "auth_code" in params:
            _FyersCallbackHandler.auth_code = params["auth_code"][0]
            _FyersCallbackHandler.received = True

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;padding:40px'>"
                b"<h2>Authentication successful.</h2>"
                b"<p>You can close this tab and return to the app.</p>"
                b"</body></html>"
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP server console output


def _run_auth_flow(telegram) -> str:
    """Run the full OAuth2 flow.

    1. Generates Fyers auth URL
    2. Sends it to Telegram as a button
    3. Starts callback server on CALLBACK_PORT
    4. Waits for auth code (timeout: AUTH_TIMEOUT_SECONDS)
    5. Exchanges auth code for access token
    6. Saves token to TOKEN_FILE
    7. Returns access token string

    Raises:
        TimeoutError: if user does not complete auth within timeout
        ValueError: if Fyers token exchange fails
    """
    from fyers_apiv3 import fyersModel

    # Reset handler state
    _FyersCallbackHandler.auth_code = None
    _FyersCallbackHandler.received = False

    # Build Fyers auth URL
    session = fyersModel.SessionModel(
        client_id=FYERS_CLIENT_ID,
        secret_key=FYERS_SECRET_KEY,
        redirect_uri=FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
    )
    auth_url = session.generate_authcode()

    # Send Telegram message with inline button
    telegram.send_auth_request(
        message=(
            "Fyers authentication required to start the trading system.\n"
            "Tap the button below, log in to Fyers, and approve access.\n"
            f"Timeout in {AUTH_TIMEOUT_SECONDS // 60} minutes."
        ),
        url=auth_url,
        button_text="Authorise Fyers →",
    )

    # Start callback HTTP server in a background thread
    server = HTTPServer(("0.0.0.0", CALLBACK_PORT), _FyersCallbackHandler)

    def _serve():
        while not _FyersCallbackHandler.received:
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    # Poll for auth code with timeout
    elapsed = 0
    while not _FyersCallbackHandler.received and elapsed < AUTH_TIMEOUT_SECONDS:
        time.sleep(1)
        elapsed += 1

    server.server_close()

    if not _FyersCallbackHandler.auth_code:
        telegram.send_message(
            "Fyers authentication timed out after "
            f"{AUTH_TIMEOUT_SECONDS // 60} minutes. "
            "System will not start. Send /authenticate to retry."
        )
        raise TimeoutError(
            f"Fyers auth not completed within {AUTH_TIMEOUT_SECONDS} seconds."
        )

    # Exchange auth code for access token
    session.set_token(_FyersCallbackHandler.auth_code)
    token_response = session.generate_token()

    if "access_token" not in token_response:
        error = token_response.get("message", "Unknown error")
        telegram.send_message(
            f"Fyers token exchange failed: {error}. Send /authenticate to retry."
        )
        raise ValueError(f"Fyers token exchange failed: {token_response}")

    access_token = token_response["access_token"]

    # Persist token to disk
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token_data = {
        "access_token": access_token,
        "generated_at": datetime.now().isoformat(),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)

    telegram.send_message(
        "Fyers authenticated successfully. "
        "Token saved. System starting market data feeds..."
    )

    return access_token


def load_or_refresh_token(telegram) -> str:
    """Main entry point called by Orchestrator at startup (6:55 AM).

    Logic:
    - If a token file exists AND was generated today -> reuse it
    - Otherwise -> run full auth flow

    This ensures that a container restart mid-day does not require
    re-authentication.

    Args:
        telegram: TelegramBot instance for sending notifications

    Returns:
        Valid Fyers access token string
    """
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE) as f:
                token_data = json.load(f)

            generated_date = token_data.get("generated_date", "")
            today = datetime.now().strftime("%Y-%m-%d")

            if generated_date == today:
                access_token = token_data["access_token"]
                telegram.send_message(
                    "Fyers token found from earlier today. "
                    "Reusing — no re-authentication needed."
                )
                return access_token

        except (json.JSONDecodeError, KeyError):
            TOKEN_FILE.unlink(missing_ok=True)

    return _run_auth_flow(telegram)


def get_fyers_client(access_token: str):
    """Returns an initialised FyersModel client ready for API calls.

    Call this after load_or_refresh_token().
    """
    from fyers_apiv3 import fyersModel

    return fyersModel.FyersModel(
        client_id=FYERS_CLIENT_ID,
        token=access_token,
        log_path="logs/",
        is_async=False,
    )


def force_reauthenticate(telegram) -> str:
    """Called when user sends /authenticate via Telegram.

    Deletes existing token and runs full auth flow.
    """
    TOKEN_FILE.unlink(missing_ok=True)
    telegram.send_message("Existing token cleared. Starting fresh authentication...")
    return _run_auth_flow(telegram)


class FyersBroker:
    def __init__(self, client_id: str, secret_key: str, redirect_uri: str):
        self.client_id = client_id
        self.secret_key = secret_key
        self.redirect_uri = redirect_uri
        self.fyers = None
        self._authenticated = False

    def authenticate(self) -> bool:
        """Authenticate with Fyers API using OAuth2.

        Requires manual browser step for first-time auth to get auth_code.
        After that, uses access_token.
        """
        try:
            from fyers_apiv3 import fyersModel

            session = fyersModel.SessionModel(
                client_id=self.client_id,
                secret_key=self.secret_key,
                redirect_uri=self.redirect_uri,
                response_type="code",
                grant_type="authorization_code",
            )

            # Generate auth URL — user must visit this URL and get auth_code
            auth_url = session.generate_authcode()
            logger.info(f"Fyers auth URL generated: {auth_url}")
            logger.warning(
                "Visit the auth URL, authorize, and paste the auth_code. "
                "For automated flow, store access_token in .env."
            )
            return False  # Manual step required

        except ImportError:
            logger.warning("fyers-apiv3 not installed, broker unavailable")
            return False
        except Exception as e:
            logger.error(f"Fyers authentication failed: {e}")
            return False

    def authenticate_with_token(self, access_token: str) -> bool:
        """Authenticate using a pre-obtained access token."""
        try:
            from fyers_apiv3 import fyersModel

            self.fyers = fyersModel.FyersModel(
                client_id=self.client_id,
                is_async=False,
                token=access_token,
                log_path="",
            )
            profile = self.fyers.get_profile()
            if profile.get("s") == "ok":
                self._authenticated = True
                logger.info("Fyers authenticated successfully")
                return True
            logger.error(f"Fyers auth failed: {profile}")
            return False
        except Exception as e:
            logger.error(f"Fyers token auth failed: {e}")
            return False

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated and self.fyers is not None

    def get_quote(self, symbol: str) -> dict:
        """Get current quote for a symbol. Symbol in Fyers format: NSE:RELIANCE-EQ"""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")
        data = {"symbols": symbol}
        response = self.fyers.quotes(data=data)
        if response.get("s") == "ok" and response.get("d"):
            q = response["d"][0]["v"]
            return {
                "symbol": symbol,
                "ltp": q.get("lp"),
                "open": q.get("open_price"),
                "high": q.get("high_price"),
                "low": q.get("low_price"),
                "close": q.get("prev_close_price"),
                "volume": q.get("volume"),
                "timestamp": q.get("tt"),
            }
        raise ValueError(f"Failed to get quote: {response}")

    def get_history(self, symbol: str, resolution: str, start: str,
                    end: str) -> pd.DataFrame:
        """Get historical OHLCV data.

        Args:
            symbol: Fyers format, e.g. "NSE:RELIANCE-EQ"
            resolution: "1", "5", "15", "60", "D"
            start: "YYYY-MM-DD"
            end: "YYYY-MM-DD"
        """
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")
        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": start,
            "range_to": end,
            "cont_flag": "1",
        }
        response = self.fyers.history(data=data)
        if response.get("s") == "ok" and response.get("candles"):
            df = pd.DataFrame(
                response["candles"],
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
            df = df.drop(columns=["timestamp"])
            return df
        raise ValueError(f"Failed to get history: {response}")

    # --- Order Methods ---

    def place_order(self, symbol: str, qty: int, order_type: str,
                    price: float, transaction_type: str,
                    product_type: str = "INTRADAY") -> dict:
        """Place an order via Fyers API.

        Args:
            symbol: Fyers format, e.g. "NSE:RELIANCE-EQ"
            qty: Number of shares
            order_type: "LIMIT" (1) or "MARKET" (2)
            price: Limit price (ignored for MARKET)
            transaction_type: "BUY" (1) or "SELL" (-1)
            product_type: "INTRADAY" or "CNC" (delivery)

        Returns: {order_id, status, message}
        """
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        type_map = {"LIMIT": 1, "MARKET": 2, "SL": 3, "SL-M": 4}
        side_map = {"BUY": 1, "SELL": -1}
        product_map = {"INTRADAY": "INTRADAY", "CNC": "CNC", "MARGIN": "MARGIN"}

        order_data = {
            "symbol": symbol,
            "qty": qty,
            "type": type_map.get(order_type, 1),
            "side": side_map.get(transaction_type, 1),
            "productType": product_map.get(product_type, "INTRADAY"),
            "limitPrice": price if order_type == "LIMIT" else 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }

        logger.bind(log_type="trade").info(
            f"LIVE ORDER: {transaction_type} {symbol} {qty}x "
            f"@ {price} ({order_type}, {product_type})"
        )

        try:
            response = self.fyers.place_order(data=order_data)
            if response.get("s") == "ok":
                order_id = response.get("id", "")
                logger.bind(log_type="trade").info(
                    f"ORDER PLACED: {order_id}"
                )
                return {
                    "order_id": order_id,
                    "status": "PLACED",
                    "message": response.get("message", ""),
                }
            logger.error(f"Order placement failed: {response}")
            return {
                "order_id": "",
                "status": "FAILED",
                "message": response.get("message", str(response)),
            }
        except Exception as e:
            logger.error(f"Order placement exception: {e}")
            return {"order_id": "", "status": "ERROR", "message": str(e)}

    def place_stoploss_order(self, symbol: str, qty: int,
                             trigger_price: float,
                             transaction_type: str = "SELL",
                             product_type: str = "INTRADAY") -> dict:
        """Place a stop-loss market order.

        Args:
            symbol: Fyers format
            qty: Shares
            trigger_price: Stop trigger price
            transaction_type: "BUY" or "SELL"
            product_type: "INTRADAY" or "CNC"

        Returns: {order_id, status, message}
        """
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        side_map = {"BUY": 1, "SELL": -1}

        order_data = {
            "symbol": symbol,
            "qty": qty,
            "type": 4,  # SL-M (stop-loss market)
            "side": side_map.get(transaction_type, -1),
            "productType": product_type,
            "limitPrice": 0,
            "stopPrice": trigger_price,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }

        logger.bind(log_type="trade").info(
            f"LIVE SL ORDER: {transaction_type} {symbol} {qty}x "
            f"trigger @ {trigger_price}"
        )

        try:
            response = self.fyers.place_order(data=order_data)
            if response.get("s") == "ok":
                order_id = response.get("id", "")
                logger.info(f"SL ORDER PLACED: {order_id}")
                return {
                    "order_id": order_id,
                    "status": "PLACED",
                    "message": response.get("message", ""),
                }
            logger.error(f"SL order failed: {response}")
            return {"order_id": "", "status": "FAILED",
                    "message": response.get("message", str(response))}
        except Exception as e:
            logger.error(f"SL order exception: {e}")
            return {"order_id": "", "status": "ERROR", "message": str(e)}

    def modify_order(self, order_id: str, qty: int = None,
                     price: float = None, trigger_price: float = None) -> dict:
        """Modify an existing order."""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        data = {"id": order_id}
        if qty is not None:
            data["qty"] = qty
        if price is not None:
            data["limitPrice"] = price
        if trigger_price is not None:
            data["stopPrice"] = trigger_price

        try:
            response = self.fyers.modify_order(data=data)
            logger.info(f"Order modified {order_id}: {response}")
            return {
                "order_id": order_id,
                "status": "MODIFIED" if response.get("s") == "ok" else "FAILED",
                "message": response.get("message", ""),
            }
        except Exception as e:
            logger.error(f"Modify order exception: {e}")
            return {"order_id": order_id, "status": "ERROR", "message": str(e)}

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order."""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        try:
            response = self.fyers.cancel_order(data={"id": order_id})
            logger.info(f"Order cancelled {order_id}: {response}")
            return {
                "order_id": order_id,
                "status": "CANCELLED" if response.get("s") == "ok" else "FAILED",
                "message": response.get("message", ""),
            }
        except Exception as e:
            logger.error(f"Cancel order exception: {e}")
            return {"order_id": order_id, "status": "ERROR", "message": str(e)}

    def get_order_status(self, order_id: str) -> dict:
        """Get status of a specific order."""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        try:
            response = self.fyers.orderbook()
            if response.get("s") == "ok":
                for order in response.get("orderBook", []):
                    if order.get("id") == order_id:
                        return {
                            "order_id": order_id,
                            "status": order.get("status"),
                            "filled_qty": order.get("filledQty", 0),
                            "fill_price": order.get("tradedPrice", 0),
                            "message": order.get("message", ""),
                        }
            return {"order_id": order_id, "status": "NOT_FOUND"}
        except Exception as e:
            logger.error(f"Get order status exception: {e}")
            return {"order_id": order_id, "status": "ERROR", "message": str(e)}

    def get_positions(self) -> list[dict]:
        """Get all open positions from broker."""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        try:
            response = self.fyers.positions()
            if response.get("s") == "ok":
                positions = []
                for p in response.get("netPositions", []):
                    if p.get("netQty", 0) != 0:
                        positions.append({
                            "symbol": p.get("symbol", ""),
                            "quantity": abs(p.get("netQty", 0)),
                            "direction": "LONG" if p.get("netQty", 0) > 0 else "SHORT",
                            "avg_price": p.get("avgPrice", 0),
                            "ltp": p.get("ltp", 0),
                            "pnl": p.get("pl", 0),
                            "product_type": p.get("productType", ""),
                        })
                return positions
            logger.error(f"Get positions failed: {response}")
            return []
        except Exception as e:
            logger.error(f"Get positions exception: {e}")
            return []

    def get_funds(self) -> dict:
        """Get available funds/margin."""
        if not self.is_authenticated:
            raise RuntimeError("Broker not authenticated")

        try:
            response = self.fyers.funds()
            if response.get("s") == "ok":
                funds = response.get("fund_limit", [])
                result = {}
                for f in funds:
                    title = f.get("title", "")
                    if title == "Total Balance":
                        result["total_balance"] = f.get("equityAmount", 0)
                    elif title == "Available Balance":
                        result["available_balance"] = f.get("equityAmount", 0)
                    elif title == "Used Margin":
                        result["used_margin"] = f.get("equityAmount", 0)
                return result
            logger.error(f"Get funds failed: {response}")
            return {}
        except Exception as e:
            logger.error(f"Get funds exception: {e}")
            return {}

    def exit_position(self, symbol: str, qty: int, direction: str,
                      product_type: str = "INTRADAY") -> dict:
        """Exit an open position by placing an opposite order.

        Args:
            symbol: Fyers format
            qty: Shares to exit
            direction: Current direction ("LONG" or "SHORT")
            product_type: "INTRADAY" or "CNC"
        """
        txn_type = "SELL" if direction == "LONG" else "BUY"
        return self.place_order(
            symbol=symbol, qty=qty, order_type="MARKET",
            price=0, transaction_type=txn_type,
            product_type=product_type,
        )
