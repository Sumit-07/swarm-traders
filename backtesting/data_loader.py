"""Historical data loader for backtesting.

Loads OHLCV data from Fyers (primary) or yfinance (fallback).
Validates data quality and fills gaps.
"""

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from tools.logger import get_agent_logger

logger = get_agent_logger("backtest_data")

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


class DataLoader:
    def __init__(self, cache_dir: str = None):
        """
        Args:
            cache_dir: Directory to cache downloaded data. None = no caching.
        """
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load(self, symbol: str, start: str, end: str,
             interval: str = "5") -> pd.DataFrame:
        """Load historical OHLCV data for a symbol.

        Args:
            symbol: Clean symbol (e.g., "RELIANCE", "NIFTY")
            start: "YYYY-MM-DD"
            end: "YYYY-MM-DD"
            interval: "1", "5", "15", "60", "D"

        Returns: DataFrame with columns [datetime, open, high, low, close, volume]
                 sorted by datetime ascending.
        """
        # Check cache first
        cache_key = f"{symbol}_{start}_{end}_{interval}m"
        if self.cache_dir:
            cache_path = self.cache_dir / f"{cache_key}.parquet"
            if cache_path.exists():
                logger.info(f"Loading {symbol} from cache")
                df = pd.read_parquet(cache_path)
                return df

        # Download data
        df = self._download(symbol, start, end, interval)

        # Validate and clean
        df = self._validate(df, symbol)

        # Filter to market hours (for intraday)
        if interval != "D":
            df = self._filter_market_hours(df)

        # Cache if enabled
        if self.cache_dir and not df.empty:
            cache_path = self.cache_dir / f"{cache_key}.parquet"
            df.to_parquet(cache_path, index=False)
            logger.info(f"Cached {symbol}: {len(df)} bars")

        return df

    def load_multiple(self, symbols: list[str], start: str, end: str,
                      interval: str = "5") -> dict[str, pd.DataFrame]:
        """Load data for multiple symbols.

        Returns: {symbol: DataFrame}
        """
        result = {}
        for symbol in symbols:
            try:
                df = self.load(symbol, start, end, interval)
                if not df.empty:
                    result[symbol] = df
                    logger.info(f"Loaded {symbol}: {len(df)} bars")
                else:
                    logger.warning(f"No data for {symbol}")
            except Exception as e:
                logger.error(f"Failed to load {symbol}: {e}")
        return result

    def _download(self, symbol: str, start: str, end: str,
                  interval: str) -> pd.DataFrame:
        """Download data via yfinance (primary for backtesting)."""
        import yfinance as yf

        # Symbol mapping
        yf_symbol = self._to_yfinance(symbol)
        yf_interval = {"1": "1m", "5": "5m", "15": "15m",
                       "60": "1h", "D": "1d"}.get(interval, "5m")

        logger.info(f"Downloading {symbol} ({yf_symbol}) {start} to {end} @ {yf_interval}")

        # yfinance has limitations on intraday data range
        # For 5m data, max ~60 days per request
        if yf_interval in ("1m", "5m", "15m"):
            df = self._download_intraday_chunks(yf_symbol, start, end, yf_interval)
        else:
            df = yf.download(
                yf_symbol, start=start, end=end,
                interval=yf_interval, progress=False,
            )
            df = df.reset_index()

        if df.empty:
            logger.warning(f"No data returned for {symbol}")
            return pd.DataFrame()

        # Normalize columns
        df = self._normalize_columns(df)
        return df

    def _download_intraday_chunks(self, yf_symbol: str, start: str,
                                   end: str, interval: str) -> pd.DataFrame:
        """Download intraday data in chunks (yfinance limits)."""
        import yfinance as yf

        start_dt = pd.Timestamp(start)
        end_dt = pd.Timestamp(end)
        chunk_days = 55 if interval == "5m" else 25  # yfinance limits

        chunks = []
        current = start_dt
        while current < end_dt:
            chunk_end = min(current + pd.Timedelta(days=chunk_days), end_dt)
            try:
                df = yf.download(
                    yf_symbol,
                    start=current.strftime("%Y-%m-%d"),
                    end=chunk_end.strftime("%Y-%m-%d"),
                    interval=interval,
                    progress=False,
                )
                if not df.empty:
                    df = df.reset_index()
                    chunks.append(df)
            except Exception as e:
                logger.warning(f"Chunk download failed ({current} to {chunk_end}): {e}")
            current = chunk_end

        if not chunks:
            return pd.DataFrame()

        return pd.concat(chunks, ignore_index=True)

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names to standard format."""
        rename_map = {
            "Date": "datetime", "Datetime": "datetime",
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
            "Adj Close": "adj_close",
        }

        # Handle MultiIndex columns from yfinance
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.rename(columns=rename_map)

        # Keep only needed columns
        keep = [c for c in ["datetime", "open", "high", "low", "close", "volume"]
                if c in df.columns]
        df = df[keep]

        # Ensure datetime type
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])

        return df.sort_values("datetime").reset_index(drop=True)

    def _validate(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Validate data quality."""
        if df.empty:
            return df

        initial_len = len(df)

        # Remove duplicates
        df = df.drop_duplicates(subset=["datetime"], keep="first")

        # Remove rows with zero or negative prices
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df = df[df[col] > 0]

        # Remove rows with zero volume (except daily data which may have 0)
        if "volume" in df.columns:
            df = df[df["volume"] >= 0]

        # Check high >= low
        if "high" in df.columns and "low" in df.columns:
            df = df[df["high"] >= df["low"]]

        removed = initial_len - len(df)
        if removed > 0:
            logger.info(f"{symbol}: removed {removed} invalid rows")

        return df.reset_index(drop=True)

    def _filter_market_hours(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter to market hours only (9:15 AM - 3:30 PM IST)."""
        if df.empty or "datetime" not in df.columns:
            return df

        dt = pd.to_datetime(df["datetime"])

        # If timezone-aware, convert to IST
        if dt.dt.tz is not None:
            dt = dt.dt.tz_convert(IST)

        times = dt.dt.time
        mask = (times >= MARKET_OPEN) & (times <= MARKET_CLOSE)
        filtered = df[mask].reset_index(drop=True)

        if len(filtered) < len(df):
            logger.debug(
                f"Filtered to market hours: {len(df)} -> {len(filtered)} bars"
            )

        return filtered

    def _to_yfinance(self, symbol: str) -> str:
        """Convert clean symbol to yfinance format."""
        index_map = {
            "NIFTY": "^NSEI",
            "BANKNIFTY": "^NSEBANK",
            "INDIAVIX": "^INDIAVIX",
            "NIFTYBEES": "NIFTYBEES.NS",
        }
        if symbol in index_map:
            return index_map[symbol]
        return f"{symbol}.NS"
