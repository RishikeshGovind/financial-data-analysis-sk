"""
Standalone technical indicator calculation functions.

All functions operate on sequences of OHLCV bars and return computed
indicator values. These are pure functions designed to be testable
in isolation and usable from the :class:`FeatureEngine`.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Sequence

import numpy as np

from data.models import OHLCVBar

logger = logging.getLogger("kis.indicators")

# ── Type Aliases ───────────────────────────────────────────────────────────

BarSeq = Sequence[OHLCVBar]
"""Ordered sequence of OHLCV bars (oldest → newest)."""


# ── VWAP ───────────────────────────────────────────────────────────────────


def vwap(bars: BarSeq) -> Decimal | None:
    """Volume-Weighted Average Price over the given bars.

    .. math::

        VWAP = \\frac{\\sum (price_i \\times volume_i)}{\\sum volume_i}

    Uses the bar's midpoint ``(high + low) // 2`` as the representative price.

    Args:
        bars: A window of OHLCV bars (oldest first).

    Returns:
        VWAP as a ``Decimal``, or ``None`` if total volume is zero.
    """
    if not bars:
        return None

    total_value = 0
    total_volume = 0
    for bar in bars:
        rep_price = (bar.high_price + bar.low_price) // 2
        total_value += rep_price * bar.volume
        total_volume += bar.volume

    if total_volume == 0:
        return None

    return Decimal(total_value) / Decimal(total_volume)


# ── RSI ────────────────────────────────────────────────────────────────────


def rsi(bars: BarSeq, window: int = 14) -> float | None:
    """Relative Strength Index using Wilder's smoothing method.

    .. math::

        RSI = 100 - \\frac{100}{1 + RS}

    where RS = average gain / average loss over the window.

    Args:
        bars: OHLCV bars ordered oldest → newest (at least ``window + 1``).
        window: The look-back period (default 14).

    Returns:
        RSI value in [0, 100], or ``None`` if insufficient data.
    """
    if len(bars) < window + 1:
        return None

    # Compute close-to-close price changes
    closes = np.array([bar.close_price for bar in bars], dtype=np.float64)
    deltas = np.diff(closes)  # length = len(bars) - 1

    gains = deltas.copy()
    losses = deltas.copy()
    gains[gains < 0] = 0.0
    losses[losses > 0] = 0.0
    losses = np.abs(losses)

    # Wilder's smoothed average
    avg_gain = np.mean(gains[:window])
    avg_loss = np.mean(losses[:window])

    if avg_loss == 0.0:
        return 100.0

    # Iterate over remaining deltas
    for i in range(window, len(deltas)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window

    if avg_loss == 0.0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(float(100.0 - (100.0 / (1.0 + rs))), 2)


# ── Order Book Imbalance ────────────────────────────────────────────────────


def order_book_imbalance(bid_price: int, ask_price: int) -> float:
    """Compute order-book imbalance from best bid and ask prices.

    Imbalance is defined as::

        imbalance = (bid - ask) / (bid + ask)

    A positive value indicates buying pressure (bid > ask),
    negative indicates selling pressure (ask > bid).

    Since we only have best bid/ask (not full depth), this is a simple
    spread-ratio approximation.

    Args:
        bid_price: Best bid price.
        ask_price: Best ask price.

    Returns:
        Imbalance in [-1, 1].
    """
    total = bid_price + ask_price
    if total == 0:
        return 0.0
    return round((bid_price - ask_price) / total, 4)


# ── Rolling Order Book Imbalance (from bars) ───────────────────────────────


def rolling_order_book_imbalance(bars: BarSeq, window: int = 10) -> float | None:
    """Average order-book imbalance over the last *window* bars.

    Uses the bar's close-price as a proxy for bid/ask imbalance
    by computing the difference between consecutive close prices
    normalised by the price level.

    Note:
        Since OHLCV bars don't carry bid/ask data directly, we approximate
        imbalance via the close-to-close move. A more accurate calculation
        would use tick-level bid/ask data.

    Args:
        bars: Recent OHLCV bars (oldest first).
        window: Number of bars to average over.

    Returns:
        Average imbalance ratio, or ``None`` if insufficient data.
    """
    if len(bars) < window + 1:
        return None

    imbalances = []
    for i in range(1, window + 1):
        prev_close = bars[-(i + 1)].close_price
        curr_close = bars[-i].close_price
        if prev_close == 0:
            continue
        # Approximate imbalance as normalised price change
        imbalance = (curr_close - prev_close) / prev_close
        imbalances.append(imbalance)

    if not imbalances:
        return 0.0
    return round(float(np.mean(imbalances)), 6)


# ── Momentum ───────────────────────────────────────────────────────────────


def momentum(bars: BarSeq, window: int = 1) -> float | None:
    """Price momentum as the percentage change over *window* bars.

    .. math::

        Momentum = \\frac{close_{t} - close_{t - window}}{close_{t - window}} \\times 100

    Args:
        bars: OHLCV bars ordered oldest → newest.
        window: Number of bars to look back.

    Returns:
        Momentum as a percentage, or ``None`` if insufficient data.
    """
    if len(bars) < window + 1:
        return None

    prev_close = bars[-(window + 1)].close_price
    curr_close = bars[-1].close_price

    if prev_close == 0:
        return None

    return round(((curr_close - prev_close) / prev_close) * 100.0, 4)


# ── Price Spread ────────────────────────────────────────────────────────────


def average_spread(bars: BarSeq, window: int = 10) -> float | None:
    """Average price spread (high - low) over the last *window* bars.

    Args:
        bars: Recent OHLCV bars (oldest first).
        window: Look-back period.

    Returns:
        Average spread, or ``None`` if insufficient data.
    """
    if len(bars) < window:
        return None

    recent = bars[-window:]
    spreads = [bar.high_price - bar.low_price for bar in recent]
    return round(float(np.mean(spreads)), 2)


# ── Volume Ratio ────────────────────────────────────────────────────────────


def volume_ratio(bars: BarSeq, window: int = 10) -> float | None:
    """Ratio of current bar volume to the average volume over *window* bars.

    A value > 1.0 means current volume is above average.

    Args:
        bars: Recent OHLCV bars (oldest first).
        window: Look-back period.

    Returns:
        Volume ratio, or ``None`` if insufficient data.
    """
    if len(bars) < window + 1:
        return None

    recent = bars[-(window + 1):]  # include current
    current_vol = recent[-1].volume
    avg_vol = float(np.mean([b.volume for b in recent[:-1]]))

    if avg_vol == 0:
        return None

    return round(current_vol / avg_vol, 4)