"""
tests/test_m2_stress.py
Adversarial Stress Test Suite for Louisiana Surf Finder - Milestone 2 Ingestion Pipelines:
1. BuoyService (NDBC .spec/.txt parser, sentinels, CDIP/C-MAN, CO-OPS client, Tide Slope Engine)
2. WeatherService (Open-Meteo client, imperial units, null arrays, NWS zone regex & gridpoints)
3. ScraperService (Surf Captain & Surf-Forecast HTML variations, non-existent routing, outage shielding)
"""

import asyncio
import json
import unittest
from datetime import datetime, timezone, timedelta
import httpx

from app.models.schemas import (
    BuoyObservation,
    NormalizedMarineReading,
    SpotDetail,
    SpotSummary,
)
from app.services.buoy_service import (
    BuoyService,
    degrees_to_cardinal,
    cardinal_to_degrees,
    parse_float_val,
    parse_ndbc_spec,
    parse_ndbc_txt,
    merge_ndbc_data,
    classify_tide_slope,
    parse_coops_predictions,
)
from app.services.weather_service import (
    WeatherService,
    SEAS_PATTERN,
    WAVE_DETAIL_PATTERN,
    WIND_PATTERN,
    parse_iso_duration,
    parse_valid_time,
)
from app.services.scraper_service import ScraperService


