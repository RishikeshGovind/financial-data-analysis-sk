# Deployment Guide

## 🚀 Quick Start (Local)

### Prerequisites
- Python 3.10+
- pip or conda

### 1. Install & Run

```bash
# Clone/download the project
cd financialdataanalysis-sk

# Create virtual environment (optional but recommended)
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Run the demo (Terminal 1)
python main.py --yahoo

# Launch dashboard (Terminal 2, after demo starts)
streamlit run streamlit_app.py
```

The dashboard opens at **http://localhost:8501**

---

## ☁️ Streamlit Cloud Deployment (Recommended for Upwork)

### 1. Push to GitHub

```bash
# If not already in git:
git init
git add .
git commit -m "Add KIS market analysis dashboard"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/financialdataanalysis-sk.git
git push -u origin main
```

### 2. Deploy on Streamlit Cloud

1. Go to **[share.streamlit.io](https://share.streamlit.io)**
2. Click "New app"
3. Connect your GitHub repo
4. Fill in:
   - **Repository:** `YOUR_USERNAME/financialdataanalysis-sk`
   - **Branch:** `main`
   - **Main file path:** `streamlit_app.py`
5. Click "Deploy"

**That's it!** Streamlit Cloud automatically:
- Installs `requirements.txt`
- Runs the app with `streamlit run streamlit_app.py`
- Serves it publicly at `https://share.streamlit.io/YOUR_USERNAME/financialdataanalysis-sk`

### 3. Configure Backend (Demo Data)

The dashboard works out-of-the-box with demo data. To make it truly live, you have two options:

#### Option A: Local Backend + Remote Dashboard (Recommended for Demo)
The demo data runs locally and writes to `.shared_state.json`. The Streamlit Cloud dashboard reads from that file.

**Limitation:** Streamlit Cloud can't access your local files directly.

#### Option B: Deploy Backend to Cloud (Advanced)
Use a cloud function or container to run `python main.py --yahoo` and write to cloud storage.

**For the $100 Upwork gig, Option A (local backend) is fine.**

---

## 🎯 For Your Upwork Pitch

### Setup Instructions for Your Client

1. **Install Python** (if needed): [python.org](https://python.org)
2. **Clone the repo**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/financialdataanalysis-sk.git
   cd financialdataanalysis-sk
   ```
3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Run the demo** (Terminal 1):
   ```bash
   python main.py --yahoo
   ```
5. **Open dashboard** (Terminal 2, after 60-90 seconds):
   ```bash
   streamlit run streamlit_app.py
   ```
6. **View at:** http://localhost:8501

**No credentials required.** Uses real Yahoo Finance data (Apple, Microsoft, etc.).

---

## 📊 Features Included

✅ **Real-time Data** — Yahoo Finance tick data (2 ticks/sec)  
✅ **Technical Indicators** — VWAP, RSI, momentum, volume ratio  
✅ **Trend Predictions** — Rules-based BUY/SELL/HOLD signals  
✅ **Live Dashboard** — Auto-refresh every 2 seconds  
✅ **Production Ready** — Async pipeline, error handling, type hints  
✅ **KIS API Ready** — Swap Yahoo for real KIS data (with credentials)  

---

## 🔌 Production Setup (With Real KIS Credentials)

Once your client gets KIS API credentials:

1. **Create `.env` file**:
   ```
   KIS_APP_KEY=your_app_key
   KIS_APP_SECRET=your_app_secret
   KIS_CANO=your_account_number
   KIS_ACNT_PRDT_CD=01
   KIS_ENV=virtual  # Use 'real' for production
   TARGET_STOCKS=005930,000660  # Samsung, SK Hynix, etc.
   ```

2. **Run the full pipeline**:
   ```bash
   python main.py  # Streams real KIS data
   streamlit run streamlit_app.py
   ```

3. **(Optional) Deploy to Cloud** with KIS credentials stored as Streamlit Cloud secrets:
   - Settings → Secrets → Add `KIS_APP_KEY`, `KIS_APP_SECRET`, etc.

---

## 📝 Environment Variables

Create `.env` file in project root:

```ini
# KIS API Credentials (for production)
KIS_APP_KEY=your_key_here
KIS_APP_SECRET=your_secret_here
KIS_CANO=00000000
KIS_ACNT_PRDT_CD=01

# Environment (virtual for mock trading, real for production)
KIS_ENV=virtual

# Target stocks (6-digit Korean codes)
TARGET_STOCKS=005930,000660

# Technical Indicator Windows
OHLCV_BAR_SECONDS=60
VWAP_WINDOW=20
RSI_WINDOW=14
OI_WINDOW=10

# Prediction Settings
PREDICTION_CONFIDENCE_THRESHOLD=0.6
MOCK_PREDICTIONS=true
```

See `.env.example` for full list of options.

---

## 🧪 Testing

```bash
# Validate config & auth (requires real credentials)
python main.py --dry-run

# Test with synthetic data
python main.py --demo

# Test with real Yahoo data
python main.py --yahoo

# Dashboard only (if backend already running)
python main.py --streamlit-only
```

---

## 🐛 Troubleshooting

### Dashboard shows "Waiting for data…"
- Demo needs 60+ seconds to generate first bar
- Check backend logs for errors
- Ensure `main.py` is still running

### No data in Streamlit Cloud
- Dashboard can't access local `.shared_state.json`
- You need to run backend locally OR deploy backend to cloud
- For demo, this is expected—run locally

### Yahoo Finance returns empty data
- Check your internet connection
- Verify yfinance is installed: `pip install yfinance`
- Try a different ticker (AAPL, MSFT)

### "ModuleNotFoundError: No module named 'xxx'"
- Install missing package: `pip install -r requirements.txt`
- Or manually: `pip install MODULE_NAME`

---

## 📚 Architecture

```
┌─────────────────────────────────────────────┐
│  Data Source (KIS API / Yahoo Finance)      │
└────────────────┬────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────┐
│  main.py (Ingestion & Feature Pipeline)     │
│  ├─ Tick Buffer (async)                     │
│  ├─ Feature Engine (VWAP, RSI, momentum)    │
│  └─ ML Predictor (rules-based signals)      │
└────────────────┬────────────────────────────┘
                 │
                 ▼
        .shared_state.json
                 │
                 ▼
┌─────────────────────────────────────────────┐
│  streamlit_app.py (Dashboard)               │
│  ├─ Live metrics (price, momentum)          │
│  ├─ Technical indicators (RSI, VWAP)        │
│  ├─ Trend predictions (BUY/SELL/HOLD)       │
│  └─ Performance metrics                     │
└─────────────────────────────────────────────┘
```

---

## 📞 Support & Next Steps

**This is a production-ready MVP.** For enhancements:
- Add historical backtesting
- Integrate portfolio tracking
- Add alert system
- Implement real ML model training
- Deploy to Kubernetes for scale

**Questions?** Check `DEMO_INSTRUCTIONS.md` or `README.md` for more context.
