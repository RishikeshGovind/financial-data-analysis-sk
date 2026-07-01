"""
Trend prediction engine for real-time financial data.

Phase 4 of the KIS Real-Time Stock Market Analysis system.

Provides two complementary prediction approaches:
  1. **RulesEngine** — Heuristic-based signals using technical indicator thresholds
     (RSI overbought/oversold, momentum direction, volume confirmation).
  2. **LightGBMPredictor** — Supervised ML model trained on historical feature
     vectors to predict short-term price direction (1-min and 5-min horizons).

The :class:`TrendPredictor` orchestrates both, using the ML model when
available and falling back to the rules engine otherwise.
"""
from __future__ import annotations

import logging
import pickle
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import numpy as np

from config.settings import settings

logger = logging.getLogger("kis.predictor")

# ── Type Aliases ───────────────────────────────────────────────────────────

FeatureVector = dict[str, float]
"""A dictionary of feature names → numeric values (from FeatureSnapshot)."""


# ── Prediction Data Model ──────────────────────────────────────────────────


class Signal(Enum):
    """Trading signal direction."""

    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class Horizon(str, Enum):
    """Prediction time horizon."""

    M1 = "1m"
    M5 = "5m"


@dataclass
class Prediction:
    """A single trend prediction for a stock at a specific horizon.

    Attributes:
        stock_code: 6-digit Korean stock code.
        horizon: Prediction time horizon (1m or 5m).
        signal: Trading signal direction.
        confidence: Confidence score in [0.0, 1.0].
        probability_up: Estimated probability of upward movement.
        timestamp: Unix timestamp when the prediction was made.
        source: Which engine produced this prediction ('rules' or 'ml').
    """

    stock_code: str
    horizon: Horizon
    signal: Signal
    confidence: float
    probability_up: float
    timestamp: float = field(default_factory=time.time)
    source: str = "rules"

    def as_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary for IPC / UI consumption."""
        return {
            "stock_code": self.stock_code,
            "horizon": self.horizon.value,
            "signal": self.signal.value,
            "confidence": round(self.confidence, 4),
            "probability_up": round(self.probability_up, 4),
            "timestamp": self.timestamp,
            "source": self.source,
        }

    @classmethod
    def hold(cls, stock_code: str, horizon: Horizon) -> "Prediction":
        """Factory: return a neutral HOLD prediction with zero confidence."""
        return cls(
            stock_code=stock_code,
            horizon=horizon,
            signal=Signal.HOLD,
            confidence=0.0,
            probability_up=0.5,
        )


# ── Signal Helpers ─────────────────────────────────────────────────────────


def _signal_from_probability(prob_up: float, threshold: float = 0.6) -> Signal:
    """Map a probability of upward movement to a trading signal.

    Args:
        prob_up: Probability of upward movement in [0.0, 1.0].
        threshold: Confidence threshold for directional signals.

    Returns:
        A :class:`Signal` value.
    """
    if prob_up >= threshold + 0.2:
        return Signal.STRONG_BUY
    if prob_up >= threshold:
        return Signal.BUY
    if prob_up <= 1.0 - threshold - 0.2:
        return Signal.STRONG_SELL
    if prob_up <= 1.0 - threshold:
        return Signal.SELL
    return Signal.HOLD


# ── Rules Engine ───────────────────────────────────────────────────────────


class RulesEngine:
    """Heuristic-based trend prediction using technical indicator thresholds.

    The rules engine uses simple, interpretable logic:
      - RSI > 70 → overbought → SELL signal
      - RSI < 30 → oversold → BUY signal
      - Strong positive momentum → BUY (confirmed by volume)
      - Strong negative momentum → SELL (confirmed by volume)
      - Volume ratio > 1.5 amplifies the confidence of momentum-based signals
    """

    # RSI thresholds
    RSI_OVERBOUGHT: float = 70.0
    RSI_OVERSOLD: float = 30.0

    # Momentum thresholds (%)
    MOMENTUM_STRONG_BUY: float = 2.0
    MOMENTUM_BUY: float = 1.0
    MOMENTUM_STRONG_SELL: float = -2.0
    MOMENTUM_SELL: float = -1.0

    # Volume confirmation threshold
    VOLUME_CONFIRMATION: float = 1.5

    def predict(
        self,
        stock_code: str,
        features: FeatureVector,
    ) -> dict[Horizon, Prediction]:
        """Generate predictions for all horizons using rules-based logic.

        Args:
            stock_code: The stock code to predict for.
            features: The latest feature vector from the feature engine.

        Returns:
            A dictionary mapping each :class:`Horizon` to a :class:`Prediction`.
        """
        predictions: dict[Horizon, Prediction] = {}

        for horizon in (Horizon.M1, Horizon.M5):
            predictions[horizon] = self._predict_single(
                stock_code=stock_code,
                features=features,
                horizon=horizon,
            )

        return predictions

    def _predict_single(
        self,
        stock_code: str,
        features: FeatureVector,
        horizon: Horizon,
    ) -> Prediction:
        """Generate a single prediction for a given horizon.

        Args:
            stock_code: The stock code.
            features: The latest feature vector.
            horizon: The prediction horizon.

        Returns:
            A :class:`Prediction` instance.
        """
        rsi_val = features.get("rsi")
        momentum_key = "momentum_1m" if horizon == Horizon.M1 else "momentum_5m"
        mom_val = features.get(momentum_key)
        vol_ratio = features.get("volume_ratio")

        # Start with neutral
        prob_up = 0.5
        reasons: list[str] = []

        # ── RSI-based signal ──────────────────────────────────────────────
        if rsi_val is not None:
            if rsi_val <= self.RSI_OVERSOLD:
                # Oversold → expect reversal upward
                oversold_strength = (self.RSI_OVERSOLD - rsi_val) / self.RSI_OVERSOLD
                prob_up += 0.15 * min(oversold_strength, 1.0)
                reasons.append("rsi_oversold")
            elif rsi_val >= self.RSI_OVERBOUGHT:
                # Overbought → expect reversal downward
                overbought_strength = (rsi_val - self.RSI_OVERBOUGHT) / (
                    100.0 - self.RSI_OVERBOUGHT
                )
                prob_up -= 0.15 * min(overbought_strength, 1.0)
                reasons.append("rsi_overbought")

        # ── Momentum-based signal ─────────────────────────────────────────
        if mom_val is not None:
            if mom_val >= self.MOMENTUM_STRONG_BUY:
                prob_up += 0.20
                reasons.append("momentum_strong_buy")
            elif mom_val >= self.MOMENTUM_BUY:
                prob_up += 0.10
                reasons.append("momentum_buy")
            elif mom_val <= self.MOMENTUM_STRONG_SELL:
                prob_up -= 0.20
                reasons.append("momentum_strong_sell")
            elif mom_val <= self.MOMENTUM_SELL:
                prob_up -= 0.10
                reasons.append("momentum_sell")

        # ── Volume confirmation ───────────────────────────────────────────
        if vol_ratio is not None and vol_ratio > self.VOLUME_CONFIRMATION:
            # Amplify the current directional bias
            if prob_up > 0.55:
                prob_up = min(prob_up + 0.05, 1.0)
                reasons.append("volume_confirmation_buy")
            elif prob_up < 0.45:
                prob_up = max(prob_up - 0.05, 0.0)
                reasons.append("volume_confirmation_sell")

        # Clamp to [0.0, 1.0]
        prob_up = max(0.0, min(1.0, prob_up))

        # Compute confidence based on how far from neutral
        confidence = abs(prob_up - 0.5) * 2.0  # [0.0, 1.0]

        # Determine signal
        signal = _signal_from_probability(prob_up, settings.PREDICTION_CONFIDENCE_THRESHOLD)

        return Prediction(
            stock_code=stock_code,
            horizon=horizon,
            signal=signal,
            confidence=confidence,
            probability_up=prob_up,
            source="rules",
        )


# ── LightGBM Predictor ────────────────────────────────────────────────────


class LightGBMPredictor:
    """Supervised ML model for short-term price direction prediction.

    Uses LightGBM to predict the probability of upward price movement over
    1-minute and 5-minute horizons based on the feature vector from the
    feature engineering pipeline.

    The model is trained on historical feature snapshots with labels derived
    from subsequent price movement. Training data is accumulated in-memory
    and the model is (re)trained periodically.

    If LightGBM is not installed or the model has not been trained yet,
    predictions fall back to neutral (HOLD).
    """

    # Minimum samples required before training
    MIN_TRAIN_SAMPLES: int = 100

    # Retrain the model every N new samples
    RETRAIN_INTERVAL: int = 50

    def __init__(
        self,
        model_path: str | Path | None = None,
    ) -> None:
        """Initialise the LightGBM predictor.

        Args:
            model_path: Optional path to a pre-trained model file. If provided,
                the model will be loaded from disk.
        """
        self._model_path = Path(model_path) if model_path else None

        # Training data buffers (per horizon)
        self._X: dict[Horizon, list[FeatureVector]] = {
            Horizon.M1: [],
            Horizon.M5: [],
        }
        self._y: dict[Horizon, list[int]] = {
            Horizon.M1: [],
            Horizon.M5: [],
        }

        # Trained models (per horizon)
        self._models: dict[Horizon, Any] = {Horizon.M1: None, Horizon.M5: None}

        # Training state
        self._samples_since_train: int = 0
        self._is_trained: bool = False

        # Attempt to load pre-trained model
        if self._model_path and self._model_path.exists():
            self._load_model()

        logger.info(
            "LightGBMPredictor initialised (model_path=%s, trained=%s)",
            self._model_path,
            self._is_trained,
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def add_sample(
        self,
        features: FeatureVector,
        future_price_change_1m: float | None,
        future_price_change_5m: float | None,
    ) -> None:
        """Add a training sample with observed future price changes.

        Args:
            features: The feature vector at prediction time.
            future_price_change_1m: Observed % price change after 1 minute
                (``None`` if not yet available).
            future_price_change_5m: Observed % price change after 5 minutes
                (``None`` if not yet available).
        """
        if future_price_change_1m is not None:
            self._X[Horizon.M1].append(features)
            self._y[Horizon.M1].append(1 if future_price_change_1m > 0 else 0)

        if future_price_change_5m is not None:
            self._X[Horizon.M5].append(features)
            self._y[Horizon.M5].append(1 if future_price_change_5m > 0 else 0)

        self._samples_since_train += 1

        # Trigger retrain if we have enough new samples
        if (
            self._samples_since_train >= self.RETRAIN_INTERVAL
            and len(self._X[Horizon.M1]) >= self.MIN_TRAIN_SAMPLES
        ):
            self._train()
            self._samples_since_train = 0

    def predict(
        self,
        stock_code: str,
        features: FeatureVector,
    ) -> dict[Horizon, Prediction]:
        """Generate ML-based predictions for all horizons.

        Args:
            stock_code: The stock code to predict for.
            features: The latest feature vector.

        Returns:
            A dictionary mapping each :class:`Horizon` to a :class:`Prediction`.
        """
        predictions: dict[Horizon, Prediction] = {}

        for horizon in (Horizon.M1, Horizon.M5):
            predictions[horizon] = self._predict_single(
                stock_code=stock_code,
                features=features,
                horizon=horizon,
            )

        return predictions

    def _predict_single(
        self,
        stock_code: str,
        features: FeatureVector,
        horizon: Horizon,
    ) -> Prediction:
        """Generate a single ML-based prediction.

        Args:
            stock_code: The stock code.
            features: The feature vector.
            horizon: The prediction horizon.

        Returns:
            A :class:`Prediction` instance.
        """
        model = self._models.get(horizon)
        if model is None or not self._is_trained:
            return Prediction.hold(stock_code, horizon)

        try:
            # Build feature array in consistent order
            feature_names = sorted(features.keys())
            X = np.array([[features.get(k, 0.0) for k in feature_names]], dtype=np.float64)

            prob_up = float(model.predict_proba(X)[0, 1])
            confidence = abs(prob_up - 0.5) * 2.0
            signal = _signal_from_probability(prob_up, settings.PREDICTION_CONFIDENCE_THRESHOLD)

            return Prediction(
                stock_code=stock_code,
                horizon=horizon,
                signal=signal,
                confidence=confidence,
                probability_up=prob_up,
                source="ml",
            )
        except Exception:
            logger.exception("ML prediction failed for %s (%s)", stock_code, horizon.value)
            return Prediction.hold(stock_code, horizon)

    def save_model(self, path: str | Path | None = None) -> None:
        """Save trained models to disk.

        Args:
            path: Directory path to save models. Defaults to ``model_path``
                provided at initialisation.
        """
        save_path = Path(path) if path else self._model_path
        if save_path is None:
            logger.warning("No model path specified; cannot save model.")
            return

        save_path.mkdir(parents=True, exist_ok=True)

        for horizon, model in self._models.items():
            if model is not None:
                model_path = save_path / f"lgbm_{horizon.value}.pkl"
                with open(model_path, "wb") as f:
                    pickle.dump(model, f)
                logger.info("Model saved to %s", model_path)

    def _load_model(self) -> None:
        """Load pre-trained models from disk."""
        if self._model_path is None:
            return

        for horizon in (Horizon.M1, Horizon.M5):
            model_path = self._model_path / f"lgbm_{horizon.value}.pkl"
            if model_path.exists():
                try:
                    with open(model_path, "rb") as f:
                        self._models[horizon] = pickle.load(f)
                    self._is_trained = True
                    logger.info("Loaded model from %s", model_path)
                except Exception:
                    logger.exception("Failed to load model from %s", model_path)

    def _train(self) -> None:
        """Train LightGBM models for each horizon."""
        try:
            import lightgbm as lgb
        except ImportError:
            logger.warning(
                "LightGBM is not installed. Install with: pip install lightgbm"
            )
            return

        for horizon in (Horizon.M1, Horizon.M5):
            X_list = self._X[horizon]
            y_list = self._y[horizon]

            if len(X_list) < self.MIN_TRAIN_SAMPLES:
                logger.debug(
                    "Not enough samples for %s: %d < %d",
                    horizon.value,
                    len(X_list),
                    self.MIN_TRAIN_SAMPLES,
                )
                continue

            # Build feature matrix with consistent column order
            feature_names = sorted(X_list[0].keys())
            X = np.array(
                [[row.get(k, 0.0) for k in feature_names] for row in X_list],
                dtype=np.float64,
            )
            y = np.array(y_list, dtype=np.int32)

            logger.info(
                "Training LightGBM model for %s: %d samples, %d features",
                horizon.value,
                len(X),
                len(feature_names),
            )

            try:
                train_data = lgb.Dataset(X, label=y, feature_name=feature_names)
                params = {
                    "objective": "binary",
                    "metric": "binary_logloss",
                    "boosting_type": "gbdt",
                    "num_leaves": 31,
                    "learning_rate": 0.05,
                    "feature_fraction": 0.8,
                    "bagging_fraction": 0.8,
                    "bagging_freq": 5,
                    "verbose": -1,
                    "seed": 42,
                }
                model = lgb.train(
                    params,
                    train_data,
                    num_boost_round=100,
                )
                self._models[horizon] = model
                self._is_trained = True

                logger.info(
                    "LightGBM model trained for %s (accuracy will be evaluated "
                    "on next batch)",
                    horizon.value,
                )
            except Exception:
                logger.exception("LightGBM training failed for %s", horizon.value)

    @property
    def is_trained(self) -> bool:
        """Whether at least one horizon model has been trained."""
        return self._is_trained

    @property
    def training_samples(self) -> dict[Horizon, int]:
        """Return the number of training samples accumulated per horizon."""
        return {h: len(self._X[h]) for h in Horizon}


# ── Trend Predictor (Orchestrator) ─────────────────────────────────────────


class TrendPredictor:
    """Orchestrates rules-based and ML-based trend prediction.

    The :class:`TrendPredictor` is the main entry point for Phase 4. It:

    1. Receives feature snapshots from the :class:`FeatureEngine`.
    2. Runs the rules engine to get immediate heuristic signals.
    3. If the ML model is trained, also runs ML predictions and uses them
       when confidence exceeds the rules engine.
    4. Stores the latest predictions for each stock.
    5. Calls an optional callback so the main pipeline can propagate
       predictions to the UI.

    Usage::

        predictor = TrendPredictor(on_prediction=my_callback)
        predictor.on_feature(snapshot)  # called from FeatureEngine callback
    """

    def __init__(
        self,
        on_prediction: Callable[[dict[str, Any]], None] | None = None,
        model_path: str | Path | None = None,
    ) -> None:
        """Initialise the trend predictor.

        Args:
            on_prediction: Optional callback invoked each time predictions
                are updated for a stock. Receives a dict with stock_code
                as key and prediction dicts as values.
            model_path: Optional path to pre-trained LightGBM models.
        """
        self._on_prediction = on_prediction
        self._rules_engine = RulesEngine()
        self._ml_predictor = LightGBMPredictor(model_path=model_path)

        # Latest predictions per stock: {stock_code: {horizon: Prediction}}
        self._latest_predictions: dict[str, dict[str, dict[str, Any]]] = {}

        # Feature buffer for training labels (per stock)
        # Stores {stock_code: [(timestamp, features_dict), ...]}
        self._feature_history: dict[str, list[tuple[float, FeatureVector]]] = (
            defaultdict(list)
        )

        # Statistics
        self._total_predictions: int = 0

        logger.info(
            "TrendPredictor initialised (ml_trained=%s, mock=%s)",
            self._ml_predictor.is_trained,
            settings.MOCK_PREDICTIONS,
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def on_feature(self, snapshot: Any) -> None:
        """Process a new feature snapshot and generate predictions.

        This method is designed to be used as the ``on_feature`` callback
        from the :class:`FeatureEngine`.

        Args:
            snapshot: A :class:`FeatureSnapshot` instance from the feature engine.
        """
        stock_code = snapshot.stock_code
        features = snapshot.as_feature_vector()
        current_time = snapshot.timestamp

        # Store feature for future label generation
        self._feature_history[stock_code].append((current_time, features))

        # Generate predictions
        predictions = self._predict(stock_code, features)

        # Store latest predictions
        self._latest_predictions[stock_code] = {
            h.value: p.as_dict() for h, p in predictions.items()
        }
        self._total_predictions += 1

        # Emit via callback
        if self._on_prediction is not None:
            try:
                self._on_prediction(self._latest_predictions)
            except Exception:
                logger.exception("Prediction callback raised for %s", stock_code)

        # Try to generate training labels from historical features
        self._generate_training_labels(stock_code)

    def get_predictions(
        self, stock_code: str
    ) -> dict[str, dict[str, Any]] | None:
        """Get the latest predictions for a stock.

        Args:
            stock_code: The 6-digit stock code.

        Returns:
            A dict mapping horizon strings to prediction dicts, or ``None``.
        """
        return self._latest_predictions.get(stock_code)

    def get_all_predictions(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Get the latest predictions for all stocks.

        Returns:
            A dict mapping stock codes to their prediction dicts.
        """
        return dict(self._latest_predictions)

    @property
    def stats(self) -> dict[str, Any]:
        """Return running statistics about the predictor."""
        return {
            "total_predictions": self._total_predictions,
            "stocks_tracked": len(self._latest_predictions),
            "ml_trained": self._ml_predictor.is_trained,
            "ml_samples_1m": self._ml_predictor.training_samples[Horizon.M1],
            "ml_samples_5m": self._ml_predictor.training_samples[Horizon.M5],
        }

    def save_models(self, path: str | Path | None = None) -> None:
        """Save ML models to disk."""
        self._ml_predictor.save_model(path)

    # ── Private Methods ────────────────────────────────────────────────────

    def _predict(
        self,
        stock_code: str,
        features: FeatureVector,
    ) -> dict[Horizon, Prediction]:
        """Generate predictions using available engines.

        Uses ML predictions when the model is trained and confidence is
        sufficient; falls back to rules-based predictions otherwise.

        Args:
            stock_code: The stock code.
            features: The feature vector.

        Returns:
            A dict mapping horizons to predictions.
        """
        # Always get rules-based predictions as baseline
        rules_preds = self._rules_engine.predict(stock_code, features)

        # If ML model is not trained or mock predictions are enabled, use rules
        if not self._ml_predictor.is_trained or settings.MOCK_PREDICTIONS:
            return rules_preds

        # Get ML predictions
        ml_preds = self._ml_predictor.predict(stock_code, features)

        # Use ML prediction when its confidence exceeds the rules confidence
        # and the ML model is trained; otherwise keep the rules prediction
        final_preds: dict[Horizon, Prediction] = {}
        for horizon in (Horizon.M1, Horizon.M5):
            ml_pred = ml_preds[horizon]
            rules_pred = rules_preds[horizon]

            if ml_pred.confidence >= rules_pred.confidence:
                final_preds[horizon] = ml_pred
            else:
                final_preds[horizon] = rules_pred

        return final_preds

    def _generate_training_labels(self, stock_code: str) -> None:
        """Generate training labels from historical feature data.

        For each stored feature snapshot, once enough time has passed to
        observe the 1-min and 5-min price changes, we create a training
        sample for the ML model.

        Args:
            stock_code: The stock code to generate labels for.
        """
        history = self._feature_history[stock_code]
        if len(history) < 2:
            return

        current_time = time.time()
        processed: list[tuple[float, FeatureVector]] = []

        for feat_time, features in history:
            # Check if we have enough data to compute labels
            future_1m = None
            future_5m = None

            # Find the price after 1 minute
            for later_time, later_feat in history:
                elapsed = later_time - feat_time
                if 55 <= elapsed <= 65:  # ~1 minute
                    current_close = features.get("close_price")
                    later_close = later_feat.get("close_price")
                    if current_close and later_close and current_close != 0:
                        future_1m = ((later_close - current_close) / current_close) * 100.0
                    break

            # Find the price after 5 minutes
            for later_time, later_feat in history:
                elapsed = later_time - feat_time
                if 295 <= elapsed <= 305:  # ~5 minutes
                    current_close = features.get("close_price")
                    later_close = later_feat.get("close_price")
                    if current_close and later_close and current_close != 0:
                        future_5m = ((later_close - current_close) / current_close) * 100.0
                    break

            # Only add sample if we have at least one label
            if future_1m is not None or future_5m is not None:
                self._ml_predictor.add_sample(features, future_1m, future_5m)
                processed.append((feat_time, features))

        # Remove processed entries from history
        for item in processed:
            history.remove(item)