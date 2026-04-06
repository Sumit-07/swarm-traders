"""Global configuration for the Trading Agent Swarm.

Calibrated for Indian markets (NSE/BSE), April 2026 regime.
Budget 2026 STT changes and January 2026 lot size changes applied.
Capital: ₹50,000 trading capital.

Risk management rules here are non-negotiable. No agent or prompt can override them.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# --- Capital ---
CAPITAL = {
    "conservative_bucket": 50_000,      # INR — updated from 25k
    "risk_bucket_monthly": 20_000,      # INR — updated from 10k (40% of conservative)
    "system_budget": 20_000,            # INR — infrastructure, stays same
}

# --- NSE/BSE Contract Specifications — JANUARY 2026 RE-BASELINING ---
# CRITICAL: Lot sizes changed effective January 2026.
# Previous Nifty lot size was 75, reduced to 65.
# Previous BankNifty was 35, reduced to 30.
# All options calculations MUST use these values.

CONTRACT_SPECIFICATIONS = {
    "NIFTY": {
        "lot_size":      65,      # changed from 75 in Jan 2026
        "tick_size":     0.05,
        "freeze_limit":  1755,    # max units per single order (27 lots)
        "freeze_lots":   27,
    },
    "BANKNIFTY": {
        "lot_size":      30,      # changed from 35 in Jan 2026
        "tick_size":     0.05,
        "freeze_limit":  600,     # max units per single order (20 lots)
        "freeze_lots":   20,
    },
    "FINNIFTY": {
        "lot_size":      60,      # changed from 65 in Jan 2026
        "tick_size":     0.05,
        "freeze_limit":  1800,
        "freeze_lots":   30,
    },
    "MIDCPNIFTY": {
        "lot_size":      120,     # changed from 140 in Jan 2026
        "tick_size":     0.05,
        "freeze_limit":  7200,
        "freeze_lots":   60,
    },
}

# --- Statutory Costs — APRIL 2026 BUDGET CHANGES ---
# CRITICAL: STT rates changed effective April 1, 2026.
# These affect all cost calculations, breakeven thresholds, and backtesting.

STATUTORY_COSTS = {
    # Securities Transaction Tax (post-Budget 2026)
    "stt_futures_sell_pct":       0.0005,   # 0.05% on notional (was 0.02%)
    "stt_options_sell_pct":       0.0015,   # 0.15% on premium (was 0.10%)
    "stt_options_exercise_pct":   0.0015,   # 0.15% on intrinsic value (was 0.125%)
    "stt_intraday_equity_pct":    0.00025,  # 0.025% — unchanged, sell side only
    "stt_delivery_equity_pct":    0.001,    # 0.1% each side — unchanged

    # Exchange and regulatory charges
    "exchange_charges_nse_pct":   0.0000297,  # 0.00297% on turnover
    "sebi_fee_per_crore":         10,          # ₹10 per crore turnover
    "gst_on_brokerage_pct":       0.18,        # 18% GST on brokerage + exchange charges
    "stamp_duty_pct":             0.00003,     # 0.003% — state-dependent, use avg

    # Brokerage (Zerodha Kite)
    "brokerage_per_order_inr":    20,       # ₹20 flat per order
    "brokerage_options_pct":      0.0,      # 0% — Zerodha charges flat ₹20 only

    # Slippage assumptions for simulation
    "slippage_equity_pct":        0.0005,   # 0.05% on equity entries/exits
    "slippage_options_pct":       0.005,    # 0.5% on options (wider spread)
    "slippage_tolerance_pts":     1.5,      # cancel if fill deviates > 1.5pts from signal
}

# Backward-compatible SIMULATION dict (used by backtesting/simulator.py, tools/order_simulator.py)
SIMULATION = {
    "slippage_pct":          STATUTORY_COSTS["slippage_equity_pct"],
    "brokerage_per_order":   STATUTORY_COSTS["brokerage_per_order_inr"],
    "stt_delivery_buy_pct":  STATUTORY_COSTS["stt_intraday_equity_pct"],
    "stt_intraday_sell_pct": STATUTORY_COSTS["stt_delivery_equity_pct"],
}

# --- Risk Limits (non-negotiable, hardcoded) ---
RISK_LIMITS = {
    # Per trade
    "max_single_trade_risk_pct": 0.015,    # 1.5% of capital = ₹750 at ₹50k
    "max_options_trade_inr":     5_000,     # ₹5,000 max per single-leg options trade
                                            # CRITICAL UPDATE: old ₹2,500 was wrong
                                            # At lot_size=65, even ₹50 premium = ₹3,250
    "max_straddle_cost_inr":     8_000,     # ₹8,000 max for straddle (both legs combined)
    "max_single_position_inr":   14_000,    # max per equity position

    # Daily
    "max_daily_loss_pct":        0.03,      # 3% of capital = ₹1,500 at ₹50k
    "max_daily_loss_inr":        1_500,     # hard rupee cap

    # Position limits
    "max_simultaneous_positions": 4,        # up from 3 at ₹25k
    "max_risk_positions":         3,        # up from 2 at ₹25k
    "max_capital_deployed_pct":   0.85,     # 85% max deployed, 15% cash reserve

    # Monthly
    "max_monthly_drawdown_pct":  0.10,      # 10% monthly = ₹5,000
    "max_monthly_drawdown_inr":  5_000,

    # Behaviour
    "averaging_down_permitted":  False,     # NEVER
    "consecutive_loss_cooldown": 3,         # trades -> 1 hour halt
    "cooldown_duration_minutes": 60,

    # Intraday
    "intraday_cutoff_time":     "15:20",    # IST — all intraday must close
    "no_new_trades_after":      "15:00",    # IST — no new entries after this
    "no_new_trades_before":     "09:30",    # except ORB

    # Options-specific
    "options_stop_loss_pct":         0.60,  # close option if down 60%
    "options_stop_loss_expiry_day":  0.50,  # tighter on expiry day
    "options_max_hold_days":         2,     # never hold options more than 2 days
    "options_no_buy_after":          "13:00",  # theta decay accelerates post-1PM
    "straddle_max_hold_time":        "12:00",  # straddles exit by noon
    "options_selling_permitted":     False,    # HARD BLOCK — insufficient margin
    "futures_trading_permitted":     False,    # HARD BLOCK

    # VIX thresholds (India VIX specific)
    "vix_no_intraday_above":        32,
    "vix_no_new_positions_above":   35,
    "vix_options_only_above":       22,
    "vix_straddle_range_min":       22,
    "vix_straddle_range_max":       32,

    # NSE freeze limits
    "respect_freeze_limits":        True,

    # Indian market specifics
    "handle_market_circuit_halt":   True,
    "check_fo_ban_list":            True,
    "avoid_fo_expiry_intraday":     True,
    "avoid_swing_into_results":     True,
    "max_swing_hold_days":          5,

    # Human approval thresholds
    "require_human_approval_days":  30,     # first 30 days: approve everything
    "auto_approve_threshold_inr":   6_000,  # after day 30: auto-approve < ₹6k
    "auto_approve_confidence":      "HIGH", # only auto-approve HIGH confidence signals
    "auto_approve_max_per_day":     2,
}

# --- Trading Hours (IST) ---
TRADING_HOURS = {
    "system_start": "06:55",
    "data_agent_wake": "07:00",
    "strategist_wake": "08:00",
    "morning_briefing": "08:30",
    "pre_open_refresh": "09:00",
    "market_open": "09:15",
    "normal_trading_start": "09:30",    # no new trades in first 15 min (ORB exception)
    "no_new_trades": "15:00",
    "intraday_cutoff": "15:20",         # force-close all intraday
    "market_close": "15:30",
    "eod_review": "15:45",
    "system_stop": "17:15",
}

# --- System Modes ---
SYSTEM_MODES = {
    "default_mode": "PAPER",            # always start in paper mode
    "live_requires_explicit_command": True,
}
VALID_MODES = ("PAPER", "LIVE", "HALTED", "REVIEW")

# --- Agent Roster ---
AGENT_IDS = [
    "orchestrator",
    "strategist",
    "risk_strategist",
    "data_agent",
    "analyst",
    "risk_agent",
    "execution_agent",
    "compliance_agent",
    "optimizer",
    "position_monitor",
]

# LLM model mapping per agent
AGENT_LLM_MODELS = {
    "orchestrator": "gpt-4o",
    "strategist": "gpt-4o",
    "risk_strategist": "gpt-4o",
    "data_agent": "gemini-flash",
    "analyst": "gpt-4o-mini",
    "risk_agent": "gpt-4o-mini",
    "execution_agent": "gpt-4o-mini",
    "compliance_agent": "gemini-flash",
    "optimizer": "gpt-4o",
    "position_monitor": None,
}

# --- Redis Channels ---
REDIS_CHANNELS = {agent_id: f"channel:{agent_id}" for agent_id in AGENT_IDS}
REDIS_CHANNELS["broadcast"] = "channel:broadcast"

# --- Allowed Communication Paths ---
# Key: from_agent, Value: list of agents it can send to.
# Any agent can always send to orchestrator (enforced separately).
ALLOWED_COMMUNICATION_PATHS = {
    "data_agent": ["orchestrator", "strategist", "risk_strategist"],
    "strategist": ["orchestrator"],
    "risk_strategist": ["orchestrator"],
    "orchestrator": [
        "data_agent", "strategist", "risk_strategist",
        "analyst", "risk_agent", "execution_agent", "compliance_agent",
        "optimizer",
        "position_monitor",
    ],
    "analyst": ["risk_agent", "orchestrator"],
    "risk_agent": ["orchestrator"],
    "execution_agent": ["orchestrator", "compliance_agent"],
    "compliance_agent": ["orchestrator"],
    "optimizer": ["orchestrator"],
    "position_monitor": ["orchestrator"],
}

# --- Default Watchlist (Nifty 50 large caps, high liquidity) ---
DEFAULT_WATCHLIST = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
    "LT", "SBIN", "BAJFINANCE", "ITC", "TATAMOTORS",
    "AXISBANK", "KOTAKBANK", "HINDUNILVR", "BHARTIARTL", "MARUTI",
    "SUNPHARMA", "WIPRO", "TATASTEEL", "NTPC", "POWERGRID",
]

# --- Strategy Library ---
CONSERVATIVE_STRATEGIES = [
    "RSI_MEAN_REVERSION",
    "VWAP_REVERSION",
    "OPENING_RANGE_BREAKOUT",
    "SWING_MOMENTUM",
    "NIFTY_OPTIONS_BUYING",
    "VOLATILITY_ADJUSTED_SWING",
    "NO_TRADE",
]

RISK_STRATEGIES = [
    "EVENT_OPTIONS",
    "EXPIRY_DIRECTIONAL",
    "MOMENTUM_EQUITY",
    "STRADDLE_BUY",
    "NO_TRADE",
]

# --- Backtest Gate Criteria ---
# A strategy must pass ALL thresholds before paper trading.
BACKTEST_GATE_CRITERIA = {
    "min_win_rate": 0.42,
    "min_profit_factor": 1.3,
    "min_sharpe_ratio": 0.8,
    "max_drawdown_pct": 0.18,
    "max_consecutive_losses": 6,
    "min_total_trades": 30,
}

# --- LT Advisor Tranche Guide ---
# At ₹50k capital, assuming ₹20-30k available for long-term investing
LT_TRANCHE_GUIDE = {
    "vix_20": {"tranche": 1, "suggested_pct": 25,
               "action": "Start buying — VIX elevated, opportunity opening"},
    "vix_25": {"tranche": 2, "suggested_pct": 25,
               "action": "Add to position — strong historical signal"},
    "vix_30": {"tranche": 3, "suggested_pct": 30,
               "action": "Aggressive add — rare opportunity for India"},
    "reserve": {"tranche": 4, "suggested_pct": 20,
                "action": "Hold in liquid fund — deploy only if VIX > 35"},
}

# --- Environment Variables ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
KITE_API_KEY = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_REDIRECT_URI = os.getenv("KITE_REDIRECT_URI", "http://localhost:8080")
AUTH_MODE = os.getenv("AUTH_MODE", "telegram")
DATA_SOURCE = os.getenv("DATA_SOURCE", "kite")

if not KITE_API_KEY or not KITE_API_SECRET:
    import warnings
    warnings.warn(
        "KITE_API_KEY or KITE_API_SECRET not set. "
        "Broker features disabled — paper mode with yfinance only."
    )

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", str(DATA_DIR / "trading_swarm.db"))
TRADING_MODE = os.getenv("TRADING_MODE", "PAPER")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
