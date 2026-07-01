"""
Cached Yahoo Finance helpers for the dashboard.

Provides company profiles and historical price series for KIS 6-digit codes
(mapped to Korea Exchange tickers, ``<code>.KS``). Results are cached so the
2-second live-refresh loop never hammers Yahoo.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
import yfinance as yf

# Human-readable names for common KIS codes (fallback if Yahoo omits them).
STOCK_NAMES = {
    "005930": "Samsung Electronics",
    "000660": "SK Hynix",
    "035420": "NAVER",
    "005380": "Hyundai Motor",
    "051910": "LG Chem",
    "035720": "Kakao",
    "000270": "Kia",
    "068270": "Celltrion",
    "005490": "POSCO Holdings",
    "105560": "KB Financial",
}


def yahoo_ticker(code: str) -> str:
    """Map a KIS 6-digit code to its Korea Exchange Yahoo ticker."""
    return f"{code}.KS"


def _period_for_days(days: int) -> str:
    """Map a day count to the closest yfinance ``period`` string.

    Using ``period`` (rather than explicit start/end dates) anchors the
    request to whatever the latest available trading day is, so the chart
    always shows the most recent data.
    """
    if days <= 7:
        return "5d"
    if days <= 31:
        return "1mo"
    if days <= 93:
        return "3mo"
    if days <= 186:
        return "6mo"
    if days <= 366:
        return "1y"
    return "2y"


@st.cache_data(show_spinner=False, ttl=3600)
def get_company_profile(code: str) -> dict:
    """Return name/sector/industry/summary/currency/market cap for a code."""
    ticker = yahoo_ticker(code)
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}

    return {
        "code": code,
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or STOCK_NAMES.get(code, code),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "summary": info.get("longBusinessSummary"),
        "currency": info.get("currency", "KRW"),
        "market_cap": info.get("marketCap"),
        "website": info.get("website"),
    }


@st.cache_data(show_spinner=False, ttl=1800)
def get_price_history(code: str, days: int = 180) -> pd.DataFrame:
    """Return a daily OHLCV dataframe covering roughly the last *days* days.

    Anchored to the latest available trading day via yfinance ``period``.
    Columns are flattened to Open/High/Low/Close/Volume with a DatetimeIndex.
    Returns an empty frame on failure.
    """
    ticker = yahoo_ticker(code)
    try:
        df = yf.Ticker(ticker).history(period=_period_for_days(days), interval="1d")
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # A plain .history() call returns simple columns, but guard for MultiIndex.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    return df[keep].dropna()


def stock_name(code: str) -> str:
    """Best-effort display name without a network call for the common cases."""
    return STOCK_NAMES.get(code, get_company_profile(code)["name"])
