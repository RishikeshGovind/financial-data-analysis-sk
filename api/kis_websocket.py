"""
KIS Real-Time WebSocket Client.

Connects to the KIS Developers API WebSocket endpoint, authenticates with an
approval key, subscribes to real-time transaction data (H0STCNT0) for the
configured target stocks, and feeds incoming ticks into the TickBuffer.

Protocol overview (KIS WebSocket), verified against KIS's official
open-trading-api sample (examples_user/kis_auth.py, examples_user/
domestic_stock/domestic_stock_functions_ws.py):
  1. POST /oauth2/Approval (REST) → get approval_key.
  2. Connect to the environment's WS endpoint (real vs. virtual use
     different ports — see ``config.settings.kis_ws_url``).
  3. Send a registration handshake per stock::
        {"header": {"approval_key": "...", "custtype": "P",
                     "tr_type": "1", "content-type": "utf-8"},
         "body": {"input": {"tr_id": "H0STCNT0", "tr_key": "<stock_code>"}}}
  4. Receive real-time data frames shaped
     ``<encrypt_flag>|<tr_id>|<data_count>|<data>`` where *data* is one or
     more ``^``-delimited H0STCNT0 records (see ``data.models.H0STCNT0_FIELDS``).
  5. Respond to server PINGPONG control frames
     (``{"header": {"tr_id": "PINGPONG"}}``) with a WebSocket PONG frame.
  6. Auto-reconnect on disconnect (configurable retry).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import httpx
import websockets
import websockets.asyncio.client
from websockets.asyncio.client import ClientConnection

from config.settings import settings
from data.models import H0STCNT0_FIELDS, TickData
from data.tick_buffer import TickBuffer

logger = logging.getLogger("kis.websocket")

# ── Constants ─────────────────────────────────────────────────────────────

TR_ID_REALTIME_TRADE = "H0STCNT0"
"""Real-time transaction data TR ID."""

RECONNECT_DELAY = 3  # default, overridden by settings
MAX_RECONNECTS = 0  # 0 = unlimited


# ── Custom Exceptions ─────────────────────────────────────────────────────


class KISWebSocketError(Exception):
    """Raised for WebSocket-level errors that are not recoverable."""


class KISApprovalError(Exception):
    """Raised when obtaining the WebSocket approval key fails."""


# ── WebSocket Client ──────────────────────────────────────────────────────


class KISWebSocketClient:
    """Async WebSocket client for KIS real-time market data.

    Usage::

        client = KISWebSocketClient(tick_buffer)
        await client.connect()
        await client.run()  # runs forever (or until cancelled)

    The client automatically:
    - Obtains an approval key on startup.
    - Connects to the KIS WebSocket endpoint.
    - Registers and subscribes to all target stocks.
    - Parses incoming messages and feeds :class:`TickData` into the buffer.
    - Handles pings from the server.
    - Reconnects with configurable delay on failure.
    """

    def __init__(
        self,
        tick_buffer: TickBuffer,
        approval_key: str | None = None,
        stocks: list[str] | None = None,
    ) -> None:
        self._buffer = tick_buffer
        self._approval_key = approval_key
        self._stocks = stocks or list(settings.TARGET_STOCKS)
        self._ws: ClientConnection | None = None
        self._should_stop = False

        # Reconnect state
        self._reconnect_delay = settings.WS_RECONNECT_DELAY_SEC
        self._max_reconnects = settings.WS_MAX_RECONNECTS
        self._reconnect_count = 0

        # Statistics
        self._ticks_received = 0
        self._bytes_received = 0
        self._start_time: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Obtain approval key (if needed) and open the WebSocket connection.

        Raises:
            KISApprovalError: If the approval key request fails.
            KISWebSocketError: If the WebSocket connection fails.
        """
        if self._approval_key is None:
            self._approval_key = await self._fetch_approval_key()

        logger.info("Connecting to KIS WebSocket at %s …", settings.kis_ws_url)
        self._ws = await websockets.asyncio.client.connect(
            settings.kis_ws_url,
            ping_interval=settings.WS_HEARTBEAT_INTERVAL_SEC,
            ping_timeout=10,
            close_timeout=5,
            max_size=2 ** 20,  # 1 MB max message size
        )
        logger.info("WebSocket connection established.")

    async def run(self) -> None:
        """Run the message processing loop (blocks until cancelled or stopped).

        Handles subscription registration, incoming message parsing, and
        automatic reconnection.
        """
        self._start_time = time.monotonic()
        self._should_stop = False

        while not self._should_stop:
            try:
                if self._ws is None:
                    await self._reconnect_or_raise()

                # Register/subscribe for all target stocks
                await self._register_subscriptions()

                # Message loop
                async for message in self._ws:
                    await self._handle_message(message)

            except websockets.ConnectionClosed as exc:
                logger.warning(
                    "WebSocket connection closed (code=%s): %s",
                    exc.code,
                    exc.reason,
                )
                self._ws = None
                if not self._should_stop:
                    await self._reconnect_or_raise()

            except asyncio.CancelledError:
                logger.info("WebSocket client cancelled.")
                self._should_stop = True
                break

            except Exception:
                logger.exception("Unexpected error in WebSocket loop.")
                self._ws = None
                if not self._should_stop:
                    await asyncio.sleep(self._reconnect_delay)
                    continue
                break

    async def stop(self) -> None:
        """Gracefully stop the WebSocket client."""
        self._should_stop = True
        if self._ws is not None:
            await self._ws.close()

    @property
    def stats(self) -> dict:
        """Return running statistics about the connection."""
        elapsed = time.monotonic() - self._start_time if self._start_time else 0.0
        return {
            "ticks_received": self._ticks_received,
            "bytes_received": self._bytes_received,
            "uptime_seconds": elapsed,
            "connected": self._ws is not None and not self._ws.closed,
            "reconnect_count": self._reconnect_count,
            "stocks": list(self._stocks),
        }

    # ── Approval Key ──────────────────────────────────────────────────────

    @staticmethod
    async def _fetch_approval_key() -> str:
        """POST to /oauth2/Approval and return the approval key string.

        Raises:
            KISApprovalError: On API failure.
        """
        url = settings.oauth_approval_url
        payload = {
            "grant_type": "client_credentials",
            "appkey": settings.KIS_APP_KEY,
            "secretkey": settings.KIS_APP_SECRET,
        }

        logger.info("Requesting WebSocket approval key from %s …", url)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body: dict = resp.json()

        approval_key = body.get("approval_key")
        if not approval_key:
            raise KISApprovalError(
                f"Failed to obtain approval key: {body.get('message', body)}"
            )

        logger.info("Approval key obtained (len=%d).", len(approval_key))
        return approval_key

    # ── Subscription ──────────────────────────────────────────────────────

    async def _register_subscriptions(self) -> None:
        """Register/subscribe to real-time trade data for all target stocks.

        The KIS WebSocket protocol uses a header/body registration message::

            {"header": {"approval_key": "...", "custtype": "P",
                         "tr_type": "1", "content-type": "utf-8"},
             "body": {"input": {"tr_id": "H0STCNT0", "tr_key": "<stock_code>"}}}

        This must be sent once per stock code after connecting.
        """
        if self._ws is None:
            return

        for stock in self._stocks:
            reg_msg = json.dumps({
                "header": {
                    "approval_key": self._approval_key,
                    "custtype": "P",
                    "tr_type": "1",  # 1 = register, 2 = unregister
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {
                        "tr_id": TR_ID_REALTIME_TRADE,
                        "tr_key": stock,
                    }
                },
            })
            logger.debug("Subscribing to %s (H0STCNT0) …", stock)
            await self._ws.send(reg_msg)

        logger.info(
            "Subscribed to %d stock(s): %s",
            len(self._stocks),
            ", ".join(self._stocks),
        )

    # ── Message Handling ──────────────────────────────────────────────────

    async def _handle_message(self, raw: str | bytes) -> None:
        """Route an incoming WebSocket message to the appropriate handler.

        KIS messages arrive as strings (JSON or pipe-delimited) or bytes.
        """
        if isinstance(raw, bytes):
            self._bytes_received += len(raw)
            text = raw.decode("utf-8", errors="replace")
        else:
            self._bytes_received += len(raw.encode("utf-8"))
            text = raw

        # Ignore empty messages
        if not text.strip():
            return

        # Check if this is a JSON message (ping, registration response, error)
        if text.startswith("{"):
            await self._handle_json_message(text)
        else:
            await self._handle_data_message(text)

    async def _handle_json_message(self, text: str) -> None:
        """Process a JSON-formatted control/system message.

        KIS control messages are shaped::

            {"header": {"tr_id": "...", "tr_key": "...", "encrypt": "N"},
             "body": {"rt_cd": "0", "msg1": "SUBSCRIBE SUCCESS", ...}}

        PING messages arrive as ``{"header": {"tr_id": "PINGPONG"}}`` (no
        body) and must be answered with a WebSocket PONG control frame
        echoing the original payload.
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Received malformed JSON message: %.120s", text)
            return

        header = data.get("header", {})
        tr_id = header.get("tr_id")

        if tr_id == "PINGPONG":
            logger.debug("Received server ping, sending pong.")
            try:
                if self._ws and not self._ws.closed:
                    await self._ws.pong(text.encode("utf-8"))
            except Exception:
                logger.exception("Failed to respond to PINGPONG.")
            return

        body = data.get("body")
        if body is not None:
            if body.get("rt_cd") == "0":
                logger.info("KIS WS ack [%s]: %s", tr_id, body.get("msg1"))
            else:
                logger.warning(
                    "KIS WS error [%s]: [%s] %s",
                    tr_id, body.get("rt_cd"), body.get("msg1"),
                )
            return

        logger.debug("Unhandled JSON message: %.200s", text)

    async def _handle_data_message(self, text: str) -> None:
        """Parse a real-time data frame and feed ticks into the buffer.

        Frames are shaped ``<encrypt_flag>|<tr_id>|<data_count>|<data>``.
        *data* contains one or more H0STCNT0 records; when batched
        (``data_count`` > 1) records are newline-separated, each a
        ``^``-delimited list of ``H0STCNT0_FIELDS``-length fields.
        """
        parts = text.split("|")
        if len(parts) < 4:
            logger.debug("Data frame missing expected sections: %.120s", text)
            return

        encrypt_flag, tr_id, payload = parts[0], parts[1], parts[3]

        if tr_id != TR_ID_REALTIME_TRADE:
            logger.debug("Ignoring data for unhandled tr_id=%s", tr_id)
            return

        if encrypt_flag == "1":
            # H0STCNT0 (public market data) is never encrypted in practice;
            # encryption is only used for private execution-notice TRs,
            # which this client does not subscribe to.
            logger.warning(
                "Received encrypted %s payload; decryption is not "
                "supported, dropping.", tr_id,
            )
            return

        field_count = len(H0STCNT0_FIELDS)
        records = payload.split("\n") if "\n" in payload else [payload]
        if len(records) == 1 and records[0].count("^") + 1 > field_count:
            # Multiple records concatenated without newlines: chunk the
            # flat field list back into fixed-size records.
            flat_fields = payload.split("^")
            records = [
                "^".join(flat_fields[i:i + field_count])
                for i in range(0, len(flat_fields), field_count)
            ]

        for record in records:
            if not record:
                continue
            fields = record.split("^")
            if len(fields) < field_count:
                logger.debug(
                    "Short H0STCNT0 record (%d/%d fields): %.120s",
                    len(fields), field_count, record,
                )
                continue

            stock_code = fields[0].strip()
            if stock_code not in self._stocks:
                logger.debug("Ignoring data for unknown stock: %s", stock_code)
                continue

            try:
                tick = TickData.from_kis_row(stock_code, fields)
            except (IndexError, ValueError) as exc:
                logger.warning("Failed to parse tick for %s: %s", stock_code, exc)
                continue

            self._ticks_received += 1
            await self._buffer.add_tick(tick)

    # ── Reconnection ──────────────────────────────────────────────────────

    async def _reconnect_or_raise(self) -> None:
        """Attempt to reconnect with exponential backoff.

        Raises:
            KISWebSocketError: If max reconnects exceeded (and >0).
        """
        self._reconnect_count += 1

        if 0 < self._max_reconnects <= self._reconnect_count:
            raise KISWebSocketError(
                f"Max reconnects ({self._max_reconnects}) exceeded."
            )

        delay = self._reconnect_delay * min(2 ** (self._reconnect_count - 1), 30)
        logger.info(
            "Reconnecting in %.0f s (attempt %d) …",
            delay,
            self._reconnect_count,
        )
        await asyncio.sleep(delay)

        # Fetch a fresh approval key before reconnecting
        self._approval_key = await self._fetch_approval_key()

        # Close old connection if still lingering
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

        await self.connect()