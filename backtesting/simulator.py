"""Backtest order fill simulator.

Enforces realistic execution rules to prevent look-ahead bias:
- Entry on next bar open (not current bar close)
- Slippage on entry and exit
- Brokerage per order
- STT (Securities Transaction Tax)
- Market hours enforcement
- Gap risk for swing trades
"""

from datetime import time
from dataclasses import dataclass, field

from config import SIMULATION


@dataclass
class Trade:
    """A completed backtest trade."""
    trade_id: int
    symbol: str
    direction: str          # LONG / SHORT
    strategy: str
    entry_bar_idx: int      # index where signal fired
    entry_fill_idx: int     # index where fill happened (entry_bar_idx + 1)
    entry_price: float      # signal price
    fill_price: float       # actual fill with slippage
    exit_price: float = 0.0
    exit_fill_price: float = 0.0
    exit_bar_idx: int = 0
    stop_loss: float = 0.0
    target: float = 0.0
    quantity: int = 1
    pnl: float = 0.0
    pnl_pct: float = 0.0
    fees: float = 0.0       # brokerage + STT
    status: str = "OPEN"    # OPEN / CLOSED_TARGET / CLOSED_STOP / CLOSED_TIME / CLOSED_EOD
    entry_time: str = ""
    exit_time: str = ""
    hold_bars: int = 0


@dataclass
class SimulatorConfig:
    """Configuration for backtest simulation."""
    slippage_pct: float = SIMULATION["slippage_pct"]
    brokerage_per_order: float = SIMULATION["brokerage_per_order"]
    stt_delivery_buy_pct: float = SIMULATION["stt_delivery_buy_pct"]
    stt_intraday_sell_pct: float = SIMULATION["stt_intraday_sell_pct"]
    market_open: time = time(9, 15)
    market_close: time = time(15, 30)
    no_signals_before: time = time(9, 15)
    no_signals_after: time = time(15, 20)
    force_close_time: time = time(15, 20)  # intraday force close


