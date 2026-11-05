# Louisiana Surf Finder: Comprehensive System Architecture & Engineering Plan

**Document Version:** 1.0.0  
**Status:** Approved Architectural Specification  
**Target Locations:** Holly Beach (Cameron Parish, LA) & Grand Isle (Jefferson Parish, LA)  
**Deliverable Tracking:** Milestone 1 (Requirement R1, R2, R3, R4, R5)  
**Author:** Worker M1 (Architecture & System Design Author)  

---

## 1. Executive Summary & Project Mission

The **Louisiana Surf Finder** is an asynchronous, high-concurrency, zero-build web platform engineered to deliver accurate, multi-source surf forecasts and live marine intelligence for the unique coastal conditions of **Holly Beach** and **Grand Isle, Louisiana**.

Surfing on the Louisiana Gulf Coast is episodic, micro-tidal, and heavily modulated by extreme coastal bathymetry. Unlike open-ocean coastlines where deep ocean groundswells break directly onto continental shores, Louisiana waves are generated predominantly by meteorological events—namely autumn, winter, and spring continental cold fronts and late-summer/fall tropical cyclones—across fetch-limited shallow waters.

Due to the ultra-shallow continental shelf (extending 80–100 nautical miles offshore in western Louisiana), wave energy suffers severe viscous mud and bottom-friction damping. Standard national surf forecast models frequently over-predict nearshore wave energy or fail to capture the brief, critical post-frontal offshore wind clean-off windows ("clean-offs").

To solve this, the **Louisiana Surf Finder** combines:
1. **Real-time Inshore Telemetry & Offshore Wave Buoys**: Dual-sensor pairing using NOAA National Data Buoy Center (NDBC) spectral feeds (`.spec`) and NOAA Center for Operational Oceanographic Products and Services (CO-OPS) tide and wind stations.
2. **Multi-Source Scraping & Forecast Models**: Ingesting Open-Meteo Marine (WaveWatch III / ECMWF), National Weather Service (NWS) Marine Coastal Zones (`GMZ450` & `GMZ553`), Surf Captain, and Surf-Forecast.com.
3. **Bathymetric Shoaling Attenuation Model**: Translating deepwater buoy energy across shallow coastal shelves ($K_{net} = 0.65$ for Holly Beach, $0.82$ for Grand Isle).
4. **Deterministic 0–100 Surf Quality Score**: A calibrated mathematical model integrating breaking wave height, non-linear period weighting, wind directional alignment relative to shoreline bearing, and diurnal tide state.
5. **Consensus Reconciliation & Anomaly Rejection**: Filtering aberrant forecast spikes ($|H_i - M| > 2.5\text{ ft}$) and computing standard-deviation variance confidence ratings ($30\% - 98\%$).
6. **"When is it up?" 48-Hour Golden Window Detector**: Automatically isolating upcoming optimal 3–8 hour surf sessions.
7. **Map-Focused Glassmorphic Bento UI**: A zero-build Vanilla ES6 JS + Leaflet.js 1.9.4 interface with animated pulsing spot markers, rotatable vector overlays, interactive buoy popups, and a 48-hour Chart.js timeline.
8. **Resilient Dual-Tier Caching & Automated Deployment**: In-memory async-safe TTL caching backed by pre-seeded disk JSON fallbacks for 100% uptime, ready for Railway deployment and Porkbun custom domain DNS automation.

---

## 2. Component Breakdown & System Architecture

### 2.1 System Architecture Diagram

```
                               ┌─────────────────────────────────────────────────────────┐
                               │                    Client Browser                       │
                               │        Vanilla ES6 JavaScript + Leaflet.js 1.9.4        │
                               │        Chart.js 4.4.1 + Dark Bento CSS (Zero Build)     │
                               └────────────────────────────┬────────────────────────────┘
                                                            │ HTTP / JSON
                                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                             FastAPI Backend (app.main:app)                                             │
│                                                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                                              Lifespan Context Manager                                            │  │
│  │   • Startup: Initialize CacheService -> Load Disk Fallbacks -> Trigger Async Warming -> Start Refresher Loop     │  │
│  │   • Shutdown: Cancel Refresher Task -> Close HTTPX AsyncClient Sessions Cleanly                                  │  │
│  └────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────┘  │
│                                                           │                                                            │
│                                                           ▼                                                            │
│  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                                          REST API Router (app/api/routes.py)                                     │  │
│  │     GET /api/spots          GET /api/spots/{id}          GET /api/spots/{id}/forecast                            │  │
│  │     GET /api/buoys          GET /api/health              GET / (HTML Template Shell)                             │  │
│  └────────────────────────────┬───────────────────────────┬───────────────────────────┬─────────────────────────────┘  │
│                               │                           │                           │                                │
│                               ▼                           ▼                           ▼                                │
│  ┌──────────────────────────────────────────┐ ┌───────────────────────┐ ┌───────────────────────────────────────────┐  │
│  │             Consensus Engine             │ │     Cache Service     │ │           Recommendation Engine             │  │
│  │  • Coastal Shoaling Translation (Knet)   │ │  • In-Memory TTL      │ │  • "When is it up?" Golden Window Detector  │  │
│  │  • Quantitative 0-100 Surf Scoring       │ │  • Async Lock Guard   │ │  • Post-Frontal Clean-Off Identifier        │  │
│  │  • Median Anomaly Filter (|H-M|>2.5ft)   │ │  • Fallback JSON Disk │ │  • 48h Hourly Sliding Window Scanner        │  │
│  │  • Cross-Source Variance Confidence      │ │  • Observability Stats│ │                                             │  │
│  └────────────────────▲─────────────────────┘ └───────────┬───────────┘ └───────────────────────────────────────────┘  │
│                       │                                   │                                                            │
│                       │                                   ▼                                                            │
│  ┌────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                                                Ingestion Services                                                │  │
│  │                                                                                                                  │  │
│  │   ┌──────────────────────────────┐ ┌──────────────────────────────┐ ┌─────────────────────────────────────────┐   │  │
│  │   │       buoy_service.py        │ │      weather_service.py      │ │           scraper_service.py            │   │  │
│  │   │   • NOAA NDBC Text (.txt)    │ │   • Open-Meteo Marine API    │ │   • Surf Captain Scraper (HB & GI)      │   │  │
│  │   │   • NDBC Spectral (.spec)    │ │   • NWS Marine Coastal Zones │ │   • Surf-Forecast.com Scraper (HB)      │   │  │
│  │   │   • NOAA CO-OPS Tides & Wind │ │     (GMZ450 & GMZ553)        │ │   • Resilient HTML Parsing & Regex      │   │  │
│  │   └──────────────┬───────────────┘ └──────────────┬───────────────┘ └────────────────────┬────────────────────┘   │  │
│  └──────────────────┼────────────────────────────────┼──────────────────────────────────────┼────────────────────────┘  │
└─────────────────────┼────────────────────────────────┼──────────────────────────────────────┼───────────────────────────┘
                      ▼                                ▼                                      ▼
            ┌───────────────────┐            ┌───────────────────┐                  ┌───────────────────┐
            │   NOAA NDBC &     │            │   Open-Meteo &    │                  │  Surf Captain &   │
            │  CO-OPS Gateways  │            │  NWS Marine API   │                  │   Surf-Forecast   │
            └───────────────────┘            └───────────────────┘                  └───────────────────┘
```

