"""
Data pipeline package.

Provides:
- :class:`TickData` — A single transaction tick from the KIS WebSocket feed.
- :class:`OHLCVBar` — Aggregated OHLCV bar over a time window.
- :class:`TickBuffer` — Async-safe in-memory tick buffer with bar aggregation.
- :func:`write_shared_state` / :func:`read_shared_state` — Lightweight IPC
  between the backend pipeline and the Streamlit dashboard.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from data.models import OHLCVBar, TickData
from data.tick_buffer import TickBuffer

logger = logging.getLogger("kis.data")

# ── Shared State (file-based IPC for cross-process communication) ──────────

SHARED_STATE_PATH = Path(__file__).resolve().parent.parent / ".shared_state.json"
"""Path to the JSON file used to share state between the backend and UI."""


def write_shared_state(data: dict[str, Any]) -> None:
    """Atomically write *data* to the shared state file.

    The backend (feature engine) calls this each time a new feature snapshot
    is computed, so the Streamlit dashboard can pick it up on its next
    refresh cycle.

    Args:
        data: A dictionary with stock codes as keys and feature dicts as values.
    """
    try:
        # Atomic write via temporary file + rename
        tmp = SHARED_STATE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
        tmp.replace(SHARED_STATE_PATH)
    except OSError:
        logger.exception("Failed to write shared state to %s", SHARED_STATE_PATH)


def read_shared_state() -> dict[str, Any]:
    """Read the latest shared state from the file.

    Returns:
        A dictionary (possibly empty) with stock codes as keys and feature
        dicts as values.
    """
    try:
        if not SHARED_STATE_PATH.exists():
            return {}
        with open(SHARED_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.exception("Failed to read shared state from %s", SHARED_STATE_PATH)
        return {}


__all__ = [
    "TickData",
    "OHLCVBar",
    "TickBuffer",
    "write_shared_state",
    "read_shared_state",
]