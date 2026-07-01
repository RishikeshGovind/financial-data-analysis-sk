"""
Data models for real-time KIS market data.

Provides typed dataclasses for individual ticks and aggregated OHLCV bars.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

# ── H0STCNT0 (real-time trade) field layout ────────────────────────────────
#
# Verified against KIS's official open-trading-api sample
# (examples_user/domestic_stock/domestic_stock_functions_ws.py). Each
# real-time H0STCNT0 record is a '^'-delimited row with these 43 fields,
# in order:
H0STCNT0_FIELDS: tuple[str, ...] = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN",
    "PRDY_VRSS", "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC",
    "STCK_HGPR", "STCK_LWPR", "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL",
    "ACML_TR_PBMN", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU",
    "CTTR", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN", "CCLD_DVSN", "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR", "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE",
    "NEW_MKOP_CLS_CODE", "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE", "MRKT_TRTM_CLS_CODE", "VI_STND_PRC",
)

_PRICE_IDX = H0STCNT0_FIELDS.index("STCK_PRPR")
_VOLUME_IDX = H0STCNT0_FIELDS.index("CNTG_VOL")
_CUM_VOLUME_IDX = H0STCNT0_FIELDS.index("ACML_VOL")
_ASK_IDX = H0STCNT0_FIELDS.index("ASKP1")
_BID_IDX = H0STCNT0_FIELDS.index("BIDP1")


@dataclass
class TickData:
    """A single transaction tick from the KIS WebSocket feed (H0STCNT0).

    Fields are populated from the '^'-delimited real-time transaction record.
    All prices are stored as integers representing the raw KIS value
    (multiply by the price factor to get the actual price).
    """

    stock_code: str
    """6-digit Korean stock code."""

    price: int
    """Current transaction price (raw value)."""

    volume: int
    """Trading volume for this transaction."""

    cumulative_volume: int
    """Cumulative volume for the day."""

    bid_price: int
    """Best bid price."""

    ask_price: int
    """Best ask price."""

    timestamp: float = field(default_factory=time.time)
    """Unix timestamp when the tick was received."""

    @property
    def price_decimal(self) -> Decimal:
        """Price as a Decimal for precise arithmetic."""
        return Decimal(self.price)

    @classmethod
    def from_kis_row(cls, stock_code: str, fields: list[str]) -> TickData:
        """Create a TickData from a parsed KIS H0STCNT0 real-time record.

        Args:
            stock_code: The 6-digit stock code this tick belongs to.
            fields: The full ``^``-delimited field list for one record, in
                ``H0STCNT0_FIELDS`` order (43 fields).

        Returns:
            A populated TickData instance.

        Raises:
            IndexError: If the fields list is too short.
            ValueError: If numeric fields cannot be parsed.
        """
        return cls(
            stock_code=stock_code,
            price=int(fields[_PRICE_IDX]),
            volume=int(fields[_VOLUME_IDX]),
            cumulative_volume=int(fields[_CUM_VOLUME_IDX]),
            bid_price=int(fields[_BID_IDX]),
            ask_price=int(fields[_ASK_IDX]),
        )


@dataclass
class OHLCVBar:
    """Aggregated OHLCV (Open, High, Low, Close, Volume) bar for a period.

    Represents price action over a fixed time window (e.g., 60 seconds).
    """

    stock_code: str
    """6-digit Korean stock code."""

    open_price: int
    """First transaction price in the window."""

    high_price: int
    """Highest transaction price in the window."""

    low_price: int
    """Lowest transaction price in the window."""

    close_price: int
    """Last (most recent) transaction price in the window."""

    volume: int
    """Total volume traded in the window."""

    vwap: Optional[Decimal] = None
    """Volume-Weighted Average Price for this bar (calculated on close)."""

    timestamp: float = field(default_factory=time.time)
    """Unix timestamp of bar close."""

    @classmethod
    def from_ticks(cls, stock_code: str, ticks: list[TickData]) -> OHLCVBar:
        """Aggregate a list of ticks into a single OHLCV bar.

        Args:
            stock_code: The stock code.
            ticks: Chronologically ordered ticks for the period.

        Returns:
            A new OHLCVBar.
        """
        if not ticks:
            raise ValueError("Cannot create OHLCVBar from empty tick list.")

        first = ticks[0]
        last = ticks[-1]
        high = max(t.price for t in ticks)
        low = min(t.price for t in ticks)
        volume = sum(t.volume for t in ticks)
        total_value = sum(t.price * t.volume for t in ticks)

        bar = cls(
            stock_code=stock_code,
            open_price=first.price,
            high_price=high,
            low_price=low,
            close_price=last.price,
            volume=volume,
            timestamp=last.timestamp,
        )

        if volume > 0:
            bar.vwap = Decimal(total_value) / Decimal(volume)

        return bar

    @property
    def spread(self) -> int:
        """Price range (high - low)."""
        return self.high_price - self.low_price

    @property
    def midpoint(self) -> int:
        """(High + Low) / 2 as an integer."""
        return (self.high_price + self.low_price) // 2