### 2.2 Asynchronous Lifecycle & Background Worker Pattern

The application utilizes Python 3.11+'s `contextlib.asynccontextmanager` for clean lifespan management:
1. **Startup**:
   - `CacheService` immediately loads bundled fallback JSON files from `app/data/fallbacks/` into memory. This guarantees that initial HTTP requests receive 200 OK responses within <5 milliseconds without blocking on external networks.
   - Launches a non-blocking background task to warm the live cache from external APIs.
   - Spawns the recurring background task `background_cache_refresher()`, which updates buoy observations every 10 minutes (600s) and forecast models every 30 minutes (1800s).
2. **Shutdown**:
   - Sends cancellation signals to the background worker task and awaits its exit.
   - Closes shared `httpx.AsyncClient` HTTP connection pools cleanly.

---

## 3. Technology Stack Selection & Rationale

| Layer | Selected Technology | Alternative Considered | Selection Rationale |
|---|---|---|---|
| **Backend Framework** | **FastAPI 0.110+** (Python 3.11+) | Flask / Django | Native asynchronous request handling, high throughput with `asyncio`, automatic OpenAPI documentation, clean dependency injection, and native Pydantic v2 validation. |
| **ASGI Web Server** | **Uvicorn 0.28+** | Gunicorn + gevent | High-performance ASGI server optimized for asynchronous I/O and low memory overhead in containerized Railway environments. |
| **Data Validation** | **Pydantic v2 (2.6+)** | Dataclasses / Marshmallow | Rust-based core providing up to 20x faster serialization/deserialization, strict type constraints, and seamless JSON schema export. |
| **HTTP Client** | **HTTPX 0.27+** (`httpx.AsyncClient`) | Requests / aiohttp | Modern async HTTP client supporting connection pooling, HTTP/2, granular socket timeout configurations, and clean pytest mocking fixtures. |
| **HTML Parsing** | **BeautifulSoup4 + LXML** | Scrapy / Playwright | Fast, resilient DOM parsing for static HTML without heavy headless browser overhead; robust against minor markup adjustments. |
| **Frontend Core** | **Zero-Build Vanilla ES6 JavaScript** | React / Vue / Svelte | Eliminates Node.js build pipelines, npm vulnerabilities, and Webpack/Vite complexity. Follows proven *Houston Bayou Watch* pattern. Instant browser parsing. |
| **Map Engine** | **Leaflet.js 1.9.4** | Mapbox GL / MapLibre | Lightweight (39KB gzip), mature, mobile-touch responsive, zero API key requirement, and fully compatible with free tile providers. |
| **Basemap Tiles** | **CartoDB Dark Matter & Esri Ocean** | Mapbox Streets | Tokenless, high-contrast dark theme optimized for glowing status markers; Esri Ocean provides accurate bathymetric shelf contours. |
| **Data Visualization** | **Chart.js 4.4.1** | D3.js / Plotly | Canvas-based GPU-accelerated rendering, responsive mobile pinch/swipe support, dual-axis line/gradient charts with minimal bundle weight. |
| **Deployment Engine** | **Railway Nixpacks** | Docker / Heroku | Automatic Python runtime detection, zero Dockerfile maintenance overhead, native support for `railway.toml` and `Procfile`. |

---

## 4. Repository Layout & Module Separation

The repository structure follows modular, testable domain separation. There are zero circular dependencies, and responsibilities are cleanly divided across schema models, services, presentation, and data fallbacks.

```
louisiana-surf-finder/
├── app/
│   ├── __init__.py                     # Package declaration (__version__ = "1.0.0")
│   ├── main.py                         # FastAPI app factory, lifespan manager, static mount & routes
│   ├── config.py                       # Pydantic BaseSettings, environment variables, spot metadata
│   ├── models/
│   │   ├── __init__.py                 # Schema package init
│   │   └── schemas.py                  # Pydantic v2 models (NormalizedMarineReading, ConsensusForecast, etc.)
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
│   ├── ARCHITECTURE_PLAN.md            # Comprehensive Milestone 1 architectural specification
│   └── DEPLOYMENT_PORKBUN.md           # Porkbun DNS and custom domain setup guide
├── scripts/
│   └── setup_porkbun_dns.py            # Automated Porkbun API v3 DNS record configuration script
├── tests/
│   ├── conftest.py                     # Pytest fixtures, mock HTTP clients, clean cache fixture
│   ├── test_buoy_service.py            # NDBC and CO-OPS parser tests with mock raw data
│   ├── test_scraper_service.py         # HTML scraper parsing and edge-case resilience tests
│   ├── test_weather_service.py         # Open-Meteo and NWS response parsing tests
│   ├── test_consensus_engine.py        # Scoring formula, shoaling, anomaly rejection, and variance tests
│   ├── test_api_routes.py              # FastAPI REST endpoint integration tests
│   └── test_e2e.py                     # End-to-end user journey and fallback degradation tests
├── railway.toml                        # Railway Nixpacks deployment configuration
├── Procfile                            # Railway process declaration
├── requirements.txt                    # Pinned production Python dependencies
├── .env.example                        # Environment variable configuration template
└── README.md                           # Project overview, architecture, and quickstart instructions
```

---

## 5. Marine Buoy Ingestion & Multi-Source Scraping Pipelines

