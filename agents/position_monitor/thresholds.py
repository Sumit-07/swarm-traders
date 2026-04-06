"""Strategy-aware thresholds for the Position Monitor agent.

Each strategy type defines threshold categories:
1. adverse_move_pct       -- how far price can move against us before alert
2. favorable_move_pct     -- how far price can move in our favour before alert
3. velocity_pct_per_bar   -- max adverse move in a SINGLE 5-min candle
4. volume_ratio           -- volume multiple vs daily average that triggers alert
5. grace_period_minutes   -- minutes after entry before monitoring begins
6. cooldown_minutes       -- minimum minutes between alerts for same position

Design rationale per strategy:
- INTRADAY: tight thresholds, short grace, short cooldown.
- SWING: wide thresholds, long grace, long cooldown.
- OPTIONS: premium-based thresholds, not price-based.
"""

from dataclasses import dataclass
from typing import Literal

StrategyType = Literal["intraday", "swing", "options"]
TriggerType = Literal[
    "adverse_move",
    "favorable_move",
    "adverse_velocity",
    "favorable_velocity",
    "volume_spike_adverse",
    "volume_spike_favorable",
    "stop_proximity",
    "target_proximity",
    "premium_decay",
    "premium_surge",
    "time_warning",
]


@dataclass(frozen=True)
class MonitorThresholds:
    strategy_type:          StrategyType
    adverse_move_pct:       float
    favorable_move_pct:     float
    adverse_velocity_pct:   float
    favorable_velocity_pct: float
    volume_ratio:           float
    stop_proximity_pct:     float
    target_proximity_pct:   float
    grace_period_minutes:   int
    cooldown_minutes:       int
    # Options-specific (ignored for non-options strategies)
    premium_decay_pct:      float = 0.0
    premium_surge_pct:      float = 0.0
    time_warning_minutes:   int   = 0


THRESHOLDS: dict[str, MonitorThresholds] = {

    # -- Intraday strategies --------------------------------------------------

    "RSI_MEAN_REVERSION": MonitorThresholds(
        strategy_type          = "intraday",
        adverse_move_pct       = 0.8,
        favorable_move_pct     = 1.4,
        adverse_velocity_pct   = 0.4,
        favorable_velocity_pct = 0.6,
        volume_ratio           = 2.5,
        stop_proximity_pct     = 25.0,
        target_proximity_pct   = 20.0,
        grace_period_minutes   = 10,
        cooldown_minutes       = 20,
        time_warning_minutes   = 45,
    ),

    "VWAP_REVERSION": MonitorThresholds(
        strategy_type          = "intraday",
        adverse_move_pct       = 0.5,
        favorable_move_pct     = 0.9,
        adverse_velocity_pct   = 0.3,
        favorable_velocity_pct = 0.5,
        volume_ratio           = 2.0,
        stop_proximity_pct     = 30.0,
        target_proximity_pct   = 20.0,
        grace_period_minutes   = 5,
        cooldown_minutes       = 15,
        time_warning_minutes   = 45,
    ),

    "OPENING_RANGE_BREAKOUT": MonitorThresholds(
        strategy_type          = "intraday",
        adverse_move_pct       = 0.6,
        favorable_move_pct     = 1.1,
        adverse_velocity_pct   = 0.5,
        favorable_velocity_pct = 0.7,
        volume_ratio           = 3.0,
        stop_proximity_pct     = 25.0,
        target_proximity_pct   = 15.0,
        grace_period_minutes   = 15,
        cooldown_minutes       = 20,
        time_warning_minutes   = 45,
    ),

    # -- Swing strategy -------------------------------------------------------

    "SWING_MOMENTUM": MonitorThresholds(
        strategy_type          = "swing",
        adverse_move_pct       = 1.5,
        favorable_move_pct     = 3.0,
        adverse_velocity_pct   = 1.0,
        favorable_velocity_pct = 1.2,
        volume_ratio           = 3.5,
        stop_proximity_pct     = 20.0,
        target_proximity_pct   = 15.0,
        grace_period_minutes   = 60,
        cooldown_minutes       = 60,
        time_warning_minutes   = 0,
    ),

    # -- Options strategy (risk bucket) ---------------------------------------

    "NIFTY_OPTIONS_BUYING": MonitorThresholds(
        strategy_type          = "options",
        adverse_move_pct       = 0.4,
        favorable_move_pct     = 0.5,
        adverse_velocity_pct   = 0.3,
        favorable_velocity_pct = 0.4,
        volume_ratio           = 2.0,
        stop_proximity_pct     = 0.0,
        target_proximity_pct   = 0.0,
        premium_decay_pct      = 40.0,
        premium_surge_pct      = 150.0,
        grace_period_minutes   = 5,
        cooldown_minutes       = 15,
        time_warning_minutes   = 45,
    ),

    # -- High-VIX strategies (VIX 22-32) -------------------------------------

    "STRADDLE_BUY": MonitorThresholds(
        strategy_type          = "options",
        adverse_move_pct       = 0.0,
        favorable_move_pct     = 0.0,
        adverse_velocity_pct   = 0.0,
        favorable_velocity_pct = 0.0,
        volume_ratio           = 0.0,
        stop_proximity_pct     = 0.0,
        target_proximity_pct   = 0.0,
        premium_decay_pct      = 30.0,
        premium_surge_pct      = 130.0,
        grace_period_minutes   = 3,
        cooldown_minutes       = 10,
        time_warning_minutes   = 30,
    ),

    "VOLATILITY_ADJUSTED_SWING": MonitorThresholds(
        strategy_type          = "swing",
        adverse_move_pct       = 2.0,
        favorable_move_pct     = 3.7,
        adverse_velocity_pct   = 1.2,
        favorable_velocity_pct = 1.4,
        volume_ratio           = 3.5,
        stop_proximity_pct     = 20.0,
        target_proximity_pct   = 15.0,
        grace_period_minutes   = 60,
        cooldown_minutes       = 60,
        time_warning_minutes   = 0,
    ),
}


def get_thresholds(strategy_name: str) -> MonitorThresholds:
    """Returns thresholds for a strategy. Raises KeyError if not found."""
    if strategy_name not in THRESHOLDS:
        raise KeyError(
            f"No monitor thresholds defined for strategy '{strategy_name}'. "
            f"Add it to agents/position_monitor/thresholds.py before using it."
        )
    return THRESHOLDS[strategy_name]


def get_all_strategy_names() -> list[str]:
    return list(THRESHOLDS.keys())
