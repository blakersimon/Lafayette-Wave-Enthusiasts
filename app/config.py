"""
app/config.py
Centralized configuration, spot definitions, buoy mappings, and environment settings.
"""

from functools import lru_cache
from pathlib import Path
from typing import Dict, Any, Optional, List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application runtime settings and environment variable bindings."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    ENVIRONMENT: str = "production"
    APP_NAME: str = "Lafayette Wave Enthusiasts"
    APP_VERSION: str = "1.0.0"
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    ALLOWED_ORIGINS: str = "*"
    LOG_LEVEL: str = "INFO"

    # Caching TTLs (seconds)
    CACHE_TTL_BUOYS: int = 600          # 10 minutes for live buoy observations
    CACHE_TTL_COOPS: int = 360          # 6 minutes for live tide gauges
    CACHE_TTL_FORECASTS: int = 1800     # 30 minutes for numerical forecasts (Open-Meteo, NWS)
    CACHE_TTL_SCRAPERS: int = 1800      # 30 minutes for scraped web pages
    CACHE_TTL_CONSENSUS: int = 600      # 10 minutes for reconciled consensus
    REFRESH_INTERVAL_SECONDS: int = 600 # 10 minutes background refresher loop
    STALE_GRACE_PERIOD_SECONDS: int = 86400  # 24 hours stale-while-revalidate safety net

    # HTTP Ingestion Client Timeouts
    REQUEST_TIMEOUT_SECONDS: float = 5.0
    REQUEST_CONNECT_TIMEOUT: float = 3.0
    REQUEST_READ_TIMEOUT: float = 4.0
    USER_AGENT: str = "LouisianaSurfFinder/1.0 (Gulf Coast Marine Intelligence; contact@blake-simon.com)"

    # Fallback Data Path
    FALLBACK_DATA_DIR: Path = Path(__file__).resolve().parent / "data" / "fallbacks"

    # Porkbun DNS Automation
    PORKBUN_DOMAIN: Optional[str] = "blake-simon.com"
    PORKBUN_SUBDOMAIN: Optional[str] = "louisiana-surf"
    PORKBUN_API_KEY: Optional[str] = None
    PORKBUN_SECRET_KEY: Optional[str] = None


# ==============================================================================
# UPSTREAM ENDPOINTS & FORMAT STRINGS
# ==============================================================================

NDBC_SPEC_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.spec"
NDBC_TXT_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"
COOPS_API_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
OPEN_METEO_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
NWS_ZONE_URL = "https://api.weather.gov/zones/forecast/{zone_id}/forecast"
NWS_GRIDPOINT_URL = "https://api.weather.gov/gridpoints/{wfo}/{x},{y}/forecast/hourly"
SURF_CAPTAIN_BASE_URL = "https://surfcaptain.com/forecast/{slug}"
SURF_FORECAST_BASE_URL = "https://www.surf-forecast.com/breaks/{slug}/forecasts/latest"


# ==============================================================================
# SPOT DEFINITIONS
# ==============================================================================