### 5.1 Multi-Sensor Pairing Strategy

Because physical wave buoys cannot survive the shallow, muddy surf zones and heavy commercial trawling traffic of the immediate Louisiana surf breaks, wave sensors are moored offshore, while meteorological and tide stations are mounted on stable inshore channel jetties. The ingestion architecture pairs each target spot with a designated inshore-offshore sensor cluster:

```
                    ┌────────────────────────────────────────────────────────┐
                    │               LOUISIANA SURF FINDER                    │
                    │           Sensor Pairing Architecture                  │
                    └────────────────────────────────────────────────────────┘
                                                │
                 ┌──────────────────────────────┴─────────────────────────────┐
                 │                                                            │
                 ▼                                                            ▼
      [ HOLLY BEACH (West LA) ]                                    [ GRAND ISLE (East LA) ]
    Lat: 29.7711°, Lon: -93.4475°                                Lat: 29.2366°, Lon: -89.9872°
    Facing: 180° (South)                                         Facing: 160° (South-Southeast)
                 │                                                            │
  ┌──────────────┴──────────────┐                              ┌──────────────┴──────────────┐
  ▼                             ▼                              ▼                             ▼
[Inshore Sensor]       [Offshore Wave]                  [Inshore Sensor]       [Offshore Wave Buoys]
NOAA CO-OPS 8768094    NOAA NDBC 42035                  NOAA CO-OPS 8761724    1. NOAA NDBC 42354 (Shelf)
(Calcasieu Pass/CAPL1) (Galveston 22NM)                 (Grand Isle / GISL1)      (Chandeleur CDIP 279)
• Lat: 29.7682°        • Lat: 29.235°                   • Lat: 29.2633°        2. NOAA NDBC 42001 (Deep)
• Lon: -93.3429°       • Lon: -94.410°                  • Lon: -89.9567°          (Mid Gulf Deepwater)
• Dist: 7.1 NM East    • Dist: 58 NM WSW                • Dist: On Island      • Dist: 78 NM E / 205 NM S
• Parameters:          • Parameters:                    • Parameters:          • Parameters:
  - Wind Spd/Dir/Gust    - Swell Ht/Period/Dir            - Wind Spd/Dir/Gust    - Swell Ht/Period/Dir
  - Tide Level (MLLW)    - Wind Wave Ht/Period            - Tide Level (MLLW)    - Dominant Wave Period
  - Tide Predictions     - Total Wave Ht (WVHT)           - Tide Predictions     - Deep Groundswell Early
  - Water & Air Temp     - Wave Steepness/APD             - Water & Air Temp       Warning (42001)
```

#### Spot Cluster Details:
1. **Holly Beach (Cameron Parish / Western LA)**:
   - **Inshore Station**: NOAA CO-OPS Station `8768094` (Calcasieu Pass / NDBC `CAPL1`) at `29.7682° N, 93.3429° W` (~7.1 NM east). Supplies actual beach wind speed/direction, gusts, air/water temperatures, and 6-minute water level (MLLW).
   - **Primary Offshore Wave Buoy**: NOAA NDBC Station `42035` (Galveston 22 NM East) at `29.235° N, 94.410° W` (depth 13.1m / 43 ft). Decomposed via spectral parser.
   - **Backup Coastal Stations**: `SRST2` (Sabine Pass, TX) and `TXPT2` (Texas Point).
2. **Grand Isle (Jefferson Parish / Eastern LA)**:
   - **Inshore Station**: NOAA CO-OPS Station `8761724` (Grand Isle / NDBC `GISL1`) at `29.2633° N, 89.9567° W`. Supplies local island winds, water level, and tide predictions.
   - **Primary Offshore Wave Buoy**: NOAA NDBC Station `42354` (SCRIPPS / CDIP Station 279 - Chandeleur Island SE) at `29.579° N, 88.645° W` (depth 18m / 59 ft). Measures significant wave height, dominant swell period, and mean wave direction.
   - **Deepwater Sentinel**: NOAA NDBC Station `42001` (Mid Gulf Deepwater - 180 NM S SW Pass) at `25.922° N, 89.638° W` (depth 3,246m). Detects long-period swells ($\ge 8.0\text{ s}$) 12–24 hours before coastal impact.

### 5.2 Parsing Specifications & Protocols

#### 1. NOAA NDBC Spectral Wave Summary (`.spec`) Parser
The standard NDBC realtime file (`<STATION>.txt`) frequently omits wave height (`WVHT = MM`) for moored shallow buoys. The spectral wave summary (`<STATION>.spec`) decomposes wave energy into separate wind-wave and swell components.
- **Endpoint**: `https://www.ndbc.noaa.gov/data/realtime2/<STATION_ID>.spec` (Uppercase Station ID).
- **Header Line 1**: `#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD`
- **Header Line 2**: `#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT`
- **Missing Value Sentinel**: Strings matching `MM` or `-99` map to Python `None`.
- **Unit Conversions**: Meters to feet: `val * 3.28084`. Compass string (`SE`, `SSE`) mapped to degrees clockwise from True North.

#### 2. NOAA NDBC Standard Meteorological (`.txt`) Parser
- **Endpoint**: `https://www.ndbc.noaa.gov/data/realtime2/<STATION_ID>.txt`
- **Header Line**: `#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE`
- **Conversions**: Wind speed/gust from m/s to mph (`val * 2.23694`), temperatures from Celsius to Fahrenheit (`(val * 9/5) + 32`).

#### 3. NOAA CO-OPS Datagetter API Client
- **Endpoint**: `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter`
- **Parameters**: `application=LouisianaSurfFinder&format=json&time_zone=gmt&units=english&datum=MLLW`
- **Products Queried**:
  - `water_level`: Live tide in feet MLLW (`date=latest`).
  - `predictions`: 24-hour harmonic predictions (240 points at 6-min intervals). The **Tide Slope Engine** computes the 30-minute derivative $\frac{\Delta v}{\Delta t}$:
    - $\Delta v > +0.10\text{ ft} \implies \text{Rising}$
    - $\Delta v < -0.10\text{ ft} \implies \text{Falling}$
    - $|\Delta v| \le 0.10\text{ ft} \land v \ge \bar{v} \implies \text{Slack High}$
    - $|\Delta v| \le 0.10\text{ ft} \land v < \bar{v} \implies \text{Slack Low}$
  - `wind`: Inshore sustained wind speed, direction, and gusts.
  - `water_temperature`: Sea surface temperature in °F.

