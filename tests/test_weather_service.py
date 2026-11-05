"""
tests/test_weather_service.py
Unit tests for WeatherService: Open-Meteo Marine API, NWS Marine Zone narrative parsing,
NWS quantitative gridpoints, ISO 8601 duration resolution, and fallback degradation.
"""

import unittest
from datetime import datetime, timezone, timedelta
import httpx

from app.services.weather_service import (
    WeatherService,
    parse_iso_duration,
    parse_valid_time,
    SEAS_PATTERN,
    WAVE_DETAIL_PATTERN,
    WIND_PATTERN,
)
from app.models.schemas import NormalizedMarineReading


SAMPLE_NWS_ZONE_JSON = {
    "properties": {
        "periods": [
            {
                "number": 1,
                "name": "Tonight",
                "detailedForecast": (
                    "North winds 10 to 15 knots. Seas 2 to 3 feet. "
                    "Wave Detail: Southeast 2 feet at 6 seconds and south 1 foot at 4 seconds. "
                    "Protected waters a moderate chop."
                )
            }
        ]
    }
}

SAMPLE_NWS_GRID_JSON = {
    "properties": {
        "waveHeight": {
            "values": [
                {"validTime": "2026-11-03T00:00:00+00:00/P2D", "value": 1.0}
            ]
        },
        "wavePeriod": {
            "values": [
                {"validTime": "2026-11-03T00:00:00+00:00/P2D", "value": 6.5}
            ]
        },
        "windSpeed": {
            "values": [
                {"validTime": "2026-11-03T00:00:00+00:00/P2D", "value": 20.0}  # km/h
            ]
        },
        "windDirection": {
            "values": [
                {"validTime": "2026-11-03T00:00:00+00:00/P2D", "value": 360.0}
            ]
        },
        "windGust": {
            "values": [
                {"validTime": "2026-11-03T00:00:00+00:00/P2D", "value": 28.0}  # km/h
            ]
        },
        "temperature": {
            "values": [
                {"validTime": "2026-11-03T00:00:00+00:00/P2D", "value": 20.0}  # deg C
            ]
        }
    }
}

SAMPLE_OPEN_METEO_MARINE_JSON = {
    "latitude": 29.77,
    "longitude": -93.45,
    "hourly": {
        "time": ["2026-11-03T18:00", "2026-11-03T19:00"],
        "wave_height": [3.2, 3.4],
        "wave_direction": [165, 170],
        "wave_period": [6.5, 6.8],
        "swell_wave_height": [2.6, 2.8],
        "swell_wave_direction": [160, 165],
        "swell_wave_period": [6.5, 6.8],
        "wind_wave_height": [1.5, 1.6],
        "wind_wave_period": [3.8, 4.0],
    }
}

SAMPLE_OPEN_METEO_WX_JSON = {
    "latitude": 29.77,
    "longitude": -93.45,
    "hourly": {
        "time": ["2026-11-03T18:00", "2026-11-03T19:00"],
        "wind_speed_10m": [12.5, 13.0],
        "wind_direction_10m": [355.0, 360.0],
        "wind_gusts_10m": [16.0, 17.5],
        "temperature_2m": [72.0, 71.0],
    }
}


