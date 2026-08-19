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
from .spike_fade import (
    SpikeFadeMetrics,
    SpikeFadeResult,
    SpikeFadeSpec,
    default_spike_fade_specs,
    evaluate_spike_fade_result,
    run_spike_fade_backtest,
    spike_fade_research_report,
)
from .limit_bracket import (
    LimitBracketMetrics,
    LimitBracketResult,
    LimitBracketSpec,
    default_limit_bracket_specs,
    build_limit_bracket_signal_event,
    evaluate_limit_bracket_result,
    limit_bracket_research_report,
    run_limit_bracket_backtest,
)
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
    "SpikeFadeMetrics",
    "SpikeFadeResult",
    "SpikeFadeSpec",
    "default_spike_fade_specs",
    "evaluate_spike_fade_result",
    "run_spike_fade_backtest",
    "spike_fade_research_report",
    "LimitBracketMetrics",
    "LimitBracketResult",
    "LimitBracketSpec",
    "default_limit_bracket_specs",
    "build_limit_bracket_signal_event",
    "evaluate_limit_bracket_result",
    "limit_bracket_research_report",
    "run_limit_bracket_backtest",
]
