"""
app/services/buoy_service.py
NOAA NDBC Buoy & CO-OPS Tides Ingestion Service for Louisiana Surf Finder.

Ingests and parses:
- NOAA NDBC .spec (spectral wave decomposition) & .txt (meteorological) feeds
- NOAA CO-OPS REST datagetter API (water level, wind, water temp, predictions)
- 1-hour Tide Slope Engine (derivative dv/dt) for Rising/Falling/Slack classification
- Compass bidirectional conversion utilities
- Resilient fallback handling for missing fields and station outages
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple

import httpx

from app.models.schemas import BuoyObservation, NormalizedMarineReading
from app.config import (
    BUOY_STATIONS,
    COOPS_TO_STATION_MAP,
    STATION_TO_COOPS_MAP,
    SPOTS,
    NDBC_SPEC_URL,
    NDBC_TXT_URL,
    COOPS_API_URL,
    get_settings,
)

logger = logging.getLogger("louisiana_surf.buoy_service")

# ---------------------------------------------------------------------------
# Compass & Unit Conversion Utilities
# ---------------------------------------------------------------------------

CARDINAL_POINTS_16 = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
]

CARDINAL_TO_DEG_MAP: Dict[str, float] = {
    "N": 0.0,
    "NNE": 22.5,
    "NE": 45.0,
    "ENE": 67.5,
    "E": 90.0,
    "ESE": 112.5,
    "SE": 135.0,
    "SSE": 157.5,
    "S": 180.0,
    "SSW": 202.5,
    "SW": 225.0,
    "WSW": 247.5,
    "W": 270.0,
    "WNW": 292.5,
    "NW": 315.0,
    "NNW": 337.5,
    "NORTH": 0.0,
    "SOUTH": 180.0,
    "EAST": 90.0,
    "WEST": 270.0,
    "NORTHEAST": 45.0,
    "NORTHWEST": 315.0,
    "SOUTHEAST": 135.0,
    "SOUTHWEST": 225.0,
}


def degrees_to_cardinal(deg: Optional[float]) -> Optional[str]:
    """Convert degrees clockwise from True North (0-360) to 16-point cardinal string."""
    if deg is None:
        return None
    try:
        deg_float = float(deg)
        norm_deg = deg_float % 360.0
        idx = int((norm_deg + 11.25) / 22.5) % 16
        return CARDINAL_POINTS_16[idx]
    except (ValueError, TypeError):
        return None


def cardinal_to_degrees(cardinal: Optional[str]) -> Optional[float]:
    """Convert 16-point cardinal compass string to degrees True North."""
    if not cardinal:
        return None
    clean = str(cardinal).strip().upper()
    if clean in ("MM", "-99", "N/A", "VAR", "VARIABLE", "NONE"):
        return None
    return CARDINAL_TO_DEG_MAP.get(clean)


def parse_float_val(token: Any) -> Optional[float]:
    """Safely parse float token, converting NDBC/CO-OPS missing sentinels to None."""
    if token is None:
        return None
    s = str(token).strip()
    if not s or s in ("MM", "-99", "-99.0", "99.0", "999", "999.0", "N/A", "null", "None"):
        return None
    try:
        val = float(s)
        if val in (-99.0, 999.0, 99.0):
            return None
        return val
    except (ValueError, TypeError):
        return None


def meters_to_feet(m: Optional[float]) -> Optional[float]:
    """Convert meters to feet rounded to 2 decimal places."""
    return round(m * 3.28084, 2) if m is not None else None


def ms_to_mph(ms: Optional[float]) -> Optional[float]:
    """Convert meters per second to miles per hour rounded to 1 decimal place."""
    return round(ms * 2.23694, 1) if ms is not None else None


def knots_to_mph(kts: Optional[float]) -> Optional[float]:
    """Convert knots to miles per hour rounded to 1 decimal place."""
    return round(kts * 1.15078, 1) if kts is not None else None


def celsius_to_fahrenheit(c: Optional[float]) -> Optional[float]:
    """Convert Celsius to Fahrenheit rounded to 1 decimal place."""
    return round((c * 1.8) + 32.0, 1) if c is not None else None


# ---------------------------------------------------------------------------
# NDBC Raw Parsers (.spec & .txt)
# ---------------------------------------------------------------------------

def parse_ndbc_spec(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse NOAA NDBC .spec spectral wave summary.
    Header line 1: #YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
    Header line 2: #yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT
    """
    if not text:
        return None

    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    data_lines = [l for l in lines if not l.startswith("#")]
    if not data_lines:
        return None

    tokens = data_lines[0].split()
    if len(tokens) < 15:
        return None

    try:
        yr = int(tokens[0])
        mo = int(tokens[1])
        dy = int(tokens[2])
        hr = int(tokens[3])
        mn = int(tokens[4])
        dt = datetime(yr, mo, dy, hr, mn, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        dt = datetime.now(timezone.utc)

    wvht_m = parse_float_val(tokens[5])
    swh_m = parse_float_val(tokens[6])
    swp = parse_float_val(tokens[7])
    wwh_m = parse_float_val(tokens[8])
    wwp = parse_float_val(tokens[9])

    swd_str = tokens[10] if tokens[10] not in ("MM", "N/A", "-99") else None
    wwd_str = tokens[11] if tokens[11] not in ("MM", "N/A", "-99") else None
    steepness = tokens[12] if tokens[12] not in ("MM", "N/A", "-99") else None

    apd = parse_float_val(tokens[13])
    mwd = parse_float_val(tokens[14])

    swell_dir_deg = cardinal_to_degrees(swd_str) if swd_str else mwd

    return {
        "timestamp": dt,
        "wave_height_ft": meters_to_feet(wvht_m),
        "swell_height_ft": meters_to_feet(swh_m),
        "swell_period_sec": round(swp, 1) if swp is not None else None,
        "swell_direction_cardinal": swd_str,
        "swell_direction_deg": swell_dir_deg,
        "wind_wave_height_ft": meters_to_feet(wwh_m),
        "wind_wave_period_sec": round(wwp, 1) if wwp is not None else None,
        "wind_wave_direction_cardinal": wwd_str,
        "steepness": steepness,
        "average_period_sec": round(apd, 1) if apd is not None else None,
        "mean_wave_direction_deg": mwd,
    }


def parse_ndbc_txt(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse NOAA NDBC .txt standard meteorological data.
    Header line: #YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
    """
    if not text:
        return None

    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    data_lines = [l for l in lines if not l.startswith("#")]
    if not data_lines:
        return None

    tokens = data_lines[0].split()
    if len(tokens) < 13:
        return None

    try:
        yr = int(tokens[0])
        mo = int(tokens[1])
        dy = int(tokens[2])
        hr = int(tokens[3])
        mn = int(tokens[4])
        dt = datetime(yr, mo, dy, hr, mn, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        dt = datetime.now(timezone.utc)

    wdir = parse_float_val(tokens[5])
    wspd_ms = parse_float_val(tokens[6])
    gst_ms = parse_float_val(tokens[7])
    wvht_m = parse_float_val(tokens[8]) if len(tokens) > 8 else None
    dpd = parse_float_val(tokens[9]) if len(tokens) > 9 else None
    apd = parse_float_val(tokens[10]) if len(tokens) > 10 else None
    mwd = parse_float_val(tokens[11]) if len(tokens) > 11 else None
    pres = parse_float_val(tokens[12]) if len(tokens) > 12 else None
    atmp_c = parse_float_val(tokens[13]) if len(tokens) > 13 else None
    wtmp_c = parse_float_val(tokens[14]) if len(tokens) > 14 else None
    tide_ft = parse_float_val(tokens[18]) if len(tokens) > 18 else None

    return {
        "timestamp": dt,
        "wind_direction_deg": wdir,
        "wind_direction_cardinal": degrees_to_cardinal(wdir),
        "wind_speed_mph": ms_to_mph(wspd_ms),
        "wind_gust_mph": ms_to_mph(gst_ms),
        "wave_height_ft": meters_to_feet(wvht_m),
        "dominant_period_sec": round(dpd, 1) if dpd is not None else None,
        "average_period_sec": round(apd, 1) if apd is not None else None,
        "mean_wave_direction_deg": mwd,
        "pressure_hpa": pres,
        "air_temp_f": celsius_to_fahrenheit(atmp_c),
        "water_temp_f": celsius_to_fahrenheit(wtmp_c),
        "tide_level_ft": tide_ft,
    }


def merge_ndbc_data(
    station_id: str,
    spec_data: Optional[Dict[str, Any]],
    txt_data: Optional[Dict[str, Any]],
) -> BuoyObservation:
    """
    Merge .spec wave breakdown and .txt meteorological readings into a unified BuoyObservation.
    Falls back gracefully when wave sensors report MM or .spec is missing (404).
    """
    meta = BUOY_STATIONS.get(station_id, {
        "station_id": station_id,
        "station_name": f"NOAA Station {station_id}",
        "latitude": 0.0,
        "longitude": 0.0,
        "station_type": "ndbc_buoy",
    })

    now_utc = datetime.now(timezone.utc)
    ts = now_utc
    if spec_data and spec_data.get("timestamp"):
        ts = spec_data["timestamp"]
    elif txt_data and txt_data.get("timestamp"):
        ts = txt_data["timestamp"]

    # Wave metrics: prioritize .spec over .txt
    wave_height_ft = None
    if spec_data and spec_data.get("wave_height_ft") is not None:
        wave_height_ft = spec_data["wave_height_ft"]
    elif txt_data and txt_data.get("wave_height_ft") is not None:
        wave_height_ft = txt_data["wave_height_ft"]

    dominant_period = None
    if txt_data and txt_data.get("dominant_period_sec") is not None:
        dominant_period = txt_data["dominant_period_sec"]
    elif spec_data and spec_data.get("swell_period_sec") is not None:
        dominant_period = spec_data["swell_period_sec"]

    avg_period = None
    if spec_data and spec_data.get("average_period_sec") is not None:
        avg_period = spec_data["average_period_sec"]
    elif txt_data and txt_data.get("average_period_sec") is not None:
        avg_period = txt_data["average_period_sec"]

    mwd = None
    if spec_data and spec_data.get("mean_wave_direction_deg") is not None:
        mwd = spec_data["mean_wave_direction_deg"]
    elif txt_data and txt_data.get("mean_wave_direction_deg") is not None:
        mwd = txt_data["mean_wave_direction_deg"]

    # Swell & wind-wave components
    swell_h = spec_data.get("swell_height_ft") if spec_data else None
    swell_p = spec_data.get("swell_period_sec") if spec_data else None
    swell_d_deg = spec_data.get("swell_direction_deg") if spec_data else None
    swell_d_card = spec_data.get("swell_direction_cardinal") if spec_data else None
    ww_h = spec_data.get("wind_wave_height_ft") if spec_data else None
    ww_p = spec_data.get("wind_wave_period_sec") if spec_data else None
    steepness = spec_data.get("steepness") if spec_data else None

    # Meteorology from .txt
    wind_spd = txt_data.get("wind_speed_mph") if txt_data else None
    wind_gst = txt_data.get("wind_gust_mph") if txt_data else None
    wind_dir = txt_data.get("wind_direction_deg") if txt_data else None
    wind_card = txt_data.get("wind_direction_cardinal") if txt_data else None
    wtmp = txt_data.get("water_temp_f") if txt_data else None
    atmp = txt_data.get("air_temp_f") if txt_data else None
    pres = txt_data.get("pressure_hpa") if txt_data else None

    is_online = bool(spec_data or txt_data)
    status_str = "online" if is_online else "offline"

    return BuoyObservation(
        station_id=station_id,
        station_name=meta["station_name"],
        latitude=meta["latitude"],
        longitude=meta["longitude"],
        station_type=meta["station_type"],
        timestamp=ts,
        last_updated=now_utc,
        wave_height_ft=wave_height_ft,
        dominant_period_sec=dominant_period,
        average_period_sec=avg_period,
        mean_wave_direction_deg=mwd,
        swell_height_ft=swell_h,
        swell_period_sec=swell_p,
        swell_direction_deg=swell_d_deg,
        swell_direction_cardinal=swell_d_card,
        wind_wave_height_ft=ww_h,
        wind_wave_period_sec=ww_p,
        steepness=steepness,
        wind_speed_mph=wind_spd,
        wind_gust_mph=wind_gst,
        wind_direction_deg=wind_dir,
        wind_direction_cardinal=wind_card,
        water_temp_f=wtmp,
        air_temp_f=atmp,
        pressure_hpa=pres,
        tide_level_ft=None,
        tide_mllw_ft=None,
        is_online=is_online,
        status=status_str,
    )


# ---------------------------------------------------------------------------
# Tide Slope Engine & NOAA CO-OPS Parsers
# ---------------------------------------------------------------------------

def classify_tide_slope(
    points: List[Tuple[datetime, float]],
    target_time: Optional[datetime] = None,
    window_minutes: int = 60,
    threshold_ft: float = 0.10,
) -> Dict[str, Any]:
    """
    Tide Slope Engine: computes derivative dv/dt over ~1 hour to classify tide state.
    Classification:
    - Delta v > +0.10 ft  => Rising
    - Delta v < -0.10 ft  => Falling
    - |Delta v| <= 0.10 ft & v >= mean => Slack High
    - |Delta v| <= 0.10 ft & v < mean  => Slack Low
    """
    if not points:
        return {
            "tide_state": "Slack High",
            "tide_status": "slack_high",
            "current_water_level_ft": 0.0,
            "slope_ft_per_hr": 0.0,
            "delta_v_ft": 0.0,
            "mean_water_level_ft": 0.0,
        }

    sorted_pts = sorted(points, key=lambda p: p[0])
    mean_v = sum(p[1] for p in sorted_pts) / len(sorted_pts)

    if target_time is None:
        target_pt = sorted_pts[-1]
    else:
        target_pt = min(sorted_pts, key=lambda p: abs((p[0] - target_time).total_seconds()))

    t_curr, v_curr = target_pt
    cutoff_time = t_curr - timedelta(minutes=window_minutes)

    candidate_pts = [p for p in sorted_pts if p[0] <= t_curr]
    if len(candidate_pts) <= 1:
        prev_pt = sorted_pts[0]
    else:
        prev_pt = min(candidate_pts, key=lambda p: abs((p[0] - cutoff_time).total_seconds()))

    t_prev, v_prev = prev_pt
    dt_hours = (t_curr - t_prev).total_seconds() / 3600.0

    delta_v = round(v_curr - v_prev, 4)
    slope_rate = (delta_v / dt_hours) if dt_hours > 0.1 else delta_v
    delta_v_eval = round(delta_v, 2)

    if delta_v_eval > threshold_ft:
        tide_state = "Rising"
        tide_status = "rising"
    elif delta_v_eval < -threshold_ft:
        tide_state = "Falling"
        tide_status = "falling"
    elif v_curr >= mean_v:
        tide_state = "Slack High"
        tide_status = "slack_high"
    else:
        tide_state = "Slack Low"
        tide_status = "slack_low"

    return {
        "tide_state": tide_state,
        "tide_status": tide_status,
        "current_water_level_ft": round(v_curr, 2),
        "slope_ft_per_hr": round(slope_rate, 2),
        "delta_v_ft": round(delta_v, 2),
        "mean_water_level_ft": round(mean_v, 2),
    }


def parse_coops_predictions(data: Dict[str, Any]) -> List[Tuple[datetime, float]]:
    """Parse CO-OPS JSON predictions into datetime/water_level pairs."""
    results: List[Tuple[datetime, float]] = []
    preds = data.get("predictions", [])
    for p in preds:
        t_str = p.get("t")
        v_str = p.get("v")
        if t_str and v_str is not None:
            val = parse_float_val(v_str)
            if val is not None:
                try:
                    dt = datetime.strptime(t_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    results.append((dt, val))
                except ValueError:
                    continue
    return results


# ---------------------------------------------------------------------------
# BuoyService Ingestion Client
# ---------------------------------------------------------------------------

class BuoyService:
    """
    Production Ingestion Service for NOAA NDBC wave buoys and CO-OPS tide/wind stations.
    Features:
    - High-concurrency async fetching via httpx.AsyncClient
    - Resilient fallback shielding (never raises unhandled exceptions)
    - Full spectral decomposition and inshore tide slope engine
    - Station-pair cluster orchestration for Louisiana surf breaks
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 5.0):
        self._client = client
        self._timeout = timeout
        self._latest_tide_predictions: Dict[str, List[Dict[str, Any]]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            settings = get_settings()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=settings.REQUEST_CONNECT_TIMEOUT, read=settings.REQUEST_READ_TIMEOUT),
                headers={"User-Agent": settings.USER_AGENT}
            )
        return self._client

    # Aliases for parser functions matching task requirements
    @staticmethod
    def parse_spec_file(text: str) -> Optional[Dict[str, Any]]:
        return parse_ndbc_spec(text)

    @staticmethod
    def parse_txt_file(text: str) -> Optional[Dict[str, Any]]:
        return parse_ndbc_txt(text)

    @staticmethod
    def calculate_tide_slope(
        points: List[Tuple[datetime, float]],
        target_time: Optional[datetime] = None,
        window_minutes: int = 60,
        threshold_ft: float = 0.10,
    ) -> Dict[str, Any]:
        return classify_tide_slope(points, target_time, window_minutes, threshold_ft)

    @staticmethod
    def degrees_to_cardinal(deg: Optional[float]) -> Optional[str]:
        return degrees_to_cardinal(deg)

    @staticmethod
    def cardinal_to_degrees(cardinal: Optional[str]) -> Optional[float]:
        return cardinal_to_degrees(cardinal)

    async def fetch_ndbc_feed(self, station_id: str, ext: str = "txt") -> Optional[str]:
        """Fetch raw NDBC text or spec feed."""
        url = NDBC_SPEC_URL.format(station_id=station_id) if ext == "spec" else NDBC_TXT_URL.format(station_id=station_id)
        client = await self._get_client()
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 404:
                logger.debug(f"NDBC station {station_id}.{ext} not found (HTTP 404)")
                return None
            else:
                logger.warning(f"NDBC {station_id}.{ext} returned HTTP {resp.status_code}")
                return None
        except httpx.TimeoutException:
            logger.warning(f"NDBC {station_id}.{ext} timed out after {self._timeout}s")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch NDBC {station_id}.{ext}: {e}")
            return None

    async def fetch_ndbc_buoy(self, station_id: str) -> BuoyObservation:
        """Fetch and merge both .spec and .txt feeds for an NDBC station."""
        spec_text, txt_text = await asyncio.gather(
            self.fetch_ndbc_feed(station_id, "spec"),
            self.fetch_ndbc_feed(station_id, "txt"),
            return_exceptions=True,
        )

        spec_data = parse_ndbc_spec(spec_text) if isinstance(spec_text, str) else None
        txt_data = parse_ndbc_txt(txt_text) if isinstance(txt_text, str) else None

        return merge_ndbc_data(station_id, spec_data, txt_data)

    async def fetch_coops_product(self, coops_id: str, product: str, **extra_params) -> Optional[Dict[str, Any]]:
        """Query NOAA CO-OPS datagetter JSON API for a specific product."""
        params = {
            "station": coops_id,
            "product": product,
            "datum": "MLLW",
            "time_zone": "gmt",
            "units": "english",
            "format": "json",
            "application": "LouisianaSurfFinder",
        }
        params.update(extra_params)
        client = await self._get_client()
        try:
            resp = await client.get(COOPS_API_URL, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    logger.warning(f"CO-OPS {coops_id} {product} error: {data['error'].get('message')}")
                    return None
                return data
            return None
        except Exception as e:
            logger.warning(f"CO-OPS API request failed for {coops_id} {product}: {e}")
            return None

    async def fetch_coops_data(self, station_id: str) -> BuoyObservation:
        """
        Fetch all products for a CO-OPS inshore station:
        water_level, wind, water_temperature, and harmonic predictions.
        Accepts either CO-OPS ID (e.g. '8768094') or station key (e.g. 'CAPL1').
        """
        coops_id = STATION_TO_COOPS_MAP.get(station_id, station_id)
        # Find station key if numeric coops_id passed
        st_key = COOPS_TO_STATION_MAP.get(coops_id, station_id)
        meta = BUOY_STATIONS.get(st_key, BUOY_STATIONS.get(station_id, {
            "station_id": st_key,
            "station_name": f"CO-OPS Station {coops_id}",
            "latitude": 0.0,
            "longitude": 0.0,
            "station_type": "coops_tide_station",
        }))

        wl_resp, wind_resp, wtmp_resp, pred_resp = await asyncio.gather(
            self.fetch_coops_product(coops_id, "water_level", date="latest"),
            self.fetch_coops_product(coops_id, "wind", date="latest"),
            self.fetch_coops_product(coops_id, "water_temperature", date="latest"),
            self.fetch_coops_product(coops_id, "predictions", range=48),
            return_exceptions=True,
        )

        now_utc = datetime.now(timezone.utc)
        water_level_ft = None
        if isinstance(wl_resp, dict) and "data" in wl_resp and wl_resp["data"]:
            water_level_ft = parse_float_val(wl_resp["data"][0].get("v"))

        wind_spd = None
        wind_gst = None
        wind_dir = None
        wind_card = None
        if isinstance(wind_resp, dict) and "data" in wind_resp and wind_resp["data"]:
            wdata = wind_resp["data"][0]
            wind_spd = knots_to_mph(parse_float_val(wdata.get("s")))
            wind_gst = knots_to_mph(parse_float_val(wdata.get("g")))
            wind_dir = parse_float_val(wdata.get("d"))
            wind_card = wdata.get("dr") or degrees_to_cardinal(wind_dir)

        water_temp_f = None
        if isinstance(wtmp_resp, dict) and "data" in wtmp_resp and wtmp_resp["data"]:
            water_temp_f = parse_float_val(wtmp_resp["data"][0].get("v"))

        tide_level_ft = water_level_ft
        tide_status = None
        if isinstance(pred_resp, dict) and "predictions" in pred_resp:
            self._latest_tide_predictions[coops_id] = pred_resp["predictions"]
            self._latest_tide_predictions[st_key] = pred_resp["predictions"]
            self._latest_tide_predictions[station_id] = pred_resp["predictions"]
            pts = parse_coops_predictions(pred_resp)
            slope_info = classify_tide_slope(pts, target_time=now_utc)
            tide_status = slope_info.get("tide_status")
            if tide_level_ft is None:
                tide_level_ft = slope_info.get("current_water_level_ft")

        is_online = any(
            isinstance(r, dict) and ("data" in r or "predictions" in r)
            for r in [wl_resp, wind_resp, wtmp_resp, pred_resp]
        )

        return BuoyObservation(
            station_id=meta["station_id"],
            station_name=meta["station_name"],
            latitude=meta["latitude"],
            longitude=meta["longitude"],
            station_type=meta["station_type"],
            timestamp=now_utc,
            last_updated=now_utc,
            wave_height_ft=None,
            dominant_period_sec=None,
            average_period_sec=None,
            mean_wave_direction_deg=None,
            swell_height_ft=None,
            swell_period_sec=None,
            swell_direction_deg=None,
            swell_direction_cardinal=None,
            wind_wave_height_ft=None,
            wind_wave_period_sec=None,
            steepness=None,
            wind_speed_mph=wind_spd,
            wind_gust_mph=wind_gst,
            wind_direction_deg=wind_dir,
            wind_direction_cardinal=wind_card,
            water_temp_f=water_temp_f,
            air_temp_f=None,
            pressure_hpa=None,
            tide_level_ft=tide_level_ft,
            tide_mllw_ft=tide_level_ft,
            tide_status=tide_status,
            is_online=is_online,
            status="online" if is_online else "offline",
        )

    # Alias for fetch_coops_data
    async def fetch_coops_station(self, station_id: str) -> BuoyObservation:
        return await self.fetch_coops_data(station_id)

    async def fetch_station_pair(self, spot_id: str) -> Dict[str, Any]:
        """
        Fetches telemetry for the sensor pair associated with a spot.
        Returns dictionary containing:
        - 'inshore': inshore tide/wind station observation
        - 'offshore': primary offshore wave buoy observation
        - 'secondary': secondary buoys (if any, e.g. 42001 for Grand Isle)
        - 'all': combined list of all observations
        """
        spot_meta = SPOTS.get(spot_id)
        if not spot_meta:
            raise ValueError(f"Unknown spot_id: {spot_id}")

        inshore_id = spot_meta["inshore_coops_station_id"]
        primary_buoy_id = spot_meta["primary_offshore_buoy_id"]
        secondary_ids = spot_meta.get("secondary_buoy_ids", [])

        # Fetch inshore and primary buoy concurrently
        tasks = [
            self.fetch_coops_data(inshore_id),
            self.fetch_ndbc_buoy(primary_buoy_id),
        ]
        for sec_id in secondary_ids:
            tasks.append(self.fetch_ndbc_buoy(sec_id))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        inshore_obs = results[0] if isinstance(results[0], BuoyObservation) else None
        offshore_obs = results[1] if isinstance(results[1], BuoyObservation) else None

        secondary_obs = []
        for r in results[2:]:
            if isinstance(r, BuoyObservation):
                secondary_obs.append(r)

        all_obs = [obs for obs in [inshore_obs, offshore_obs] + secondary_obs if obs is not None]

        tide_preds = (
            self._latest_tide_predictions.get(inshore_id)
            or self._latest_tide_predictions.get(spot_meta.get("inshore_coops_station_id"))
            or []
        )

        return {
            "spot_id": spot_id,
            "inshore": inshore_obs,
            "offshore": offshore_obs,
            "secondary": secondary_obs,
            "all": all_obs,
            "tide_predictions": tide_preds,
        }

    @staticmethod
    def to_normalized_reading(obs: BuoyObservation) -> NormalizedMarineReading:
        """Convert a BuoyObservation into a NormalizedMarineReading for consensus fusion."""
        st_type = "tide_gauge" if obs.station_type == "coops_tide_station" else "buoy_observation"
        return NormalizedMarineReading(
            source_id=f"ndbc_{obs.station_id.lower()}",
            source_name=obs.station_name,
            source_type=st_type,
            timestamp=obs.timestamp,
            wave_height_ft=obs.wave_height_ft,
            wave_period_sec=obs.dominant_period_sec or obs.average_period_sec,
            wave_direction_deg=obs.mean_wave_direction_deg,
            swell_height_ft=obs.swell_height_ft,
            swell_period_sec=obs.swell_period_sec,
            swell_direction_deg=obs.swell_direction_deg,
            swell_direction_cardinal=obs.swell_direction_cardinal,
            wind_wave_height_ft=obs.wind_wave_height_ft,
            wind_wave_period_sec=obs.wind_wave_period_sec,
            wind_speed_mph=obs.wind_speed_mph,
            wind_gust_mph=obs.wind_gust_mph,
            wind_direction_deg=obs.wind_direction_deg,
            wind_direction_cardinal=obs.wind_direction_cardinal,
            water_temp_f=obs.water_temp_f,
            air_temp_f=obs.air_temp_f,
            pressure_hpa=obs.pressure_hpa,
            tide_ft=obs.tide_level_ft,
            tide_status=obs.tide_status,
            raw_condition_label=obs.steepness,
            confidence_weight=0.9 if obs.is_online else 0.2,
            status="valid" if obs.is_online else "stale",
        )
