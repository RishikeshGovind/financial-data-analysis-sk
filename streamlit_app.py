"""
KIS Real-Time Market Analysis Dashboard.

Combines an instant view of a Korean stock (company profile + historical
price chart from Yahoo Finance) with the live streaming analysis produced by
the backend pipeline (`main.py --yahoo` for the demo, or `main.py` with KIS
credentials in production).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from config.settings import settings
from data import read_shared_state
from dashboard_data import get_company_profile, get_price_history, stock_name

logger = logging.getLogger("kis.streamlit")

REFRESH_SECONDS = 2

# ── Self-contained backend ────────────────────────────────────────────────
# Streamlit Cloud only runs this file — there's no second terminal to start
# `main.py --yahoo` in. So the dashboard starts the Yahoo Finance tick
# generator itself, once per server process, in a background thread. Every
# browser session that opens this app shares the same running backend.

_backend_lock = threading.Lock()
_backend_started = False


def _start_backend_once() -> None:
    global _backend_started
    with _backend_lock:
        if _backend_started:
            return
        _backend_started = True

    def _run() -> None:
        from yahoo_demo import run_yahoo_demo

        try:
            asyncio.run(
                run_yahoo_demo(
                    codes=list(settings.TARGET_STOCKS),
                    lookback_days=5,
                    loop=True,
                    ticks_per_second=2.0,
                )
            )
        except Exception:
            logger.exception("Background Yahoo demo backend crashed.")

    threading.Thread(target=_run, name="yahoo-backend", daemon=True).start()
    logger.info("Started Yahoo Finance backend thread.")


_start_backend_once()

# ── Page config & light card styling ─────────────────────────────────────

st.set_page_config(
    page_title="KIS Market Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .card{
        background:#ffffff;
        padding:1.1rem 1.4rem;
        border-radius:10px;
        box-shadow:0 2px 6px rgba(0,0,0,.06);
        margin-bottom:1.2rem;
      }
      div[data-testid="stMetric"]{margin:0.2rem 0;}
    </style>
    """,
    unsafe_allow_html=True,
)

if "start_time" not in st.session_state:
    st.session_state.start_time = time.time()


# ── Formatting helpers ───────────────────────────────────────────────────

def won(val: Any) -> str:
    return "—" if val is None else f"₩{val:,.0f}"


def pct(val: Any) -> str:
    return "—" if val is None else f"{val:+.2f}%"


def num(val: Any, d: int = 2) -> str:
    return "—" if val is None else f"{val:,.{d}f}"


def big_won(val: Any) -> str:
    if val is None:
        return "—"
    if val >= 1e12:
        return f"₩{val / 1e12:,.1f}T"
    if val >= 1e8:
        return f"₩{val / 1e8:,.1f}억"
    return f"₩{val:,.0f}"


SIGNAL_ICON = {
    "STRONG_BUY": "🟢🟢", "BUY": "🟢", "HOLD": "⚪",
    "SELL": "🔴", "STRONG_SELL": "🔴🔴",
}


def conf_label(c: float) -> str:
    return "High" if c >= 0.8 else "Medium" if c >= 0.5 else "Low" if c >= 0.2 else "None"


# ── Sidebar: stock selector + config ─────────────────────────────────────

with st.sidebar:
    st.markdown("## 📈 KIS Dashboard")
    st.caption(f"Environment · **{settings.KIS_ENV.value.upper()}**")

    st.divider()
    selected = st.selectbox(
        "Select stock",
        options=list(settings.TARGET_STOCKS),
        format_func=lambda c: f"{stock_name(c)}  ({c})",
    )
    chart_period = st.radio(
        "Price history",
        options=[("1M", 30), ("3M", 90), ("6M", 180), ("1Y", 365)],
        index=2,
        format_func=lambda x: x[0],
        horizontal=True,
    )[1]

    st.divider()
    st.markdown("#### ⚙️ Indicator windows")
    st.caption(f"Bar window · **{settings.OHLCV_BAR_SECONDS}s**")
    st.caption(f"VWAP · **{settings.VWAP_WINDOW} bars**")
    st.caption(f"RSI · **{settings.RSI_WINDOW} bars**")

    st.markdown("#### 🤖 Prediction engine")
    st.caption("Mode · **" + ("Rules-based" if settings.MOCK_PREDICTIONS else "ML model") + "**")
    st.caption(f"Confidence threshold · **{settings.PREDICTION_CONFIDENCE_THRESHOLD:.0%}**")

    st.divider()
    st.caption(
        "Demo streams real Korea-Exchange history via Yahoo Finance. "
        "Production uses the KIS real-time WebSocket feed."
    )


# ── Company header + profile ─────────────────────────────────────────────

profile = get_company_profile(selected)
history = get_price_history(selected, days=chart_period)
live = read_shared_state()
live_feat = live.get(selected, {})
predictions = live.get("_predictions", {}).get(selected, {})

st.title("📊 Real-Time Korean Market Analysis")

head_l, head_r = st.columns([3, 1])
with head_l:
    st.markdown(f"### 🏢 {profile['name']}  ·  `{profile['ticker']}`")
    meta = " · ".join(m for m in [profile.get("sector"), profile.get("industry")] if m)
    if meta:
        st.markdown(f"**{meta}**")
with head_r:
    if profile.get("website"):
        st.markdown(f"[Company website]({profile['website']})")

if profile.get("summary"):
    with st.expander("About this company ℹ️", expanded=False):
        st.write(profile["summary"])

