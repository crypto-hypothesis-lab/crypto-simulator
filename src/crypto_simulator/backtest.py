from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from .models import OHLCVBar


def _strategy_signal(strategy, history: list[OHLCVBar]):
    signal_sorted = getattr(strategy, "signal_sorted", None)
    if callable(signal_sorted):
        return signal_sorted(history)
    return strategy.signal(history)


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: Decimal = Decimal("100000")
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    max_holding_days: int = 30

    def __post_init__(self) -> None:
        if self.max_holding_days <= 0:
            raise ValueError("max_holding_days must be positive")


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    timestamp: str
    side: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    reason: str


@dataclass(slots=True)
class BacktestResult:
    initial_cash: Decimal
    final_equity: Decimal
    cash: Decimal
    quantity: Decimal
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[str, Decimal]] = field(default_factory=list)
    position_curve: list[tuple[str, Decimal]] = field(default_factory=list)

    @property
    def return_fraction(self) -> Decimal:
        if self.initial_cash == 0:
            return Decimal("0")
        return self.final_equity / self.initial_cash - Decimal("1")


def run_backtest(
    bars: list[OHLCVBar],
    strategy,
    config: BacktestConfig | None = None,
    *,
    start_index: int = 0,
) -> BacktestResult:
    """Run a no-lookahead backtest.

    ``start_index`` is useful for walk-forward evaluation: all earlier bars are
    supplied as indicator warm-up history, but trades and the equity curve start
    flat at ``start_index``.
    """

    config = config or BacktestConfig()
    if not bars:
        raise ValueError("at least one bar is required")
    bars = sorted(bars, key=lambda bar: bar.timestamp)
    if start_index < 0 or start_index >= len(bars):
        raise ValueError("start_index must refer to a bar in the dataset")
    cash = config.initial_cash
    quantity = Decimal("0")
    trades: list[BacktestTrade] = []
    equity_curve: list[tuple[str, Decimal]] = []
    position_curve: list[tuple[str, Decimal]] = []
    pending_action = "hold"
    pending_reason = "initial"
    entry_timestamp = None
    max_holding = timedelta(days=config.max_holding_days)
    history: list[OHLCVBar] = []

    for index, bar in enumerate(bars):
        history.append(bar)
        if index < start_index:
            signal = _strategy_signal(strategy, history)
            pending_action = signal.action
            pending_reason = signal.reason
            continue
        if entry_timestamp is not None and bar.timestamp - entry_timestamp >= max_holding:
            pending_action = "sell"
            pending_reason = "max_holding_period"
        if index > 0 and pending_action in {"buy", "sell"}:
            price = bar.open * (Decimal("1") + config.slippage_bps / Decimal("10000") if pending_action == "buy" else Decimal("1") - config.slippage_bps / Decimal("10000"))
            if pending_action == "buy" and quantity == 0 and cash > 0:
                fee_rate = config.fee_bps / Decimal("10000")
                quantity = cash / (price * (Decimal("1") + fee_rate))
                notional = quantity * price
                fee = notional * fee_rate
                cash -= notional + fee
                entry_timestamp = bar.timestamp
                trades.append(BacktestTrade(bar.timestamp.isoformat(), "buy", price, quantity, fee, pending_reason))
            elif pending_action == "sell" and quantity > 0:
                notional = quantity * price
                fee = notional * config.fee_bps / Decimal("10000")
                cash += notional - fee
                trades.append(BacktestTrade(bar.timestamp.isoformat(), "sell", price, quantity, fee, pending_reason))
                quantity = Decimal("0")
                entry_timestamp = None

        equity_curve.append((bar.timestamp.isoformat(), cash + quantity * bar.close))
        position_curve.append((bar.timestamp.isoformat(), quantity))
        signal = _strategy_signal(strategy, history)
        pending_action = signal.action
        pending_reason = signal.reason

    final_equity = cash + quantity * bars[-1].close
    return BacktestResult(config.initial_cash, final_equity, cash, quantity, trades, equity_curve, position_curve)