#### 4. Open-Meteo Marine API Client
- **Endpoint**: `https://marine-api.open-meteo.com/v1/marine`
- **Parameters**: `latitude={lat}&longitude={lon}&hourly=wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,swell_wave_period&length_unit=imperial&wind_speed_unit=mph&timezone=America/Chicago&forecast_days=3`
- **Role**: Supplies 72 hours of synchronized wave, swell, and wind forecast vectors without API keys or rate-limit tokens.

#### 5. National Weather Service (NWS) Marine Zones & Gridpoints
- **Zones**: `GMZ450` (Cameron LA to High Island TX) and `GMZ553` (SW Pass to Port Fourchon).
- **Text Parser Regex**:
  - Winds: `([A-Za-z]+)\s+winds?\s+(?:around\s+(\d+)|(\d+)\s+to\s+(\d+))\s*knots`
  - Seas: `Seas\s+(?:around\s+(\d+)|(\d+)\s+to\s+(\d+)|(\d+)\s*foot\s+or\s+less)`
  - Swell Detail: `Wave\s+Detail:\s*([A-Za-z]+)\s+(\d+)\s*(?:to\s*(\d+))?\s*(?:foot|feet)\s+at\s+(\d+)\s*seconds`
- **Gridpoints**: `LCH/51,74` (Lake Charles WFO) and `LIX/72,57` (New Orleans WFO) for quantitative hourly parameters.

#### 6. Web Scrapers: Surf Captain & Surf-Forecast.com
- **Surf Captain Scraper** (Holly Beach & Grand Isle):
  - Endpoints: `https://surfcaptain.com/forecast/holly-beach-louisiana` and `https://surfcaptain.com/forecast/grand-isle-louisiana`.
  - DOM Selectors: `<h1 id="fcst-current-title">` extracts current wave range (e.g., `1-2 ft`) and condition description (`semi glassy/bumpy`). Class tokens from `.fcst-current-cond` (`clean`, `fair`, `choppy`, `flat`) determine condition rating.
- **Surf-Forecast.com Scraper** (Holly Beach):
  - Endpoint: `https://www.surf-forecast.com/breaks/Holly-Beach/forecasts/latest`.
  - DOM Extraction: `<table class="forecast-table__table">` rows with `data-row="swell"`, `data-row="wind"`, `data-row="wind-state"`, and `data-row="rating"` extract 24 forecast timesteps.

### 5.3 Concurrency, Timeouts & Error Shielding
- Singleton `httpx.AsyncClient` shared across the application with:
  - Total timeout: `5.0` seconds
  - Connect timeout: `3.0` seconds
  - Read timeout: `4.0` seconds
  - Max connections: `50`
- Fetch orchestration wraps all parallel calls inside `asyncio.gather(*tasks, return_exceptions=True)`.
- If an individual source times out or returns HTTP 5xx/404, the pipeline encapsulates the failure as a `SourceError`, logs a structured warning, and substitutes the source's cached or fallback value with `status="fallback"`. **The pipeline never throws an unhandled exception or terminates.**

---

## 6. Pydantic v2 Data Models & Interface Contracts

The platform enforces strict Pydantic v2 contracts across all service boundaries:

