"""
Demo mode: synthetic real-time tick generator for testing without KIS credentials.

Generates realistic price movements and feeds them through the full pipeline
(TickBuffer → FeatureEngine → TrendPredictor → Streamlit dashboard).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict

from config.settings import settings
from data import TickBuffer, write_shared_state
from data.models import TickData
from features import FeatureEngine, FeatureSnapshot
from ml import TrendPredictor

logger = logging.getLogger("kis.demo")


class SyntheticTickGenerator:
    """Generates realistic synthetic H0STCNT0 ticks for demo/testing."""

    def __init__(self, stocks: list[str]):
        self._stocks = stocks
        # Price state per stock (base price, current price)
        self._prices: dict[str, float] = {stock: 70000 + random.randint(-5000, 5000) for stock in stocks}
        self._cumulative_volumes: dict[str, int] = {stock: random.randint(1_000_000, 10_000_000) for stock in stocks}
        self._tick_count: int = 0

    async def run(
        self,
        tick_buffer: TickBuffer,
        duration_seconds: float = 600.0,
        ticks_per_second: float = 2.0,
    ) -> None:
        """Generate and emit synthetic ticks for a given duration.

        Args:
            tick_buffer: The buffer to feed ticks into.
            duration_seconds: How long to generate ticks for.
            ticks_per_second: Tick emission rate.
        """
        start_time = time.time()
        interval = 1.0 / ticks_per_second

        logger.info(
            "Synthetic tick generator starting: %d stock(s), %.1f ticks/sec, %.0f sec duration",
            len(self._stocks),
            ticks_per_second,
            duration_seconds,
        )

        while time.time() - start_time < duration_seconds:
            for stock in self._stocks:
                tick = self._generate_tick(stock)
                await tick_buffer.add_tick(tick)
                self._tick_count += 1

            await asyncio.sleep(interval)

        logger.info("Synthetic tick generator finished (%d ticks emitted).", self._tick_count)

    def _generate_tick(self, stock_code: str) -> TickData:
        """Generate a single realistic synthetic tick."""
        current_price = self._prices[stock_code]

        # Random walk: ±0.5% per tick, with slight upward drift
        change_pct = (random.gauss(0.1, 0.5)) / 100.0
        new_price = max(10000, current_price * (1.0 + change_pct))
        self._prices[stock_code] = new_price

        # Volume: random 100-1000 shares per tick
        tick_volume = random.randint(100, 1000)
        self._cumulative_volumes[stock_code] += tick_volume

        # Bid-ask spread: 0.05-0.15% of mid price
        mid_price = new_price
        spread_pct = random.uniform(0.0005, 0.0015)
        spread = int(mid_price * spread_pct)
        ask_price = int(mid_price + spread / 2)
        bid_price = int(mid_price - spread / 2)

        return TickData(
            stock_code=stock_code,
            price=int(new_price),
            volume=tick_volume,
            cumulative_volume=self._cumulative_volumes[stock_code],
            bid_price=bid_price,
            ask_price=ask_price,
            timestamp=time.time(),
        )


async def run_demo(duration_seconds: float = 600.0) -> None:
    """Run the full demo pipeline: synthetic ticks → features → predictions → UI state.

    Args:
        duration_seconds: How long to run the demo for (default: 10 minutes).
    """
    # Shared state for UI
    ui_state: dict[str, dict] = {}

    def _on_prediction(predictions: dict) -> None:
        """Callback: predictions updated, merge into UI state."""
        ui_state["_predictions"] = predictions
        write_shared_state(ui_state)

    def _on_feature(snapshot: FeatureSnapshot) -> None:
        """Callback: features computed, store for UI and feed to predictor."""
        ui_state[snapshot.stock_code] = snapshot.as_feature_vector()
        write_shared_state(ui_state)
        predictor.on_feature(snapshot)

    # Instantiate the pipeline components
    tick_buffer = TickBuffer()
    feature_engine = FeatureEngine(on_feature=_on_feature)
    predictor = TrendPredictor(on_prediction=_on_prediction)

    # Wire feature engine to tick buffer (called when bars complete)
    tick_buffer._on_bar = feature_engine.register_bar

    # Start tick generator and run it
    generator = SyntheticTickGenerator(settings.TARGET_STOCKS)
    await generator.run(tick_buffer, duration_seconds=duration_seconds, ticks_per_second=2.0)

    logger.info("Demo pipeline complete.")
