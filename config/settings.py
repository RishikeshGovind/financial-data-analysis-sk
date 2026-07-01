"""
KIS API Configuration Management.

Uses pydantic-settings to load and validate environment variables.
Supports .env file loading via python-dotenv.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, ClassVar, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Project root directory (two levels up from this file)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


class KISEnvironment(str, Enum):
    """Korea Investment & Securities API environment types."""

    REAL = "real"
    VIRTUAL = "virtual"  # 모의투자 (simulated trading)


class KISSettings(BaseSettings):
    """Global configuration for KIS API connectivity and application behaviour.

    Loads values from environment variables or a .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── KIS API Credentials ──────────────────────────────────────────────
    KIS_APP_KEY: str = Field(
        default="your_app_key_here",
        description="KIS Developers app key (8-40 chars).",
        min_length=8,
        max_length=40,
    )
    KIS_APP_SECRET: str = Field(
        default="your_app_secret_here",
        description="KIS Developers app secret.",
        min_length=8,
    )
    KIS_CANO: str = Field(
        default="00000000",
        description="KIS account number (8 digits).",
        min_length=8,
        max_length=8,
    )
    KIS_ACNT_PRDT_CD: str = Field(
        default="01",
        description="KIS account product code (usually '01').",
        min_length=2,
        max_length=2,
    )

    # ── API Environment ──────────────────────────────────────────────────
    KIS_ENV: KISEnvironment = Field(
        default=KISEnvironment.VIRTUAL,
        description="API environment: 'real' or 'virtual'.",
    )

    # ── Target Stocks ────────────────────────────────────────────────────
    TARGET_STOCKS: Annotated[List[str], NoDecode] = Field(
        default=["005930", "000660"],
        description="Comma-separated list of 6-digit Korean stock codes.",
    )

    # ── WebSocket ────────────────────────────────────────────────────────
    WS_RECONNECT_DELAY_SEC: int = Field(
        default=3,
        ge=1,
        le=60,
        description="Delay (seconds) before WebSocket reconnect attempt.",
    )
    WS_MAX_RECONNECTS: int = Field(
        default=0,
        ge=0,
        le=50,
        description="Max reconnect attempts (0 = unlimited).",
    )
    WS_HEARTBEAT_INTERVAL_SEC: int = Field(
        default=30,
        ge=5,
        le=120,
        description="WebSocket ping interval in seconds.",
    )

    # ── REST Rate Limiting ───────────────────────────────────────────────
    REST_CALLS_PER_SECOND: float = Field(
        default=15.0,
        ge=1.0,
        le=20.0,
        description="KIS REST API rate limit (calls/sec).",
    )

    # ── Data Pipeline ────────────────────────────────────────────────────
    OHLCV_BAR_SECONDS: int = Field(
        default=60,
        ge=5,
        le=300,
        description="Rolling OHLCV bar window in seconds.",
    )
    MAX_TICK_BUFFER: int = Field(
        default=10_000,
        description="Maximum number of ticks kept in memory per stock.",
    )

    # ── Feature Engineering ──────────────────────────────────────────────
    VWAP_WINDOW: int = Field(
        default=20,
        ge=1,
        description="Rolling window for VWAP calculation (bars).",
    )
    RSI_WINDOW: int = Field(
        default=14,
        ge=1,
        description="Rolling window for RSI calculation (bars).",
    )
    OI_WINDOW: int = Field(
        default=10,
        ge=1,
        description="Rolling window for order-book imbalance (bars).",
    )

    # ── ML / Rules Engine ────────────────────────────────────────────────
    PREDICTION_CONFIDENCE_THRESHOLD: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Minimum confidence to emit a prediction signal.",
    )
    MOCK_PREDICTIONS: bool = Field(
        default=True,
        description="Use rules-based mock predictions when ML model is unavailable.",
    )

    # ── Derived Properties ───────────────────────────────────────────────

    @property
    def kis_base_url(self) -> str:
        """Return the KIS REST API base URL for the chosen environment."""
        if self.KIS_ENV == KISEnvironment.REAL:
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"

    @property
    def kis_ws_url(self) -> str:
        """Return the KIS WebSocket endpoint for the chosen environment."""
        if self.KIS_ENV == KISEnvironment.REAL:
            return "ws://ops.koreainvestment.com:21000"
        return "ws://ops.koreainvestment.com:31000"  # Separate port for virtual (모의투자)

    @property
    def oauth_token_url(self) -> str:
        """Return the full OAuth2 token endpoint."""
        return f"{self.kis_base_url}/oauth2/tokenP"

    @property
    def oauth_revoke_url(self) -> str:
        """Return the full OAuth2 token revocation endpoint."""
        return f"{self.kis_base_url}/oauth2/revokeP"

    @property
    def oauth_approval_url(self) -> str:
        """Return the full OAuth2 WebSocket approval endpoint."""
        return f"{self.kis_base_url}/oauth2/Approval"

    # ── Validators ───────────────────────────────────────────────────────

    @field_validator("KIS_CANO")
    @classmethod
    def validate_cano(cls, v: str) -> str:
        """Ensure account number is exactly 8 digits."""
        if not v.isdigit() or len(v) != 8:
            raise ValueError(f"KIS_CANO must be exactly 8 digits, got '{v}'")
        return v

    @field_validator("TARGET_STOCKS", mode="before")
    @classmethod
    def parse_target_stocks(cls, v: str | list[str]) -> list[str]:
        """Allow TARGET_STOCKS to be a comma-separated string or a list."""
        if isinstance(v, str):
            parts = [s.strip() for s in v.split(",") if s.strip()]
            return parts
        return v

    # ── Class-level helpers ──────────────────────────────────────────────

    DEFAULT_ENV_PATH: ClassVar[Path] = DEFAULT_ENV_PATH

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "KISSettings":
        """Factory: load settings from an optional .env file path.

        If *env_file* is ``None`` the default ``.env`` in the project root
        will be used (if it exists).
        """
        if env_file is None:
            env_file = cls.DEFAULT_ENV_PATH
        return cls(_env_file=str(env_file))  # type: ignore[call-arg]


# Module-level singleton -- importers use the same cached instance
settings = KISSettings.load()