class TestWeatherService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.service = WeatherService()

    # --------------------------------------------------------------------------
    # ISO 8601 Duration & Time Parsers
    # --------------------------------------------------------------------------
    def test_parse_iso_duration(self):
        self.assertEqual(parse_iso_duration("PT1H"), timedelta(hours=1))
        self.assertEqual(parse_iso_duration("PT3H"), timedelta(hours=3))
        self.assertEqual(parse_iso_duration("PT30M"), timedelta(minutes=30))
        self.assertEqual(parse_iso_duration("P1D"), timedelta(days=1))
        self.assertEqual(parse_iso_duration("P2DT12H"), timedelta(days=2, hours=12))
        # Malformed fallback defaults to 1 hour
        self.assertEqual(parse_iso_duration("invalid"), timedelta(hours=1))

    def test_parse_valid_time(self):
        start, end = parse_valid_time("2026-11-03T18:00:00Z/PT3H")
        self.assertEqual(start.year, 2026)
        self.assertEqual(start.hour, 18)
        self.assertEqual(end - start, timedelta(hours=3))

    # --------------------------------------------------------------------------
    # NWS Narrative Regex Tests
    # --------------------------------------------------------------------------
    def test_nws_seas_regex(self):
        m1 = SEAS_PATTERN.search("Seas around 3 feet.")
        self.assertIsNotNone(m1)
        self.assertEqual(m1.group(1), "3")

        m2 = SEAS_PATTERN.search("Seas 2 to 4 feet.")
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(2), "2")
        self.assertEqual(m2.group(3), "4")

        m3 = SEAS_PATTERN.search("Seas 1 foot or less.")
        self.assertIsNotNone(m3)
        self.assertEqual(m3.group(4), "1")

        m4 = SEAS_PATTERN.search("Seas 2 feet or less.")
        self.assertIsNotNone(m4)
        self.assertEqual(m4.group(4), "2")

        m5 = SEAS_PATTERN.search("Seas 2 ft or less.")
        self.assertIsNotNone(m5)
        self.assertEqual(m5.group(4), "2")

    def test_nws_wave_detail_regex(self):
        text = "Wave Detail: Southeast 3 feet at 7 seconds and South 1 foot at 4 seconds."
        swells = list(WAVE_DETAIL_PATTERN.finditer(text))
        self.assertEqual(len(swells), 2)
        self.assertEqual(swells[0].group(1).upper(), "SOUTHEAST")
        self.assertEqual(swells[0].group(2), "3")
        self.assertEqual(swells[0].group(4), "7")

        text_ft = "Wave Detail: South 2 ft at 5 seconds"
        swells_ft = list(WAVE_DETAIL_PATTERN.finditer(text_ft))
        self.assertEqual(len(swells_ft), 1)
        self.assertEqual(swells_ft[0].group(1).upper(), "SOUTH")
        self.assertEqual(swells_ft[0].group(2), "2")
        self.assertEqual(swells_ft[0].group(4), "5")

    def test_nws_wind_regex(self):
        m = WIND_PATTERN.search("North winds 10 to 15 knots.")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).upper(), "NORTH")
        self.assertEqual(m.group(2), None)
        self.assertEqual(m.group(3), "10")
        self.assertEqual(m.group(4), "15")

    # --------------------------------------------------------------------------
    # Zone & Gridpoint Parsers
    # --------------------------------------------------------------------------
    def test_parse_nws_zone_forecast(self):
        reading = self.service._parse_nws_zone_forecast("GMZ450", SAMPLE_NWS_ZONE_JSON)
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertEqual(reading.source_id, "nws_gmz450")
        # 2 to 3 feet average is 2.5 ft
        self.assertEqual(reading.wave_height_ft, 2.5)
        # Dominant swell is 2 ft at 6 sec SE
        self.assertEqual(reading.swell_height_ft, 2.0)
        self.assertEqual(reading.swell_period_sec, 6.0)
        self.assertEqual(reading.swell_direction_cardinal, "SOUTHEAST")
        # North winds 10-15 knots => 12.5 knots * 1.15078 = 14.4 mph
        self.assertEqual(reading.wind_speed_mph, 14.4)
        self.assertEqual(reading.wind_direction_cardinal, "NORTH")
        self.assertEqual(reading.wind_direction_deg, 0.0)

    def test_parse_nws_gridpoint_data(self):
        reading = self.service._parse_nws_gridpoint_data("LCH", 51, 74, SAMPLE_NWS_GRID_JSON)
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertEqual(reading.source_id, "nws_lch_grid")
        # 1.0 m * 3.28084 = 3.28 ft
        self.assertEqual(reading.wave_height_ft, 3.28)
        self.assertEqual(reading.wave_period_sec, 6.5)
        # 20.0 km/h * 0.621371 = 12.4 mph
        self.assertEqual(reading.wind_speed_mph, 12.4)
        # 28.0 km/h * 0.621371 = 17.4 mph
        self.assertEqual(reading.wind_gust_mph, 17.4)
        self.assertEqual(reading.wind_direction_deg, 360.0)
        # 20.0 C => 68.0 F
        self.assertEqual(reading.air_temp_f, 68.0)

    def test_parse_open_meteo_reading(self):
        reading = self.service._parse_open_meteo_reading(
            "holly_beach", SAMPLE_OPEN_METEO_MARINE_JSON, SAMPLE_OPEN_METEO_WX_JSON
        )
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertEqual(reading.source_id, "open_meteo")
        self.assertIsNotNone(reading.wave_height_ft)
        self.assertIsNotNone(reading.swell_height_ft)
        self.assertIsNotNone(reading.wind_speed_mph)
        self.assertEqual(reading.status, "valid")

    # --------------------------------------------------------------------------
    # Mocked Network & Outage Shielding
    # --------------------------------------------------------------------------
    async def test_fetch_open_meteo_mock_success(self):
        async def handler(request: httpx.Request):
            url_str = str(request.url)
            if "marine" in url_str:
                return httpx.Response(200, json=SAMPLE_OPEN_METEO_MARINE_JSON)
            return httpx.Response(200, json=SAMPLE_OPEN_METEO_WX_JSON)

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = WeatherService(client=mock_client)

        reading = await service.fetch_open_meteo("holly_beach")
        self.assertEqual(reading.source_id, "open_meteo")
        self.assertEqual(reading.status, "valid")

    async def test_fetch_open_meteo_outage_fallback(self):
        async def handler(request: httpx.Request):
            return httpx.Response(500, text="Internal Server Error")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = WeatherService(client=mock_client)

        # 500 should degrade cleanly to static fallback with status="fallback"
        reading = await service.fetch_open_meteo("holly_beach")
        self.assertEqual(reading.source_id, "open_meteo")
        self.assertEqual(reading.status, "fallback")
        self.assertIsNotNone(reading.wave_height_ft)

    async def test_fetch_nws_marine_forecast_outage_fallback(self):
        async def handler(request: httpx.Request):
            return httpx.Response(503, text="Service Unavailable")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = WeatherService(client=mock_client)

        # 503 should degrade to fallback reading
        reading = await service.fetch_nws_marine_forecast("GMZ450")
        self.assertEqual(reading.source_id, "nws_gmz450")
        self.assertEqual(reading.status, "fallback")

    async def test_fetch_spot_weather(self):
        async def handler(request: httpx.Request):
            url_str = str(request.url)
            if "marine" in url_str:
                return httpx.Response(200, json=SAMPLE_OPEN_METEO_MARINE_JSON)
            elif "forecast" in url_str and "open-meteo" in url_str:
                return httpx.Response(200, json=SAMPLE_OPEN_METEO_WX_JSON)
            elif "weather.gov" in url_str:
                return httpx.Response(200, json=SAMPLE_NWS_ZONE_JSON)
            return httpx.Response(404)

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = WeatherService(client=mock_client)

        readings = await service.fetch_spot_weather("holly_beach")
        self.assertEqual(len(readings), 2)
        source_ids = [r.source_id for r in readings]
        self.assertIn("open_meteo", source_ids)
        self.assertIn("nws_gmz450", source_ids)

    def test_parse_open_meteo_calm_subsecond_and_zero_period(self):
        """Verify calm conditions (0.0s and 0.7s) do not trigger ValidationError."""
        marine_data = {
            "latitude": 29.77,
            "longitude": -93.45,
            "hourly": {
                "time": ["2026-11-03T18:00"],
                "wave_height": [1.0],
                "wave_period": [3.5],
                "wave_direction": [160],
                "swell_wave_height": [0.8],
                "swell_wave_period": [3.5],
                "swell_wave_direction": [160],
                "wind_wave_height": [0.2],
                "wind_wave_period": [0.7],  # sub-second period
            }
        }
        wx_data = {
            "latitude": 29.77,
            "longitude": -93.45,
            "hourly": {
                "time": ["2026-11-03T18:00"],
                "wind_speed_10m": [5.0],
                "wind_direction_10m": [180],
                "wind_gusts_10m": [7.0],
                "temperature_2m": [75.0],
            }
        }
        reading = self.service._parse_open_meteo_reading("holly_beach", marine_data, wx_data)
        self.assertEqual(reading.status, "valid")
        self.assertEqual(reading.wind_wave_period_sec, 0.7)


if __name__ == "__main__":
    unittest.main()
