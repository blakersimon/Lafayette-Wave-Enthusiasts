"""
tests/test_challenger_m2_retry.py
Empirical Challenger M2 Retry 1 verification suite.
Exhaustively tests:
1. Open-Meteo live and synthetic calm period parsing (status == 'valid').
2. Surf-Forecast live and synthetic tooltip isolation and clean wind state extraction.
3. NWS marine narrative regex coverage ('feet or less', 'ft', ranges, wave details).
4. Cache pre-seeding non-shadowing and cross-event-loop lock isolation.
"""

import asyncio
import re
import unittest
from datetime import datetime, timezone
import httpx

from app.models.schemas import NormalizedMarineReading, BuoyObservation
from app.services.weather_service import (
    WeatherService,
    SEAS_PATTERN,
    WAVE_DETAIL_PATTERN,
    WIND_PATTERN,
)
from app.services.scraper_service import ScraperService
from app.services.cache_service import CacheService
from app.services.buoy_service import BuoyService


class TestChallengerM2RetryVerification(unittest.IsolatedAsyncioTestCase):
    """Empirical verification suite for Challenger M2 Retry 1."""

    # =========================================================================
    # 1. Open-Meteo Ingestion & Calm Period Parsing
    # =========================================================================
    async def test_open_meteo_live_calm_periods_valid_status(self):
        """Live API test: Open-Meteo returns valid status without validation error or fallback."""
        ws = WeatherService()
        for spot_id in ["holly_beach", "grand_isle"]:
            reading = await ws.fetch_open_meteo(spot_id)
            self.assertEqual(
                reading.status,
                "valid",
                f"Open-Meteo degraded to fallback for {spot_id}: status={reading.status}"
            )
            self.assertEqual(reading.source_id, "open_meteo")
            self.assertIsNotNone(reading.wave_height_ft)
            self.assertIsNotNone(reading.wave_period_sec)
            self.assertGreaterEqual(reading.wave_height_ft, 0.0)
            self.assertGreaterEqual(reading.wave_period_sec, 0.0)
        if ws._client and not ws._client.is_closed:
            await ws._client.aclose()

    def test_open_meteo_synthetic_calm_and_subsecond_periods(self):
        """Synthetic tests: calm sea with 0.0s, subsecond, None, and negative periods."""
        ws = WeatherService()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")

        # 0.0s period (flat calm)
        marine_data_zero = {
            "hourly": {
                "time": [now_str],
                "wave_height": [0.0],
                "wave_period": [0.0],
                "wave_direction": [180.0],
                "swell_wave_height": [0.0],
                "swell_wave_period": [0.0],
                "swell_wave_direction": [180.0],
                "wind_wave_height": [0.0],
                "wind_wave_period": [0.0],
            }
        }
        wx_data = {
            "hourly": {
                "time": [now_str],
                "wind_speed_10m": [2.0],
                "wind_direction_10m": [180.0],
                "temperature_2m": [70.0],
            }
        }
        reading_zero = ws._parse_open_meteo_reading("holly_beach", marine_data_zero, wx_data)
        self.assertEqual(reading_zero.status, "valid")
        self.assertEqual(reading_zero.wave_height_ft, 0.0)
        self.assertEqual(reading_zero.wave_period_sec, 0.0)
        self.assertEqual(reading_zero.wind_wave_period_sec, 0.0)
        self.assertEqual(reading_zero.swell_period_sec, 0.0)

        # Subsecond period (0.5s chop)
        marine_data_sub = {
            "hourly": {
                "time": [now_str],
                "wave_height": [0.3],
                "wave_period": [0.5],
                "swell_wave_period": [0.8],
                "wind_wave_period": [0.4],
            }
        }
        reading_sub = ws._parse_open_meteo_reading("grand_isle", marine_data_sub, wx_data)
        self.assertEqual(reading_sub.status, "valid")
        self.assertEqual(reading_sub.wave_period_sec, 0.5)
        self.assertEqual(reading_sub.swell_period_sec, 0.8)
        self.assertEqual(reading_sub.wind_wave_period_sec, 0.4)

        # Negative period should normalize to None without crashing
        marine_data_neg = {
            "hourly": {
                "time": [now_str],
                "wave_height": [1.0],
                "wave_period": [-1.0],
                "swell_wave_period": [-5.0],
                "wind_wave_period": [-0.5],
            }
        }
        reading_neg = ws._parse_open_meteo_reading("holly_beach", marine_data_neg, wx_data)
        self.assertEqual(reading_neg.status, "valid")
        self.assertIsNone(reading_neg.wave_period_sec)
        self.assertIsNone(reading_neg.swell_period_sec)
        self.assertIsNone(reading_neg.wind_wave_period_sec)

    # =========================================================================
    # 2. Surf-Forecast Scraping & Clean Wind State Isolation
    # =========================================================================
    async def test_surf_forecast_live_scraping_clean_wind_state(self):
        """Live API test: Surf-Forecast scraping extracts clean state without tooltip legend."""
        ss = ScraperService()
        reading = await ss.fetch_surf_forecast("holly_beach")
        self.assertIsNotNone(reading)
        self.assertEqual(reading.status, "valid")
        self.assertEqual(reading.source_id, "surf_forecast")
        self.assertIn("Rating:", reading.raw_condition_label)
        self.assertIn("State:", reading.raw_condition_label)

        # Verify tooltip text leak is completely absent
        legend_tokens = [
            "Wind Stateon-shore",
            "cross-onshorecross-shore",
            "cross-offshoreoff-shore",
            "cross-offshore",
            "glassy",
        ]
        for token in legend_tokens:
            self.assertNotIn(
                token,
                reading.raw_condition_label,
                f"Found legend tooltip leak '{token}' in raw_condition_label: {reading.raw_condition_label}"
            )
        if ss._client and not ss._client.is_closed:
            await ss._client.aclose()

    def test_surf_forecast_synthetic_tooltip_isolation(self):
        """Synthetic test: exact DOM layout with header tooltip legend and multiple table cells."""
        ss = ScraperService()
        html_with_legend = """
        <table class="forecast-table__table">
          <tr data-row="rating">
            <th>Rating</th>
            <td><div class="star-rating__rating">3</div></td>
          </tr>
          <tr data-row="swell">
            <th>Wave</th>
            <td>
              <div class="swell-icon" data-height="0.6"></div>
              <div class="forecast-table__swell-period">5</div>
              <div>SSE</div>
            </td>
          </tr>
          <tr data-row="wind">
            <th>Wind</th>
            <td>
              <div class="wind-icon" data-speed="12.0"></div>
              <div class="wind-icon__letters">SSE</div>
            </td>
          </tr>
          <tr data-row="wind-state">
            <th>
              <span class="tooltip">Wind State
                <span>on-shore</span><span>cross-onshore</span><span>cross-shore</span>
                <span>cross-offshore</span><span>off-shore</span><span>glassy</span>
              </span>
            </th>
            <td>
              <div class="wind-state cross-on">cross-on</div>
            </td>
            <td>
              <div class="wind-state cross-off">cross-off</div>
            </td>
          </tr>
        </table>
        """
        reading = ss.parse_surf_forecast_html("holly_beach", html_with_legend)
        self.assertEqual(reading.status, "valid")
        self.assertEqual(reading.raw_condition_label, "Rating: 3/10, State: cross-on")
        self.assertNotIn("on-shorecross-onshore", reading.raw_condition_label)

    # =========================================================================
    # 3. NWS Narrative Regex Coverage ('feet or less', 'ft', variations)
    # =========================================================================
    def test_nws_seas_pattern_feet_or_less_and_abbreviations(self):
        """Verify SEAS_PATTERN against 'feet or less', 'ft or less', and variations."""
        cases = [
            ("Seas 1 foot or less.", 1.0),
            ("Seas 2 feet or less.", 2.0),
            ("Seas 2 ft or less.", 2.0),
            ("Seas 3 feet or less.", 3.0),
            ("Seas 4 ft or less.", 4.0),
            ("Seas around 1 foot.", 1.0),
            ("Seas around 2 feet.", 2.0),
            ("Seas around 3 ft.", 3.0),
            ("Seas around 4 ft.", 4.0),
            ("Seas 1 to 2 feet.", 1.5),
            ("Seas 2 to 3 ft.", 2.5),
            ("Seas 3 to 5 feet.", 4.0),
            ("Seas 4 to 6 ft.", 5.0),
            ("SEAS 2 FEET OR LESS.", 2.0),
            ("seas 2 ft or less.", 2.0),
            ("Seas  2   feet   or   less.", 2.0),
        ]
        for text, expected_h in cases:
            m = SEAS_PATTERN.search(text)
            self.assertIsNotNone(m, f"SEAS_PATTERN failed to match: '{text}'")
            if m.group(1):
                val = float(m.group(1))
            elif m.group(2) and m.group(3):
                val = (float(m.group(2)) + float(m.group(3))) / 2.0
            elif m.group(4):
                val = float(m.group(4))
            else:
                self.fail(f"No capture group populated for '{text}'")
            self.assertEqual(val, expected_h, f"Failed wave height for '{text}': got {val}, expected {expected_h}")

    def test_nws_wave_detail_pattern_ft_and_feet(self):
        """Verify WAVE_DETAIL_PATTERN matches 'ft', 'feet', 'foot' across singular and plural."""
        cases = [
            ("Wave Detail: South 2 ft at 5 seconds", ("SOUTH", 2.0, 5.0)),
            ("Wave Detail: South 2 feet at 5 seconds", ("SOUTH", 2.0, 5.0)),
            ("Wave Detail: South 1 foot at 4 seconds", ("SOUTH", 1.0, 4.0)),
            ("Wave Detail: South 1 to 2 ft at 6 seconds", ("SOUTH", 1.5, 6.0)),
            ("Wave Detail: South 2 to 4 feet at 7 seconds", ("SOUTH", 3.0, 7.0)),
            ("Wave Detail: SSE 3 ft at 6 seconds", ("SSE", 3.0, 6.0)),
            ("WAVE DETAIL: SOUTH 2 FT AT 5 SECONDS", ("SOUTH", 2.0, 5.0)),
        ]
        for text, (exp_card, exp_h, exp_p) in cases:
            m = WAVE_DETAIL_PATTERN.search(text)
            self.assertIsNotNone(m, f"WAVE_DETAIL_PATTERN failed to match: '{text}'")
            card = m.group(1).upper()
            h = (float(m.group(2)) + float(m.group(3))) / 2.0 if m.group(3) else float(m.group(2))
            p = float(m.group(4))
            self.assertEqual(card, exp_card)
            self.assertEqual(h, exp_h)
            self.assertEqual(p, exp_p)

    async def test_nws_live_zone_forecast_ingestion(self):
        """Live API test: fetch NWS coastal marine zones GMZ450 and GMZ553."""
        ws = WeatherService()
        for zone_id in ["GMZ450", "GMZ553"]:
            reading = await ws.fetch_nws_marine_forecast(zone_id)
            self.assertEqual(
                reading.status,
                "valid",
                f"NWS live fetch for {zone_id} failed: status={reading.status}"
            )
            self.assertIsNotNone(reading.raw_condition_label)
            self.assertIsNotNone(reading.wave_height_ft)
        if ws._client and not ws._client.is_closed:
            await ws._client.aclose()

    # =========================================================================
    # 4. Cache Pre-Seeding Non-Shadowing & Concurrency Safety
    # =========================================================================
    async def test_cache_pre_seeding_does_not_shadow_live_fetch(self):
        """Verify pre-seeded disk fallbacks have ttl=0 and do NOT shadow live upstream fetches."""
        cache = CacheService()
        cache.load_disk_fallbacks()

        fetch_called = False

        async def mock_live_fetch():
            nonlocal fetch_called
            fetch_called = True
            return {"spot": "holly_beach", "live": True}

        val, status = await cache.get_or_set("spot:holly_beach", mock_live_fetch, ttl=600.0)
        self.assertTrue(fetch_called, "Live fetch was shadowed by disk fallback!")
        self.assertEqual(status, "MISS", f"Expected MISS on first live fetch, got {status}")
        self.assertEqual(val, {"spot": "holly_beach", "live": True})

        # Second call within TTL should HIT
        fetch_called = False
        val2, status2 = await cache.get_or_set("spot:holly_beach", mock_live_fetch, ttl=600.0)
        self.assertFalse(fetch_called)
        self.assertEqual(status2, "HIT")

    async def test_cross_event_loop_concurrency(self):
        """Ensure CacheService handles cross-thread or multi-loop requests without lock errors."""
        cache = CacheService()

        def run_in_thread(thread_idx: int):
            async def worker():
                async def fetch():
                    await asyncio.sleep(0.005)
                    return f"val_{thread_idx}"
                return await cache.get_or_set(f"thread_key_{thread_idx % 3}", fetch, ttl=60.0)

            return asyncio.run(worker())

        tasks = [asyncio.to_thread(run_in_thread, i) for i in range(12)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, Exception, f"Cross-event-loop task failed: {r}")


if __name__ == "__main__":
    unittest.main()
