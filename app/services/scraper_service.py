"""
app/services/scraper_service.py
Web scrapers for Surf Captain and Surf-Forecast.com with resilient HTML parsing,
BeautifulSoup DOM traversal, regex fallbacks, and multi-tier outage shielding.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import httpx
from bs4 import BeautifulSoup

from app.models.schemas import NormalizedMarineReading
from app.config import (
    SPOTS,
    SURF_CAPTAIN_BASE_URL,
    SURF_FORECAST_BASE_URL,
    get_settings,
)
from app.services.buoy_service import cardinal_to_degrees, degrees_to_cardinal

logger = logging.getLogger("louisiana_surf.scraper_service")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


SURFLINE_BASE_URL = "https://services.surfline.com/kbyg/spots/forecasts/wave?spotId={spot_id}"

SURFLINE_SPOTS: Dict[str, Dict[str, str]] = {
    "holly_beach": {
        "spot_id": "5842041f4e65fad6a7708c9f",
        "slug": "holly-beach",
        "name": "Holly Beach",
    },
    "grand_isle": {
        "spot_id": "5842041f4e65fad6a7708ca0",
        "slug": "grand-isle",
        "name": "Grand Isle",
    },
}


class ScraperService:
    """Live web scrapers for Surf Captain, Surf-Forecast, and Surfline with graceful degradation."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 5.0):
        self._client = client
        self._timeout = timeout

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            settings = get_settings()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=settings.REQUEST_CONNECT_TIMEOUT, read=settings.REQUEST_READ_TIMEOUT),
                headers=BROWSER_HEADERS,
                follow_redirects=True,
            )
        return self._client

    # --------------------------------------------------------------------------
    # Surf Captain Scraper
    # --------------------------------------------------------------------------
    async def fetch_surf_captain(self, spot_id: str) -> NormalizedMarineReading:
        """
        Scrape current surf metrics from Surf Captain for Holly Beach or Grand Isle.
        """
        meta = SPOTS.get(spot_id)
        if not meta or not meta.get("surf_captain_slug"):
            raise ValueError(f"No Surf Captain slug configured for spot: {spot_id}")

        slug = meta["surf_captain_slug"]
        url = SURF_CAPTAIN_BASE_URL.format(slug=slug)

        client = await self._get_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
            return self.parse_surf_captain_html(spot_id, html)
        except Exception as exc:
            logger.warning(f"Surf Captain scraping failed for {spot_id}: {exc}; using fallback")
            return self._get_fallback_reading(spot_id, "surf_captain")

    def parse_surf_captain_html(self, spot_id: str, html: str) -> NormalizedMarineReading:
        """
        Parse Surf Captain HTML using BeautifulSoup and regex fallbacks.
        Extracts current wave height, condition badge, wind, and air temp.
        """
        ts = datetime.now(timezone.utc)
        soup = BeautifulSoup(html, "html.parser")

        # 1. Title & Wave height extraction
        title_tag = soup.find(id="fcst-current-title")
        if title_tag:
            clean_title = title_tag.get_text(separator=" ", strip=True)
        else:
            m_title = re.search(r"id=[\"\x27]fcst-current-title[\"\x27][^>]*>(.*?)</h1", html, re.DOTALL | re.IGNORECASE)
            title_raw = m_title.group(1) if m_title else ""
            clean_title = re.sub(r"<[^>]+>", " ", title_raw).strip()

        wave_h = 1.5  # default
        m_wave = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*(\+)?\s*(?:ft|feet)", clean_title, re.IGNORECASE)
        if m_wave:
            if m_wave.group(2):
                wave_h = round((float(m_wave.group(1)) + float(m_wave.group(2))) / 2.0, 2)
            elif m_wave.group(3):
                wave_h = float(m_wave.group(1)) + 0.5
            else:
                wave_h = float(m_wave.group(1))
        elif "flat" in clean_title.lower():
            wave_h = 0.5

        # 2. Condition badge token
        cond_token = "fair"
        cond_tag = soup.find(class_=re.compile(r"fcst-current-cond"))
        if cond_tag:
            classes = cond_tag.get("class", [])
            for c in ["clean", "fair", "choppy", "flat", "none"]:
                if c in classes:
                    cond_token = c
                    break
        else:
            m_cond = re.search(r"class=[\"\x27]([^\"\x27]*fcst-current-cond[^\"\x27]*)[\"\x27]", html, re.IGNORECASE)
            if m_cond:
                classes = m_cond.group(1).split()
                for c in ["clean", "fair", "choppy", "flat", "none"]:
                    if c in classes:
                        cond_token = c
                        break

        # 3. Weather container extraction
        air_temp, wind_dir, wind_spd, wind_gst = None, None, None, None
        m_wx = re.search(
            r"(\d+)(?:&deg;|°)\s*([A-Za-z]+)\s*@\s*(\d+)(?:\s*(?:&rarr;|→|->)\s*(\d+))?\s*mph",
            html, re.IGNORECASE
        )
        if m_wx:
            air_temp = float(m_wx.group(1))
            wind_dir = m_wx.group(2).upper()
            wind_spd = float(m_wx.group(3))
            wind_gst = float(m_wx.group(4)) if m_wx.group(4) else None

        wind_deg = cardinal_to_degrees(wind_dir) if wind_dir else None

        raw_label = f"{wave_h} ft ({cond_token})"
        if clean_title:
            raw_label = clean_title[:120]

        return NormalizedMarineReading(
            source_id="surf_captain",
            source_name="Surf Captain",
            source_type="scraped_forecast",
            timestamp=ts,
            wave_height_ft=wave_h,
            wave_period_sec=7.0,
            swell_height_ft=round(wave_h * 0.85, 2),
            swell_period_sec=7.0,
            swell_direction_cardinal=wind_dir,
            swell_direction_deg=wind_deg,
            wind_speed_mph=wind_spd,
            wind_gust_mph=wind_gst,
            wind_direction_cardinal=wind_dir,
            wind_direction_deg=wind_deg,
            air_temp_f=air_temp,
            raw_condition_label=raw_label,
            confidence_weight=0.20,
            status="valid"
        )

    # --------------------------------------------------------------------------
    # Surf-Forecast.com Scraper (Holly Beach Only)
    # --------------------------------------------------------------------------
    async def fetch_surf_forecast(self, spot_id: str) -> Optional[NormalizedMarineReading]:
        """
        Scrape current forecast table row from Surf-Forecast.com.
        Note: Grand Isle is not present on Surf-Forecast.com and returns None cleanly.
        """
        meta = SPOTS.get(spot_id)
        if not meta or not meta.get("surf_forecast_slug"):
            logger.debug(f"Surf-Forecast.com not available for spot {spot_id}; skipping gracefully")
            return None

        slug = meta["surf_forecast_slug"]
        url = SURF_FORECAST_BASE_URL.format(slug=slug)

        client = await self._get_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
            return self.parse_surf_forecast_html(spot_id, html)
        except Exception as exc:
            logger.warning(f"Surf-Forecast scraping failed for {spot_id}: {exc}; using fallback")
            return self._get_fallback_reading(spot_id, "surf_forecast")

    def parse_surf_forecast_html(self, spot_id: str, html: str) -> NormalizedMarineReading:
        """
        Parse Surf-Forecast.com forecast table rows.
        Extracts star rating, swell height/period/direction, wind speed, and wind state.
        """
        ts = datetime.now(timezone.utc)
        soup = BeautifulSoup(html, "html.parser")

        # 1. Rating (0-10)
        rating = 0
        rating_row = soup.find("tr", attrs={"data-row": "rating"})
        if rating_row:
            star_div = rating_row.find(class_=re.compile(r"star-rating__rating"))
            if star_div and star_div.text.strip().isdigit():
                rating = int(star_div.text.strip())
        else:
            m_rating = re.search(
                r"data-row=[\"\x27]rating[\"\x27].*?<td[^>]*>.*?star-rating__rating[^>]*>\s*(\d+)",
                html, re.DOTALL | re.IGNORECASE
            )
            if m_rating:
                rating = int(m_rating.group(1))

        # 2. Swell height (meters -> feet), period, and cardinal
        swell_h_ft = 2.0
        swell_p = 5.0
        swell_card = "SSE"

        swell_row = soup.find("tr", attrs={"data-row": "swell"})
        if swell_row:
            swell_icon = swell_row.find(class_=re.compile(r"swell-icon"))
            if swell_icon and swell_icon.get("data-height"):
                try:
                    h_m = float(swell_icon["data-height"])
                    swell_h_ft = round(h_m * 3.28084, 2)
                except ValueError:
                    pass
            period_elem = swell_row.find(class_=re.compile(r"forecast-table__swell-period"))
            if period_elem and period_elem.text.strip().replace(".", "", 1).isdigit():
                swell_p = float(period_elem.text.strip())
        else:
            m_swell_h = re.search(
                r"data-row=[\"\x27]swell[\"\x27].*?<td[^>]*>.*?data-height=[\"\x27]([\d\.]+)[\"\x27]",
                html, re.DOTALL | re.IGNORECASE
            )
            if m_swell_h:
                swell_h_ft = round(float(m_swell_h.group(1)) * 3.28084, 2)
            m_period = re.search(
                r"data-row=[\"\x27]swell[\"\x27].*?<td[^>]*>.*?forecast-table__swell-period[^>]*>\s*(\d+)",
                html, re.DOTALL | re.IGNORECASE
            )
            if m_period:
                swell_p = float(m_period.group(1))

        m_swell_d = re.search(
            r"data-row=[\"\x27]swell[\"\x27].*?<td[^>]*>.*?<div>([A-Z]{1,3})</div>",
            html, re.DOTALL | re.IGNORECASE
        )
        if m_swell_d:
            swell_card = m_swell_d.group(1).upper()
        swell_deg = cardinal_to_degrees(swell_card) or 160.0

        # 3. Wind speed and direction
        wind_spd_mph = 12.0
        wind_card = "SSE"
        wind_row = soup.find("tr", attrs={"data-row": "wind"})
        if wind_row:
            wind_icon = wind_row.find(class_=re.compile(r"wind-icon"))
            if wind_icon and wind_icon.get("data-speed"):
                try:
                    kmh = float(wind_icon["data-speed"])
                    wind_spd_mph = round(kmh * 0.621371, 1)
                except ValueError:
                    pass
            wind_letters = wind_row.find(class_=re.compile(r"wind-icon__letters"))
            if wind_letters:
                wind_card = wind_letters.text.strip().upper()
        else:
            m_wind_spd = re.search(
                r"data-row=[\"\x27]wind[\"\x27].*?<td[^>]*>.*?data-speed=[\"\x27]([\d\.]+)[\"\x27]",
                html, re.DOTALL | re.IGNORECASE
            )
            if m_wind_spd:
                wind_spd_mph = round(float(m_wind_spd.group(1)) * 0.621371, 1)
            m_wind_d = re.search(
                r"data-row=[\"\x27]wind[\"\x27].*?<td[^>]*>.*?wind-icon__letters[^>]*>\s*([A-Za-z]+)",
                html, re.DOTALL | re.IGNORECASE
            )
            if m_wind_d:
                wind_card = m_wind_d.group(1).upper()

        wind_deg = cardinal_to_degrees(wind_card) or 160.0

        # 4. Wind state
        wind_state = "cross-shore"
        state_row = soup.find("tr", attrs={"data-row": "wind-state"})
        if state_row:
            state_td = state_row.find("td")
            if state_td:
                state_div = state_td.find(class_=re.compile(r"wind-state"))
                if state_div and state_div.text.strip():
                    wind_state = state_div.text.strip()
                elif state_td.text.strip():
                    wind_state = state_td.text.strip()
        else:
            m_state = re.search(
                r"data-row=[\"\x27]wind-state[\"\x27].*?<td[^>]*>.*?wind-state[^>]*>\s*([^<]+)",
                html, re.DOTALL | re.IGNORECASE
            )
            if m_state:
                wind_state = m_state.group(1).strip()

        raw_label = f"Rating: {rating}/10, State: {wind_state}"

        return NormalizedMarineReading(
            source_id="surf_forecast",
            source_name="Surf-Forecast.com",
            source_type="scraped_forecast",
            timestamp=ts,
            wave_height_ft=swell_h_ft,
            wave_period_sec=swell_p,
            wave_direction_deg=swell_deg,
            swell_height_ft=round(swell_h_ft * 0.9, 2),
            swell_period_sec=swell_p,
            swell_direction_deg=swell_deg,
            swell_direction_cardinal=swell_card,
            wind_speed_mph=wind_spd_mph,
            wind_direction_deg=wind_deg,
            wind_direction_cardinal=wind_card,
            raw_condition_label=raw_label,
            confidence_weight=0.15,
            status="valid"
        )

    # --------------------------------------------------------------------------
    # Surfline Scraper (Holly Beach & Grand Isle)
    # --------------------------------------------------------------------------
    async def fetch_surfline(self, spot_id: str) -> NormalizedMarineReading:
        """
        Scrape current surf metrics from Surfline (API or HTML) with fallback resilience
        against Cloudflare / 403 / anti-bot challenges.
        """
        spot_info = SURFLINE_SPOTS.get(spot_id)
        if not spot_info:
            logger.warning(f"No Surfline mapping configured for spot {spot_id}; using fallback")
            return self._get_fallback_reading(spot_id, "surfline")

        url = SURFLINE_BASE_URL.format(spot_id=spot_info["spot_id"])
        client = await self._get_client()
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    return self.parse_surfline_json(spot_id, data)
                except Exception:
                    return self.parse_surfline_html(spot_id, resp.text)
            else:
                logger.warning(f"Surfline returned status {resp.status_code} for {spot_id}; using fallback")
                return self._get_fallback_reading(spot_id, "surfline")
        except Exception as exc:
            logger.warning(f"Surfline scraping failed for {spot_id}: {exc}; using fallback")
            return self._get_fallback_reading(spot_id, "surfline")

    def parse_surfline_json(self, spot_id: str, data: Dict[str, Any]) -> NormalizedMarineReading:
        """
        Parse Surfline KBYG wave JSON payload.
        Extracts min/max wave height, dominant swell, wind, and humanRelation condition.
        """
        ts = datetime.now(timezone.utc)
        wave_entries = data.get("data", {}).get("wave", [])
        pt = wave_entries[0] if wave_entries else {}
        surf = pt.get("surf", {})

        min_h = float(surf.get("min", 1.0))
        max_h = float(surf.get("max", min_h))
        wave_h = round((min_h + max_h) / 2.0, 2) if (min_h + max_h) > 0 else max_h
        human_rel = surf.get("humanRelation", "")

        # Primary swell
        swells = pt.get("swells", [])
        swell_h = round(wave_h * 0.85, 2)
        swell_p = 6.5
        swell_dir = 160.0
        if swells:
            primary_swell = max(swells, key=lambda s: float(s.get("height", 0.0)))
            swell_h = float(primary_swell.get("height", swell_h))
            swell_p = float(primary_swell.get("period", swell_p))
            swell_dir = float(primary_swell.get("direction", swell_dir))

        # Wind data
        wind_entries = data.get("data", {}).get("wind", [])
        wind_pt = wind_entries[0] if wind_entries else {}
        wind_spd = float(wind_pt.get("speed", 10.0))
        wind_dir = float(wind_pt.get("direction", 350.0))

        cond_label = f"{round(min_h)}-{round(max_h)} ft ({human_rel})" if human_rel else f"{wave_h} ft"

        return NormalizedMarineReading(
            source_id="surfline",
            source_name="Surfline",
            source_type="scraped_forecast",
            timestamp=ts,
            wave_height_ft=wave_h,
            wave_period_sec=swell_p,
            wave_direction_deg=swell_dir,
            swell_height_ft=swell_h,
            swell_period_sec=swell_p,
            swell_direction_deg=swell_dir,
            swell_direction_cardinal=degrees_to_cardinal(swell_dir),
            wind_speed_mph=wind_spd,
            wind_direction_deg=wind_dir,
            wind_direction_cardinal=degrees_to_cardinal(wind_dir),
            raw_condition_label=cond_label,
            confidence_weight=0.15,
            status="valid"
        )

    def parse_surfline_html(self, spot_id: str, html: str) -> NormalizedMarineReading:
        """
        Parse Surfline HTML web page via BeautifulSoup and regex fallbacks.
        Extracts quiver headline, condition badge, wind, and swell info.
        """
        import json
        ts = datetime.now(timezone.utc)
        soup = BeautifulSoup(html, "html.parser")

        # 1. Check for Next.js embedded data
        next_data_script = soup.find("script", id="__NEXT_DATA__")
        if next_data_script and next_data_script.string:
            try:
                next_json = json.loads(next_data_script.string)
                page_props = next_json.get("props", {}).get("pageProps", {})
                if "wave" in page_props or "data" in page_props:
                    return self.parse_surfline_json(spot_id, page_props)
            except Exception:
                pass

        # 2. Headline & Wave Height
        wave_h = 2.0
        headline_tag = soup.find(class_=re.compile(r"quiver-surf-headline"))
        clean_headline = headline_tag.get_text(strip=True) if headline_tag else ""
        if not clean_headline:
            m_hl = re.search(r"class=[\"\x27][^\"\x27]*quiver-surf-headline[^\"\x27]*[\"\x27][^>]*>(.*?)<", html, re.IGNORECASE)
            clean_headline = m_hl.group(1).strip() if m_hl else ""

        m_wave = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*(?:FT|ft|feet)", clean_headline, re.IGNORECASE)
        if not m_wave:
            m_wave = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*(?:FT|ft|feet)", html, re.IGNORECASE)

        if m_wave:
            if m_wave.group(2):
                wave_h = round((float(m_wave.group(1)) + float(m_wave.group(2))) / 2.0, 2)
            else:
                wave_h = float(m_wave.group(1))
        elif "flat" in clean_headline.lower():
            wave_h = 0.5

        # 3. Condition badge
        cond_badge = "Fair"
        cond_tag = soup.find(class_=re.compile(r"quiver-colored-condition"))
        if cond_tag:
            cond_badge = cond_tag.get_text(strip=True)
        else:
            m_cond = re.search(r"class=[\"\x27][^\"\x27]*quiver-colored-condition[^\"\x27]*[\"\x27][^>]*>(.*?)<", html, re.IGNORECASE)
            if m_cond:
                cond_badge = m_cond.group(1).strip()

        # 4. Wind speed and direction
        wind_spd = 10.0
        wind_card = "N"
        wind_speed_tag = soup.find(class_=re.compile(r"wind-speed"))
        if wind_speed_tag:
            m_s = re.search(r"(\d+(?:\.\d+)?)", wind_speed_tag.get_text())
            if m_s:
                wind_spd = float(m_s.group(1))
        else:
            m_spd = re.search(r"(\d+(?:\.\d+)?)\s*(?:kts|knots|mph)", html, re.IGNORECASE)
            if m_spd:
                wind_spd = float(m_spd.group(1))

        wind_dir_tag = soup.find(class_=re.compile(r"wind-direction"))
        if wind_dir_tag:
            m_d = re.search(r"([A-Za-z]{1,3})", wind_dir_tag.get_text())
            if m_d:
                wind_card = m_d.group(1).upper()
        else:
            m_wind = re.search(r"(\d+(?:\.\d+)?)\s*(?:kts|knots|mph)\s*([A-Za-z]{1,3})", html, re.IGNORECASE)
            if m_wind:
                wind_spd = float(m_wind.group(1))
                wind_card = m_wind.group(2).upper()
        wind_deg = cardinal_to_degrees(wind_card) or 0.0

        raw_label = f"{clean_headline or f'{wave_h} ft'} ({cond_badge})"

        return NormalizedMarineReading(
            source_id="surfline",
            source_name="Surfline",
            source_type="scraped_forecast",
            timestamp=ts,
            wave_height_ft=wave_h,
            wave_period_sec=6.5,
            swell_height_ft=round(wave_h * 0.85, 2),
            swell_period_sec=6.5,
            swell_direction_deg=160.0,
            swell_direction_cardinal="SSE",
            wind_speed_mph=wind_spd,
            wind_direction_deg=wind_deg,
            wind_direction_cardinal=wind_card,
            raw_condition_label=raw_label,
            confidence_weight=0.15,
            status="valid"
        )

    async def fetch_all_scrapers(self, spot_id: str) -> List[NormalizedMarineReading]:
        """Fetch all scrapers applicable to the given spot (Surf Captain, Surfline, Surf-Forecast)."""
        tasks = [
            self.fetch_surf_captain(spot_id),
            self.fetch_surfline(spot_id),
        ]
        meta = SPOTS.get(spot_id, {})
        if meta.get("surf_forecast_slug"):
            tasks.append(self.fetch_surf_forecast(spot_id))

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
                "surf_captain": NormalizedMarineReading(
                    source_id="surf_captain",
                    source_name="Surf Captain",
                    source_type="scraped_forecast",
                    timestamp=ts,
                    wave_height_ft=3.0,
                    wave_period_sec=7.0,
                    wave_direction_deg=170.0,
                    swell_height_ft=2.6,
                    swell_period_sec=7.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=12.0,
                    wind_gust_mph=15.0,
                    wind_direction_deg=360.0,
                    wind_direction_cardinal="N",
                    air_temp_f=68.0,
                    raw_condition_label="3 ft and clean (Fallback)",
                    confidence_weight=0.10,
                    status="fallback"
                ),
                "surf_forecast": NormalizedMarineReading(
                    source_id="surf_forecast",
                    source_name="Surf-Forecast.com",
                    source_type="scraped_forecast",
                    timestamp=ts,
                    wave_height_ft=2.8,
                    wave_period_sec=6.5,
                    wave_direction_deg=165.0,
                    swell_height_ft=2.4,
                    swell_period_sec=6.5,
                    swell_direction_deg=165.0,
                    swell_direction_cardinal="SSE",
                    wind_speed_mph=13.0,
                    wind_direction_deg=355.0,
                    wind_direction_cardinal="N",
                    raw_condition_label="Rating: 4/10, State: offshore (Fallback)",
                    confidence_weight=0.08,
                    status="fallback"
                ),
                "surfline": NormalizedMarineReading(
                    source_id="surfline",
                    source_name="Surfline",
                    source_type="scraped_forecast",
                    timestamp=ts,
                    wave_height_ft=2.5,
                    wave_period_sec=6.5,
                    wave_direction_deg=170.0,
                    swell_height_ft=2.2,
                    swell_period_sec=6.5,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=11.0,
                    wind_direction_deg=360.0,
                    wind_direction_cardinal="N",
                    air_temp_f=70.0,
                    raw_condition_label="2-3 ft (Fair) - Fallback",
                    confidence_weight=0.10,
                    status="fallback"
                ),
            },
            "grand_isle": {
                "surf_captain": NormalizedMarineReading(
                    source_id="surf_captain",
                    source_name="Surf Captain",
                    source_type="scraped_forecast",
                    timestamp=ts,
                    wave_height_ft=3.5,
                    wave_period_sec=7.5,
                    wave_direction_deg=150.0,
                    swell_height_ft=3.0,
                    swell_period_sec=7.5,
                    swell_direction_deg=150.0,
                    swell_direction_cardinal="SSE",
                    wind_speed_mph=11.0,
                    wind_gust_mph=14.0,
                    wind_direction_deg=340.0,
                    wind_direction_cardinal="NNW",
                    air_temp_f=70.0,
                    raw_condition_label="3-4 ft and clean (Fallback)",
                    confidence_weight=0.10,
                    status="fallback"
                ),
                "surfline": NormalizedMarineReading(
                    source_id="surfline",
                    source_name="Surfline",
                    source_type="scraped_forecast",
                    timestamp=ts,
                    wave_height_ft=3.0,
                    wave_period_sec=7.0,
                    wave_direction_deg=155.0,
                    swell_height_ft=2.6,
                    swell_period_sec=7.0,
                    swell_direction_deg=155.0,
                    swell_direction_cardinal="SSE",
                    wind_speed_mph=10.0,
                    wind_direction_deg=340.0,
                    wind_direction_cardinal="NNW",
                    air_temp_f=72.0,
                    raw_condition_label="2-4 ft (Fair to Good) - Fallback",
                    confidence_weight=0.10,
                    status="fallback"
                ),
            }
        }
        return defaults.get(spot_id, {}).get(source_id, NormalizedMarineReading(
            source_id=source_id,
            source_name=source_id.replace("_", " ").title(),
            source_type="scraped_forecast",
            timestamp=ts,
            wave_height_ft=2.0,
            wave_period_sec=6.0,
            confidence_weight=0.05,
            status="fallback"
        ))
