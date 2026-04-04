"""Market data provider with Fyers primary and yfinance fallback.

All symbols internally use clean format (e.g., "RELIANCE", "NIFTY").
Conversion to broker-specific format happens inside this module only.
"""

import time
from datetime import datetime, timedelta

import pandas as pd

from tools.broker import FyersBroker
from tools.logger import get_agent_logger

logger = get_agent_logger("market_data")

# Symbol mappings
YFINANCE_INDEX_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "INDIAVIX": "^INDIAVIX",
}

FYERS_INDEX_MAP = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "INDIAVIX": "NSE:INDIAVIX-INDEX",
}

# Rate limiting
_last_call_time = 0.0
_MIN_CALL_INTERVAL = 0.34  # ~3 calls/sec


def _rate_limit():
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)
    _last_call_time = time.time()


def _to_fyers_symbol(symbol: str) -> str:
    """Convert clean symbol to Fyers format."""
    if symbol in FYERS_INDEX_MAP:
        return FYERS_INDEX_MAP[symbol]
    return f"NSE:{symbol}-EQ"


def _to_yfinance_symbol(symbol: str) -> str:
    """Convert clean symbol to yfinance format."""
    if symbol in YFINANCE_INDEX_MAP:
        return YFINANCE_INDEX_MAP[symbol]
    return f"{symbol}.NS"


