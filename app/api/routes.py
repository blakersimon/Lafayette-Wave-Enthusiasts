"""
app/api/routes.py
FastAPI router providing REST endpoints for surf spots, multi-source consensus,
48-hour forecast timelines, live NOAA buoy/CO-OPS telemetry, and health probes.
"""

from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from app.config import BUOY_STATIONS, SPOTS, get_settings
from app.models.schemas import (
    BuoyObservation,
    ConsensusForecast,
    HealthCheckResponse,
    HourlyForecastPoint,
    NormalizedMarineReading,
    SpotDetail,
    TimelineForecastResponse,
)
from app.services.buoy_service import BuoyService
from app.services.cache_service import get_cache_service
from app.services.consensus_engine import ConsensusEngine
from app.services.recommendation_engine import RecommendationEngine
from app.services.scraper_service import ScraperService
from app.services.weather_service import WeatherService

logger = logging.getLogger("louisiana_surf.api")

router = APIRouter(prefix="/api", tags=["surf-telemetry"])

# Shared service singletons
_buoy_service: Optional[BuoyService] = None
_weather_service: Optional[WeatherService] = None
_scraper_service: Optional[ScraperService] = None
_consensus_engine: Optional[ConsensusEngine] = None
_recommendation_engine: Optional[RecommendationEngine] = None
_app_start_time = time.monotonic()


def get_buoy_service() -> BuoyService:
    global _buoy_service
    if _buoy_service is None:
        _buoy_service = BuoyService()
    return _buoy_service


def get_weather_service() -> WeatherService:
    global _weather_service
    if _weather_service is None:
        _weather_service = WeatherService()
    return _weather_service


def get_scraper_service() -> ScraperService:
    global _scraper_service
    if _scraper_service is None:
        _scraper_service = ScraperService()
    return _scraper_service


def get_consensus_engine() -> ConsensusEngine:
    global _consensus_engine
    if _consensus_engine is None:
        _consensus_engine = ConsensusEngine()
    return _consensus_engine


def get_recommendation_engine() -> RecommendationEngine:
    global _recommendation_engine
    if _recommendation_engine is None:
        _recommendation_engine = RecommendationEngine()
    return _recommendation_engine


async def close_all_services() -> None:
    """Gracefully closes async HTTPX clients on application shutdown."""
    for svc in (_buoy_service, _weather_service, _scraper_service):
        if svc is not None:
            try:
                await svc.close()
            except Exception as exc:
                logger.debug(f"Error closing service client: {exc}")


def _hydrate_spot_detail(raw_spot: Any) -> SpotDetail:
    """
    Converts a raw dictionary or SpotDetail instance into a fully enriched SpotDetail
    with board_recommendation, wetsuit_recommendation, and spot metadata populated.
    """
    rec_engine = get_recommendation_engine()
    if isinstance(raw_spot, SpotDetail):
        spot = raw_spot
    elif isinstance(raw_spot, dict):
        spot = SpotDetail.model_validate(raw_spot)
    else:
        raise ValueError(f"Unsupported spot payload type: {type(raw_spot)}")

    meta = SPOTS.get(spot.spot_id, SPOTS["holly_beach"])
    cons = spot.consensus

    # Ensure 48h timeline is populated if missing or empty
    if not cons.timeline_48h:
        cache = get_cache_service()
        fc_fallback = cache.get_fallback(f"forecast:{spot.spot_id}")
        if isinstance(fc_fallback, dict) and fc_fallback.get("hourly"):
            cons.timeline_48h = [
                HourlyForecastPoint.model_validate(pt) for pt in fc_fallback["hourly"]
            ]
        else:
            primary_buoy = next(
                (r for r in cons.source_breakdown if "buoy" in r.source_type or "ndbc" in r.source_id),
                None,
            )
            cons.timeline_48h = rec_engine.synthesize_48h_timeline(
                spot_id=spot.spot_id,
                hourly_model_points=[],
                live_buoy_reading=primary_buoy,
            )

    if spot.board_recommendation is None:
        spot.board_recommendation = rec_engine.recommend_board(
            breaking_height_ft=cons.breaking_height_ft,
            period_sec=cons.primary_swell_period_sec,
            wind_regime=cons.wind_character,
            spot_id=spot.spot_id,
        )

    if spot.wetsuit_recommendation is None:
        spot.wetsuit_recommendation = rec_engine.recommend_wetsuit(
            water_temp_f=cons.water_temp_f,
            wind_speed_mph=cons.wind_speed_mph,
        )

    if spot.bottom_type is None:
        spot.bottom_type = meta.get("bottom_type")
    if spot.ideal_swell_window is None:
        spot.ideal_swell_window = meta.get("ideal_swell_window")
    if spot.access_notes is None:
        spot.access_notes = meta.get("access_notes")

    return spot