SPOTS: Dict[str, Dict[str, Any]] = {
    "holly_beach": {
        "spot_id": "holly_beach",
        "spot_name": "Holly Beach (Cajun Riviera)",
        "parish": "Cameron Parish",
        "coordinates": {"latitude": 29.7711, "longitude": -93.4475},
        "shoreline_bearing_deg": 180.0,
        "offshore_wind_deg": 0.0,
        "shoaling_k_net": 0.65,
        "inshore_coops_station_id": "8768094",  # Calcasieu Pass / NDBC CAPL1
        "primary_offshore_buoy_id": "42035",    # Galveston 22NM East
        "secondary_buoy_ids": [],
        "nws_zone_id": "GMZ450",                # Cameron LA to High Island TX
        "nws_wfo": "LCH",
        "nws_gridpoint": "51,74",
        "nws_grid_coords": (51, 74),
        "surf_captain_slug": "holly-beach-louisiana",
        "surf_forecast_slug": "Holly-Beach",
        "bathymetry_notes": "80-100 NM ultra-wide shallow shelf with viscous fluid mud damping. High shoaling attenuation.",
        "bottom_type": "Shallow sandbars & viscous fluid mud shelf (K_net = 0.65)",
        "ideal_swell_window": "SE to SSW (145° – 205°)",
        "access_notes": "Drive directly on the sand along LA-82 (4WD recommended); check Cameron Ferry status when arriving from the east.",
    },
    "grand_isle": {
        "spot_id": "grand_isle",
        "spot_name": "Grand Isle",
        "parish": "Jefferson Parish",
        "coordinates": {"latitude": 29.2366, "longitude": -89.9873},
        "shoreline_bearing_deg": 160.0,
        "offshore_wind_deg": 340.0,
        "shoaling_k_net": 0.82,
        "inshore_coops_station_id": "8761724",  # Grand Isle / NDBC GISL1
        "primary_offshore_buoy_id": "42354",    # Chandeleur Island SE (CDIP 279)
        "secondary_buoy_ids": ["42001"],        # Mid-Gulf Deepwater sentinel
        "nws_zone_id": "GMZ553",                # SW Pass to Port Fourchon
        "nws_wfo": "LIX",
        "nws_gridpoint": "72,57",
        "nws_grid_coords": (72, 57),
        "surf_captain_slug": "grand-isle-louisiana",
        "surf_forecast_slug": None,             # Grand Isle not present on Surf-Forecast
        "bathymetry_notes": "25-35 NM narrow shelf adjacent to Mississippi Canyon. Moderate attenuation, punchier wave faces.",
        "bottom_type": "Sandy barrier island beach break with rock groins & Barataria jetty (K_net = 0.82)",
        "ideal_swell_window": "ESE to SSW (120° – 200°)",
        "access_notes": "Cross the LA-1 elevated toll bridge ($3.00); park at Grand Isle State Park or public beach crossovers; watch rip currents near rock groins.",
    },
}


# ==============================================================================
# BUOY STATIONS
# ==============================================================================

BUOY_STATIONS: Dict[str, Dict[str, Any]] = {
    "42035": {
        "station_id": "42035",
        "station_name": "Galveston 22 NM Offshore",
        "latitude": 29.235,
        "longitude": -94.410,
        "depth_m": 13.1,
        "station_type": "ndbc_buoy",
        "spot_association": "holly_beach",
        "role": "Primary offshore wave buoy for Holly Beach (Western LA)",
    },
    "42354": {
        "station_id": "42354",
        "station_name": "Chandeleur Island SE, LA (CDIP 279)",
        "latitude": 29.579,
        "longitude": -88.645,
        "depth_m": 18.0,
        "station_type": "cdip_waverider",
        "spot_association": "grand_isle",
        "role": "Primary offshore wave buoy for Grand Isle (Eastern LA)",
    },
    "42001": {
        "station_id": "42001",
        "station_name": "Mid Gulf Deepwater (180 NM S SW Pass)",
        "latitude": 25.922,
        "longitude": -89.638,
        "depth_m": 3246.0,
        "station_type": "ndbc_buoy",
        "spot_association": "grand_isle",
        "role": "Deepwater sentinel detecting long-period swell 12-24h early",
    },
    "CAPL1": {
        "station_id": "CAPL1",
        "coops_id": "8768094",
        "station_name": "Calcasieu Pass, LA (CO-OPS 8768094)",
        "latitude": 29.768,
        "longitude": -93.343,
        "depth_m": None,
        "station_type": "coops_tide_station",
        "spot_association": "holly_beach",
        "role": "Inshore beach wind, water level, and temp for Holly Beach",
    },
    "GISL1": {
        "station_id": "GISL1",
        "coops_id": "8761724",
        "station_name": "Grand Isle, LA (CO-OPS 8761724)",
        "latitude": 29.265,
        "longitude": -89.958,
        "depth_m": None,
        "station_type": "coops_tide_station",
        "spot_association": "grand_isle",
        "role": "Inshore island wind, water level, and temp for Grand Isle",
    },
}

COOPS_TO_STATION_MAP = {
    "8768094": "CAPL1",
    "8761724": "GISL1",
}

STATION_TO_COOPS_MAP = {
    "CAPL1": "8768094",
    "GISL1": "8761724",
}


@lru_cache()
def get_settings() -> Settings:
    """Returns singleton settings instance cached in memory."""
    return Settings()
