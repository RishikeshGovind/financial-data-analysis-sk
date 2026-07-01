"""
KIS REST & WebSocket API client package.

Provides:
- :class:`KISWebSocketClient` — Real-time WebSocket data ingestion.
- :class:`KISApprovalError` — Approval key acquisition failure.
- :class:`KISWebSocketError` — WebSocket-level errors.
"""
from __future__ import annotations

from api.kis_websocket import KISApprovalError, KISWebSocketClient, KISWebSocketError

__all__ = [
    "KISWebSocketClient",
    "KISApprovalError",
    "KISWebSocketError",
]