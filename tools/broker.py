"""Fyers API wrapper for broker operations.

Phase 1: Only authentication and data methods implemented.
Order methods are stubs for Phase 5+.
"""

import pandas as pd
from tools.logger import get_agent_logger

logger = get_agent_logger("broker")


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

    # --- Order Methods (stubs for Phase 5+) ---

    def place_order(self, symbol: str, qty: int, order_type: str,
                    price: float, transaction_type: str) -> dict:
        raise NotImplementedError("Order placement not yet implemented")

    def place_stoploss_order(self, symbol: str, qty: int,
                             trigger_price: float) -> dict:
        raise NotImplementedError("Stop-loss order not yet implemented")

    def get_order_status(self, order_id: str) -> dict:
        raise NotImplementedError("Order status not yet implemented")

    def cancel_order(self, order_id: str) -> dict:
        raise NotImplementedError("Order cancel not yet implemented")

    def get_positions(self) -> list[dict]:
        raise NotImplementedError("Positions not yet implemented")

    def get_funds(self) -> dict:
        raise NotImplementedError("Funds not yet implemented")
