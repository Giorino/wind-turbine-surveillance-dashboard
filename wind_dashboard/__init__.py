"""Wind turbine surveillance dashboard package."""

from .analysis import AnalysisConfig, AnalysisResult, analyze_dataset
from .config import TURBINES, TurbineConfig

__all__ = [
    "AnalysisConfig",
    "AnalysisResult",
    "TURBINES",
    "TurbineConfig",
    "analyze_dataset",
]