# ==============================================================================
# 1. NDBC Buoy Parser Stress Tests
# ==============================================================================
class TestNDBCParserStress(unittest.IsolatedAsyncioTestCase):
    """Stress testing NOAA NDBC .spec and .txt parsers against real-world feed variations."""

    def setUp(self):
        self.service = BuoyService()

    def test_ndbc_spec_headers_and_whitespace(self):
        """Test spec parser handles multiple header lines, tabs, and irregular whitespace."""
        feed = (
            "# Program: NDBC Wave Processing System v4.2\n"
            "# Station: 42035 - Galveston Offshore\n"
            "#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD\n"
            "#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT\n"
            "2026\t11\t03\t19\t15   2.0   1.6   8.0   0.8   4.0   SSE\tS\tSTEEP   6.0\t160\n"
            "2026 11 03 18 15   1.9   1.5   7.8   0.7   3.9   SSE S AVERAGE 5.9 158\n"
        )
        res = parse_ndbc_spec(feed)
        self.assertIsNotNone(res)
        self.assertEqual(res["timestamp"].year, 2026)
        self.assertEqual(res["timestamp"].month, 11)
        self.assertEqual(res["timestamp"].hour, 19)
        self.assertAlmostEqual(res["wave_height_ft"], 6.56, delta=0.05)
        self.assertAlmostEqual(res["swell_height_ft"], 5.25, delta=0.05)
        self.assertEqual(res["swell_period_sec"], 8.0)
        self.assertEqual(res["swell_direction_cardinal"], "SSE")
        self.assertEqual(res["swell_direction_deg"], 157.5)
        self.assertEqual(res["steepness"], "STEEP")
        self.assertEqual(res["average_period_sec"], 6.0)
        self.assertEqual(res["mean_wave_direction_deg"], 160.0)

    def test_ndbc_spec_sentinel_variations(self):
        """Test spec parser with diverse sentinel values: MM, -99.0, -99, 99.0, 999."""
        feed = (
            "#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD\n"
            "#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT\n"
            "2026 11 03 19 15   MM  -99 -99.0 99.0 999.0  MM  N/A        MM  MM -99\n"
        )
        res = parse_ndbc_spec(feed)
        self.assertIsNotNone(res)
        self.assertIsNone(res["wave_height_ft"])
        self.assertIsNone(res["swell_height_ft"])
        self.assertIsNone(res["swell_period_sec"])
        self.assertIsNone(res["wind_wave_height_ft"])
        self.assertIsNone(res["wind_wave_period_sec"])
        self.assertIsNone(res["swell_direction_cardinal"])
        self.assertIsNone(res["wind_wave_direction_cardinal"])
        self.assertIsNone(res["steepness"])
        self.assertIsNone(res["average_period_sec"])
        self.assertIsNone(res["mean_wave_direction_deg"])

    def test_ndbc_txt_sentinel_variations(self):
        """Test txt parser with sentinels across all columns."""
        feed = (
            "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
            "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft\n"
            "2026 11 03 19 15   MM   MM   MM    MM    MM    MM   MM    MM    MM    MM    MM   MM   MM    MM\n"
        )
        res = parse_ndbc_txt(feed)
        self.assertIsNotNone(res)
        self.assertIsNone(res["wind_direction_deg"])
        self.assertIsNone(res["wind_direction_cardinal"])
        self.assertIsNone(res["wind_speed_mph"])
        self.assertIsNone(res["wind_gust_mph"])
        self.assertIsNone(res["wave_height_ft"])
        self.assertIsNone(res["dominant_period_sec"])
        self.assertIsNone(res["average_period_sec"])
        self.assertIsNone(res["mean_wave_direction_deg"])
        self.assertIsNone(res["pressure_hpa"])
        self.assertIsNone(res["air_temp_f"])
        self.assertIsNone(res["water_temp_f"])
        self.assertIsNone(res["tide_level_ft"])

    def test_cdip_waverider_42354_lacking_wind(self):
        """CDIP Waverider 42354 measures spectral wave physics but NO wind/meteorology."""
        spec_feed = (
            "#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD\n"
            "#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT\n"
            "2026 11 03 19 00  1.6  1.3  7.5  0.6  4.2  SSE  S  AVERAGE  5.5 155\n"
        )
        txt_feed = (
            "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
            "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft\n"
            "2026 11 03 19 00   MM   MM   MM   1.6   7.5   5.5  155    MM    MM  22.5    MM   MM   MM    MM\n"
        )
        spec_data = parse_ndbc_spec(spec_feed)
        txt_data = parse_ndbc_txt(txt_feed)
        obs = merge_ndbc_data("42354", spec_data, txt_data)

        self.assertEqual(obs.station_id, "42354")
        self.assertTrue(obs.is_online)
        self.assertEqual(obs.status, "online")
        self.assertAlmostEqual(obs.wave_height_ft, 5.25, delta=0.05)
        self.assertEqual(obs.dominant_period_sec, 7.5)
        self.assertIsNone(obs.wind_speed_mph)
        self.assertIsNone(obs.wind_direction_deg)
        self.assertEqual(obs.water_temp_f, 72.5)

        # Verify Pydantic conversion
        norm = BuoyService.to_normalized_reading(obs)
        self.assertIsInstance(norm, NormalizedMarineReading)
        self.assertEqual(norm.source_id, "ndbc_42354")
        self.assertAlmostEqual(norm.wave_height_ft, 5.25, delta=0.05)
        self.assertIsNone(norm.wind_speed_mph)

    def test_cman_capl1_lacking_wave(self):
        """C-MAN station CAPL1 has anemometer/barometer but NO wave sensor (.spec 404)."""
        txt_feed = (
            "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
            "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft\n"
            "2026 11 03 19 00  010  6.0  8.0    MM    MM    MM   MM 1018.5  21.0  23.0  14.0   MM   MM    MM\n"
        )
        spec_data = None  # Station returns 404 for .spec
        txt_data = parse_ndbc_txt(txt_feed)
        obs = merge_ndbc_data("CAPL1", spec_data, txt_data)

        self.assertEqual(obs.station_id, "CAPL1")
        self.assertTrue(obs.is_online)
        self.assertEqual(obs.status, "online")
        self.assertIsNone(obs.wave_height_ft)
        self.assertIsNone(obs.dominant_period_sec)
        self.assertAlmostEqual(obs.wind_speed_mph, 13.4, delta=0.2)
        self.assertEqual(obs.wind_direction_deg, 10.0)
        self.assertEqual(obs.wind_direction_cardinal, "N")
        self.assertEqual(obs.air_temp_f, 69.8)

        # Verify Pydantic conversion succeeds with None wave metrics
        norm = BuoyService.to_normalized_reading(obs)
        self.assertIsInstance(norm, NormalizedMarineReading)
        self.assertEqual(norm.source_id, "ndbc_capl1")
        self.assertIsNone(norm.wave_height_ft)
        self.assertAlmostEqual(norm.wind_speed_mph, 13.4, delta=0.2)

    def test_both_spec_and_txt_missing_graceful_offline(self):
        """When both feeds return 404/empty, merge_ndbc_data returns offline BuoyObservation."""
        obs = merge_ndbc_data("42035", None, None)
        self.assertEqual(obs.station_id, "42035")
        self.assertFalse(obs.is_online)
        self.assertEqual(obs.status, "offline")

        norm = BuoyService.to_normalized_reading(obs)
        self.assertEqual(norm.status, "stale")
        self.assertEqual(norm.confidence_weight, 0.2)

    def test_empty_or_corrupted_feed_texts(self):
        """Parser handles empty strings, corrupted lines, or incomplete rows without crashing."""
        self.assertIsNone(parse_ndbc_spec(""))
        self.assertIsNone(parse_ndbc_spec("   \n\n  \t"))
        self.assertIsNone(parse_ndbc_spec("corrupted random text line\n"))
        self.assertIsNone(parse_ndbc_spec("#Header only\n"))

        self.assertIsNone(parse_ndbc_txt(""))
        self.assertIsNone(parse_ndbc_txt("   \n\n  \t"))
        self.assertIsNone(parse_ndbc_txt("corrupted random text line\n"))
        self.assertIsNone(parse_ndbc_txt("#Header only\n"))

    def test_compass_degree_normalization(self):
        """Degrees wrap around properly (0, 360, >360, negative)."""
        self.assertEqual(degrees_to_cardinal(0.0), "N")
        self.assertEqual(degrees_to_cardinal(360.0), "N")
        self.assertEqual(degrees_to_cardinal(361.0), "N")
        self.assertEqual(degrees_to_cardinal(720.0), "N")
        self.assertEqual(degrees_to_cardinal(180.0), "S")
        self.assertIsNone(degrees_to_cardinal(None))
        self.assertIsNone(degrees_to_cardinal("invalid"))

        self.assertEqual(cardinal_to_degrees("N"), 0.0)
        self.assertEqual(cardinal_to_degrees("n"), 0.0)
        self.assertEqual(cardinal_to_degrees("SSE"), 157.5)
        self.assertIsNone(cardinal_to_degrees("MM"))
        self.assertIsNone(cardinal_to_degrees("-99"))
        self.assertIsNone(cardinal_to_degrees("UNKNOWN"))


