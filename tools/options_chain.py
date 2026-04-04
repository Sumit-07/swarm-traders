"""NSE options chain parser.

Fetches and parses options chain data for Nifty, BankNifty, and stock options.
"""

import pandas as pd
import numpy as np

from tools.logger import get_agent_logger

logger = get_agent_logger("options_chain")


class OptionsChainParser:
    def get_chain(self, symbol: str, expiry: str = None) -> pd.DataFrame:
        """Fetch options chain from NSE.

        Args:
            symbol: "NIFTY", "BANKNIFTY", or stock symbol
            expiry: "DD-MMM-YYYY" format, or None for nearest expiry

        Returns: DataFrame with columns:
            strike, ce_ltp, pe_ltp, ce_oi, pe_oi, ce_volume, pe_volume, ce_iv, pe_iv
        """
        try:
            from nsepython import option_chain
            data = option_chain(symbol)

            records = []
            for item in data.get("records", {}).get("data", []):
                record = {"strike": item.get("strikePrice")}
                ce = item.get("CE", {})
                pe = item.get("PE", {})
                record["ce_ltp"] = ce.get("lastPrice", 0)
                record["pe_ltp"] = pe.get("lastPrice", 0)
                record["ce_oi"] = ce.get("openInterest", 0)
                record["pe_oi"] = pe.get("openInterest", 0)
                record["ce_volume"] = ce.get("totalTradedVolume", 0)
                record["pe_volume"] = pe.get("totalTradedVolume", 0)
                record["ce_iv"] = ce.get("impliedVolatility", 0)
                record["pe_iv"] = pe.get("impliedVolatility", 0)
                records.append(record)

            df = pd.DataFrame(records)
            logger.info(f"Options chain fetched for {symbol}: {len(df)} strikes")
            return df
        except Exception as e:
            logger.error(f"Failed to fetch options chain for {symbol}: {e}")
            return pd.DataFrame()

    def get_atm_strike(self, symbol: str, spot_price: float) -> int:
        """Get the nearest ATM strike price."""
        # Standard strike intervals
        if symbol in ("NIFTY",):
            interval = 50
        elif symbol in ("BANKNIFTY",):
            interval = 100
        else:
            interval = 50  # default for stocks

        return int(round(spot_price / interval) * interval)

    def get_pcr(self, chain_df: pd.DataFrame) -> float:
        """Put-Call Ratio from open interest."""
        if chain_df.empty:
            return 0.0
        total_put_oi = chain_df["pe_oi"].sum()
        total_call_oi = chain_df["ce_oi"].sum()
        if total_call_oi == 0:
            return 0.0
        return total_put_oi / total_call_oi

    def get_max_pain(self, chain_df: pd.DataFrame) -> float:
        """Calculate max pain strike price.

        Max pain is the strike at which option writers would lose the least.
        """
        if chain_df.empty:
            return 0.0

        strikes = chain_df["strike"].values
        min_pain = float("inf")
        max_pain_strike = strikes[0]

        for strike in strikes:
            # Calculate total intrinsic value at this strike
            call_pain = np.maximum(strikes - strike, 0) * chain_df["ce_oi"].values
            put_pain = np.maximum(strike - strikes, 0) * chain_df["pe_oi"].values
            total_pain = call_pain.sum() + put_pain.sum()

            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = strike

        return float(max_pain_strike)