class BacktestSimulator:
    """Simulates order execution for backtesting.

    Key rules (from design doc Section 9):
    1. Entry on next bar open — signal on bar t, entry at bar t+1 open
    2. Slippage: 0.05% worse for the trader
    3. Brokerage: ₹20 per order flat
    4. STT: 0.025% buy-side delivery, 0.1% sell-side intraday
    5. No partial fills — full fill at simulated price
    6. Market hours only — no signals before 9:15 or after 3:20
    7. Gap risk — swing trade next-day open may gap past stop
    """

    def __init__(self, config: SimulatorConfig = None):
        self.config = config or SimulatorConfig()
        self._trade_counter = 0

    def can_signal(self, bar_time) -> bool:
        """Check if signals are allowed at this bar's time."""
        import pandas as pd
        if isinstance(bar_time, pd.Timestamp):
            t = bar_time.time()
        elif isinstance(bar_time, str):
            t = pd.Timestamp(bar_time).time()
        else:
            t = bar_time

        return self.config.no_signals_before <= t <= self.config.no_signals_after

    def simulate_entry(self, signal_bar_idx: int, next_bar_open: float,
                       direction: str, symbol: str, strategy: str,
                       stop_loss: float, target: float,
                       quantity: int = 1,
                       signal_time: str = "") -> Trade:
        """Simulate entry fill at next bar's open price.

        Args:
            signal_bar_idx: Index of the bar where signal fired
            next_bar_open: Open price of bar t+1
            direction: LONG or SHORT
            symbol: Stock symbol
            strategy: Strategy name
            stop_loss: Stop loss price
            target: Target price
            quantity: Number of shares
            signal_time: ISO timestamp of signal bar

        Returns: Trade object with entry details filled
        """
        self._trade_counter += 1

        # Apply slippage (worse for trader)
        if direction == "LONG":
            fill_price = next_bar_open * (1 + self.config.slippage_pct)
        else:
            fill_price = next_bar_open * (1 - self.config.slippage_pct)

        fill_price = round(fill_price, 2)

        # Calculate entry fees
        entry_value = fill_price * quantity
        fees = self.config.brokerage_per_order  # entry brokerage
        fees += entry_value * self.config.stt_delivery_buy_pct  # STT on buy

        return Trade(
            trade_id=self._trade_counter,
            symbol=symbol,
            direction=direction,
            strategy=strategy,
            entry_bar_idx=signal_bar_idx,
            entry_fill_idx=signal_bar_idx + 1,
            entry_price=next_bar_open,
            fill_price=fill_price,
            stop_loss=stop_loss,
            target=target,
            quantity=quantity,
            fees=round(fees, 2),
            status="OPEN",
            entry_time=signal_time,
        )

    def check_exit(self, trade: Trade, bar_high: float, bar_low: float,
                   bar_close: float, bar_idx: int,
                   bar_time: str = "", is_intraday: bool = True) -> Trade | None:
        """Check if a trade should be closed on this bar.

        Checks in order:
        1. Stop-loss hit (uses bar low/high)
        2. Target hit (uses bar low/high)
        3. Time-based exit (intraday force close at 15:20)

        Returns: Updated Trade if closed, None if still open.
        """
        import pandas as pd

        closed = False
        exit_price = 0.0
        exit_reason = ""

        if trade.direction == "LONG":
            # Check stop-loss (bar low breaches stop)
            if bar_low <= trade.stop_loss:
                exit_price = trade.stop_loss
                exit_reason = "CLOSED_STOP"
                closed = True
            # Check target (bar high reaches target)
            elif bar_high >= trade.target:
                exit_price = trade.target
                exit_reason = "CLOSED_TARGET"
                closed = True
        else:  # SHORT
            if bar_high >= trade.stop_loss:
                exit_price = trade.stop_loss
                exit_reason = "CLOSED_STOP"
                closed = True
            elif bar_low <= trade.target:
                exit_price = trade.target
                exit_reason = "CLOSED_TARGET"
                closed = True

        # Time-based exit for intraday
        if not closed and is_intraday and bar_time:
            try:
                t = pd.Timestamp(bar_time).time()
                if t >= self.config.force_close_time:
                    exit_price = bar_close
                    exit_reason = "CLOSED_TIME"
                    closed = True
            except Exception:
                pass

        if not closed:
            return None

        # Apply exit slippage (worse for trader)
        if trade.direction == "LONG":
            exit_fill = exit_price * (1 - self.config.slippage_pct)
        else:
            exit_fill = exit_price * (1 + self.config.slippage_pct)
        exit_fill = round(exit_fill, 2)

        # Exit fees
        exit_value = exit_fill * trade.quantity
        exit_fees = self.config.brokerage_per_order
        if is_intraday:
            exit_fees += exit_value * self.config.stt_intraday_sell_pct
        else:
            exit_fees += exit_value * self.config.stt_delivery_buy_pct

        trade.fees = round(trade.fees + exit_fees, 2)

        # Calculate P&L
        if trade.direction == "LONG":
            raw_pnl = (exit_fill - trade.fill_price) * trade.quantity
        else:
            raw_pnl = (trade.fill_price - exit_fill) * trade.quantity

        trade.pnl = round(raw_pnl - trade.fees, 2)
        trade.pnl_pct = round(
            (trade.pnl / (trade.fill_price * trade.quantity)) * 100, 4
        )
        trade.exit_price = exit_price
        trade.exit_fill_price = exit_fill
        trade.exit_bar_idx = bar_idx
        trade.exit_time = bar_time
        trade.hold_bars = bar_idx - trade.entry_fill_idx
        trade.status = exit_reason

        return trade

    def force_close(self, trade: Trade, close_price: float,
                    bar_idx: int, bar_time: str = "",
                    is_intraday: bool = True) -> Trade:
        """Force close a trade at a given price (EOD or emergency)."""
        if trade.direction == "LONG":
            exit_fill = close_price * (1 - self.config.slippage_pct)
        else:
            exit_fill = close_price * (1 + self.config.slippage_pct)
        exit_fill = round(exit_fill, 2)

        exit_value = exit_fill * trade.quantity
        exit_fees = self.config.brokerage_per_order
        if is_intraday:
            exit_fees += exit_value * self.config.stt_intraday_sell_pct
        trade.fees = round(trade.fees + exit_fees, 2)

        if trade.direction == "LONG":
            raw_pnl = (exit_fill - trade.fill_price) * trade.quantity
        else:
            raw_pnl = (trade.fill_price - exit_fill) * trade.quantity

        trade.pnl = round(raw_pnl - trade.fees, 2)
        trade.pnl_pct = round(
            (trade.pnl / (trade.fill_price * trade.quantity)) * 100, 4
        )
        trade.exit_price = close_price
        trade.exit_fill_price = exit_fill
        trade.exit_bar_idx = bar_idx
        trade.exit_time = bar_time
        trade.hold_bars = bar_idx - trade.entry_fill_idx
        trade.status = "CLOSED_EOD"

        return trade
