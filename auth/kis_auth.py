"""
KIS OAuth2 Token Management.

Handles access token generation, caching, and automatic renewal for the
Korea Investment & Securities (KIS) Developers API.

Token lifecycles:
  - Access tokens expire after 86 400 s (24 h) for REST usage.
  - WebSocket tokens are separate and may have different TTLs.
  - Approval tokens expire after 72 h.

This module implements a token manager that:
  1. Requests a fresh token on first use.
  2. Caches the token in memory (and optionally to a JSON file for
     process restarts).
  3. Monitors token expiry and proactively refreshes before revocation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from config.settings import PROJECT_ROOT, settings

logger = logging.getLogger("kis.auth")

# ── Data Types ───────────────────────────────────────────────────────────


@dataclass
class TokenInfo:
    """Container for KIS OAuth2 token data."""

    access_token: str
    token_type: str = "Bearer"
    expires_at: float = 0.0  # Unix timestamp (wall-clock seconds, time.time())

    @property
    def is_expired(self) -> bool:
        """Check if token is expired or will expire within 5 minutes."""
        return time.time() + 300 >= self.expires_at

    @property
    def expires_in_seconds(self) -> int:
        """Seconds until expiry (clamped to 0)."""
        remaining = self.expires_at - time.time()
        return max(0, int(remaining))

    @classmethod
    def from_api_response(cls, data: dict) -> TokenInfo:
        """Build a TokenInfo from a KIS ``/oauth2/tokenP`` response."""
        expires_in = int(data.get("access_token_token_expired", 86400))
        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + expires_in,
        )

    def to_cache_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_cache_dict(cls, data: dict) -> TokenInfo:
        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=data["expires_at"],
        )


# ── Token Manager ────────────────────────────────────────────────────────


class KISAuth:
    """Manages OAuth2 tokens for the KIS REST and WebSocket APIs.

    Thread-safe, async-first token lifecycle manager.
    Supports file-backed caching so tokens survive process restarts within
    their validity window.

    Usage::

        auth = KISAuth()
        await auth.ensure_token()        # force token generation
        token = auth.token               # current valid token
        headers = auth.auth_headers      # ready-to-use HTTP headers
    """

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        cache_file: str | Path | None = None,
    ) -> None:
        self._app_key = app_key or settings.KIS_APP_KEY
        self._app_secret = app_secret or settings.KIS_APP_SECRET
        self._cache_path = (
            Path(cache_file)
            if cache_file
            else PROJECT_ROOT / ".token_cache.json"
        )

        self._token: Optional[TokenInfo] = None
        self._lock = asyncio.Lock()
        self._renew_task: Optional[asyncio.Task] = None

        # Attempt to hydrate from cache on init
        self._load_from_cache()

    # ── Public Properties ────────────────────────────────────────────────

    @property
    def token(self) -> Optional[TokenInfo]:
        """Current cached token (may be expired)."""
        return self._token

    @property
    def auth_headers(self) -> dict[str, str]:
        """HTTP headers with valid Bearer token.

        Raises ``RuntimeError`` if no token has been acquired yet.
        """
        if self._token is None:
            raise RuntimeError(
                "No token available. Call `await auth.ensure_token()` first."
            )
        return {
            "authorization": (
                f"Bearer {self._token.access_token}"
            ),
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }

    @property
    def has_valid_token(self) -> bool:
        """True if a non-expired token is cached."""
        return self._token is not None and not self._token.is_expired

    # ── Token Acquisition ────────────────────────────────────────────────

    async def ensure_token(self, force_refresh: bool = False) -> TokenInfo:
        """Return a valid token, requesting one if necessary.

        If *force_refresh* is ``True`` a new token is always fetched from the
        API, overriding any cached value.
        """
        async with self._lock:
            if force_refresh or not self.has_valid_token:
                logger.info("Requesting new KIS OAuth2 token …")
                self._token = await self._request_token()
                self._persist_cache()
                logger.info(
                    "Token acquired, expires in %d s",
                    self._token.expires_in_seconds,
                )
            return self._token  # type: ignore[return-value]

    async def revoke_token(self) -> None:
        """Revoke the current token at the KIS API."""
        async with self._lock:
            if self._token is None:
                return
            logger.info("Revoking KIS OAuth2 token …")
            await self._post(
                settings.oauth_revoke_url,
                json={
                    "appkey": self._app_key,
                    "appsecret": self._app_secret,
                    "token": self._token.access_token,
                },
            )
            self._token = None
            self._clear_cache()

    # ── Automatic Renewal ────────────────────────────────────────────────

    def start_auto_renew(self) -> None:
        """Launch a background task that renews the token before expiry.

        The renewal is scheduled at 80 % of the token's TTL.
        Only one renewal task runs at a time.
        """
        if self._renew_task is not None and not self._renew_task.done():
            logger.debug("Auto-renew task already running.")
            return

        async def _renew_loop() -> None:
            while True:
                if self._token is None:
                    await asyncio.sleep(30)
                    continue
                sleep_for = int(self._token.expires_in_seconds * 0.8)
                if sleep_for < 60:
                    sleep_for = 60  # minimum 1 min between checks
                logger.debug("Token auto-renew sleeping %d s …", sleep_for)
                await asyncio.sleep(sleep_for)
                try:
                    await self.ensure_token(force_refresh=True)
                except Exception:
                    logger.exception("Token auto-renew failed, will retry.")

        self._renew_task = asyncio.create_task(_renew_loop())
        logger.info("Token auto-renew task started.")

    def stop_auto_renew(self) -> None:
        """Cancel the background renewal task."""
        if self._renew_task is not None and not self._renew_task.done():
            self._renew_task.cancel()
            self._renew_task = None
            logger.info("Token auto-renew task stopped.")

    # ── Private API Client ───────────────────────────────────────────────

    async def _request_token(self) -> TokenInfo:
        """POST to ``/oauth2/tokenP`` and return parsed TokenInfo."""
        data = await self._post(
            settings.oauth_token_url,
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            },
        )
        return TokenInfo.from_api_response(data)

    @staticmethod
    async def _post(url: str, json: dict) -> dict:
        """Thin POST helper with timeout & error handling."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=json)
            resp.raise_for_status()
            body: dict = resp.json()
            # KIS error responses carry a "code" and "message" field
            if "code" in body and body["code"] != "0":
                raise KISAuthError(
                    code=body["code"],
                    message=body.get("message", body.get("msg", "Unknown error")),
                )
            return body

    # ── Cache Persistence ────────────────────────────────────────────────

    def _load_from_cache(self) -> None:
        """Try to load a previously persisted token from disk."""
        try:
            if self._cache_path.exists():
                raw = self._cache_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                self._token = TokenInfo.from_cache_dict(data)
                if self._token.is_expired:
                    logger.info("Cached token expired, will request new one.")
                    self._token = None
                else:
                    logger.info(
                        "Loaded cached token — expires in %d s",
                        self._token.expires_in_seconds,
                    )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load token cache: %s", exc)
            self._token = None

    def _persist_cache(self) -> None:
        """Persist the current token to a JSON file."""
        if self._token is None:
            return
        try:
            self._cache_path.write_text(
                json.dumps(self._token.to_cache_dict(), indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Could not persist token cache: %s", exc)

    def _clear_cache(self) -> None:
        """Remove the cache file."""
        try:
            self._cache_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove token cache: %s", exc)


# ── Exceptions ───────────────────────────────────────────────────────────


class KISAuthError(Exception):
    """Raised when the KIS API returns a non-zero error code during auth."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")