async def compute_live_spot_detail(spot_id: str) -> SpotDetail:
    """
    Fetches live marine telemetry (NOAA NDBC + CO-OPS), numerical weather models
    (Open-Meteo WW3 + NWS Marine), and external surf forecast scrapers
    (Surfline, Surf-Forecast.com, Surf Captain), then runs the Consensus &
    Wave Transformation Engine.
    """
    if spot_id not in SPOTS:
        raise ValueError(f"Unknown spot_id: {spot_id}")

    meta = SPOTS[spot_id]
    buoy_svc = get_buoy_service()
    weather_svc = get_weather_service()
    scraper_svc = get_scraper_service()
    consensus_eng = get_consensus_engine()
    cache = get_cache_service()

    station_task = buoy_svc.fetch_station_pair(spot_id)
    weather_task = weather_svc.fetch_spot_weather(spot_id)
    hourly_task = weather_svc.fetch_open_meteo_hourly(spot_id)
    scraper_task = scraper_svc.fetch_all_scrapers(spot_id)

    station_res, weather_res, hourly_res, scraper_res = await asyncio.gather(
        station_task, weather_task, hourly_task, scraper_task, return_exceptions=True
    )

    readings: List[NormalizedMarineReading] = []

    # 1. Buoy & CO-OPS observations
    if isinstance(station_res, dict):
        for obs in station_res.get("all", []):
            if isinstance(obs, BuoyObservation):
                # Cache individual buoy observation as well
                cache.set(
                    f"buoy:{obs.station_id}",
                    obs.model_dump(mode="json"),
                    ttl=get_settings().CACHE_TTL_BUOYS,
                    source="live",
                )
                readings.append(BuoyService.to_normalized_reading(obs))

    # 2. Numerical weather & wave models (Open-Meteo WW3 + NWS Coastal Marine Zone)
    if isinstance(weather_res, list):
        for r in weather_res:
            if isinstance(r, NormalizedMarineReading):
                readings.append(r)

    # 3. External surf website scrapers (Surfline, Surf-Forecast.com, Surf Captain)
    if isinstance(scraper_res, list):
        for r in scraper_res:
            if isinstance(r, NormalizedMarineReading):
                readings.append(r)

    if not readings:
        raise RuntimeError(f"All upstream sources failed for spot '{spot_id}'")

    # If all readings degraded to internal fallback objects, prefer the curated disk fallback
    if all(r.status == "fallback" for r in readings):
        fallback_spot = cache.get_fallback(f"spot:{spot_id}")
        if fallback_spot:
            return _hydrate_spot_detail(fallback_spot)

    tide_predictions = station_res.get("tide_predictions", []) if isinstance(station_res, dict) else []
    hourly_weather = hourly_res if isinstance(hourly_res, list) and hourly_res else None

    consensus: ConsensusForecast = consensus_eng.generate_spot_consensus(
        spot_id=spot_id,
        readings=readings,
        hourly_weather=hourly_weather,
        tide_predictions=tide_predictions,
    )

    spot_detail = SpotDetail(
        spot_id=spot_id,
        spot_name=meta["spot_name"],
        parish=meta["parish"],
        coordinates=meta["coordinates"],
        shoreline_bearing_deg=meta["shoreline_bearing_deg"],
        offshore_wind_deg=meta["offshore_wind_deg"],
        shoaling_k_net=meta["shoaling_k_net"],
        consensus=consensus,
        recommendation=consensus.best_window,
        source_breakdown=consensus.source_breakdown,
    )
    return _hydrate_spot_detail(spot_detail)


async def fetch_spot_with_cache(spot_id: str) -> tuple[SpotDetail, str]:
    """Retrieves a SpotDetail via CacheService with automatic fallback."""
    cache = get_cache_service()
    settings = get_settings()

    async def _fetcher() -> Dict[str, Any]:
        detail = await compute_live_spot_detail(spot_id)
        return detail.model_dump(mode="json")

    raw_val, cache_status = await cache.get_or_set(
        key=f"spot:{spot_id}",
        fetch_coro=_fetcher,
        ttl=settings.CACHE_TTL_FORECASTS,
        fallback_key=f"spot:{spot_id}",
    )

    if raw_val is None:
        raw_val = cache.get_fallback(f"spot:{spot_id}")
        cache_status = "FALLBACK"

    spot_detail = _hydrate_spot_detail(raw_val)
    if cache_status == "FALLBACK":
        spot_detail.consensus.is_fallback = True
    return spot_detail, cache_status