```python
"""
app/models/schemas.py
Production Pydantic v2 schemas for Louisiana Surf Finder.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator


class NormalizedMarineReading(BaseModel):
    """
    Unified contract representing an atomic marine reading or forecast point
    from any source (NDBC buoy, CO-OPS gauge, Open-Meteo, NWS, or scraper).
    """
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    source_id: str = Field(..., description="Unique source key, e.g. 'ndbc_42035', 'surf_captain'")
    source_name: str = Field(..., description="Display label, e.g. 'NOAA Buoy 42035 (Galveston 22NM)'")
    source_type: Literal["buoy_observation", "forecast_model", "scraped_forecast", "tide_gauge"]
    timestamp: datetime
    
    # Wave Metrics
    wave_height_ft: Optional[float] = Field(None, ge=0.0, le=40.0)
    wave_period_sec: Optional[float] = Field(None, ge=1.0, le=30.0)
    wave_direction_deg: Optional[float] = Field(None, ge=0.0, lt=360.0)
    
    # Swell Decomposition
    swell_height_ft: Optional[float] = Field(None, ge=0.0, le=40.0)
    swell_period_sec: Optional[float] = Field(None, ge=1.0, le=30.0)
    swell_direction_deg: Optional[float] = Field(None, ge=0.0, lt=360.0)
    swell_direction_cardinal: Optional[str] = None
    
    # Wind Wave Decomposition
    wind_wave_height_ft: Optional[float] = Field(None, ge=0.0, le=40.0)
    wind_wave_period_sec: Optional[float] = Field(None, ge=1.0, le=20.0)

    # Meteorology
    wind_speed_mph: Optional[float] = Field(None, ge=0.0, le=150.0)
    wind_gust_mph: Optional[float] = Field(None, ge=0.0, le=180.0)
    wind_direction_deg: Optional[float] = Field(None, ge=0.0, lt=360.0)
    wind_direction_cardinal: Optional[str] = None
    
    # Oceanography
    water_temp_f: Optional[float] = Field(None, ge=30.0, le=105.0)
    air_temp_f: Optional[float] = Field(None, ge=-10.0, le=125.0)
    pressure_hpa: Optional[float] = Field(None, ge=900.0, le=1080.0)
    
    # Tide
    tide_ft: Optional[float] = None
    tide_status: Optional[Literal["rising", "falling", "slack_high", "slack_low"]] = None
    
    # Status & Weight
    raw_condition_label: Optional[str] = None
    confidence_weight: float = Field(default=0.8, ge=0.0, le=1.0)
    status: Literal["valid", "stale", "fallback", "outlier_rejected"] = "valid"

    @field_validator("wave_height_ft", "swell_height_ft", "wind_wave_height_ft", mode="before")
    @classmethod
    def round_heights(cls, v: Any) -> Optional[float]:
        return round(float(v), 2) if v is not None else None


class HourlyForecastPoint(BaseModel):
    """Hourly time-series point for 48h/72h forecast timelines."""
    timestamp: datetime
    breaking_height_ft: float = Field(..., ge=0.0)
    breaking_range: str = Field(..., description="e.g. '2-3 ft'")
    dominant_period_sec: float = Field(..., ge=1.0)
    swell_direction_deg: float = Field(..., ge=0.0, lt=360.0)
    swell_direction_cardinal: str
    wind_speed_mph: float = Field(..., ge=0.0)
    wind_direction_deg: float = Field(..., ge=0.0, lt=360.0)
    wind_direction_cardinal: str
    wind_regime: Literal["Offshore Groomed", "Cross-Shore", "Onshore Blown-Out", "Light / Glassy"]
    surf_score: int = Field(..., ge=0, le=100)
    decision: Literal["Poor / Flat", "Fair", "Good / Decent", "Firing / Epic"]
    tide_height_ft: Optional[float] = None
    tide_status: Optional[str] = None


class BuoyObservation(BaseModel):
    """Telemetry observation from an individual NDBC buoy or CO-OPS station."""
    station_id: str
    station_name: str
    latitude: float
    longitude: float
    station_type: Literal["ndbc_buoy", "coops_tide_station", "cdip_waverider", "c_man"]
    timestamp: datetime
    last_updated: datetime
    wave_height_ft: Optional[float] = None
    dominant_period_sec: Optional[float] = None
    average_period_sec: Optional[float] = None
    mean_wave_direction_deg: Optional[float] = None
    swell_height_ft: Optional[float] = None
    swell_period_sec: Optional[float] = None
    swell_direction_deg: Optional[float] = None
    swell_direction_cardinal: Optional[str] = None
    wind_wave_height_ft: Optional[float] = None
    wind_wave_period_sec: Optional[float] = None
    steepness: Optional[str] = None
    wind_speed_mph: Optional[float] = None
    wind_gust_mph: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    wind_direction_cardinal: Optional[str] = None
    water_temp_f: Optional[float] = None
    air_temp_f: Optional[float] = None
    pressure_hpa: Optional[float] = None
    tide_level_ft: Optional[float] = None
    tide_mllw_ft: Optional[float] = None
    is_online: bool = True
    status: Literal["online", "offline", "stale"] = "online"


class GoldenWindowRecommendation(BaseModel):
    """Upcoming optimal surf window identified by the recommendation engine."""
    has_good_window: bool = False
    window_title: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    peak_time: Optional[datetime] = None
    peak_score: int = Field(default=0, ge=0, le=100)
    peak_wave_range: str = "Flat"
    wind_summary: str = "Calm"
    tide_summary: str = "Mid-Rising"
    human_verdict: str


class ConsensusForecast(BaseModel):
    """Unified multi-source consensus decision for a surf spot."""
    spot_id: Literal["holly_beach", "grand_isle"]
    spot_name: str
    latitude: float
    longitude: float
    timestamp: datetime
    surf_score: int = Field(..., ge=0, le=100)
    overall_decision: Literal["Poor / Flat", "Fair", "Good / Decent", "Firing / Epic"]
    badge_color: Literal["emerald", "amber", "crimson", "purple"]
    breaking_height_ft: float = Field(..., ge=0.0)
    breaking_range_str: str
    primary_swell_period_sec: float
    primary_swell_direction_deg: float
    primary_swell_compass: str
    wind_speed_mph: float
    wind_direction_deg: float
    wind_compass: str
    wind_character: Literal["Offshore Groomed", "Cross-Shore", "Onshore Blown-Out", "Light / Glassy"]
    water_temp_f: Optional[float] = None
    tide_height_ft: Optional[float] = None
    tide_state: Literal["Rising", "Falling", "Slack High", "Slack Low"]
    confidence_score_pct: int = Field(..., ge=0, le=100)
    confidence_label: Literal["High", "Moderate", "Low (Divergent)"]
    divergence_warning: Optional[str] = None
    is_fallback: bool = False
    best_window: GoldenWindowRecommendation
    source_breakdown: List[NormalizedMarineReading]
    timeline_48h: List[HourlyForecastPoint] = Field(default_factory=list)


class SpotDetail(BaseModel):
    """Top-level spot model containing configuration, consensus, and timeline."""
    spot_id: str
    spot_name: str
    parish: str
    coordinates: Dict[str, float]
    shoreline_bearing_deg: float
    offshore_wind_deg: float
    shoaling_k_net: float
    consensus: ConsensusForecast
    recommendation: GoldenWindowRecommendation
    source_breakdown: List[NormalizedMarineReading]


class HealthCheckResponse(BaseModel):
    """Contract for /api/health monitoring probe."""
    status: Literal["healthy", "degraded", "unhealthy"]
    service: str = "louisiana-surf-finder"
    version: str = "1.0.0"
    timestamp: datetime
    uptime_seconds: float
    cache: Dict[str, Any]
    spots_loaded: List[str]
    buoys_monitored: List[str]
```

---

## 7. Multi-Source Consensus & Surf Prediction Engine

### 7.1 Coastal Oceanography & Bathymetric Shoaling Model

Louisiana wave physics are governed by dramatic bathymetric contrasts between Western and Eastern coastal segments:

```
               WESTERN LOUISIANA (Holly Beach)                EASTERN LOUISIANA (Grand Isle)
Shoreline:     Faces South (180°)                             Faces South-Southeast (160°)
Shelf Width:   80–100 NM to 100m depth contour (Ultra-wide)   25–35 NM to 100m depth contour (Narrow)
Seafloor:      Chenier silt, clay, mud lumps, soft sand       Fine sand, rock groins, barrier island
Deepwater:     Far offshore; extreme bottom drag              Near Mississippi Canyon drop-off
Attenuation:   Severe frictional damping (Kf = 0.50–0.60)     Moderate frictional damping (Kf = 0.72–0.80)
Wave Punch:    Soft, slopey, barrelling on low/mid tide       Punchier, steeper, 1.0–2.0 ft larger faces
```

To translate offshore buoy measurements ($H_{s,buoy}$) into nearshore breaking wave height ($H_{break}$), we apply linear wave theory with shallow-water friction damping:

$$H_{break} = H_{s,buoy} \cdot K_{net} = H_{s,buoy} \cdot K_s \cdot K_r \cdot K_f$$

