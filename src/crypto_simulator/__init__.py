"""Public, exchange-neutral crypto research components."""

from .backtest import BacktestConfig, BacktestResult, run_backtest
from .models import OHLCVBar
from .portfolio import (
    FundingPoint,
    PortfolioConfig,
    PortfolioMetrics,
    ThemeMomentumSpec,
    ThemeMomentumStrategy,
    funding_rates_by_interval,
    portfolio_research_report,
    run_portfolio_backtest,
)
from .research import PerformanceMetrics, StrategySpec, dataset_quality, forward_test_report, research_report
from .strategy import MultiTimeframeSignal, MultiTimeframeStrategy
from .timeframes import resample_ohlcv

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "MultiTimeframeSignal",
    "MultiTimeframeStrategy",
    "OHLCVBar",
    "FundingPoint",
    "PortfolioConfig",
    "PortfolioMetrics",
    "PerformanceMetrics",
    "StrategySpec",
    "ThemeMomentumSpec",
    "ThemeMomentumStrategy",
    "dataset_quality",
    "funding_rates_by_interval",
    "forward_test_report",
    "portfolio_research_report",
    "resample_ohlcv",
    "research_report",
    "run_backtest",
    "run_portfolio_backtest",
]