async def compute_live_buoy(station_id: str) -> BuoyObservation:
    """Fetches a single NDBC buoy or CO-OPS tide station observation."""
    norm_id = station_id.upper()
    if norm_id not in BUOY_STATIONS:
        raise ValueError(f"Unknown station_id: {station_id}")

    meta = BUOY_STATIONS[norm_id]
    buoy_svc = get_buoy_service()
    if meta["station_type"] == "coops_tide_station":
        obs = await buoy_svc.fetch_coops_data(norm_id)
    else:
        obs = await buoy_svc.fetch_ndbc_buoy(norm_id)

    if not obs.is_online:
        raise RuntimeError(f"Station {norm_id} returned offline observation")
    return obs


async def fetch_buoy_with_cache(station_id: str) -> tuple[BuoyObservation, str]:
    """Retrieves a BuoyObservation via CacheService with automatic fallback."""
    norm_id = station_id.upper()
    cache = get_cache_service()
    settings = get_settings()

    async def _fetcher() -> Dict[str, Any]:
        obs = await compute_live_buoy(norm_id)
        return obs.model_dump(mode="json")

    raw_val, status = await cache.get_or_set(
        key=f"buoy:{norm_id}",
        fetch_coro=_fetcher,
        ttl=settings.CACHE_TTL_BUOYS,
        fallback_key=f"buoy:{norm_id}",
    )
    if raw_val is None:
        raw_val = cache.get_fallback(f"buoy:{norm_id}")
        status = "FALLBACK"

    if isinstance(raw_val, BuoyObservation):
        return raw_val, status
    return BuoyObservation.model_validate(raw_val), status