1. **Shoaling Coefficient ($K_s$):** Energy flux conservation as wave celerity decreases: $K_s = \sqrt{c_{g,0}/c_g} \approx 1.15$.
2. **Refraction Coefficient ($K_r$):** Refraction over divergent bathymetry: $K_r = \sqrt{b_0/b} \approx 0.91$.
3. **Bottom Friction & Mud Attenuation ($K_f$):** Turbulent boundary-layer dissipation over shelf distance $x$:
   - **Holly Beach ($x_{shelf} \approx 70\text{ NM}$, fluid mud):** $K_f \approx 0.62$.
   - **Grand Isle ($x_{shelf} \approx 30\text{ NM}$, sandy seabed near canyon):** $K_f \approx 0.78$.

#### Calibrated Attenuation Coefficients:
- **Holly Beach:** $K_{net,HB} = 1.15 \times 0.91 \times 0.62 \approx \mathbf{0.65}$  
  *(5.0 ft offshore buoy swell translates to $5.0 \times 0.65 = \mathbf{3.25\text{ ft}}$ breaking surf).*
- **Grand Isle:** $K_{net,GI} = 1.15 \times 0.91 \times 0.78 \approx \mathbf{0.82}$  
  *(4.0 ft offshore buoy swell translates to $4.0 \times 0.82 = \mathbf{3.28\text{ ft}}$ breaking surf).*

---

### 7.2 Objective 0–100 Surf Quality Scoring Formula

The surf quality score is an objective, deterministic function $Q \in [0, 100]$:

$$Q = \text{clamp}\Big( \text{BaseScore}(H_{break}) \times M_{period}(T_p) \times W_{wind}(V_w, \Delta\theta) \times T_{tide}(\text{state}), \; 0, \; 100 \Big)$$

#### 1. Breaking Wave Height Base Score ($\text{BaseScore}(H)$)
Calibrated specifically for shallow Gulf beach breaks:
$$\text{BaseScore}(H) = 
\begin{cases}
H \times 15.0 & 0.0 \le H < 1.0\text{ ft (Flat / Ankle slop)} \\
15.0 + (H - 1.0) \times 25.0 & 1.0 \le H < 2.0\text{ ft (Knee to Thigh / Longboard)} \\
40.0 + (H - 2.0) \times 20.0 & 2.0 \le H < 3.5\text{ ft (Waist to Chest / Funboards)} \\
70.0 + (H - 3.5) \times 10.0 & 3.5 \le H < 5.5\text{ ft (Chest to Head-High / Sweet Spot)} \\
90.0 - (H - 5.5) \times 5.0 & 5.5 \le H \le 8.0\text{ ft (Overhead / Heavy Drift)} \\
\max\Big(40.0, \; 77.5 - (H - 8.0) \times 6.0\Big) & H > 8.0\text{ ft (Storm Washout / Hazardous)}
\end{cases}$$

#### 2. Swell Period Multiplier ($M_{period}(T_p)$)
Non-linear scaling reflecting wave energy density ($E \propto H_s^2 T_p^2$):
- $T_p < 4.0\text{ s}$: **$0.55$** (Chaotic windchop slop, collapses prematurely)
- $4.0 \le T_p < 5.0\text{ s}$: **$0.75$** (Weak push, requires high-volume boards)
- $5.0 \le T_p < 6.2\text{ s}$: **$0.95$** (Standard Gulf windswell)
- $6.2 \le T_p < 7.8\text{ s}$: **$1.15$** (Organized front swell with peeling shoulders)
- $7.8 \le T_p < 9.5\text{ s}$: **$1.30$** (Longer-period groundswell, hollow barreling takeoffs)
- $T_p \ge 9.5\text{ s}$: **$1.40$** (Rare deep-Gulf or tropical groundswell)

#### 3. Wind Alignment & Velocity Factor ($W_{wind}(V_w, \Delta\theta)$)
Calculates angular divergence from the spot's ideal offshore axis ($\theta_{offshore} = 0^\circ$ for Holly Beach, $340^\circ$ for Grand Isle):
$$\Delta\theta = |(\theta_{wind} - \theta_{offshore} + 180^\circ) \pmod{360^\circ} - 180^\circ|$$
- **Glassy / Light & Variable ($V_w < 4.0\text{ mph}$):** $W_{wind} = 1.15$
- **Offshore Winds ($\Delta\theta \le 45^\circ$):**
  - $4.0 \le V_w \le 15.0\text{ mph}$: **$1.25$** (Ideal offshore groom; spray off lip)
  - $15.0 < V_w \le 22.0\text{ mph}$: **$1.05$** (Hollow but stiff offshore takeoff)
  - $V_w > 22.0\text{ mph}$: **$0.85$** (Gale offshore; blown off wave backs)
- **Cross-shore Winds ($45^\circ < \Delta\theta \le 90^\circ$):**
  $$W_{wind} = \max\Big(0.45, \; 1.0 - \frac{V_w}{35.0}\Big)$$
- **Onshore Winds ($\Delta\theta > 90^\circ$):**
  $$W_{wind} = \max\Big(0.18, \; 0.72 - \frac{V_w}{22.0}\Big)$$

#### 4. Tide State Modifier ($T_{tide}$)
- **Rising Mid-to-High Tide**: $1.05$ (Promotes shape, prevents bottom scraping)
- **Slack High Tide**: $1.00$ (Full depth, softer break)
- **Falling Mid Tide**: $0.95$ (Increasing backwash)
- **Extreme Low Tide**: $0.90$ (Dumping, dredging closeouts on mud/sand)

#### 5. Decision Tiers & Badge Tokens:
- **80 – 100**: **Firing / Epic** (Purple `#a855f7`)
- **60 – 79**: **Good / Decent** (Emerald `#10b981`)
- **35 – 59**: **Fair / Marginal** (Amber `#f59e0b`)
- **0 – 34**: **Poor / Flat** (Crimson `#ef4444`)

---

### 7.3 Multi-Source Consensus Reconciliation & Anomaly Rejection

```
[Live NDBC Buoy (Shoaled)]  [Open-Meteo Marine]  [Surf Captain]  [Surf-Forecast]  [NWS Coastal]
            │                       │                   │               │               │
            └───────────────────────┼───────────────────┴───────────────┼───────────────┘
                                    ▼
                 Stage 1: Median-Based Outlier Detection
                   (|H_i - Median| > 2.5 ft rejected)
                                    │
                                    ▼
                 Stage 2: Dynamic Temporal Weight Fusion
                   (Weights re-normalized over valid sources)
                                    │
                                    ▼
                 Stage 3: Variance-Derived Confidence Scoring
                   (StdDev σ_wave -> Confidence 30% to 98%)
                                    │
                                    ▼
                 Unified Consensus Forecast Object
```

