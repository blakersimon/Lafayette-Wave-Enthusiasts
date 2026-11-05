# 🏄 Louisiana Surf Finder

> **Real-time marine buoy telemetry, multi-source surf forecasting, and consensus intelligence for Holly Beach and Grand Isle, Louisiana.**

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/framework-FastAPI%200.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Deployment](https://img.shields.io/badge/deploy-Railway%20Nixpacks-0B0D0E.svg)](https://railway.app/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 🌊 Overview

Surfing on the Louisiana Gulf Coast is episodic, micro-tidal, and heavily modulated by extreme coastal bathymetry. Unlike open-ocean coastlines where deep ocean swells break directly onto steep continental shores, Louisiana waves are generated predominantly by autumn, winter, and spring continental cold fronts and tropical cyclones across fetch-limited shallow waters.

Due to the ultra-shallow continental shelf (extending 80–100 nautical miles offshore in Cameron Parish), wave energy suffers severe viscous mud and bottom-friction damping. Standard national forecast models frequently over-predict wave face heights or miss the brief, critical post-frontal offshore wind windows ("clean-offs").

**Louisiana Surf Finder** solves this by:
1. **Pairing inshore meteorological and tide sensors** with **offshore directional wave buoys** (NOAA NDBC & CO-OPS).
2. **Reconciling multi-source forecast models and scrapers** (Open-Meteo Marine, NWS Coastal Zones, Surf Captain, Surf-Forecast.com).
3. **Applying physical shoaling attenuation** ($K_{net} = 0.65$ for Holly Beach, $0.82$ for Grand Isle).
4. **Computing an objective 0–100 Surf Quality Score** incorporating breaking wave height, non-linear period weighting, wind directional alignment relative to shoreline bearing, and tide stage.
5. **Detecting upcoming 48-hour "Golden Windows"** when wind swings offshore to groom clean peelers.
6. **Delivering a zero-build, map-focused Bento UI** with Leaflet.js, glowing pulsing spot markers, rotatable vector arrows, and 48-hour Chart.js timelines.

---

## 🏛️ System Architecture

```
                               ┌──────────────────────────────────────────────┐
                               │            Client Browser (UI)               │
                               │  Leaflet 1.9.4 + Chart.js 4.4 + ES6 Modules  │
                               └──────────────────────┬───────────────────────┘
                                                      │ HTTP / JSON
                                                      ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                                FastAPI Backend (app.main:app)                              │
│                                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                                 Lifespan Context Manager                             │  │
│  │   • Startup: Init CacheService -> Warm Cache in Background -> Start Async Refresher  │  │
│  │   • Shutdown: Cancel Background Task -> Close HTTPX Client Sessions cleanly          │  │
│  └──────────────────────────────────────────┬───────────────────────────────────────────┘  │
│                                             │                                              │
│                                             ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                                 REST API Router (app/api/routes.py)                  │  │
│  │    GET /api/spots          GET /api/spots/{id}          GET /api/spots/{id}/forecast │  │
│  │    GET /api/buoys          GET /api/health              GET / (HTML Template Shell)  │  │
│  └──────────────┬───────────────────────────┬───────────────────────────┬───────────────┘  │
│                 │                           │                           │                  │
│                 ▼                           ▼                           ▼                  │
│  ┌─────────────────────────────┐ ┌─────────────────────┐ ┌─────────────────────────────┐  │
│  │    Consensus Engine         │ │    Cache Service    │ │   Recommendation Engine     │  │
│  │ • Bathymetric Shoaling (Knet)│ │ • In-Memory TTL     │ │ • "When is it up?" Golden   │  │
│  │ • 0-100 Surf Scoring        │ │ • Async Lock        │ │   Window Detector (48h)     │  │
│  │ • Median Anomaly Rejection  │ │ • Disk Fallback JSON│ │ • Offshore Clean-Off Window │  │
│  │ • Variance Confidence       │ │ • Cache Observability│ │   Identifier                │  │
│  └──────────────▲──────────────┘ └──────────┬──────────┘ └─────────────────────────────┘  │
│                 │                           │                                              │
│                 │                           ▼                                              │
│  ┌──────────────┴───────────────────────────────────────────────────────────────────────┐  │
│  │                                  Ingestion Services                                   │  │
│  │   • buoy_service.py: NOAA NDBC (.txt / .spec) & CO-OPS Tides/Wind                    │  │
│  │   • weather_service.py: Open-Meteo Marine API & NWS Coastal Zones (GMZ450 & GMZ553)  │  │
│  │   • scraper_service.py: Surf Captain & Surf-Forecast.com resilient web scrapers      │  │
│  └──────────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Repository Structure

```
louisiana-surf-finder/
├── app/
│   ├── __init__.py                     # Package declaration (__version__ = "1.0.0")
│   ├── main.py                         # FastAPI app factory, lifespan manager, static mount & routes
│   ├── config.py                       # Pydantic BaseSettings, environment variables, spot metadata
│   ├── models/
│   │   ├── __init__.py                 # Schema package init
│   │   └── schemas.py                  # Pydantic v2 schemas (NormalizedMarineReading, etc.)
│   ├── services/
│   │   ├── __init__.py                 # Service package init
│   │   ├── cache_service.py            # Async-safe in-memory TTL cache with disk fallback loader
│   │   ├── buoy_service.py             # NOAA NDBC (.txt / .spec) & CO-OPS API ingestion client
│   │   ├── scraper_service.py          # Surf Captain & Surf-Forecast resilient HTML scrapers
│   │   ├── weather_service.py          # Open-Meteo Marine API & NWS Coastal Zone client
│   │   ├── consensus_engine.py         # Shoaling attenuation, 0-100 scoring, multi-source fusion
│   │   └── recommendation_engine.py    # 48-hour golden window detector ("When is it up?")
│   ├── api/
│   │   ├── __init__.py                 # API package init
│   │   └── routes.py                   # REST API endpoints (/api/spots, /api/buoys, /api/health)
│   ├── static/
│   │   ├── css/
│   │   │   └── styles.css              # Glassmorphic Bento-box dark marine theme
│   │   └── js/
│   │       ├── app.js                  # Central UI orchestrator & state manager
│   │       ├── map.js                  # Leaflet.js map, pulsing markers, vector arrows
│   │       ├── drawer.js               # Spot detail drawer, source comparison table, logistics
│   │       └── chart.js                # Chart.js 4.4 48-hour wave & wind timeline graph
│   ├── templates/
│   │   └── index.html                  # Jinja2 single-page HTML5 shell
│   └── data/
│       └── fallbacks/                  # Immutable static JSON snapshots for offline resilience
│           ├── buoys_fallback.json
│           ├── forecasts_fallback.json
│           └── spots_fallback.json
├── docs/
│   ├── ARCHITECTURE_PLAN.md            # Comprehensive architectural specification (R1)
│   └── DEPLOYMENT_PORKBUN.md           # Porkbun DNS and custom domain setup guide
├── scripts/
│   └── setup_porkbun_dns.py            # Automated Porkbun API v3 DNS configuration script
├── tests/
│   ├── conftest.py                     # Pytest fixtures and test harnesses
│   ├── test_buoy_service.py            # NDBC and CO-OPS parser tests
│   ├── test_scraper_service.py         # HTML scraper tests
│   ├── test_weather_service.py         # Open-Meteo and NWS client tests
│   ├── test_consensus_engine.py        # Scoring and shoaling tests
│   ├── test_api_routes.py              # REST endpoint integration tests
│   └── test_e2e.py                     # End-to-end integration and fallback tests
├── railway.toml                        # Railway Nixpacks deployment configuration
├── Procfile                            # Railway process declaration
├── requirements.txt                    # Pinned production Python dependencies
├── .env.example                        # Environment variable configuration template
└── README.md                           # Project documentation
```

---

## ⚡ Quickstart

### Prerequisites
- Python 3.11+
- Virtual environment tool (`venv` or `uv`)

### 1. Clone & Setup Environment
```bash
git clone https://github.com/blake-simon/louisiana-surf-finder.git
cd louisiana-surf-finder

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env if configuring custom ports or Porkbun API keys
```

### 3. Run Locally
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Open [http://localhost:8000](http://localhost:8000) in your web browser.

### 4. Run Automated Test Suite
```bash
pytest tests/ -v
```

---

## 🔌 API Endpoints Reference

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | `GET` | Single-page map interface shell. |
| `GET /api/spots` | `GET` | List all monitored surf spots with summary consensus conditions. |
| `GET /api/spots/{spot_id}` | `GET` | Detailed consensus forecast, golden window, and source comparison for a spot. |
| `GET /api/spots/{spot_id}/forecast` | `GET` | 48-hour hourly sequence points for Chart.js timeline rendering. |
| `GET /api/buoys` | `GET` | Live telemetry readings from all monitored offshore buoys and inshore stations. |
| `GET /api/health` | `GET` | Operational health probe (cache status, uptime, spots loaded, buoys monitored). |

---

## 🚀 Deployment

The project is pre-configured for automated deployment to **Railway** via Nixpacks:
1. Push repository to GitHub.
2. In Railway, click **New Project** -> **Deploy from GitHub repo**.
3. Railway detects `railway.toml` and `Procfile`, installs dependencies, and deploys.
4. Active health probe at `/api/health` validates zero-downtime rolling deploys.
5. To configure a custom domain on **Porkbun**, execute:
   ```bash
   python scripts/setup_porkbun_dns.py --subdomain louisiana-surf --target <your-app>.up.railway.app
   ```

---

## 📄 License

MIT License. Designed and built with pride for Louisiana surfers.
