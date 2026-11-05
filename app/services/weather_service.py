"""
app/services/weather_service.py
Open-Meteo Marine API & National Weather Service (NWS) Marine Ingestion Clients.

Provides:
- Open-Meteo WaveWatch III marine and weather model ingestion
- NWS Marine Coastal Zone text narrative parsing (GMZ450 & GMZ553)
- NWS Quantitative Gridpoint ingestion (LCH & LIX)
- ISO 8601 duration parser (PT3H, P1D, etc.)
- Resilient fallback shielding against upstream API outages
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple, Union
import httpx

from app.models.schemas import NormalizedMarineReading, HourlyForecastPoint
from app.config import (
    SPOTS,
    OPEN_METEO_MARINE_URL,
    OPEN_METEO_WEATHER_URL,
    NWS_ZONE_URL,
    NWS_GRIDPOINT_URL,
    get_settings,
)
from app.services.buoy_service import degrees_to_cardinal, cardinal_to_degrees

logger = logging.getLogger("louisiana_surf.weather_service")

# Regex Patterns for NWS Marine Text Forecasts
SEAS_PATTERN = re.compile(
    r"Seas\s+(?:around\s+(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:foot|feet|ft)\s+or\s+less)",
    re.IGNORECASE
)

WAVE_DETAIL_PATTERN = re.compile(
    r"([A-Za-z]+)\s+(\d+(?:\.\d+)?)\s*(?:to\s*(\d+(?:\.\d+)?))?\s*(?:foot|feet|ft)\s+at\s+(\d+(?:\.\d+)?)\s*seconds?",
    re.IGNORECASE
)

WIND_PATTERN = re.compile(
    r"([A-Za-z]+)\s+winds?\s+(?:around\s+(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*knots?\s+or\s+less)",
    re.IGNORECASE
)


def parse_iso_duration(duration_str: str) -> timedelta:
    """
    Parse ISO 8601 duration strings, e.g. 'PT1H', 'PT3H', 'P1DT6H', 'PT30M'.
    Defaults to 1 hour if string is unrecognized.
    """
    if not duration_str:
        return timedelta(hours=1)
    pattern = r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
    m = re.match(pattern, duration_str.strip())
    if not m:
        return timedelta(hours=1)
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_valid_time(valid_time_str: str) -> Tuple[datetime, datetime]:
    """Parse NWS grid validTime formatted as '<start_iso>/<duration_iso>'."""
    if "/" not in valid_time_str:
        dt = datetime.fromisoformat(valid_time_str.replace("Z", "+00:00"))
        return dt, dt + timedelta(hours=1)
    start_str, duration_str = valid_time_str.split("/", 1)
    if start_str.endswith("Z"):
        start_dt = datetime.fromisoformat(start_str[:-1] + "+00:00")
    else:
        start_dt = datetime.fromisoformat(start_str)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    duration = parse_iso_duration(duration_str)
    return start_dt, start_dt + duration


class WeatherService:
    """
    Ingestion service for Open-Meteo Marine API and National Weather Service (NWS).
    Guarantees non-blocking async execution, regex narrative parsing, and fallback safety.
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 5.0):
        self._client = client
        self._timeout = timeout

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            settings = get_settings()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=settings.REQUEST_CONNECT_TIMEOUT, read=settings.REQUEST_READ_TIMEOUT),
                headers={"User-Agent": settings.USER_AGENT}
            )
        return self._client

    # --------------------------------------------------------------------------
    # Open-Meteo Marine & Forecast API
    # --------------------------------------------------------------------------
    async def fetch_open_meteo(
        self,
        spot_id_or_lat: Union[str, float],
        lon: Optional[float] = None,
    ) -> NormalizedMarineReading:
        """
        Fetch numerical forecast from Open-Meteo Marine and Weather APIs.
        Accepts either spot_id ('holly_beach', 'grand_isle') or (lat, lon).
        """
        if isinstance(spot_id_or_lat, str):
            spot_id = spot_id_or_lat
            meta = SPOTS.get(spot_id)
            if not meta:
                raise ValueError(f"Unknown spot_id: {spot_id}")
            lat = meta["coordinates"]["latitude"]
            lng = meta["coordinates"]["longitude"]
        else:
            lat = spot_id_or_lat
            lng = lon if lon is not None else 0.0
            # Identify closest spot_id
            spot_id = "holly_beach" if lng < -91.5 else "grand_isle"

        marine_params = {
            "latitude": lat,
            "longitude": lng,
            "hourly": "wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,swell_wave_period,wind_wave_height,wind_wave_period",
            "length_unit": "imperial",
            "wind_speed_unit": "mph",
            "timezone": "UTC",
            "forecast_days": 3,
        }
        weather_params = {
            "latitude": lat,
            "longitude": lng,
            "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m",
            "wind_speed_unit": "mph",
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
            "forecast_days": 3,
        }

        client = await self._get_client()
        try:
            marine_resp, weather_resp = await asyncio.gather(
                client.get(OPEN_METEO_MARINE_URL, params=marine_params),
                client.get(OPEN_METEO_WEATHER_URL, params=weather_params),
                return_exceptions=True
            )

            if isinstance(marine_resp, Exception):
                raise marine_resp
            marine_resp.raise_for_status()
            marine_data = marine_resp.json()

            wx_data = {}
            if not isinstance(weather_resp, Exception) and weather_resp.status_code == 200:
                wx_data = weather_resp.json()

            return self._parse_open_meteo_reading(spot_id, marine_data, wx_data)
        except Exception as exc:
            logger.warning(f"Open-Meteo fetch failed for {spot_id}: {exc}; using fallback")
            return self._get_fallback_reading(spot_id, "open_meteo")

    def _parse_open_meteo_reading(
        self, spot_id: str, marine_data: Dict[str, Any], weather_data: Dict[str, Any]
    ) -> NormalizedMarineReading:
        hourly_m = marine_data.get("hourly", {})
        hourly_wx = weather_data.get("hourly", {})
        times = hourly_m.get("time", [])

        def safe_period(p: Any) -> Optional[float]:
            if p is None:
                return None
            try:
                val = float(p)
                return round(val, 1) if val >= 0.0 else None
            except (ValueError, TypeError):
                return None

        idx = 0
        now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
        for i, t in enumerate(times):
            if t >= now_utc_str:
                idx = i
                break

        ts = datetime.now(timezone.utc)
        if times and idx < len(times):
            try:
                t_val = times[idx]
                ts = datetime.fromisoformat(t_val)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                ts = datetime.now(timezone.utc)

        wave_h = hourly_m.get("wave_height", [None])[idx] if hourly_m.get("wave_height") else None
        wave_p = hourly_m.get("wave_period", [None])[idx] if hourly_m.get("wave_period") else None
        wave_d = hourly_m.get("wave_direction", [None])[idx] if hourly_m.get("wave_direction") else None

        swell_h = hourly_m.get("swell_wave_height", [None])[idx] if hourly_m.get("swell_wave_height") else None
        swell_p = hourly_m.get("swell_wave_period", [None])[idx] if hourly_m.get("swell_wave_period") else None
        swell_d = hourly_m.get("swell_wave_direction", [None])[idx] if hourly_m.get("swell_wave_direction") else None

        wind_wave_h = hourly_m.get("wind_wave_height", [None])[idx] if hourly_m.get("wind_wave_height") else None
        wind_wave_p = hourly_m.get("wind_wave_period", [None])[idx] if hourly_m.get("wind_wave_period") else None

        wind_spd = hourly_wx.get("wind_speed_10m", [None])[idx] if hourly_wx.get("wind_speed_10m") else None
        wind_gst = hourly_wx.get("wind_gusts_10m", [None])[idx] if hourly_wx.get("wind_gusts_10m") else None
        wind_dir = hourly_wx.get("wind_direction_10m", [None])[idx] if hourly_wx.get("wind_direction_10m") else None
        air_temp = hourly_wx.get("temperature_2m", [None])[idx] if hourly_wx.get("temperature_2m") else None

        return NormalizedMarineReading(
            source_id="open_meteo",
            source_name="Open-Meteo Marine (WW3)",
            source_type="forecast_model",
            timestamp=ts,
            wave_height_ft=round(float(wave_h), 2) if wave_h is not None else None,
            wave_period_sec=safe_period(wave_p),
            wave_direction_deg=float(wave_d) if wave_d is not None else None,
            swell_height_ft=round(float(swell_h), 2) if swell_h is not None else None,
            swell_period_sec=safe_period(swell_p),
            swell_direction_deg=float(swell_d) if swell_d is not None else None,
            swell_direction_cardinal=degrees_to_cardinal(swell_d),
            wind_wave_height_ft=round(float(wind_wave_h), 2) if wind_wave_h is not None else None,
            wind_wave_period_sec=safe_period(wind_wave_p),
            wind_speed_mph=round(float(wind_spd), 1) if wind_spd is not None else None,
            wind_gust_mph=round(float(wind_gst), 1) if wind_gst is not None else None,
            wind_direction_deg=float(wind_dir) if wind_dir is not None else None,
            wind_direction_cardinal=degrees_to_cardinal(wind_dir),
            air_temp_f=round(float(air_temp), 1) if air_temp is not None else None,
            raw_condition_label="High-Res WaveWatch III",
            confidence_weight=0.30,
            status="valid"
        )

    # --------------------------------------------------------------------------
    # NWS Marine Coastal Zones (Narrative Text Forecasts)
    # --------------------------------------------------------------------------
    async def fetch_nws_marine_forecast(self, zone_id: str) -> NormalizedMarineReading:
        """
        Fetch and parse National Weather Service Coastal Marine Forecast.
        Target zones: GMZ450 (Cameron to High Island) & GMZ553 (SW Pass to Port Fourchon).
        """
        zone_url = NWS_ZONE_URL.format(zone_id=zone_id)
        client = await self._get_client()
        try:
            resp = await client.get(zone_url)
            resp.raise_for_status()
            data = resp.json()
            return self._parse_nws_zone_forecast(zone_id, data)
        except Exception as exc:
            logger.warning(f"NWS zone fetch failed for {zone_id}: {exc}; using fallback")
            spot_id = "holly_beach" if "450" in zone_id else "grand_isle"
            return self._get_fallback_reading(spot_id, f"nws_{zone_id.lower()}")

    def _parse_nws_zone_forecast(self, zone_id: str, data: Dict[str, Any]) -> NormalizedMarineReading:
        periods = data.get("properties", {}).get("periods", [])
        if not periods:
            raise ValueError(f"No forecast periods returned from NWS zone {zone_id}")

        curr = periods[0]
        text = curr.get("detailedForecast", "")
        ts = datetime.now(timezone.utc)

        # Seas regex
        m_seas = SEAS_PATTERN.search(text)
        wave_h = None
        if m_seas:
            if m_seas.group(1):
                wave_h = float(m_seas.group(1))
            elif m_seas.group(2) and m_seas.group(3):
                wave_h = (float(m_seas.group(2)) + float(m_seas.group(3))) / 2.0
            elif m_seas.group(4):
                wave_h = float(m_seas.group(4))

        # Swell detail regex
        swells = []
        for m in WAVE_DETAIL_PATTERN.finditer(text):
            d = m.group(1).upper()
            h = (float(m.group(2)) + float(m.group(3))) / 2.0 if m.group(3) else float(m.group(2))
            p = float(m.group(4))
            swells.append((h, p, d))

        swell_h, swell_p, swell_d = None, None, None
        if swells:
            # Sort by period descending
            swells.sort(key=lambda s: (s[1], s[0]), reverse=True)
            swell_h, swell_p, swell_d = swells[0]

        # Wind regex
        m_wind = WIND_PATTERN.search(text)
        wind_card, wind_spd = None, None
        if m_wind:
            wind_card = m_wind.group(1).upper()
            if m_wind.group(2):
                kts = float(m_wind.group(2))
            elif m_wind.group(3) and m_wind.group(4):
                kts = (float(m_wind.group(3)) + float(m_wind.group(4))) / 2.0
            elif m_wind.group(5):
                kts = float(m_wind.group(5))
            else:
                kts = 5.0
            wind_spd = round(kts * 1.15078, 1)

        source_key = f"nws_{zone_id.lower()}"
        return NormalizedMarineReading(
            source_id=source_key,
            source_name=f"NWS Coastal Marine {zone_id}",
            source_type="forecast_model",
            timestamp=ts,
            wave_height_ft=round(wave_h, 2) if wave_h is not None else None,
            wave_period_sec=swell_p,
            wave_direction_deg=cardinal_to_degrees(swell_d),
            swell_height_ft=round(swell_h, 2) if swell_h is not None else None,
            swell_period_sec=swell_p,
            swell_direction_deg=cardinal_to_degrees(swell_d),
            swell_direction_cardinal=swell_d,
            wind_speed_mph=wind_spd,
            wind_direction_deg=cardinal_to_degrees(wind_card),
            wind_direction_cardinal=wind_card,
            raw_condition_label=text[:160] if text else None,
            confidence_weight=0.10,
            status="valid"
        )

    # --------------------------------------------------------------------------
    # NWS High-Resolution Gridpoints
    # --------------------------------------------------------------------------
    async def fetch_nws_gridpoints(
        self, office: str, grid_x: int, grid_y: int
    ) -> NormalizedMarineReading:
        """
        Fetch quantitative gridpoint data from NWS API.
        e.g. office='LCH', grid_x=51, grid_y=74 for Holly Beach.
        """
        grid_url = f"https://api.weather.gov/gridpoints/{office}/{grid_x},{grid_y}"
        client = await self._get_client()
        try:
            resp = await client.get(grid_url)
            resp.raise_for_status()
            data = resp.json()
            return self._parse_nws_gridpoint_data(office, grid_x, grid_y, data)
        except Exception as exc:
            logger.warning(f"NWS gridpoint fetch failed for {office}/{grid_x},{grid_y}: {exc}")
            spot_id = "holly_beach" if office == "LCH" else "grand_isle"
            return self._get_fallback_reading(spot_id, f"nws_{office.lower()}_grid")

    def _parse_nws_gridpoint_data(
        self, office: str, grid_x: int, grid_y: int, data: Dict[str, Any]
    ) -> NormalizedMarineReading:
        props = data.get("properties", {})
        now = datetime.now(timezone.utc)

        def get_current_val(prop_name: str) -> Optional[float]:
            prop = props.get(prop_name, {})
            for item in prop.get("values", []):
                vt = item.get("validTime")
                if vt:
                    s, e = parse_valid_time(vt)
                    if s <= now < e:
                        return item.get("value")
            vals = prop.get("values", [])
            return vals[0].get("value") if vals else None

        wave_h_m = get_current_val("waveHeight")
        wave_p = get_current_val("wavePeriod")
        wind_spd_kmh = get_current_val("windSpeed")
        wind_dir = get_current_val("windDirection")
        wind_gst_kmh = get_current_val("windGust")
        temp_c = get_current_val("temperature")

        wave_h_ft = round(wave_h_m * 3.28084, 2) if wave_h_m is not None else None
        wind_spd_mph = round(wind_spd_kmh * 0.621371, 1) if wind_spd_kmh is not None else None
        wind_gst_mph = round(wind_gst_kmh * 0.621371, 1) if wind_gst_kmh is not None else None
        air_temp_f = round((temp_c * 9/5) + 32, 1) if temp_c is not None else None

        return NormalizedMarineReading(
            source_id=f"nws_{office.lower()}_grid",
            source_name=f"NWS Gridpoint {office}/{grid_x},{grid_y}",
            source_type="forecast_model",
            timestamp=now,
            wave_height_ft=wave_h_ft,
            wave_period_sec=float(wave_p) if wave_p is not None else None,
            wind_speed_mph=wind_spd_mph,
            wind_gust_mph=wind_gst_mph,
            wind_direction_deg=float(wind_dir) if wind_dir is not None else None,
            wind_direction_cardinal=degrees_to_cardinal(wind_dir),
            air_temp_f=air_temp_f,
            raw_condition_label=f"NWS Quantitative Grid {office}/{grid_x},{grid_y}",
            confidence_weight=0.10,
            status="valid"
        )

    async def fetch_spot_weather(self, spot_id: str) -> List[NormalizedMarineReading]:
        """Fetch all relevant numerical weather models (Open-Meteo + NWS Zone) for a spot."""
        meta = SPOTS.get(spot_id)
        if not meta:
            raise ValueError(f"Unknown spot_id: {spot_id}")

        zone_id = meta["nws_zone_id"]
        tasks = [
            self.fetch_open_meteo(spot_id),
            self.fetch_nws_marine_forecast(zone_id),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        readings: List[NormalizedMarineReading] = []
        for r in results:
            if isinstance(r, NormalizedMarineReading):
                readings.append(r)
        return readings

    def _get_fallback_reading(self, spot_id: str, source_id: str) -> NormalizedMarineReading:
        ts = datetime.now(timezone.utc)
        defaults: Dict[str, Dict[str, NormalizedMarineReading]] = {
            "holly_beach": {
                "open_meteo": NormalizedMarineReading(
                    source_id="open_meteo",
                    source_name="Open-Meteo Marine (WW3)",
                    source_type="forecast_model",
                    timestamp=ts,
                    wave_height_ft=3.4,
                    wave_period_sec=7.5,
                    wave_direction_deg=165.0,
                    swell_height_ft=2.7,
                    swell_period_sec=7.5,
                    swell_direction_deg=160.0,
                    swell_direction_cardinal="SSE",
                    wind_speed_mph=11.5,
                    wind_direction_deg=355.0,
                    wind_direction_cardinal="N",
                    air_temp_f=68.5,
                    raw_condition_label="High-Res WaveWatch III (Fallback)",
                    confidence_weight=0.15,
                    status="fallback"
                ),
                "nws_gmz450": NormalizedMarineReading(
                    source_id="nws_gmz450",
                    source_name="NWS Coastal Marine GMZ450",
                    source_type="forecast_model",
                    timestamp=ts,
                    wave_height_ft=3.3,
                    wave_period_sec=7.0,
                    wave_direction_deg=170.0,
                    swell_height_ft=2.8,
                    swell_period_sec=7.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=12.0,
                    wind_direction_deg=355.0,
                    wind_direction_cardinal="N",
                    air_temp_f=68.0,
                    raw_condition_label="Seas 3 to 4 ft. North winds 10-15 kt. (Fallback)",
                    confidence_weight=0.05,
                    status="fallback"
                )
            },
            "grand_isle": {
                "open_meteo": NormalizedMarineReading(
                    source_id="open_meteo",
                    source_name="Open-Meteo Marine (WW3)",
                    source_type="forecast_model",
                    timestamp=ts,
                    wave_height_ft=3.9,
                    wave_period_sec=8.0,
                    wave_direction_deg=150.0,
                    swell_height_ft=3.4,
                    swell_period_sec=8.0,
                    swell_direction_deg=150.0,
                    swell_direction_cardinal="SSE",
                    wind_speed_mph=10.0,
                    wind_direction_deg=335.0,
                    wind_direction_cardinal="NNW",
                    air_temp_f=70.5,
                    raw_condition_label="High-Res WaveWatch III (Fallback)",
                    confidence_weight=0.15,
                    status="fallback"
                ),
                "nws_gmz553": NormalizedMarineReading(
                    source_id="nws_gmz553",
                    source_name="NWS Coastal Marine GMZ553",
                    source_type="forecast_model",
                    timestamp=ts,
                    wave_height_ft=3.8,
                    wave_period_sec=7.5,
                    wave_direction_deg=150.0,
                    swell_height_ft=3.2,
                    swell_period_sec=7.5,
                    swell_direction_deg=150.0,
                    swell_direction_cardinal="SSE",
                    wind_speed_mph=10.0,
                    wind_direction_deg=340.0,
                    wind_direction_cardinal="NNW",
                    air_temp_f=70.0,
                    raw_condition_label="Seas 3 to 5 ft. Northwest winds 10 kt. (Fallback)",
                    confidence_weight=0.05,
                    status="fallback"
                )
            }
        }
        return defaults.get(spot_id, {}).get(source_id, NormalizedMarineReading(
            source_id=source_id,
            source_name="Fallback Marine Model",
            source_type="forecast_model",
            timestamp=ts,
            wave_height_ft=2.5,
            wave_period_sec=6.0,
            confidence_weight=0.05,
            status="fallback"
        ))
