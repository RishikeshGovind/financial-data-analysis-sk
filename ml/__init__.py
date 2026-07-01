"""
Machine Learning / Rules Engine package.

Phase 4 of the KIS Real-Time Stock Market Analysis system.

Provides:
- :class:`TrendPredictor` — Orchestrates rules-based and ML-based trend prediction.
- :class:`RulesEngine` — Heuristic-based signals using technical indicator thresholds.
- :class:`LightGBMPredictor` — Supervised ML model for short-term price direction.
- :class:`Prediction` — Data model for a single trend prediction.
- :class:`Signal` — Enum of trading signal directions.
- :class:`Horizon` — Enum of prediction time horizons.
"""
from __future__ import annotations

from ml.predictor import (
    Horizon,
    LightGBMPredictor,
    Prediction,
    RulesEngine,
    Signal,
    TrendPredictor,
)

__all__ = [
    "Horizon",
    "LightGBMPredictor",
    "Prediction",
    "RulesEngine",
    "Signal",
    "TrendPredictor",
]