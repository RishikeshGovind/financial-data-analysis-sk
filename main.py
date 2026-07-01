"""
KIS Real-Time Stock Market Analysis Application.

Entry-point that orchestrates the full pipeline:
  1. Initialises authentication with the KIS API.
  2. Launches the async WebSocket data ingestion layer.
  3. Runs the feature-engineering / ML pipeline as a background worker.
  4. Spawns the Streamlit UI dashboard.

Usage:
    python main.py                    # Full pipeline
    python main.py --dry-run          # Auth check only (no data streaming)
    python main.py --streamlit-only   # Launch dashboard only (expects
                                      #   running backend)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on the path for sibling imports
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from config.settings import settings

logger = logging.getLogger("kis.main")


# ── Argument Parsing ─────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KIS Real-Time Stock Market Analysis",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Authenticate & report success, but do not start streaming.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in demo mode with synthetic ticks (no KIS credentials required).",
    )
    parser.add_argument(
        "--yahoo",
        action="store_true",
        help="Run in demo mode with real Yahoo Finance data (no credentials required).",
    )
    parser.add_argument(
        "--streamlit-only",
        action="store_true",
        help="Only launch the Streamlit dashboard (backend must be running).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser.parse_args(argv)


# ── Logging Setup ────────────────────────────────────────────────────────


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=(
            "[%(asctime)s] %(levelname)-8s "
            "%(name)s:%(lineno)d — %(message)s"
        ),
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


# ── Core Application Logic ────────────────────────────────────────────────


async def _async_init() -> None:
    """Async startup: initialise auth, create shared state, etc."""
    from auth import KISAuth

    logger.info("Initialising KIS authentication …")
    auth = KISAuth()
    token = await auth.ensure_token()
    logger.info(
        "Token obtained (expires in %d s). Starting auto-renew …",
        token.expires_in_seconds,
    )
    auth.start_auto_renew()

    logger.info(
        "Configuration loaded:\n"
        "  Environment : %s\n"
        "  Target stocks : %s\n"
        "  Bar window    : %d s",
        settings.KIS_ENV.value,
        ", ".join(settings.TARGET_STOCKS),
        settings.OHLCV_BAR_SECONDS,
    )


async def _async_dry_run() -> None:
    """Validate authentication & config, then exit."""
    try:
        await _async_init()
        logger.info("Dry-run completed successfully.")
    except Exception:
        logger.exception("Dry-run failed.")
        sys.exit(1)


async def _async_demo() -> None:
    """Run demo mode with synthetic ticks (no KIS credentials needed)."""
    from demo import run_demo

    logger.info(
        "Demo mode: synthetic ticks for %d stock(s). Duration: 10 min.\n"
        "  Stocks: %s\n"
        "  Press Ctrl+C to stop.",
        len(settings.TARGET_STOCKS),
        ", ".join(settings.TARGET_STOCKS),
    )

    try:
        await run_demo(duration_seconds=600.0)
    except KeyboardInterrupt:
        logger.info("Demo stopped by user.")
    except Exception:
        logger.exception("Demo failed.")
        sys.exit(1)


async def _async_yahoo_demo() -> None:
    """Run demo mode with real Yahoo Finance data (no credentials needed)."""
    from yahoo_demo import run_yahoo_demo

    logger.info(
        "Yahoo Finance demo mode: real Korean market data.\n"
        "  Stocks: %s (fetched from Yahoo as <code>.KS, looped)\n"
        "  Press Ctrl+C to stop.",
        ", ".join(settings.TARGET_STOCKS),
    )

    try:
        await run_yahoo_demo(
            codes=list(settings.TARGET_STOCKS),
            lookback_days=5,
            loop=True,
            ticks_per_second=2.0,
        )
    except KeyboardInterrupt:
        logger.info("Yahoo demo stopped by user.")
    except Exception:
        logger.exception("Yahoo demo failed.")
        sys.exit(1)


async def _async_full() -> None:
    """Full pipeline: auth, WebSocket streaming, feature engine, UI."""
    await _async_init()

    from api import KISWebSocketClient
    from data import TickBuffer, write_shared_state
    from features import FeatureEngine
    from ml import TrendPredictor

    # ── Shared state for UI ──────────────────────────────────────────────
    # Collect feature snapshots and write them to the shared state file
    # so the Streamlit dashboard can pick them up.
    _ui_state: dict[str, dict] = {}

    # ── Initialise Trend Predictor (Phase 4) ─────────────────────────────
    logger.info("Initialising trend prediction engine …")

    def _on_prediction(predictions: dict[str, dict]) -> None:
        """Callback invoked each time predictions are updated."""
        logger.debug(
            "Predictions updated for %d stock(s)",
            len(predictions),
        )
        # Merge prediction data into the UI state under a 'predictions' key
        _ui_state["_predictions"] = predictions
        write_shared_state(_ui_state)

    predictor = TrendPredictor(on_prediction=_on_prediction)
    logger.info("TrendPredictor initialised (mock=%s)", settings.MOCK_PREDICTIONS)

    # ── Initialise Feature Engine ────────────────────────────────────────
    logger.info("Initialising feature engineering pipeline …")

    def _on_feature(snapshot: "FeatureSnapshot") -> None:
        """Callback invoked each time a new feature snapshot is computed."""
        logger.debug(
            "Features updated for %s: RSI=%s VWAP=%s M1=%s",
            snapshot.stock_code,
            snapshot.rsi,
            snapshot.vwap,
            snapshot.momentum_1m,
        )
        # Store feature vector for UI
        _ui_state[snapshot.stock_code] = snapshot.as_feature_vector()
        write_shared_state(_ui_state)
        # Also feed the feature snapshot to the predictor
        predictor.on_feature(snapshot)

    feature_engine = FeatureEngine(on_feature=_on_feature)
    logger.info(
        "FeatureEngine initialised: VWAP_window=%d RSI_window=%d OI_window=%d",
        settings.VWAP_WINDOW,
        settings.RSI_WINDOW,
        settings.OI_WINDOW,
    )

    # ── Initialise Tick Buffer (with bar callback → feature engine) ───────
    logger.info("Initialising tick buffer …")
    tick_buffer = TickBuffer(on_bar=feature_engine.register_bar)

    # ── Initialise WebSocket Client ───────────────────────────────────────
    logger.info("Initialising WebSocket client …")
    ws_client = KISWebSocketClient(tick_buffer)

    # Launch the WebSocket client as a background task
    ws_task = asyncio.create_task(ws_client.run(), name="ws-client")

    # Give the client a moment to connect
    await asyncio.sleep(2)

    logger.info(
        "WebSocket client launched. Streaming %d stock(s): %s\n"
        "  Bar window : %d s\n"
        "  Feature windows : VWAP=%d RSI=%d OI=%d\n"
        "  Press Ctrl+C to stop.",
        len(ws_client.stats["stocks"]),
        ", ".join(ws_client.stats["stocks"]),
        settings.OHLCV_BAR_SECONDS,
        settings.VWAP_WINDOW,
        settings.RSI_WINDOW,
        settings.OI_WINDOW,
    )

    # Keep running until cancelled
    try:
        await ws_task
    except asyncio.CancelledError:
        logger.info("WebSocket task cancelled.")
    except Exception:
        logger.exception("WebSocket task exited with error.")
    finally:
        logger.info("Shutting down WebSocket client …")
        await ws_client.stop()

    # Log final feature engine stats
    logger.info(
        "Feature engine stats: %d snapshots produced across %d stock(s).",
        feature_engine.stats["snapshots_produced"],
        feature_engine.stats["stocks_tracked"],
    )

    logger.info("Full pipeline shutdown complete.")


# ── Entry Point ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    """Synchronous entry-point called from CLI."""
    args = _parse_args(argv)
    _configure_logging(args.log_level)

    if args.dry_run:
        asyncio.run(_async_dry_run())
        return

    if args.demo:
        asyncio.run(_async_demo())
        return

    if args.yahoo:
        asyncio.run(_async_yahoo_demo())
        return

    if args.streamlit_only:
        logger.info("Streamlit-only mode: launching dashboard …")
        # Defer Streamlit import so the CLI mode works without it installed.
        import streamlit.web.bootstrap as st_bootstrap

        st_bootstrap.run(
            str(_HERE / "streamlit_app.py"),
            "streamlit run",
            [],
            flag_options={},
        )
        return

    asyncio.run(_async_full())


if __name__ == "__main__":
    main()