class MarketDataProvider:
    def __init__(self, fyers_broker: FyersBroker = None,
                 sqlite_store=None):
        self.fyers = fyers_broker
        self.db = sqlite_store

    def _log_data_event(self, source: str, data_type: str, symbol: str = None,
                        success: bool = True, error_message: str = None,
                        fallback_used: bool = False):
        if self.db:
            self.db.log_data_event(
                source=source, data_type=data_type, symbol=symbol,
                success=success, error_message=error_message,
                fallback_used=fallback_used,
            )

    def get_quote(self, symbol: str) -> dict:
        """Get current quote for a symbol.

        Returns: {symbol, ltp, open, high, low, close, volume, timestamp}
        """
        # Try Fyers first
        if self.fyers and self.fyers.is_authenticated:
            try:
                _rate_limit()
                quote = self.fyers.get_quote(_to_fyers_symbol(symbol))
                self._log_data_event("fyers", "quote", symbol)
                return quote
            except Exception as e:
                logger.warning(f"Fyers quote failed for {symbol}: {e}, falling back to yfinance")

        # Fallback: yfinance
        return self._yfinance_quote(symbol)

    def _yfinance_quote(self, symbol: str) -> dict:
        try:
            import yfinance as yf
            _rate_limit()
            ticker = yf.Ticker(_to_yfinance_symbol(symbol))
            info = ticker.fast_info
            quote = {
                "symbol": symbol,
                "ltp": info.last_price,
                "open": info.open,
                "high": info.day_high,
                "low": info.day_low,
                "close": info.previous_close,
                "volume": info.last_volume,
                "timestamp": datetime.now().isoformat(),
            }
            self._log_data_event("yfinance", "quote", symbol,
                                 fallback_used=self.fyers is not None)
            return quote
        except Exception as e:
            self._log_data_event("yfinance", "quote", symbol,
                                 success=False, error_message=str(e))
            raise RuntimeError(f"All data sources failed for {symbol}: {e}")

    def get_ohlcv(self, symbol: str, interval: str = "5",
                  count: int = 100) -> pd.DataFrame:
        """Get OHLCV data.

        Args:
            symbol: Clean symbol (e.g., "RELIANCE")
            interval: "1", "5", "15", "60", "D"
            count: Number of bars

        Returns: DataFrame with columns [datetime, open, high, low, close, volume]
        """
        # Try Fyers
        if self.fyers and self.fyers.is_authenticated:
            try:
                _rate_limit()
                end = datetime.now()
                # Estimate start date from count and interval
                minutes = int(interval) if interval != "D" else 1440
                start = end - timedelta(minutes=minutes * count * 1.5)
                df = self.fyers.get_history(
                    symbol=_to_fyers_symbol(symbol),
                    resolution=interval,
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                )
                self._log_data_event("fyers", "ohlcv", symbol)
                return df.tail(count).reset_index(drop=True)
            except Exception as e:
                logger.warning(f"Fyers OHLCV failed for {symbol}: {e}, falling back")

        # Fallback: yfinance
        return self._yfinance_ohlcv(symbol, interval, count)

    def _yfinance_ohlcv(self, symbol: str, interval: str,
                        count: int) -> pd.DataFrame:
        try:
            import yfinance as yf
            _rate_limit()

            # Map interval to yfinance format
            yf_interval_map = {
                "1": "1m", "5": "5m", "15": "15m", "60": "1h", "D": "1d",
            }
            yf_interval = yf_interval_map.get(interval, "5m")

            # yfinance period heuristic
            if yf_interval in ("1m", "5m"):
                period = "5d"
            elif yf_interval in ("15m", "1h"):
                period = "1mo"
            else:
                period = "6mo"

            ticker = yf.Ticker(_to_yfinance_symbol(symbol))
            df = ticker.history(period=period, interval=yf_interval)

            if df.empty:
                raise ValueError(f"No data returned for {symbol}")

            df = df.reset_index()
            # Normalize column names
            rename_map = {
                "Date": "datetime", "Datetime": "datetime",
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            }
            df = df.rename(columns=rename_map)
            df = df[["datetime", "open", "high", "low", "close", "volume"]]
            df = df.tail(count).reset_index(drop=True)

            self._log_data_event("yfinance", "ohlcv", symbol,
                                 fallback_used=self.fyers is not None)
            return df
        except Exception as e:
            self._log_data_event("yfinance", "ohlcv", symbol,
                                 success=False, error_message=str(e))
            raise RuntimeError(f"All data sources failed for {symbol} OHLCV: {e}")

    def get_index_data(self, index: str = "NIFTY") -> dict:
        """Get current index data (Nifty, BankNifty, VIX).

        Returns: {symbol, ltp, open, high, low, close, volume, timestamp}
        """
        return self.get_quote(index)

    def get_historical(self, symbol: str, start: str, end: str,
                       interval: str = "5") -> pd.DataFrame:
        """Get historical data for backtesting. Larger date ranges.

        Args:
            symbol: Clean symbol
            start: "YYYY-MM-DD"
            end: "YYYY-MM-DD"
            interval: "1", "5", "15", "60", "D"
        """
        # Try Fyers
        if self.fyers and self.fyers.is_authenticated:
            try:
                _rate_limit()
                df = self.fyers.get_history(
                    symbol=_to_fyers_symbol(symbol),
                    resolution=interval,
                    start=start,
                    end=end,
                )
                self._log_data_event("fyers", "historical", symbol)
                return df
            except Exception as e:
                logger.warning(f"Fyers historical failed for {symbol}: {e}")

        # Fallback: yfinance
        try:
            import yfinance as yf
            _rate_limit()

            yf_interval_map = {
                "1": "1m", "5": "5m", "15": "15m", "60": "1h", "D": "1d",
            }
            yf_interval = yf_interval_map.get(interval, "5m")

            df = yf.download(
                _to_yfinance_symbol(symbol),
                start=start, end=end,
                interval=yf_interval,
                progress=False,
            )
            if df.empty:
                raise ValueError(f"No historical data for {symbol}")

            df = df.reset_index()
            rename_map = {
                "Date": "datetime", "Datetime": "datetime",
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            }
            df = df.rename(columns=rename_map)
            # Handle MultiIndex columns from yfinance
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                df = df.rename(columns=rename_map)
            df = df[["datetime", "open", "high", "low", "close", "volume"]]

            self._log_data_event("yfinance", "historical", symbol,
                                 fallback_used=self.fyers is not None)
            return df
        except Exception as e:
            self._log_data_event("yfinance", "historical", symbol,
                                 success=False, error_message=str(e))
            raise RuntimeError(f"All sources failed for {symbol} historical: {e}")
