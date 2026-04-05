"""Global configuration for the Trading Agent Swarm.

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
    "conservative_bucket": 25_000,      # INR — update to your actual amount
    "risk_bucket_monthly": 10_000,      # INR — fixed monthly allocation
    "system_budget": 20_000,            # INR — infrastructure only
}

# --- Risk Limits (non-negotiable, hardcoded) ---
RISK_LIMITS = {
    # Per trade
    "max_single_trade_risk_pct": 0.02,  # 2% of capital per trade
    "max_options_trade": 2_500,         # INR hard limit per options trade

    # Daily
    "max_daily_loss_pct": 0.05,         # 5% daily loss -> mandatory halt
    "max_simultaneous_positions": 3,    # conservative bucket
    "max_risk_positions": 2,            # risk bucket

    # Behaviour
    "averaging_down_permitted": False,  # NEVER
    "consecutive_loss_cooldown": 3,     # trades -> 1 hour halt
    "cooldown_duration_minutes": 60,

    # Intraday
    "intraday_cutoff_time": "15:20",    # IST — all intraday must close
    "no_new_trades_after": "15:00",     # IST — no new entries after this

    # Options-specific
    "options_stop_loss_pct": 0.60,      # close option if down 60%
    "options_max_hold_days": 2,         # never hold options more than 2 days

    # Human approval thresholds
    "require_human_approval_days": 30,  # first 30 days: approve everything
    "auto_approve_threshold": 3_000,    # after day 30: auto-approve < 3000 INR
    "auto_approve_confidence": "HIGH",  # only auto-approve HIGH confidence signals
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

# --- Strategy Library ---
CONSERVATIVE_STRATEGIES = [
    "RSI_MEAN_REVERSION",
    "VWAP_REVERSION",
    "OPENING_RANGE_BREAKOUT",
    "SWING_MOMENTUM",
    "NIFTY_OPTIONS_BUYING",
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

# --- Backtest Simulation Rules ---
SIMULATION = {
    "slippage_pct": 0.0005,         # 0.05%
    "brokerage_per_order": 20,      # INR flat
    "stt_delivery_buy_pct": 0.00025,    # 0.025% on buy-side delivery
    "stt_intraday_sell_pct": 0.001,     # 0.1% on sell-side intraday
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
