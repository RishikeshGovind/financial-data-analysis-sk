# KIS Real-Time Stock Market Analysis

A high-throughput algorithmic trading system that connects to the **Korea Investment & Securities (KIS) Developers API**, streams real-time tick data, calculates rolling technical indicators, and predicts short-term market trends.

## 📋 Architecture

```
┌──────────────────────────────────────────────────────┐
│                    main.py                           │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ Ingestion    │→ │ Feature/ML   │→ │ Streamlit │  │
│  │ (WebSocket)  │  │ Pipeline     │  │ Dashboard │  │
│  └──────────────┘  └──────────────┘  └───────────┘  │
└──────────────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
   ┌────────────┐     ┌──────────────┐
   │ KIS API    │     │ In-Memory    │
   │ (REST/WS)  │     │ Tick Buffer  │
   └────────────┘     └──────────────┘
```

### Layers
1. **Ingestion Layer (Async)** — Persistent WebSocket connection streaming tick-by-tick transaction data.
2. **Data Pipeline / Cache** — Fast in-memory pandas DataFrame buffer acting as a thread-safe queue.
3. **Analytical & ML Engine** — Worker that calculates rolling features (VWAP, Order Book Imbalance, RSI) and runs a LightGBM / rules-based momentum engine for 1-min and 5-min trend predictions.
4. **UI Dashboard** — Streamlit interface auto-refreshing with live ticker, metrics, and predictions.

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- KIS Developers API credentials ([register here](https://apiportal.koreainvestment.com/))

### Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd financialdataanalysis-sk

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your KIS credentials
```

### Configuration

Edit `.env` with your KIS Developers credentials:

| Variable | Description |
|---|---|
| `KIS_APP_KEY` | Your KIS Developers app key |
| `KIS_APP_SECRET` | Your KIS Developers app secret |
| `KIS_CANO` | Your 8-digit account number |
| `KIS_ACNT_PRDT_CD` | Account product code (default: `01`) |
| `KIS_ENV` | `virtual` for simulated trading, `real` for production |
| `TARGET_STOCKS` | Comma-separated stock codes (e.g., `005930,000660`) |

### Running

```bash
# Dry-run: validate auth & config
python main.py --dry-run

# Full pipeline (starts streaming + dashboard)
python main.py

# Dashboard only (if backend already running)
python main.py --streamlit-only
```

## 📁 Project Structure

```
financialdataanalysis-sk/
├── config/
│   ├── __init__.py
│   └── settings.py          # Pydantic-based configuration
├── auth/
│   ├── __init__.py
│   └── kis_auth.py          # OAuth2 token management
├── api/
│   └── __init__.py           # KIS REST client (in progress)
├── data/
│   ├── __init__.py           # Tick processor, cache
│   └── ...
├── features/
│   ├── __init__.py           # Technical indicators
│   └── ...
├── ml/
│   ├── __init__.py           # ML model, rules engine
│   └── ...
├── main.py                   # Application entry point
├── streamlit_app.py          # Streamlit dashboard
├── requirements.txt
├── .env.example
└── README.md
```

## 🧪 Development Phases

| Phase | Description | Status |
|---|---|---|
| **1** | Environment setup & OAuth2 auth | ✅ Complete |
| **2** | Real-time WebSocket client | ⬜ Pending |
| **3** | Feature engineering pipeline | ⬜ Pending |
| **4** | Trend prediction (ML / rules) | ⬜ Pending |
| **5** | Streamlit UI dashboard | ⬜ Skeleton |

## 🛠 Technology Stack

- **Python** 3.10+
- **Async I/O** — `asyncio`, `httpx`, `websockets`
- **Data** — `pandas`, `numpy`
- **ML** — `scikit-learn`, `lightgbm`
- **UI** — `streamlit`
- **Config** — `pydantic-settings`

## ⚠️ Disclaimer

This software is for educational purposes only. Trading stocks involves substantial risk. Do not use this system with real funds without thorough testing and understanding of the risks involved.