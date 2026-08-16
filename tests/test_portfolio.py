import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.models import OHLCVBar
from crypto_simulator.portfolio import (
    FundingPoint,
    PortfolioConfig,
    PortfolioDecision,
    ThemeMomentumSpec,
    ThemeMomentumStrategy,
    funding_rates_by_interval,
    portfolio_research_report,
    run_portfolio_backtest,
)


def make_universe(count: int = 150) -> dict[str, list[OHLCVBar]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    universe = {}
    for symbol, base, drift in (("BTC", 100, 0.2), ("ETH", 50, 0.7), ("SOL", 20, 1.0)):
        series = []
        for index in range(count):
            close = Decimal(str(base + drift * index + (index % 9) * 0.1))
            series.append(
                OHLCVBar(
                    "test",
                    symbol,
                    "perpetual",
                    start + timedelta(days=index),
                    close,
                    close,
                    close,
                    close,
                    "100",
                )
            )
        universe[symbol] = series
    return universe


class SequenceStrategy(ThemeMomentumStrategy):
    def decision(self, history):
        count = len(history["BTC"])
        if count == 2:
            return PortfolioDecision({"ETH": 0.8}, "risk_on", 1.0, {"ETH": 1.0})
        return PortfolioDecision({}, "risk_off", 0.0, {"ETH": 0.0})


def test_portfolio_backtest_closes_spot_position_when_regime_turns_off() -> None:
    universe = {symbol: series[:5] for symbol, series in make_universe(5).items()}
    strategy = SequenceStrategy(ThemeMomentumSpec("sequence", market="spot", momentum_fast=1, momentum_slow=2, regime_fast=1, regime_slow=2))
    result = run_portfolio_backtest(
        universe,
        strategy,
        PortfolioConfig(fee_bps=0, slippage_bps=0),
    )

    assert [trade.side for trade in result.trades] == ["buy", "sell"]
    assert result.positions["ETH"] == 0


def test_perpetual_funding_is_charged_to_long_positions() -> None:
    universe = {symbol: series[:6] for symbol, series in make_universe(6).items()}
    strategy = SequenceStrategy(ThemeMomentumSpec("sequence", market="perpetual", momentum_fast=1, momentum_slow=2, regime_fast=1, regime_slow=2))
    timestamps = [bar.timestamp for bar in universe["BTC"]]
    funding = {"ETH": {timestamp: Decimal("0.001") for timestamp in timestamps}}
    result = run_portfolio_backtest(
        universe,
        strategy,
        PortfolioConfig(fee_bps=0, slippage_bps=0),
        funding_rates=funding,
    )
    no_funding = run_portfolio_backtest(
        universe,
        strategy,
        PortfolioConfig(fee_bps=0, slippage_bps=0),
    )

    assert result.funding_cost > 0
    assert result.final_equity < no_funding.final_equity


def test_funding_rates_are_aggregated_to_daily_price_buckets() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    points = [
        FundingPoint("hyperliquid", "BTC", start + timedelta(hours=1, seconds=1), "0.001"),
        FundingPoint("hyperliquid", "BTC", start + timedelta(hours=2, seconds=1), "0.002"),
    ]

    rates = funding_rates_by_interval(points, "1day")

    assert rates["BTC"][start] == Decimal("0.003")


def test_theme_portfolio_report_is_walk_forward_and_json_serializable() -> None:
    report = portfolio_research_report(
        make_universe(),
        market="perpetual",
        config=PortfolioConfig(fee_bps=0, slippage_bps=0, max_gross_leverage=1),
        train_days=60,
        test_days=30,
        step_days=30,
    )

    assert report["dataset"]["series_count"] == 3
    assert report["method"]["theme_proxy"]
    assert report["summary"]["walk_forward_windows"] > 0
    json.dumps(report)


def test_perpetual_leverage_is_confidence_scaled_and_capped_at_five() -> None:
    strategy = ThemeMomentumStrategy(
        ThemeMomentumSpec(
            "dynamic",
            market="perpetual",
            momentum_fast=14,
            momentum_slow=42,
            regime_fast=20,
            regime_slow=100,
            max_leverage=5,
            risk_off_max_leverage=2,
        )
    )

    decision = strategy.decision(make_universe())

    assert decision.regime == "risk_on"
    assert 1.0 <= decision.leverage <= 5.0
    assert 0.0 <= decision.confidence <= 1.0
    assert sum(abs(weight) for weight in decision.target_weights.values()) <= 5.0
