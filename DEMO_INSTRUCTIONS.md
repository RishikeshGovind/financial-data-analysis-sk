# KIS Real-Time Stock Market Analysis — Demo Mode

## Quick Start (No Credentials Required)

Run the demo with **synthetic real-time ticks**:

```bash
python main.py --demo
```

This will:
1. Generate realistic synthetic stock ticks for Samsung (005930) and SK Hynix (000660)
2. Run the full analysis pipeline:
   - **Tick buffering** → aggregate ticks into 60-second bars
   - **Feature engineering** → compute VWAP, RSI, momentum, volume ratio
   - **Rules-based prediction** → generate BUY/SELL/HOLD signals
3. Write live data to a shared JSON file
4. Run for 10 minutes, then exit

## View the Dashboard

In a **separate terminal**, launch the Streamlit dashboard:

```bash
python main.py --streamlit-only
```

Or directly:

```bash
streamlit run streamlit_app.py
```

The dashboard will:
- Auto-refresh every 2 seconds (showing live "market data")
- Display technical indicators (VWAP, RSI, momentum)
- Show trend predictions (1m and 5m horizons)
- Update live as the demo backend generates ticks

## What You'll See

**Top-level metrics:**
- Live price and 1-minute momentum for each stock
- VWAP across the window

**Technical Indicators (per stock):**
- RSI (14-period): oversold/neutral/overbought zones
- Momentum (1m, 5m): percentage change
- Order-book imbalance (approximated)
- Volume ratio: current vol / average vol

**Trend Prediction:**
- Rules-based signals: STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL
- Confidence scores
- Probability of upward movement (1m and 5m)

## Command-Line Options

```bash
# Full real-time pipeline (requires KIS credentials)
python main.py

# Demo mode with synthetic data (no credentials)
python main.py --demo

# Validate authentication without streaming
python main.py --dry-run

# Dashboard only (if backend is already running)
python main.py --streamlit-only

# Control logging verbosity
python main.py --demo --log-level DEBUG
```

## Demo Behavior

**Tick Generation:**
- Generates ~2 ticks per second per stock
- Price follows a random walk with slight upward drift (±0.5% per tick)
- Bid-ask spread: 0.05–0.15% of mid-price
- Volume: 100–1000 shares per tick
- Cumulative volume: realistic trading volume accumulation

**Bar Aggregation:**
- Ticks aggregate into 60-second OHLCV bars
- Each bar triggers feature computation
- Features feed into the rules-based predictor

**Predictions:**
- Rules-based: uses RSI, momentum, volume thresholds
- No ML model needed (demo uses mock predictions by default)
- Signals update each time a bar completes

## For Your Upwork Demo

This is production-ready to show a client:

1. Start the demo: `python main.py --demo`
2. Wait 60-90 seconds for the first bar/features to appear
3. Launch the dashboard: `streamlit run streamlit_app.py`
4. Watch the dashboard update live — live price, indicators, predictions
5. Press Ctrl+C to stop the demo

**Key selling points:**
- ✅ Full real-time pipeline (data → features → predictions)
- ✅ No external dependencies or credentials needed (demo mode)
- ✅ Live-updating Streamlit dashboard
- ✅ Plugs into real KIS API when credentials provided
- ✅ Ready for production use with Korean brokerage accounts

## Customizing the Demo

Edit `demo.py` or the call in `main.py`:

```python
# Change duration (default 600 seconds / 10 minutes)
await run_demo(duration_seconds=1800.0)  # 30 minutes

# Change tick frequency (default 2 ticks/sec)
await generator.run(tick_buffer, duration_seconds=600, ticks_per_second=5.0)
```

Or edit `.env` to change target stocks, bar window, indicator windows, etc.

## Troubleshooting

**Dashboard shows "Waiting for data …"?**
- Demo needs at least 60 seconds to complete the first bar
- Check that `--demo` backend is still running: look for log messages about tick generation

**No predictions appearing?**
- Predictions appear once features are computed
- Features require at least one complete bar (~60 seconds)
- Patience! The first 60 seconds is always silent

**Performance issues?**
- Reduce tick generation rate: edit `demo.py` line ~117
- Or reduce refresh rate in `streamlit_app.py` (currently 2 seconds)