# ==============================================================================
# 2. NOAA CO-OPS Client & Tide Slope Stress Tests
# ==============================================================================
class TestCOOPSClientStress(unittest.IsolatedAsyncioTestCase):
    """Stress testing NOAA CO-OPS datagetter REST client and Tide Slope Engine."""

    async def test_coops_network_timeouts_resilience(self):
        """Simulate connection and read timeouts on CO-OPS requests."""
        async def timeout_handler(request: httpx.Request):
            raise httpx.ReadTimeout("CO-OPS upstream timed out after 5.0s")

        client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
        service = BuoyService(client=client)

        # Single product query returns None
        prod = await service.fetch_coops_product("8768094", "water_level")
        self.assertIsNone(prod)

        # Full station fetch returns offline observation without exception
        obs = await service.fetch_coops_data("CAPL1")
        self.assertEqual(obs.station_id, "CAPL1")
        self.assertFalse(obs.is_online)
        self.assertEqual(obs.status, "offline")

    async def test_coops_api_error_response_payload(self):
        """Simulate CO-OPS returning an application-level JSON error payload."""
        async def error_handler(request: httpx.Request):
            return httpx.Response(200, json={
                "error": {"message": "Station 8768094 not active for product water_temperature."}
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(error_handler))
        service = BuoyService(client=client)

        prod = await service.fetch_coops_product("8768094", "water_temperature")
        self.assertIsNone(prod)

    async def test_coops_http_500_and_503(self):
        """Simulate upstream HTTP 500 and 503 server errors."""
        async def server_error_handler(request: httpx.Request):
            return httpx.Response(503, text="Service Unavailable")

        client = httpx.AsyncClient(transport=httpx.MockTransport(server_error_handler))
        service = BuoyService(client=client)

        prod = await service.fetch_coops_product("8768094", "water_level")
        self.assertIsNone(prod)

        obs = await service.fetch_coops_data("8768094")
        self.assertFalse(obs.is_online)

    async def test_coops_malformed_json_response(self):
        """Simulate CO-OPS returning non-JSON HTML error page."""
        async def html_handler(request: httpx.Request):
            return httpx.Response(200, text="<html><body>502 Bad Gateway</body></html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(html_handler))
        service = BuoyService(client=client)

        prod = await service.fetch_coops_product("8768094", "water_level")
        self.assertIsNone(prod)

    async def test_coops_empty_data_arrays(self):
        """CO-OPS returning empty data arrays or sentinel values inside data arrays."""
        async def empty_handler(request: httpx.Request):
            url_str = str(request.url)
            if "water_level" in url_str:
                return httpx.Response(200, json={"data": [{"v": "MM"}]})
            elif "wind" in url_str:
                return httpx.Response(200, json={"data": []})
            elif "water_temperature" in url_str:
                return httpx.Response(200, json={"data": [{"v": "-99"}]})
            return httpx.Response(200, json={"predictions": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(empty_handler))
        service = BuoyService(client=client)

        obs = await service.fetch_coops_data("CAPL1")
        self.assertIsNone(obs.water_temp_f)
        self.assertIsNone(obs.wind_speed_mph)
        self.assertIsNotNone(obs.station_id)

    def test_tide_slope_boundary_conditions(self):
        """Test Tide Slope Engine on boundary cases: empty list, single point, identical times."""
        now = datetime.now(timezone.utc)

        # 1. Empty list
        res_empty = classify_tide_slope([])
        self.assertEqual(res_empty["tide_state"], "Slack High")
        self.assertEqual(res_empty["current_water_level_ft"], 0.0)

        # 2. Single point
        res_single = classify_tide_slope([(now, 1.5)])
        self.assertIn(res_single["tide_state"], ["Slack High", "Slack Low"])
        self.assertEqual(res_single["current_water_level_ft"], 1.5)
        self.assertEqual(res_single["delta_v_ft"], 0.0)

        # 3. Two points with identical timestamps
        res_identical_time = classify_tide_slope([(now, 1.2), (now, 1.2)])
        self.assertEqual(res_identical_time["current_water_level_ft"], 1.2)
        self.assertEqual(res_identical_time["delta_v_ft"], 0.0)

        # 4. Target time far beyond point timestamps
        far_future = now + timedelta(days=10)
        pts = [(now - timedelta(minutes=60), 1.0), (now, 1.3)]
        res_future = classify_tide_slope(pts, target_time=far_future)
        self.assertEqual(res_future["tide_state"], "Rising")


# ==============================================================================
# 3. Open-Meteo Client Stress Tests
# ==============================================================================
class TestOpenMeteoClientStress(unittest.IsolatedAsyncioTestCase):
    """Stress testing Open-Meteo Marine & Forecast API ingestion."""

    def setUp(self):
        self.service = WeatherService()

    def test_open_meteo_empty_hourly_dictionaries(self):
        """Test parser when Open-Meteo returns empty hourly dictionaries."""
        marine_data = {"latitude": 29.77, "longitude": -93.45, "hourly": {}}
        weather_data = {"latitude": 29.77, "longitude": -93.45, "hourly": {}}

        reading = self.service._parse_open_meteo_reading("holly_beach", marine_data, weather_data)
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertEqual(reading.source_id, "open_meteo")
        self.assertIsNone(reading.wave_height_ft)
        self.assertIsNone(reading.wind_speed_mph)
        self.assertEqual(reading.status, "valid")

    def test_open_meteo_null_entries_in_arrays(self):
        """Test parser when hourly arrays contain None values."""
        marine_data = {
            "latitude": 29.77,
            "longitude": -93.45,
            "hourly": {
                "time": ["2026-11-03T18:00"],
                "wave_height": [None],
                "wave_direction": [None],
                "wave_period": [None],
                "swell_wave_height": [None],
                "swell_wave_period": [None],
                "swell_wave_direction": [None],
                "wind_wave_height": [None],
                "wind_wave_period": [None],
            }
        }
        weather_data = {
            "latitude": 29.77,
            "longitude": -93.45,
            "hourly": {
                "time": ["2026-11-03T18:00"],
                "wind_speed_10m": [None],
                "wind_direction_10m": [None],
                "wind_gusts_10m": [None],
                "temperature_2m": [None],
            }
        }

        reading = self.service._parse_open_meteo_reading("grand_isle", marine_data, weather_data)
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertIsNone(reading.wave_height_ft)
        self.assertIsNone(reading.swell_height_ft)
        self.assertIsNone(reading.wind_speed_mph)
        self.assertIsNone(reading.air_temp_f)

    async def test_open_meteo_network_timeout_triggers_fallback(self):
        """Test that network timeout on Open-Meteo degrades to static fallback."""
        async def timeout_handler(request: httpx.Request):
            raise httpx.ConnectTimeout("Open-Meteo connection timed out")

        client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
        service = WeatherService(client=client)

        reading = await service.fetch_open_meteo("holly_beach")
        self.assertEqual(reading.source_id, "open_meteo")
        self.assertEqual(reading.status, "fallback")
        self.assertIsNotNone(reading.wave_height_ft)
        self.assertIsNotNone(reading.wind_speed_mph)

    async def test_open_meteo_500_server_error_triggers_fallback(self):
        """Test HTTP 500 error degrades cleanly to fallback."""
        async def err_handler(request: httpx.Request):
            return httpx.Response(500, text="Internal Server Error")

        client = httpx.AsyncClient(transport=httpx.MockTransport(err_handler))
        service = WeatherService(client=client)

        reading = await service.fetch_open_meteo("grand_isle")
        self.assertEqual(reading.source_id, "open_meteo")
        self.assertEqual(reading.status, "fallback")
        self.assertIsNotNone(reading.wave_height_ft)


# ==============================================================================
# 4. NWS Zone Narrative Regex Stress Tests
# ==============================================================================
class TestNWSNarrativeRegexStress(unittest.IsolatedAsyncioTestCase):
    """Stress testing NWS Marine Coastal Zone text narrative parsing with diverse wordings."""

    def setUp(self):
        self.service = WeatherService()

    def test_nws_seas_pattern_variations(self):
        """Test SEAS_PATTERN against various real-world NWS marine text formulations."""
        test_cases = [
            ("Seas 2 to 3 feet.", 2.5),
            ("Seas around 4 feet.", 4.0),
            ("Seas 1 foot or less.", 1.0),
            ("Seas 2 to 4 ft.", 3.0),
            ("Seas 4 to 6 feet occasionally up to 8 feet.", 5.0),
            ("Tonight: North winds 10 kt. Seas around 3 feet. Moderate chop.", 3.0),
        ]
        for text, expected in test_cases:
            payload = {"properties": {"periods": [{"detailedForecast": text}]}}
            reading = self.service._parse_nws_zone_forecast("GMZ450", payload)
            self.assertEqual(reading.wave_height_ft, expected, f"Failed on: {text}")

    def test_nws_wave_detail_pattern_variations(self):
        """Test WAVE_DETAIL_PATTERN for swell decomposition and dominant swell selection."""
        # Single swell
        text1 = "Wave Detail: South 3 feet at 8 seconds. Choppy."
        payload1 = {"properties": {"periods": [{"detailedForecast": text1}]}}
        reading1 = self.service._parse_nws_zone_forecast("GMZ450", payload1)
        self.assertEqual(reading1.swell_height_ft, 3.0)
        self.assertEqual(reading1.swell_period_sec, 8.0)
        self.assertEqual(reading1.swell_direction_cardinal, "SOUTH")
        self.assertEqual(reading1.swell_direction_deg, 180.0)

        # Multi-component swells: should select longest period / dominant swell
        text2 = "Wave Detail: Southeast 2 feet at 6 seconds and South 3 feet at 8 seconds."
        payload2 = {"properties": {"periods": [{"detailedForecast": text2}]}}
        reading2 = self.service._parse_nws_zone_forecast("GMZ553", payload2)
        # Dominant swell is South 3 ft at 8 sec
        self.assertEqual(reading2.swell_height_ft, 3.0)
        self.assertEqual(reading2.swell_period_sec, 8.0)
        self.assertEqual(reading2.swell_direction_cardinal, "SOUTH")

    def test_nws_wind_pattern_variations(self):
        """Test WIND_PATTERN for various wind velocities and directions."""
        test_cases = [
            ("North winds 10 to 15 knots.", "NORTH", 14.4),  # 12.5 kts * 1.15078 = 14.4 mph
            ("South winds around 10 knots.", "SOUTH", 11.5),  # 10 kts * 1.15078 = 11.5 mph
            ("East winds 5 knots or less.", "EAST", 5.8),     # 5 kts * 1.15078 = 5.8 mph
            ("Northwest winds 15 to 20 knots with gusts to 25 knots.", "NORTHWEST", 20.1),
        ]
        for text, exp_card, exp_spd in test_cases:
            payload = {"properties": {"periods": [{"detailedForecast": text}]}}
            reading = self.service._parse_nws_zone_forecast("GMZ450", payload)
            self.assertEqual(reading.wind_direction_cardinal, exp_card)
            self.assertAlmostEqual(reading.wind_speed_mph, exp_spd, delta=0.2)

    def test_nws_empty_or_nonmarine_narratives(self):
        """Test NWS text containing no marine patterns returns valid reading with None metrics."""
        text = "Sunny and clear. Highs around 75 degrees. Light winds."
        payload = {"properties": {"periods": [{"detailedForecast": text}]}}
        reading = self.service._parse_nws_zone_forecast("GMZ450", payload)
        self.assertIsNone(reading.wave_height_ft)
        self.assertIsNone(reading.wind_speed_mph)
        self.assertIsNone(reading.swell_period_sec)
        self.assertEqual(reading.status, "valid")

    async def test_nws_empty_periods_triggers_fallback(self):
        """Test NWS API returning empty periods list gracefully falls back."""
        async def empty_periods_handler(request: httpx.Request):
            return httpx.Response(200, json={"properties": {"periods": []}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(empty_periods_handler))
        service = WeatherService(client=client)

        reading = await service.fetch_nws_marine_forecast("GMZ450")
        self.assertEqual(reading.source_id, "nws_gmz450")
        self.assertEqual(reading.status, "fallback")


# ==============================================================================
# 5. Surf Scraper Stress Tests (Surf Captain & Surf-Forecast)
# ==============================================================================
class TestScraperServiceStress(unittest.IsolatedAsyncioTestCase):
    """Stress testing Surf Captain & Surf-Forecast scrapers across HTML mutations and routing."""

    def setUp(self):
        self.service = ScraperService()

    def test_surf_captain_html_structures(self):
        """Test Surf Captain HTML parser against varying DOM structures."""
        # Case 1: Standard range with weather arrow
        html1 = """
        <div class="fcst-header">
          <h1 id="fcst-current-title">Surf is 3-4 ft and clean right now for Holly Beach, LA</h1>
          <div class="fcst-current-cond clean">Clean</div>
          <div class="current-data-desc">72° N @ 10 → 14mph</div>
        </div>
        """
        r1 = self.service.parse_surf_captain_html("holly_beach", html1)
        self.assertEqual(r1.wave_height_ft, 3.5)
        self.assertEqual(r1.wind_speed_mph, 10.0)
        self.assertEqual(r1.wind_gust_mph, 14.0)
        self.assertEqual(r1.air_temp_f, 72.0)
        self.assertEqual(r1.wind_direction_cardinal, "N")

        # Case 2: Single height (e.g. "4 ft and fair")
        html2 = """
        <h1 id="fcst-current-title">The surf is 4 ft and fair right now</h1>
        <div class="fcst-current-cond fair">Fair</div>
        <div class="current-data-desc">75° NE @ 12mph</div>
        """
        r2 = self.service.parse_surf_captain_html("holly_beach", html2)
        self.assertEqual(r2.wave_height_ft, 4.0)
        self.assertEqual(r2.wind_speed_mph, 12.0)
        self.assertIsNone(r2.wind_gust_mph)

        # Case 3: Flat condition
        html3 = """
        <h1 id="fcst-current-title">The surf is Flat right now</h1>
        <div class="fcst-current-cond flat">Flat</div>
        """
        r3 = self.service.parse_surf_captain_html("holly_beach", html3)
        self.assertEqual(r3.wave_height_ft, 0.5)

        # Case 4: Stripped HTML missing #fcst-current-title (safe fallback)
        html4 = "<div>Maintenance mode. Service unavailable.</div>"
        r4 = self.service.parse_surf_captain_html("holly_beach", html4)
        self.assertEqual(r4.wave_height_ft, 1.5)
        self.assertIsNone(r4.wind_speed_mph)

    def test_surf_forecast_html_structures(self):
        """Test Surf-Forecast HTML parser against varying table layouts."""
        # Case 1: Standard table
        html1 = """
        <table class="forecast-table__table">
          <tr data-row="rating">
            <td><div class="star-rating__rating">7</div></td>
          </tr>
          <tr data-row="swell">
            <td>
              <div class="swell-icon" data-height="1.5"></div>
              <div class="forecast-table__swell-period">8</div>
              <div>SSE</div>
            </td>
          </tr>
          <tr data-row="wind">
            <td>
              <div class="wind-icon" data-speed="18.0"></div>
              <div class="wind-icon__letters">N</div>
            </td>
          </tr>
          <tr data-row="wind-state">
            <td><div class="wind-state">offshore</div></td>
          </tr>
        </table>
        """
        r1 = self.service.parse_surf_forecast_html("holly_beach", html1)
        self.assertAlmostEqual(r1.wave_height_ft, 4.92, delta=0.05)
        self.assertEqual(r1.wave_period_sec, 8.0)
        self.assertEqual(r1.swell_direction_cardinal, "SSE")
        self.assertAlmostEqual(r1.wind_speed_mph, 11.2, delta=0.2)
        self.assertEqual(r1.wind_direction_cardinal, "N")
        self.assertIn("Rating: 7/10", r1.raw_condition_label)

        # Case 2: Corrupted numeric values (fallback to defaults without crash)
        html2 = """
        <table class="forecast-table__table">
          <tr data-row="swell">
            <td>
              <div class="swell-icon" data-height="corrupted"></div>
              <div class="forecast-table__swell-period">invalid</div>
            </td>
          </tr>
        </table>
        """
        r2 = self.service.parse_surf_forecast_html("holly_beach", html2)
        self.assertEqual(r2.wave_height_ft, 2.0)
        self.assertEqual(r2.wave_period_sec, 5.0)

    async def test_nonexistent_spot_routing_surf_forecast(self):
        """Verify Grand Isle safely bypasses Surf-Forecast and returns None cleanly."""
        res_grand_isle = await self.service.fetch_surf_forecast("grand_isle")
        self.assertIsNone(res_grand_isle)

        # Unknown spot ID also returns None
        res_unknown = await self.service.fetch_surf_forecast("non_existent_spot")
        self.assertIsNone(res_unknown)

    async def test_scraper_outages_fallback_resilience(self):
        """Simulate upstream HTTP 500 and timeout on scrapers."""
        async def err_handler(request: httpx.Request):
            raise httpx.ReadTimeout("Scraper upstream read timeout")

        client = httpx.AsyncClient(transport=httpx.MockTransport(err_handler))
        service = ScraperService(client=client)

        r_sc = await service.fetch_surf_captain("holly_beach")
        self.assertEqual(r_sc.source_id, "surf_captain")
        self.assertEqual(r_sc.status, "fallback")
        self.assertIsNotNone(r_sc.wave_height_ft)

        r_sf = await service.fetch_surf_forecast("holly_beach")
        self.assertEqual(r_sf.source_id, "surf_forecast")
        self.assertEqual(r_sf.status, "fallback")
        self.assertIsNotNone(r_sf.wave_height_ft)


if __name__ == "__main__":
    unittest.main()
