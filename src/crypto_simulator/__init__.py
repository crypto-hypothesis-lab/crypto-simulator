"""Public, exchange-neutral crypto research components."""

from .backtest import BacktestConfig, BacktestResult, run_backtest
from .models import OHLCVBar
from .research import PerformanceMetrics, StrategySpec, dataset_quality, research_report
from .strategy import MultiTimeframeSignal, MultiTimeframeStrategy
from .timeframes import resample_ohlcv

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "MultiTimeframeSignal",
    "MultiTimeframeStrategy",
    "OHLCVBar",
    "PerformanceMetrics",
    "StrategySpec",
    "dataset_quality",
    "resample_ohlcv",
    "research_report",
    "run_backtest",
]