1. **Median-Based Outlier Filtering**:
   If $N \ge 3$ sources are present, compute median $M = \text{median}(S)$. Any source where $|H_i - M| > 2.5\text{ ft}$ is flagged as an anomaly (`status="outlier_rejected"`) and excluded from fusion.
2. **Dynamic Temporal Weighting**:
   - $t = 0$ (Now): Live Buoy $40\%$, Open-Meteo $30\%$, Scrapers $20\%$, NWS $10\%$.
   - $1 \le t \le 24\text{h}$: Open-Meteo $45\%$, Scrapers $35\%$, NWS $20\%$.
   - $25 \le t \le 72\text{h}$: Open-Meteo $50\%$, Scrapers $30\%$, NWS $20\%$.
3. **Variance-Derived Confidence Rating**:
   Computed from cross-source standard deviation $\sigma_{wave}$:
   $$C = \text{clamp}\Big( \text{round}(100 - (\sigma_{wave} \times 35)), \; 30, \; 98 \Big)$$
   - $\sigma_{wave} \le 0.45\text{ ft} \implies \text{High Confidence } (\ge 85\%)$
   - $0.45 < \sigma_{wave} \le 1.0\text{ ft} \implies \text{Moderate Confidence } (65\% - 84\%)$
   - $\sigma_{wave} > 1.0\text{ ft} \implies \text{Low (Divergent) } (< 65\%)$, triggers UI divergence alert banner.

### 7.4 "When is it up?" 48-Hour Golden Window Recommendation Algorithm

The recommendation engine scans the 48-hour forward projection to identify contiguous windows ($\ge 2$ hours) where $Q(t) \ge 60$. It specifically tags **post-frontal clean-off sessions** when winds veer offshore ($N, NNW$) while breaking swell persists $\ge 2.5\text{ ft}$. If no high-quality window exists, it computes the best 3-hour sliding window and provides a realistic marginal assessment (e.g., "Longboard / foil recommended").

---

## 8. Interactive Map & UI Layout Specifications

Following the zero-build Vanilla ES6 JS + Leaflet.js architecture established in reference project *Houston Bayou Watch*, the UI provides an instantaneous, GPU-accelerated dark marine experience with zero Node.js dependencies.

### 8.1 Map Viewport & Dual Basemaps
- **Center**: `[29.35, -91.50]` (Geographic midpoint of coastal Louisiana).
- **Default Zoom**: `8` (Covers Cameron Parish in the west to Plaquemines Parish in the east).
- **Max Bounds**: `[[27.50, -95.50], [31.00, -88.00]]` with viscosity `0.85`.
- **Tile Layers**:
  1. *CartoDB Dark Matter* (Default): Tokenless high-contrast dark basemap.
  2. *Esri Ocean Basemap*: High-resolution bathymetric contours depicting the shallow shelf and Mississippi Canyon drop-off.

### 8.2 Custom Spot Markers & Vector Overlays
- **Pulsing Spot Badges**: SVG markers with expanding CSS ripple rings (`@keyframes marker-pulse-anim`), displaying spot name, wave height range (e.g., `3-4 FT`), and 0–100 score badge.
- **Rotatable Vector Arrows**: Separate cyan swell vectors (pointing toward the beach) and wind vectors (color-coded green for offshore, amber for cross-shore, crimson for onshore) with dynamic CSS rotation angles.
- **Marine Buoy Overlays**: Moored buoys (diamonds) and inshore tide stations (hexagons) with telemetry popups displaying live wave height, period, wind speed, water temperature, and estimated shoaled breaking height.

### 8.3 Sliding Bento Drawer Layout
When a user clicks Holly Beach or Grand Isle, the map pans smoothly and the **Spot Detail Bento Drawer** opens:
- **Desktop ($\ge 1024\text{px}$)**: Fixed 440px right sidebar with minimizable tab.
- **Mobile ($< 1024\text{px}$)**: Swipeable bottom sheet (72px collapsed pill to 88vh expanded view).
- **Bento Content Structure**:
  1. *Consensus Hero Card*: Large 0–100 score donut gauge, overall decision label, and key metrics (Wave face range, dominant period, wind speed/character, tide level).
  2. *"When is it up?" Golden Window Card*: Glowing gold-accented card highlighting the best upcoming session with countdown ("Window opens in 8 hours").
  3. *Multi-Source Comparison Matrix*: Side-by-side reconciliation table displaying live buoy, Open-Meteo, Surf Captain, and NWS readings with source weights, status badges, and divergence warnings.
  4. *48-Hour Chart.js Timeline*: Dual-axis combined line/area chart with seafoam teal gradient for wave height, dashed cyan line for swell period, and quality-colored bar below the time axis.
  5. *Spot Logistics & Webcams*: Beach driving regulations, pass permits, tide tables, and live surf cam links.

---

## 9. Caching, Reliability & Dual-Tier Fallback Strategy

### 9.1 Multi-Tier Caching Flow

```
                      Client Request: GET /api/spots/{spot_id}
                                       │
                                       ▼
                   ┌───────────────────────────────────────┐
                   │ Check In-Memory Cache (Key: spot_id)  │
                   └───────────────────┬───────────────────┘
                                       │
                  ┌────────────────────┴────────────────────┐
                  │                                         │
       [Hit & Valid TTL]                           [Miss or Expired]
                  │                                         │
                  ▼                                         ▼
         Return Cached JSON                    Acquire Async Lock for Key
       (Header: X-Cache: HIT)                               │
                                            ┌───────────────┴───────────────┐
                                            │                               │
                                      [Double Check]                  [Execute Fetch]
                                   (Another coroutine              Parallel httpx calls
                                    completed fetch?)              Buoys + Forecasts + Scrapers
                                            │                               │
                                            ▼                               ▼
                                   Return Cached JSON           ┌───────────┴───────────┐
                                                                │                       │
                                                           [Success]                [Exception]
                                                                │                   (Timeout/500/404)
                                                                ▼                       │
                                                      Update In-Memory Cache            ▼
                                                      TTL: 600s / 1800s        Check Stale In-Memory
                                                      Return Live Forecast              │
                                                      (Header: X-Cache: MISS)   ┌───────┴───────┐
                                                                                │               │
                                                                           [Stale Valid]   [No Memory]
                                                                                │               │
                                                                                ▼               ▼
                                                                           Return Stale   Load Static JSON
                                                                           Forecast       Fallback from Disk
                                                                           (X-Cache:      `app/data/fallbacks/`
                                                                            STALE)        (X-Cache: FALLBACK)
```

