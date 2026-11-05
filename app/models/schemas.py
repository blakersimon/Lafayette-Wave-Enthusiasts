"""
app/models/schemas.py
Production Pydantic v2 schemas for Louisiana Surf Finder.
Provides strict marine contracts, degree normalization (0.0 to 360.0),
graceful model validation, and clean serialization across all ingestion pipelines.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


class NormalizedMarineReading(BaseModel):
    """
    Unified contract representing an atomic marine reading or forecast point
    from any source (NDBC buoy, CO-OPS gauge, Open-Meteo, NWS, or scraper).
    """
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    source_id: str = Field(..., description="Unique source key, e.g. 'ndbc_42035', 'surf_captain'")
    source_name: str = Field(..., description="Display label, e.g. 'NOAA Buoy 42035 (Galveston 22NM)'")
    source_type: Literal[
        "buoy_observation", "forecast_model", "scraped_forecast", "tide_gauge",
        "buoy", "model", "scraper", "tide_station"
    ]
    timestamp: datetime

    # Wave Metrics
    wave_height_ft: Optional[float] = Field(None, ge=0.0, le=40.0)
    wave_period_sec: Optional[float] = Field(None, ge=0.0, le=30.0)
    wave_direction_deg: Optional[float] = Field(None, ge=0.0, le=360.0)

    # Swell Decomposition
    swell_height_ft: Optional[float] = Field(None, ge=0.0, le=40.0)
    swell_period_sec: Optional[float] = Field(None, ge=0.0, le=30.0)
    swell_direction_deg: Optional[float] = Field(None, ge=0.0, le=360.0)
    swell_direction_cardinal: Optional[str] = None

    # Wind Wave Decomposition
    wind_wave_height_ft: Optional[float] = Field(None, ge=0.0, le=40.0)
    wind_wave_period_sec: Optional[float] = Field(None, ge=0.0, le=20.0)

    # Meteorology
    wind_speed_mph: Optional[float] = Field(None, ge=0.0, le=150.0)
    wind_gust_mph: Optional[float] = Field(None, ge=0.0, le=180.0)
    wind_direction_deg: Optional[float] = Field(None, ge=0.0, le=360.0)
    wind_direction_cardinal: Optional[str] = None

    # Oceanography
    water_temp_f: Optional[float] = Field(None, ge=30.0, le=105.0)
    air_temp_f: Optional[float] = Field(None, ge=-10.0, le=125.0)
    pressure_hpa: Optional[float] = Field(None, ge=900.0, le=1080.0)

    # Tide
    tide_ft: Optional[float] = None
    tide_status: Optional[str] = None

    # Status & Weight
    raw_condition_label: Optional[str] = None
    confidence_weight: float = Field(default=0.8, ge=0.0, le=1.0)
    status: Literal["valid", "stale", "fallback", "outlier_rejected"] = "valid"

    @field_validator("wave_height_ft", "swell_height_ft", "wind_wave_height_ft", mode="before")
    @classmethod
    def round_heights(cls, v: Any) -> Optional[float]:
        return round(float(v), 2) if v is not None else None

    @field_validator("wave_period_sec", "swell_period_sec", "wind_wave_period_sec", mode="before")
    @classmethod
    def round_periods(cls, v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            val = float(v)
            if val < 0.0:
                return None
            return round(val, 1)
        except (ValueError, TypeError):
            return None

    @field_validator("wave_direction_deg", "swell_direction_deg", "wind_direction_deg", mode="before")
    @classmethod
    def normalize_degrees(cls, v: Any) -> Optional[float]:
        if v is None:
            return None
        val = float(v)
        if val < 0.0 or val > 360.0:
            val = val % 360.0
        return val


class HourlyForecastPoint(BaseModel):
    """Hourly time-series point for 48h/72h forecast timelines."""
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    timestamp: datetime
    breaking_height_ft: float = Field(..., ge=0.0)
    breaking_range: str = Field(..., description="e.g. '2-3 ft'")
    dominant_period_sec: float = Field(..., ge=1.0)
    swell_direction_deg: float = Field(..., ge=0.0, le=360.0)
    swell_direction_cardinal: str
    wind_speed_mph: float = Field(..., ge=0.0)
    wind_direction_deg: float = Field(..., ge=0.0, le=360.0)
    wind_direction_cardinal: str
    wind_regime: Literal["Offshore Groomed", "Cross-Shore", "Onshore Blown-Out", "Light / Glassy"]
    surf_score: int = Field(..., ge=0, le=100)
    decision: Literal["Poor / Flat", "Fair", "Good / Decent", "Firing / Epic"]
    tide_height_ft: Optional[float] = None
    tide_status: Optional[str] = None

    @field_validator("swell_direction_deg", "wind_direction_deg", mode="before")
    @classmethod
    def normalize_degrees(cls, v: Any) -> float:
        val = float(v)
        if val < 0.0 or val > 360.0:
            val = val % 360.0
        return val


class BuoyObservation(BaseModel):
    """Telemetry observation from an individual NDBC buoy or CO-OPS station."""
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    station_id: str
    station_name: str
    latitude: float
    longitude: float
    station_type: Literal["ndbc_buoy", "coops_tide_station", "cdip_waverider", "c_man", "buoy", "tide_station"]
    timestamp: datetime
    last_updated: datetime
    wave_height_ft: Optional[float] = None
    dominant_period_sec: Optional[float] = None
    average_period_sec: Optional[float] = None
    mean_wave_direction_deg: Optional[float] = Field(None, ge=0.0, le=360.0)
    swell_height_ft: Optional[float] = None
    swell_period_sec: Optional[float] = None
    swell_direction_deg: Optional[float] = Field(None, ge=0.0, le=360.0)
    swell_direction_cardinal: Optional[str] = None
    wind_wave_height_ft: Optional[float] = None
    wind_wave_period_sec: Optional[float] = None
    steepness: Optional[str] = None
    wind_speed_mph: Optional[float] = None
    wind_gust_mph: Optional[float] = None
    wind_direction_deg: Optional[float] = Field(None, ge=0.0, le=360.0)
    wind_direction_cardinal: Optional[str] = None
    water_temp_f: Optional[float] = None
    air_temp_f: Optional[float] = None
    pressure_hpa: Optional[float] = None
    tide_level_ft: Optional[float] = None
    tide_mllw_ft: Optional[float] = None
    tide_status: Optional[str] = None
    is_online: bool = True
    status: Literal["online", "offline", "stale"] = "online"

    @field_validator("mean_wave_direction_deg", "swell_direction_deg", "wind_direction_deg", mode="before")
    @classmethod
    def normalize_degrees(cls, v: Any) -> Optional[float]:
        if v is None:
            return None
        val = float(v)
        if val < 0.0 or val > 360.0:
            val = val % 360.0
        return val


class GoldenWindowRecommendation(BaseModel):
    """Upcoming optimal surf window identified by the recommendation engine."""
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

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
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

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
    primary_swell_direction_deg: float = Field(..., ge=0.0, le=360.0)
    primary_swell_compass: str
    wind_speed_mph: float
    wind_direction_deg: float = Field(..., ge=0.0, le=360.0)
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
    source_breakdown: List[NormalizedMarineReading] = Field(default_factory=list)
    timeline_48h: List[HourlyForecastPoint] = Field(default_factory=list)

    @field_validator("primary_swell_direction_deg", "wind_direction_deg", mode="before")
    @classmethod
    def normalize_degrees(cls, v: Any) -> float:
        val = float(v)
        if val < 0.0 or val > 360.0:
            val = val % 360.0
        return val


class SpotDetail(BaseModel):
    """Top-level spot model containing configuration, consensus, recommendation, and breakdown."""
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    spot_id: str
    spot_name: str
    parish: str
    coordinates: Dict[str, float]
    shoreline_bearing_deg: float
    offshore_wind_deg: float
    shoaling_k_net: float
    consensus: ConsensusForecast
    recommendation: Optional[GoldenWindowRecommendation] = None
    source_breakdown: Optional[List[NormalizedMarineReading]] = None
    board_recommendation: Optional[Dict[str, str]] = None
    wetsuit_recommendation: Optional[Dict[str, str]] = None
    bottom_type: Optional[str] = None
    ideal_swell_window: Optional[str] = None
    access_notes: Optional[str] = None

    @model_validator(mode="after")
    def populate_nested_fields(self) -> "SpotDetail":
        """
        Guarantees that if source_breakdown or recommendation are not provided
        at root level, they automatically populate from consensus without error.
        """
        if self.source_breakdown is None and self.consensus is not None:
            self.source_breakdown = self.consensus.source_breakdown
        if self.recommendation is None and self.consensus is not None:
            self.recommendation = self.consensus.best_window
        return self


class SpotSummary(BaseModel):
    """Lightweight projection of a spot for card and map summary lists."""
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    spot_id: str
    spot_name: str
    parish: str
    coordinates: Dict[str, float]
    surf_score: int
    overall_decision: str
    badge_color: str
    breaking_height_ft: float
    breaking_range_str: str
    wind_character: str
    wind_speed_mph: float
    wind_compass: str
    tide_state: str
    confidence_label: str
    is_fallback: bool


class TimelineForecastResponse(BaseModel):
    """Endpoint response contract for GET /api/spots/{spot_id}/forecast."""
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    spot_id: str
    spot_name: str
    generated_at: datetime
    hourly: List[HourlyForecastPoint]


class HealthCheckResponse(BaseModel):
    """Contract for /api/health monitoring probe."""
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    status: Literal["healthy", "degraded", "unhealthy"]
    service: str = "louisiana-surf-finder"
    version: str = "1.0.0"
    timestamp: datetime
    uptime_seconds: float
    cache: Dict[str, Any]
    spots_loaded: List[str]
    buoys_monitored: List[str]
