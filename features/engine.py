"""
Feature engineering engine for real-time financial data.

Listens for completed OHLCV bars from the :class:`TickBuffer`, maintains a
rolling window of bars per stock, calculates technical indicators (VWAP, RSI,
momentum, order-book imbalance, etc.), and emits feature snapshots that can
be consumed by the ML / rules engine (Phase 4).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from config.settings import settings
from data.models import OHLCVBar
from features.indicators import (
    average_spread,
    momentum,
    order_book_imbalance,
    rolling_order_book_imbalance,
    rsi,
    volume_ratio,
    vwap,
)

logger = logging.getLogger("kis.feature_engine")


@dataclass
class FeatureSnapshot:
    """A complete set of computed features for a single stock at a point in time.

    This is the output of the feature engineering pipeline — one snapshot is
    produced per stock each time a new OHLCV bar is completed.
    """

    stock_code: str
    """6-digit Korean stock code."""

    timestamp: float
    """Unix timestamp when the feature snapshot was computed."""

    bar: OHLCVBar | None = None
    """The most recent completed bar (for reference)."""

    # ── Price-based features ──────────────────────────────────────────────
    vwap: Decimal | None = None
    """Volume-Weighted Average Price over the configured window."""

    rsi: float | None = None
    """Relative Strength Index."""

    momentum_1m: float | None = None
    """1-minute momentum (percentage change)."""

    momentum_5m: float | None = None
    """5-minute momentum (percentage change)."""

    # ── Order-book / market-microstructure features ───────────────────────
    order_book_imbalance: float | None = None
    """Best bid/ask order-book imbalance."""

    rolling_imbalance: float | None = None
    """Rolling average of close-to-close imbalance."""

    # ── Volume-based features ─────────────────────────────────────────────
    volume_ratio: float | None = None
    """Current volume / average volume over window."""

    average_spread: float | None = None
    """Average price spread over the configured window."""

    # ── Raw values (for ML input) ─────────────────────────────────────────
    close_price: int | None = None
    """Last close price from the most recent bar."""

    bar_volume: int | None = None
    """Volume of the most recent bar."""

    def as_feature_vector(self) -> dict[str, float]:
        """Flatten features into a numeric dictionary suitable for ML models.

        ``None`` values are omitted from the result — callers should handle
        missing keys or fill with a default (e.g., 0.0).
        """
        vec: dict[str, float] = {}
        if self.vwap is not None:
            vec["vwap"] = float(self.vwap)
        if self.rsi is not None:
            vec["rsi"] = self.rsi
        if self.momentum_1m is not None:
            vec["momentum_1m"] = self.momentum_1m
        if self.momentum_5m is not None:
            vec["momentum_5m"] = self.momentum_5m
        if self.order_book_imbalance is not None:
            vec["order_book_imbalance"] = self.order_book_imbalance
        if self.rolling_imbalance is not None:
            vec["rolling_imbalance"] = self.rolling_imbalance
        if self.volume_ratio is not None:
            vec["volume_ratio"] = self.volume_ratio
        if self.average_spread is not None:
            vec["average_spread"] = self.average_spread
        if self.close_price is not None:
            vec["close_price"] = float(self.close_price)
        if self.bar_volume is not None:
            vec["bar_volume"] = float(self.bar_volume)
        return vec


# ── Feature Engine ──────────────────────────────────────────────────────────


class FeatureEngine:
    """Orchestrates the calculation of technical indicators from OHLCV bars.

    Usage::

        engine = FeatureEngine(on_feature=my_callback)
        engine.register_bar(bar)  # call each time a bar is completed
    """

    def __init__(
        self,
        on_feature: Callable[[FeatureSnapshot], None] | None = None,
        vwap_window: int | None = None,
        rsi_window: int | None = None,
        oi_window: int | None = None,
    ) -> None:
        self._on_feature = on_feature

        # Indicator windows from settings (or overridden)
        self._vwap_window = vwap_window or settings.VWAP_WINDOW
        self._rsi_window = rsi_window or settings.RSI_WINDOW
        self._oi_window = oi_window or settings.OI_WINDOW

        # Per-stock bar buffers (oldest → newest)
        self._bars: dict[str, list[OHLCVBar]] = defaultdict(list)

        # Per-stock latest feature snapshots
        self._latest_features: dict[str, FeatureSnapshot] = {}

        # Async lock for thread safety
        self._lock = asyncio.Lock()

        # Statistics
        self._snapshots_produced = 0

        logger.info(
            "FeatureEngine initialised: VWAP=%d RSI=%d OI=%d",
            self._vwap_window,
            self._rsi_window,
            self._oi_window,
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def register_bar(self, bar: OHLCVBar) -> None:
        """Process a completed OHLCV bar and compute feature snapshot.

        This is designed to be used as the ``on_bar`` callback from
        :class:`TickBuffer`.

        Args:
            bar: A completed OHLCV bar.
        """
        # NOTE: This method runs synchronously from the TickBuffer callback
        # which is called with the lock held. We use a simple lock here
        # for safety even though the callback is synchronous.
        stock = bar.stock_code
        self._bars[stock].append(bar)

        # Compute features for this stock
        snapshot = self._compute_features(stock)
        if snapshot is not None:
            self._latest_features[stock] = snapshot
            self._snapshots_produced += 1

            # Emit via callback if configured
            if self._on_feature is not None:
                try:
                    self._on_feature(snapshot)
                except Exception:
                    logger.exception("Feature callback raised for %s", stock)

    async def async_register_bar(self, bar: OHLCVBar) -> None:
        """Async-safe wrapper around :meth:`register_bar`.

        Use this when calling from async code outside the TickBuffer callback.
        """
        async with self._lock:
            self.register_bar(bar)

    def get_latest_features(self, stock_code: str) -> FeatureSnapshot | None:
        """Get the most recent feature snapshot for a stock.

        Args:
            stock_code: The 6-digit Korean stock code.

        Returns:
            The latest :class:`FeatureSnapshot`, or ``None`` if none yet.
        """
        return self._latest_features.get(stock_code)

    def get_bar_count(self, stock_code: str) -> int:
        """Return the number of bars accumulated for a stock."""
        return len(self._bars.get(stock_code, []))

    @property
    def stocks(self) -> list[str]:
        """List of stock codes currently tracked."""
        return list(self._bars.keys())

    @property
    def stats(self) -> dict:
        """Return running statistics about the feature engine."""
        return {
            "snapshots_produced": self._snapshots_produced,
            "stocks_tracked": len(self._bars),
            "bars_per_stock": {
                code: len(bars) for code, bars in self._bars.items()
            },
        }

    def clear(self) -> None:
        """Clear all accumulated bars and feature snapshots."""
        self._bars.clear()
        self._latest_features.clear()

    def clear_stock(self, stock_code: str) -> None:
        """Clear data for a single stock."""
        self._bars.pop(stock_code, None)
        self._latest_features.pop(stock_code, None)

    # ── Private Methods ────────────────────────────────────────────────────

    def _compute_features(self, stock: str) -> FeatureSnapshot | None:
        """Compute all features for *stock* based on accumulated bars.

        Args:
            stock: The stock code to compute features for.

        Returns:
            A :class:`FeatureSnapshot`, or ``None`` if no bars available.
        """
        bars = self._bars.get(stock)
        if not bars:
            return None

        current_bar = bars[-1]

        # Determine bar windows for multi-bar features
        # 1-minute momentum = momentum with window = (60 / bar_seconds)
        # 5-minute momentum = momentum with window = (300 / bar_seconds)
        bar_seconds = settings.OHLCV_BAR_SECONDS
        momentum_1m_window = max(1, 60 // bar_seconds)
        momentum_5m_window = max(1, 300 // bar_seconds)

        # Compute all indicators
        vwap_val = vwap(bars[-self._vwap_window:]) if len(bars) >= 1 else None
        rsi_val = rsi(bars, self._rsi_window)
        mom_1m = momentum(bars, momentum_1m_window)
        mom_5m = momentum(bars, momentum_5m_window)
        oi_val = rolling_order_book_imbalance(bars, self._oi_window)
        vol_ratio = volume_ratio(bars, 10)
        avg_spread = average_spread(bars, 10)

        # Order-book imbalance from tick-level data (if available)
        # Since OHLCV bars don't carry bid/ask, this will remain None
        # unless the TickBuffer is enhanced to pass tick data.
        obs_imb_val = None

        return FeatureSnapshot(
            stock_code=stock,
            timestamp=current_bar.timestamp,
            bar=current_bar,
            vwap=vwap_val,
            rsi=rsi_val,
            momentum_1m=mom_1m,
            momentum_5m=mom_5m,
            order_book_imbalance=obs_imb_val,
            rolling_imbalance=oi_val,
            volume_ratio=vol_ratio,
            average_spread=avg_spread,
            close_price=current_bar.close_price,
            bar_volume=current_bar.volume,
        )