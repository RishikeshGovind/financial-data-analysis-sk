"""
Demo mode with real Yahoo Finance data.

Fetches recent 1-minute OHLCV bars from Yahoo Finance, converts them to
realistic tick data, and feeds through the full analysis pipeline.
No credentials or Korean account required.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import yfinance as yf

from config.settings import settings
from data import TickBuffer, write_shared_state
from data.models import TickData
from features import FeatureEngine, FeatureSnapshot
from ml import TrendPredictor

logger = logging.getLogger("kis.yahoo_demo")


class YahooTickGenerator:
    """Fetches real OHLCV data from Yahoo Finance and converts to tick stream.

    Works with KIS-style 6-digit codes (e.g. '005930'), fetching the matching
    Korea Exchange ticker from Yahoo ('005930.KS') but keying all emitted ticks
    and buffers by the plain KIS code so downstream components (settings,
    dashboard) stay consistent.
    """

    def __init__(self, codes: list[str], lookback_days: int = 5):
        """Fetch real data for demo.

        Args:
            codes: List of KIS 6-digit stock codes (e.g., ['005930', '000660']).
                Each is mapped to its Yahoo ticker by appending '.KS'.
            lookback_days: How many days of historical data to fetch.
        """
        self._codes = codes
        # KIS code -> Yahoo ticker (Korea Exchange suffix)
        self._yahoo_ticker = {code: f"{code}.KS" for code in codes}
        self._lookback_days = lookback_days
        self._bars: dict[str, list] = {}
        self._cumulative_volumes: dict[str, int] = {}
        self._tick_count: int = 0

        self._fetch_data()

    def _fetch_data(self) -> None:
        """Fetch recent hourly OHLCV data from Yahoo Finance.

        Uses yfinance ``period`` (anchored to the latest available trading
        day) rather than explicit dates, so the demo always replays the most
        recent real market data.
        """
        # Hourly data is available for up to ~730 days; grab a comfortable
        # window so a full loop has plenty of bars to stream.
        period = "1mo" if self._lookback_days <= 31 else "3mo"

        logger.info(
            "Fetching Yahoo Finance data (period=%s, 1h): %s",
            period,
            ", ".join(self._yahoo_ticker.values()),
        )

        for code in self._codes:
            ticker = self._yahoo_ticker[code]
            try:
                # 1-hour bars, anchored to the most recent session.
                data = yf.Ticker(ticker).history(period=period, interval="1h")
                if data is None or data.empty:
                    logger.warning("No data fetched for %s", ticker)
                    self._bars[code] = []
                    continue

                # Convert to list of bars
                # Handle MultiIndex columns from yfinance (when ticker is in column name)
                bars = []
                for idx, row in data.iterrows():
                    try:
                        timestamp = idx.timestamp()

                        # Try both column formats: "Open" and ("Open", "TICKER")
                        if ("Open", ticker) in data.columns:
                            open_price = float(row[("Open", ticker)])
                            high_price = float(row[("High", ticker)])
                            low_price = float(row[("Low", ticker)])
                            close_price = float(row[("Close", ticker)])
                            volume = int(row[("Volume", ticker)])
                        else:
                            open_price = float(row["Open"])
                            high_price = float(row["High"])
                            low_price = float(row["Low"])
                            close_price = float(row["Close"])
                            volume = int(row["Volume"])

                        bars.append({
                            "timestamp": timestamp,
                            "open": open_price,
                            "high": high_price,
                            "low": low_price,
                            "close": close_price,
                            "volume": volume,
                        })
                    except (ValueError, TypeError, KeyError, IndexError) as e:
                        logger.debug("Skipping malformed bar for %s: %s", ticker, e)
                        continue

                self._bars[code] = bars
                self._cumulative_volumes[code] = sum(b["volume"] for b in bars)
                logger.info("Fetched %d bars for %s (%s)", len(bars), ticker, code)

            except Exception as exc:
                logger.exception("Failed to fetch data for %s: %s", ticker, exc)
                self._bars[code] = []

    async def run(
        self,
        tick_buffer: TickBuffer,
        loop: bool = True,
        ticks_per_second: float = 2.0,
    ) -> None:
        """Generate ticks from fetched bars and feed into buffer.

        Args:
            tick_buffer: The buffer to feed ticks into.
            loop: If True, restart from the beginning after finishing.
            ticks_per_second: Tick emission rate.
        """
        interval = 1.0 / ticks_per_second
        iteration = 0

        while True:
            iteration += 1
            logger.info("Yahoo demo iteration %d (loop=%s)", iteration, loop)

            for code in self._codes:
                bars = self._bars.get(code, [])
                if not bars:
                    logger.warning("No bars available for %s", code)
                    continue

                for bar in bars:
                    ticks_this_bar = self._ticks_from_bar(code, bar)
                    for tick in ticks_this_bar:
                        await tick_buffer.add_tick(tick)
                        self._tick_count += 1
                        await asyncio.sleep(interval)

            if not loop:
                break

            logger.info("Yahoo demo completed iteration %d, restarting...", iteration)
            await asyncio.sleep(2)  # Brief pause between iterations

        logger.info("Yahoo demo finished (%d ticks emitted).", self._tick_count)

    def _ticks_from_bar(self, code: str, bar: dict) -> list[TickData]:
        """Convert a single OHLCV bar into 10-20 realistic ticks.

        Distributes the bar's volume across multiple ticks and traces a price
        path from open → close that respects the high/low bounds.
        """
        open_price = bar["open"]
        close_price = bar["close"]
        high_price = bar["high"]
        low_price = bar["low"]
        total_volume = bar["volume"]
        bar_timestamp = bar["timestamp"]

        # Generate 10-20 ticks per bar
        num_ticks = random.randint(10, 20)
        tick_times = [
            bar_timestamp + (i / (num_ticks - 1)) * 60 if num_ticks > 1 else bar_timestamp
            for i in range(num_ticks)
        ]

        # Distribute volume across ticks (exponential weighting: more volume early)
        tick_volumes = []
        remaining = total_volume
        for i in range(num_ticks):
            # Favor earlier ticks
            weight = (num_ticks - i) / (num_ticks * (num_ticks + 1) / 2)
            vol = max(1, int(remaining * weight))
            tick_volumes.append(vol)
            remaining -= vol
        if remaining > 0:
            tick_volumes[-1] += remaining

        # Generate realistic price path through the bar
        price_path = [open_price]
        for i in range(1, num_ticks):
            t = i / (num_ticks - 1)
            # Weighted interpolation: favor high/low extremes at midpoint
            trend_price = open_price + t * (close_price - open_price)
            # Add some deviation towards high/low
            if 0.4 < t < 0.6:
                # Middle of bar: move towards high or low
                if high_price > low_price:
                    deviation = (random.random() - 0.5) * (high_price - low_price) * 0.5
                    price = trend_price + deviation
                    price = max(low_price, min(high_price, price))
                else:
                    price = trend_price
            else:
                price = trend_price
            price_path.append(price)

        # Adjust path to respect high/low
        price_path = [max(low_price, min(high_price, p)) for p in price_path]

        # Generate ticks
        ticks = []
        cum_vol = self._cumulative_volumes.get(code, 0)
        for i in range(num_ticks):
            price = price_path[i]
            volume = tick_volumes[i]
            cum_vol += volume

            # Bid-ask spread: 0.05-0.15% of price
            spread_pct = random.uniform(0.0005, 0.0015)
            spread = max(1, int(price * spread_pct))
            bid_price = int(price - spread / 2)
            ask_price = int(price + spread / 2)

            tick = TickData(
                stock_code=code,
                price=int(price),
                volume=volume,
                cumulative_volume=cum_vol,
                bid_price=bid_price,
                ask_price=ask_price,
                timestamp=tick_times[i],
            )
            ticks.append(tick)

        self._cumulative_volumes[code] = cum_vol
        return ticks


async def run_yahoo_demo(
    codes: list[str] | None = None,
    lookback_days: int = 5,
    loop: bool = True,
    ticks_per_second: float = 2.0,
) -> None:
    """Run the full demo pipeline with real Yahoo Finance data.

    Args:
        codes: List of KIS 6-digit stock codes to stream (default: the
            configured ``TARGET_STOCKS``, e.g. Samsung/SK Hynix). Each is
            fetched from Yahoo as ``<code>.KS``.
        lookback_days: Days of historical data to fetch (default: 5).
        loop: If True, restart after finishing (default: True).
        ticks_per_second: Tick emission rate (default: 2.0).
    """
    if codes is None:
        codes = list(settings.TARGET_STOCKS)

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

    # Fetch and stream real data
    generator = YahooTickGenerator(codes, lookback_days=lookback_days)
    await generator.run(tick_buffer, loop=loop, ticks_per_second=ticks_per_second)