async def warm_live_cache_background() -> None:
    """
    Non-blocking background task that warms live spot and buoy telemetry
    after FastAPI startup completes.
    """
    try:
        await asyncio.gather(
            fetch_spot_with_cache("holly_beach"),
            fetch_spot_with_cache("grand_isle"),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.warning(f"Background cache warm encountered an error: {exc}")


# ==============================================================================
# REST API ENDPOINTS
# ==============================================================================

@router.api_route("/health", methods=["GET", "HEAD"], response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    """
    Lightweight readiness/liveness probe for Railway deployment healthchecks.
    Never blocks on external network I/O.
    """
    cache = get_cache_service()
    stats = cache.get_stats()
    return HealthCheckResponse(
        status="healthy",
        service="louisiana-surf-finder",
        version="1.0.0",
        timestamp=datetime.now(timezone.utc),
        uptime_seconds=round(time.monotonic() - _app_start_time, 2),
        cache=stats,
        spots_loaded=list(SPOTS.keys()),
        buoys_monitored=["42035", "42354", "42001", "CAPL1", "GISL1"],
    )


@router.get("/spots")
async def list_spots(response: Response) -> Dict[str, Any]:
    """
    Returns both Louisiana surf spots (Holly Beach & Grand Isle) with their
    full multi-source consensus, 48-hour golden window recommendations,
    quiver/wetsuit advice, and head-to-head spot comparison.
    """
    (hb_detail, hb_status), (gi_detail, gi_status) = await asyncio.gather(
        fetch_spot_with_cache("holly_beach"),
        fetch_spot_with_cache("grand_isle"),
    )

    overall_cache_status = (
        "HIT"
        if hb_status == "HIT" and gi_status == "HIT"
        else ("FALLBACK" if "FALLBACK" in (hb_status, gi_status) else hb_status)
    )
    response.headers["X-Cache"] = overall_cache_status

    rec_engine = get_recommendation_engine()
    comparison = rec_engine.generate_spot_comparison(
        hb_detail.consensus, gi_detail.consensus
    )

    return {
        "spots": [hb_detail.model_dump(mode="json"), gi_detail.model_dump(mode="json")],
        "comparison": comparison,
        "cache_status": overall_cache_status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/spots/{spot_id}", response_model=SpotDetail)
async def get_spot_detail(spot_id: str, response: Response) -> SpotDetail:
    """Returns full SpotDetail consensus and source breakdown for a single spot."""
    clean_id = spot_id.lower().strip().replace("-", "_")
    if clean_id not in SPOTS:
        raise HTTPException(
            status_code=404,
            detail=f"Spot '{spot_id}' not found. Valid spots: {list(SPOTS.keys())}",
        )

    spot_detail, cache_status = await fetch_spot_with_cache(clean_id)
    response.headers["X-Cache"] = cache_status
    return spot_detail


@router.get("/spots/{spot_id}/forecast", response_model=TimelineForecastResponse)
async def get_spot_forecast(
    spot_id: str,
    response: Response,
    hours: int = Query(default=48, ge=1, le=72),
) -> TimelineForecastResponse:
    """Returns the 48-hour hourly breaking surf & wind forecast timeline for a spot."""
    clean_id = spot_id.lower().strip().replace("-", "_")
    if clean_id not in SPOTS:
        raise HTTPException(
            status_code=404,
            detail=f"Spot '{spot_id}' not found. Valid spots: {list(SPOTS.keys())}",
        )

    spot_detail, cache_status = await fetch_spot_with_cache(clean_id)
    response.headers["X-Cache"] = cache_status

    hourly = spot_detail.consensus.timeline_48h[:hours]
    return TimelineForecastResponse(
        spot_id=spot_detail.spot_id,
        spot_name=spot_detail.spot_name,
        generated_at=spot_detail.consensus.timestamp,
        hourly=hourly,
    )


@router.get("/spots/{spot_id}/sources")
async def get_spot_sources(spot_id: str, response: Response) -> Dict[str, Any]:
    """Returns the source-by-source comparison matrix for a spot."""
    clean_id = spot_id.lower().strip().replace("-", "_")
    if clean_id not in SPOTS:
        raise HTTPException(
            status_code=404,
            detail=f"Spot '{spot_id}' not found. Valid spots: {list(SPOTS.keys())}",
        )

    spot_detail, cache_status = await fetch_spot_with_cache(clean_id)
    response.headers["X-Cache"] = cache_status
    cons = spot_detail.consensus
    return {
        "spot_id": spot_detail.spot_id,
        "spot_name": spot_detail.spot_name,
        "consensus_breaking_height_ft": cons.breaking_height_ft,
        "consensus_period_sec": cons.primary_swell_period_sec,
        "confidence_score_pct": cons.confidence_score_pct,
        "confidence_label": cons.confidence_label,
        "divergence_warning": cons.divergence_warning,
        "sources": [s.model_dump(mode="json") for s in cons.source_breakdown],
    }


@router.get("/buoys", response_model=List[BuoyObservation])
async def list_buoys(response: Response) -> List[BuoyObservation]:
    """
    Returns telemetry for all 5 monitored Gulf of Mexico & coastal Louisiana stations:
    NDBC 42035, CDIP/NDBC 42354, NDBC 42001, CO-OPS CAPL1 (8768094), and CO-OPS GISL1 (8761724).
    """
    station_ids = ["42035", "42354", "42001", "CAPL1", "GISL1"]
    results = await asyncio.gather(
        *(fetch_buoy_with_cache(sid) for sid in station_ids),
        return_exceptions=True,
    )

    buoys: List[BuoyObservation] = []
    statuses: List[str] = []
    cache = get_cache_service()

    for sid, res in zip(station_ids, results):
        if isinstance(res, tuple):
            obs, st = res
            buoys.append(obs)
            statuses.append(st)
        else:
            fb = cache.get_fallback(f"buoy:{sid}")
            if fb:
                buoys.append(BuoyObservation.model_validate(fb))
                statuses.append("FALLBACK")

    overall_status = (
        "HIT"
        if statuses and all(s == "HIT" for s in statuses)
        else ("FALLBACK" if "FALLBACK" in statuses else "MISS")
    )
    response.headers["X-Cache"] = overall_status
    return buoys


@router.get("/buoys/{station_id}", response_model=BuoyObservation)
async def get_buoy(station_id: str, response: Response) -> BuoyObservation:
    """Returns telemetry for a single NDBC buoy or CO-OPS tide station."""
    norm_id = station_id.upper().strip()
    if norm_id not in BUOY_STATIONS:
        raise HTTPException(
            status_code=404,
            detail=f"Buoy station '{station_id}' not monitored.",
        )

    obs, cache_status = await fetch_buoy_with_cache(norm_id)
    response.headers["X-Cache"] = cache_status
    return obs


@router.get("/comparison")
async def compare_spots(response: Response) -> Dict[str, Any]:
    """Returns head-to-head surf comparison between Holly Beach and Grand Isle."""
    (hb_detail, hb_status), (gi_detail, gi_status) = await asyncio.gather(
        fetch_spot_with_cache("holly_beach"),
        fetch_spot_with_cache("grand_isle"),
    )
    response.headers["X-Cache"] = "HIT" if hb_status == "HIT" and gi_status == "HIT" else "MISS"
    rec_engine = get_recommendation_engine()
    return rec_engine.generate_spot_comparison(hb_detail.consensus, gi_detail.consensus)


@router.post("/refresh")
async def trigger_refresh(response: Response) -> Dict[str, Any]:
    """Invalidates cached spot forecasts and triggers a fresh multi-source fetch."""
    cache = get_cache_service()
    for sid in SPOTS:
        cache.invalidate(f"spot:{sid}")
    for bid in ("42035", "42354", "42001", "CAPL1", "GISL1"):
        cache.invalidate(f"buoy:{bid}")

    return await list_spots(response)