### 9.2 TTL Configuration Matrix

| Cache Key Namespace | Upstream Source | Recommended TTL | Stale Grace Period | Purpose |
|---|---|---|---|---|
| `buoy:{station_id}` | NOAA NDBC (`.txt` & `.spec`) | 600s (10 min) | 86400s (24 hr) | Live offshore wave observations |
| `coops:{station_id}` | NOAA CO-OPS Tides & Wind | 360s (6 min) | 86400s (24 hr) | Inshore water level & wind vectors |
| `forecast:open_meteo:{spot_id}` | Open-Meteo Marine WW3 | 1800s (30 min) | 86400s (24 hr) | 72h numerical wave model |
| `forecast:nws:{zone_id}` | NWS Coastal Zone Forecast | 1800s (30 min) | 86400s (24 hr) | Official coastal marine zone text |
| `scraper:surf_captain:{spot_id}` | Surf Captain Scraper | 1800s (30 min) | 86400s (24 hr) | Web forecast rating & height |
| `scraper:surf_forecast:{spot_id}` | Surf-Forecast.com Scraper | 1800s (30 min) | 86400s (24 hr) | Multi-timestep forecast table |
| `consensus:{spot_id}` | Computed Consensus Engine | 600s (10 min) | Permanent | Spot detail object with timeline |
| `fallbacks:*` | Bundled Static JSON | 86400s (24 hr) | Permanent | Zero-crash offline safety net |

### 9.3 Offline Resilience & Fallback Datasets
Pre-seeded static JSON snapshots are checked into Git in `app/data/fallbacks/`:
- `spots_fallback.json`: Spot metadata and consensus objects for Holly Beach and Grand Isle.
- `buoys_fallback.json`: Verified telemetry for stations 42035, 42354, 42001, CAPL1, and GISL1.
- `forecasts_fallback.json`: 48-hour hourly sequence points for Chart.js rendering.

If an upstream server experiences downtime, the system transparently serves fallback data flagged with `is_fallback=True` and `X-Cache: FALLBACK`. The server never throws an unhandled 500 error or crashes.

---

## 10. Deployment & Infrastructure Blueprint

### 10.1 Railway Nixpacks Configuration
The project deploys natively to Railway via Nixpacks without custom Dockerfiles.

#### `railway.toml`:
```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2 --access-log"
healthcheckPath = "/api/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3

[environments.production.variables]
ENVIRONMENT = "production"
PORT = "8080"
CACHE_TTL_BUOYS = "600"
CACHE_TTL_FORECASTS = "1800"
REFRESH_INTERVAL_SECONDS = "600"
```

#### `Procfile`:
```procfile
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2 --access-log
```

### 10.2 Health Probe Contract (`GET /api/health`)
Railway requires an HTTP probe returning 200 OK within `healthcheckTimeout` seconds to declare a deployment healthy.
```json
{
  "status": "healthy",
  "service": "louisiana-surf-finder",
  "version": "1.0.0",
  "timestamp": "2026-11-03T18:30:00Z",
  "uptime_seconds": 1420.5,
  "cache": {
    "backend": "memory",
    "active_entries": 14,
    "hits": 182,
    "misses": 6,
    "is_warmed": true
  },
  "spots_loaded": ["holly_beach", "grand_isle"],
  "buoys_monitored": ["42035", "42354", "42001", "CAPL1", "GISL1"]
}
```

### 10.3 Porkbun DNS Automation (`scripts/setup_porkbun_dns.py`)
To attach a custom domain (e.g. `surf.blake-simon.com`), Porkbun API v3 automation provisions:
1. **CNAME record**: Points subdomain to `<service>.up.railway.app`.
2. **TXT verification record**: Configures `_railway-verify.<subdomain>` with Railway's verification token.

---

## 11. Implementation Roadmap & Milestone Schedule

| Milestone | Scope & Deliverables | Primary Roles | Exit Criteria |
|---|---|---|---|
| **M1: Architecture & Design Plan** | Author `docs/ARCHITECTURE_PLAN.md`, `requirements.txt`, `.env.example`, `README.md`, and `app/data/fallbacks/*.json`. | Worker M1 | Plan complete, valid schemas, fallback tests pass. |
| **M2: Buoy & Scraper Pipelines** | Implement `buoy_service.py` (NDBC & CO-OPS), `weather_service.py` (Open-Meteo & NWS), `scraper_service.py` (Surf Captain & Surf-Forecast), and `cache_service.py`. | Worker M2 | Live & mock tests pass for all 5 feeds with graceful error handling. |
| **M3: Consensus & Surf Prediction Engine** | Implement `consensus_engine.py` (shoaling, 0-100 scoring, anomaly rejection, variance confidence) and `recommendation_engine.py` (golden window). | Worker M3 | Formula verification tests, edge cases, and simulated model divergence pass. |
| **M4: Map Web Interface & REST API** | Implement FastAPI core (`main.py`, `routes.py`), HTML5/CSS3 glassmorphic Bento UI, Leaflet map with pulsing markers, vector arrows, drawer, and 48h Chart.js. | Worker M4 | Web UI loads cleanly, zero console errors, responsive mobile drawer. |
| **M5: Deployment & Porkbun DNS** | Configure `railway.toml`, `Procfile`, `setup_porkbun_dns.py`, `docs/DEPLOYMENT_PORKBUN.md`, and verify local start. | Worker M5 | `/api/health` 200 OK, DNS script verified against Porkbun API v3. |
| **E2E Testing Track** | Independent opaque-box test runner covering Tiers 1-4 (Category-Partition, Boundary Values, Pairwise, Real-World scenarios). | E2E Runner | 100% test pass published in `TEST_READY.md`. |
| **M6: Final Verification & Audit** | Execute all test suites end-to-end; independent forensic auditor verification. | Orchestrator & Auditor | Complete zero-defect signoff. |
