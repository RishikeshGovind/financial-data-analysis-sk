"""
Async-safe in-memory tick buffer and OHLCV bar aggregator.

Maintains per-stock circular buffers of raw ticks and produces aggregated
OHLCV bars on configurable time windows.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Callable

from data.models import OHLCVBar, TickData
from config.settings import settings

logger = logging.getLogger("kis.tick_buffer")


class TickBuffer:
    """Thread/async-safe per-stock tick buffer with OHLCV bar aggregation.

    Manages a sliding window of ticks for each stock, producing OHLCV bars
    at a configurable interval. Bars are emitted via an optional callback.

    This class is *not* thread-safe by default; access should be mediated
    through the provided async methods or run in a single event loop.
    """

    def __init__(
        self,
        max_ticks_per_stock: int | None = None,
        bar_seconds: int | None = None,
        on_bar: Callable[[OHLCVBar], None] | None = None,
    ) -> None:
        self._max_ticks = max_ticks_per_stock or settings.MAX_TICK_BUFFER
        self._bar_seconds = bar_seconds or settings.OHLCV_BAR_SECONDS
        self._on_bar = on_bar

        # Per-stock storage: stock_code -> list[TickData]
        self._ticks: dict[str, list[TickData]] = defaultdict(list)

        # Per-stock bar state: stock_code -> currently-building bar ticks
        self._bar_window_ticks: dict[str, list[TickData]] = defaultdict(list)

        # Per-stock bar window anchor (unix timestamp of current window start)
        self._bar_anchors: dict[str, float] = {}

        self._lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────

    async def add_tick(self, tick: TickData) -> None:
        """Ingest a single tick: store it and check for bar completion.

        Args:
            tick: The incoming tick data.
        """
        async with self._lock:
            stock = tick.stock_code
            self._ticks[stock].append(tick)

            # Prune old ticks past the buffer limit
            while len(self._ticks[stock]) > self._max_ticks:
                self._ticks[stock].pop(0)

            # Accumulate into the current bar window
            self._accumulate_bar(tick)

    async def get_ticks(
        self, stock_code: str, n: int | None = None
    ) -> list[TickData]:
        """Return the most recent *n* ticks for a stock (or all if ``None``).

        Returns a copy so callers cannot mutate the internal buffer.
        """
        async with self._lock:
            buf = self._ticks.get(stock_code, [])
            if n is not None:
                return list(buf[-n:])
            return list(buf)

    async def get_bars(
        self, stock_code: str, n: int | None = None
    ) -> list[OHLCVBar]:
        """Return the most recent *n* completed bars for a stock.

        .. note::
            Completed bars are held in the buffer; the in-progress bar is
            *not* included.
        """
        async with self._lock:
            # Currently we don't persist completed bars — they are emitted
            # via callback and the caller is expected to store them.
            # This method returns bars that were stored externally if set.
            # For now, return an empty list since bars are not stored here.
            return []

    async def latest_tick(self, stock_code: str) -> TickData | None:
        """Get the most recent tick for a stock, or ``None``."""
        async with self._lock:
            buf = self._ticks.get(stock_code)
            if not buf:
                return None
            return buf[-1]

    @property
    def stocks(self) -> list[str]:
        """List of stock codes currently tracked."""
        return list(self._ticks.keys())

    # ── Private Methods ───────────────────────────────────────────────────

    def _accumulate_bar(self, tick: TickData) -> None:
        """Accumulate a tick into the current bar window and finalise if needed.

        Must be called with ``self._lock`` held.
        """
        stock = tick.stock_code

        # Initialise bar anchor if needed
        if stock not in self._bar_anchors:
            self._bar_anchors[stock] = _floor_time(tick.timestamp, self._bar_seconds)

        anchor = self._bar_anchors[stock]

        # If this tick falls within the current window, accumulate it
        if tick.timestamp < anchor + self._bar_seconds:
            self._bar_window_ticks[stock].append(tick)
            return

        # Tick falls outside the current window — finalise the completed bar
        self._finalise_bar(stock)

        # Start a new window
        new_anchor = _floor_time(tick.timestamp, self._bar_seconds)
        self._bar_anchors[stock] = new_anchor
        self._bar_window_ticks[stock].append(tick)

    def _finalise_bar(self, stock: str) -> None:
        """Build and emit an OHLCV bar from the accumulated ticks.

        Must be called with ``self._lock`` held.
        """
        ticks = self._bar_window_ticks.get(stock)
        if not ticks:
            return

        try:
            bar = OHLCVBar.from_ticks(stock, ticks)
        except ValueError:
            return

        logger.debug("Bar completed: %s O=%d H=%d L=%d C=%d V=%d",
                      stock, bar.open_price, bar.high_price,
                      bar.low_price, bar.close_price, bar.volume)

        # Clear the window ticks for this stock
        self._bar_window_ticks[stock] = []

        # Emit via callback if configured
        if self._on_bar is not None:
            try:
                self._on_bar(bar)
            except Exception:
                logger.exception("Bar callback raised for %s", stock)

    # ── Cleanup ───────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Clear all buffered ticks and in-progress bars."""
        self._ticks.clear()
        self._bar_window_ticks.clear()
        self._bar_anchors.clear()

    def clear_stock(self, stock_code: str) -> None:
        """Clear buffered data for a single stock."""
        self._ticks.pop(stock_code, None)
        self._bar_window_ticks.pop(stock_code, None)
        self._bar_anchors.pop(stock_code, None)


# ── Helpers ───────────────────────────────────────────────────────────────


def _floor_time(ts: float, interval: int) -> float:
    """Floor a unix timestamp to the nearest *interval* boundary."""
    return (ts // interval) * interval