st.divider()

# ── Hero metrics ─────────────────────────────────────────────────────────

spot = live_feat.get("close_price")
if spot is None and not history.empty:
    spot = float(history["Close"].iloc[-1])

hist_ret = None
if not history.empty and len(history) > 1:
    hist_ret = (history["Close"].iloc[-1] / history["Close"].iloc[0] - 1) * 100

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric("Spot Price", won(spot), delta=pct(live_feat.get("momentum_1m")))
    st.caption("Latest traded price (live tick if streaming, else last close).")
with m2:
    st.metric("Market Cap", big_won(profile.get("market_cap")))
    st.caption("Total equity value on the Korea Exchange.")
with m3:
    st.metric(f"{chart_period}d Return", pct(hist_ret))
    st.caption("Price change over the selected history window.")
with m4:
    streaming = len([k for k in live.keys() if k != "_predictions"]) > 0
    st.metric("Backend", "🟢 Streaming" if streaming else "🟡 Idle")
    up = int(time.time() - st.session_state.start_time)
    st.caption(f"Dashboard uptime · {up // 60}m {up % 60}s")

st.divider()

# ── Price chart (instant, from real history) ─────────────────────────────

st.subheader("📉 Price History & Volume")

if history.empty:
    st.warning("Could not load price history for this stock from Yahoo Finance.")
else:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25], vertical_spacing=0.03,
    )
    fig.add_trace(
        go.Candlestick(
            x=history.index,
            open=history["Open"], high=history["High"],
            low=history["Low"], close=history["Close"],
            name="OHLC",
            increasing_line_color="#d62728", decreasing_line_color="#1f77b4",
        ),
        row=1, col=1,
    )
    # Live VWAP reference line (from the streaming pipeline), if available.
    vwap = live_feat.get("vwap")
    if vwap:
        fig.add_hline(
            y=vwap, line_dash="dash", line_color="#ff7f0e",
            annotation_text=f"Live VWAP ₩{vwap:,.0f}", annotation_position="top left",
            row=1, col=1,
        )
    fig.add_trace(
        go.Bar(x=history.index, y=history["Volume"], name="Volume",
               marker_color="#9467bd", opacity=0.5),
        row=2, col=1,
    )
    fig.update_layout(
        template="plotly_white", height=460, showlegend=False,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_rangeslider_visible=False,
    )
    fig.update_yaxes(title_text="Price (₩)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Daily candles from the Korea Exchange (red = up, blue = down, following the "
        "Korean market convention). The dashed orange line marks the **live VWAP** from "
        "the real-time pipeline, so you can see where the current session's volume-weighted "
        "price sits against recent history."
    )

st.divider()

# ── Live streaming section (auto-refreshing) ─────────────────────────────

st.subheader("⚡ Live Analysis")
st.caption(
    "Streamed tick-by-tick and recomputed every "
    f"{REFRESH_SECONDS}s: 1-minute bars → technical indicators → trend signals."
)


@st.fragment(run_every=REFRESH_SECONDS)
def live_panel() -> None:
    state = read_shared_state()
    feat = state.get(selected, {})
    preds = state.get("_predictions", {}).get(selected, {})

    if not feat:
        st.info(
            "⏳ Backend is warming up — fetching Yahoo Finance data and "
            "building the first live bar (≈60-90s after the app starts)."
        )
        return

    st.caption(f"Last update · {datetime.now().strftime('%H:%M:%S')}")

    # Indicators
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        rsi = feat.get("rsi")
        st.metric("RSI (14)", num(rsi, 1))
        if rsi is not None:
            st.caption("🔴 Overbought" if rsi >= 70 else "🟢 Oversold" if rsi <= 30 else "⚪ Neutral")
    with k2:
        st.metric("Live VWAP", won(feat.get("vwap")))
        st.caption("Volume-weighted avg price")
    with k3:
        st.metric("Momentum 1m", pct(feat.get("momentum_1m")))
        st.caption("1-minute price change")
    with k4:
        st.metric("Volume Ratio", num(feat.get("volume_ratio"), 2))
        st.caption("Current vs. average volume")

    # Predictions
    if preds:
        st.markdown("##### 🤖 Trend Signal")
        cols = st.columns(2)
        for col, horizon in zip(cols, ("1m", "5m")):
            p = preds.get(horizon, {})
            sig = p.get("signal", "HOLD")
            with col:
                st.metric(
                    f"{horizon} horizon",
                    f"{SIGNAL_ICON.get(sig, '⚪')} {sig}",
                    delta=f"{conf_label(p.get('confidence', 0.0))} confidence",
                    delta_color="off",
                )
                st.caption(f"P(up) · {pct(p.get('probability_up', 0.5) * 100)}  ·  source: {p.get('source', '—')}")
    else:
        st.info("Predictions appear once enough bars have accumulated.")

    with st.expander("Raw feature vector"):
        vec = {k: v for k, v in feat.items()}
        st.dataframe(
            pd.DataFrame(sorted(vec.items()), columns=["Feature", "Value"]),
            use_container_width=True, hide_index=True,
        )


live_panel()

st.divider()
st.caption(
    "Data · Yahoo Finance (demo) / KIS API (production)  |  "
    "Indicators · VWAP, RSI, momentum, volume, spread  |  "
    "Signals · rules-based, 1-min & 5-min horizons"
)

logger.info("Dashboard rendered (%s).", settings.KIS_ENV.value)
