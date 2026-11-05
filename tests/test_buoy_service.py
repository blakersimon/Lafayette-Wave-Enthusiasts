"""
tests/test_buoy_service.py
Unit tests for BuoyService: NOAA NDBC .spec/.txt parsing, CO-OPS predictions,
Tide Slope Engine, compass conversions, and error shielding.
"""

import unittest
from datetime import datetime, timezone, timedelta
import httpx

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
from app.models.schemas import BuoyObservation, NormalizedMarineReading


RAW_SPEC_SAMPLE = """#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT
2026 11 03 18 10  1.5  1.2  7.1  0.6  4.5  SE SSE AVERAGE  5.4 155
2026 11 03 17 10  1.4  1.1  7.0  0.6  4.4  SE SSE AVERAGE  5.3 154
"""

RAW_SPEC_MISSING_SAMPLE = """#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD
#yr  mo dy hr mn    m    m  sec    m  sec  -  degT     -      sec degT
2026 11 03 18 10   MM   MM   MM   MM   MM  MM  MM        MM   MM  MM
"""

RAW_TXT_SAMPLE = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2026 11 03 18 10  350  6.5  8.1   1.4   7.1   5.4  155 1018.2  20.0  22.0  16.0   MM   MM    MM
"""

RAW_TXT_MM_WAVE_SAMPLE = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2026 11 03 18 10  350  6.5  8.1    MM    MM    MM   MM 1018.2  20.0  22.0  16.0   MM   MM    MM
"""


class TestBuoyService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.service = BuoyService()

    # --------------------------------------------------------------------------
    # Compass & Conversion Tests
    # --------------------------------------------------------------------------
    def test_compass_degrees_to_cardinal(self):
        self.assertEqual(degrees_to_cardinal(0.0), "N")
        self.assertEqual(degrees_to_cardinal(360.0), "N")
        self.assertEqual(degrees_to_cardinal(22.5), "NNE")
        self.assertEqual(degrees_to_cardinal(45.0), "NE")
        self.assertEqual(degrees_to_cardinal(90.0), "E")
        self.assertEqual(degrees_to_cardinal(135.0), "SE")
        self.assertEqual(degrees_to_cardinal(180.0), "S")
        self.assertEqual(degrees_to_cardinal(225.0), "SW")
        self.assertEqual(degrees_to_cardinal(270.0), "W")
        self.assertEqual(degrees_to_cardinal(315.0), "NW")
        self.assertEqual(degrees_to_cardinal(350.0), "N")
        self.assertIsNone(degrees_to_cardinal(None))

    def test_compass_cardinal_to_degrees(self):
        self.assertEqual(cardinal_to_degrees("N"), 0.0)
        self.assertEqual(cardinal_to_degrees("NORTH"), 0.0)
        self.assertEqual(cardinal_to_degrees("SE"), 135.0)
        self.assertEqual(cardinal_to_degrees("S"), 180.0)
        self.assertEqual(cardinal_to_degrees("NNW"), 337.5)
        self.assertIsNone(cardinal_to_degrees("MM"))
        self.assertIsNone(cardinal_to_degrees("N/A"))
        self.assertIsNone(cardinal_to_degrees(None))

    def test_parse_float_val_sentinels(self):
        self.assertEqual(parse_float_val("12.5"), 12.5)
        self.assertIsNone(parse_float_val("MM"))
        self.assertIsNone(parse_float_val("-99"))
        self.assertIsNone(parse_float_val("999.0"))
        self.assertIsNone(parse_float_val("N/A"))
        self.assertIsNone(parse_float_val(None))

    # --------------------------------------------------------------------------
    # NDBC Parsers (.spec & .txt)
    # --------------------------------------------------------------------------
    def test_parse_ndbc_spec(self):
        parsed = self.service.parse_spec_file(RAW_SPEC_SAMPLE)
        self.assertIsNotNone(parsed)
        # WVHT: 1.5m * 3.28084 = 4.92 ft
        self.assertAlmostEqual(parsed["wave_height_ft"], 4.92, delta=0.05)
        # Swell: 1.2m * 3.28084 = 3.94 ft
        self.assertAlmostEqual(parsed["swell_height_ft"], 3.94, delta=0.05)
        self.assertEqual(parsed["swell_period_sec"], 7.1)
        self.assertEqual(parsed["swell_direction_cardinal"], "SE")
        self.assertEqual(parsed["swell_direction_deg"], 135.0)
        self.assertEqual(parsed["steepness"], "AVERAGE")
        self.assertEqual(parsed["mean_wave_direction_deg"], 155.0)

    def test_parse_ndbc_spec_missing_sentinels(self):
        parsed = self.service.parse_spec_file(RAW_SPEC_MISSING_SAMPLE)
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed["wave_height_ft"])
        self.assertIsNone(parsed["swell_height_ft"])
        self.assertIsNone(parsed["steepness"])

    def test_parse_ndbc_txt(self):
        parsed = self.service.parse_txt_file(RAW_TXT_SAMPLE)
        self.assertIsNotNone(parsed)
        # WSPD: 6.5 m/s * 2.23694 = 14.5 mph
        self.assertAlmostEqual(parsed["wind_speed_mph"], 14.5, delta=0.2)
        # GST: 8.1 m/s * 2.23694 = 18.1 mph
        self.assertAlmostEqual(parsed["wind_gust_mph"], 18.1, delta=0.2)
        self.assertEqual(parsed["wind_direction_deg"], 350.0)
        self.assertEqual(parsed["wind_direction_cardinal"], "N")
        # ATMP: (20 * 9/5) + 32 = 68.0 F
        self.assertEqual(parsed["air_temp_f"], 68.0)
        # WTMP: (22 * 9/5) + 32 = 71.6 F
        self.assertEqual(parsed["water_temp_f"], 71.6)

    def test_merge_ndbc_data_cross_merge(self):
        spec_data = self.service.parse_spec_file(RAW_SPEC_SAMPLE)
        txt_data = self.service.parse_txt_file(RAW_TXT_MM_WAVE_SAMPLE)

        # Even though txt has MM for wave height, spec should supply it
        obs = merge_ndbc_data("42035", spec_data, txt_data)
        self.assertIsInstance(obs, BuoyObservation)
        self.assertEqual(obs.station_id, "42035")
        self.assertIsNotNone(obs.wave_height_ft)
        self.assertIsNotNone(obs.wind_speed_mph)
        self.assertEqual(obs.wind_direction_deg, 350.0)
        self.assertEqual(obs.status, "online")

    # --------------------------------------------------------------------------
    # Tide Slope Engine Tests
    # --------------------------------------------------------------------------
    def test_tide_slope_rising(self):
        now = datetime.now(timezone.utc)
        pts = [
            (now - timedelta(minutes=60), 1.0),
            (now - timedelta(minutes=30), 1.15),
            (now, 1.30),  # Delta = +0.30 ft > +0.10 ft
        ]
        res = self.service.calculate_tide_slope(pts, target_time=now)
        self.assertEqual(res["tide_state"], "Rising")
        self.assertGreater(res["delta_v_ft"], 0.10)

    def test_tide_slope_falling(self):
        now = datetime.now(timezone.utc)
        pts = [
            (now - timedelta(minutes=60), 2.0),
            (now - timedelta(minutes=30), 1.8),
            (now, 1.6),  # Delta = -0.40 ft < -0.10 ft
        ]
        res = self.service.calculate_tide_slope(pts, target_time=now)
        self.assertEqual(res["tide_state"], "Falling")
        self.assertLess(res["delta_v_ft"], -0.10)

    def test_tide_slope_slack_high(self):
        now = datetime.now(timezone.utc)
        # Mean is ~2.0 ft, current is 2.15 ft, delta is +0.02 ft (<= 0.10)
        pts = [
            (now - timedelta(hours=6), 1.5),
            (now - timedelta(minutes=60), 2.13),
            (now - timedelta(minutes=30), 2.14),
            (now, 2.15),
            (now + timedelta(hours=6), 1.6),
        ]
        res = self.service.calculate_tide_slope(pts, target_time=now)
        self.assertEqual(res["tide_state"], "Slack High")

    def test_tide_slope_slack_low(self):
        now = datetime.now(timezone.utc)
        # Mean is ~1.5 ft, current is 0.52 ft, delta is -0.01 ft (<= 0.10)
        pts = [
            (now - timedelta(hours=6), 2.0),
            (now - timedelta(minutes=60), 0.53),
            (now - timedelta(minutes=30), 0.52),
            (now, 0.52),
            (now + timedelta(hours=6), 1.8),
        ]
        res = self.service.calculate_tide_slope(pts, target_time=now)
        self.assertEqual(res["tide_state"], "Slack Low")

    def test_parse_coops_predictions(self):
        sample_json = {
            "predictions": [
                {"t": "2026-11-03 18:00", "v": "1.45"},
                {"t": "2026-11-03 18:06", "v": "1.48"},
            ]
        }
        pts = parse_coops_predictions(sample_json)
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0][1], 1.45)
        self.assertEqual(pts[1][1], 1.48)

    # --------------------------------------------------------------------------
    # Mocked Network & Error Shielding Tests
    # --------------------------------------------------------------------------
    async def test_fetch_ndbc_buoy_with_mock_client(self):
        async def handler(request: httpx.Request):
            url_str = str(request.url)
            if url_str.endswith(".spec"):
                return httpx.Response(200, text=RAW_SPEC_SAMPLE)
            elif url_str.endswith(".txt"):
                return httpx.Response(200, text=RAW_TXT_SAMPLE)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        service = BuoyService(client=mock_client)

        obs = await service.fetch_ndbc_buoy("42035")
        self.assertEqual(obs.station_id, "42035")
        self.assertAlmostEqual(obs.wave_height_ft, 4.92, delta=0.05)
        self.assertEqual(obs.wind_direction_deg, 350.0)

        # Convert to NormalizedMarineReading
        norm = BuoyService.to_normalized_reading(obs)
        self.assertIsInstance(norm, NormalizedMarineReading)
        self.assertEqual(norm.source_id, "ndbc_42035")
        self.assertEqual(norm.wave_height_ft, obs.wave_height_ft)

    async def test_fetch_ndbc_buoy_404_resilience(self):
        async def handler(request: httpx.Request):
            return httpx.Response(404, text="Not Found")

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        service = BuoyService(client=mock_client)

        # 404 should NOT raise exception; returns offline observation
        obs = await service.fetch_ndbc_buoy("99999")
        self.assertEqual(obs.station_id, "99999")
        self.assertFalse(obs.is_online)
        self.assertEqual(obs.status, "offline")

    async def test_fetch_station_pair(self):
        async def handler(request: httpx.Request):
            url_str = str(request.url)
            if "datagetter" in url_str:
                if "water_level" in url_str:
                    return httpx.Response(200, json={"data": [{"v": "1.52"}]})
                elif "wind" in url_str:
                    return httpx.Response(200, json={"data": [{"s": "10.0", "g": "15.0", "d": "350", "dr": "N"}]})
                elif "water_temperature" in url_str:
                    return httpx.Response(200, json={"data": [{"v": "72.5"}]})
                return httpx.Response(200, json={"predictions": []})
            elif url_str.endswith(".spec"):
                return httpx.Response(200, text=RAW_SPEC_SAMPLE)
            elif url_str.endswith(".txt"):
                return httpx.Response(200, text=RAW_TXT_SAMPLE)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        service = BuoyService(client=mock_client)

        pair = await service.fetch_station_pair("holly_beach")
        self.assertIn("inshore", pair)
        self.assertIn("offshore", pair)
        self.assertEqual(pair["inshore"].station_id, "CAPL1")
        self.assertEqual(pair["offshore"].station_id, "42035")

    def test_tide_slope_ieee754_boundary(self):
        """Test that exact 0.10 difference (1.10 - 1.00) is classified as Slack High, not Rising."""
        now = datetime.now(timezone.utc)
        pts = [
            (now - timedelta(minutes=60), 1.00),
            (now, 1.10),
        ]
        res = self.service.calculate_tide_slope(pts, target_time=now, threshold_ft=0.10)
        self.assertEqual(res["tide_state"], "Slack High")
        self.assertEqual(res["tide_status"], "slack_high")

    async def test_coops_tide_status_propagation(self):
        """Verify fetch_coops_data populates tide_status and to_normalized_reading preserves it."""
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M")
        t_prev_str = (now - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M")

        async def handler(request: httpx.Request):
            url_str = str(request.url)
            if "water_level" in url_str:
                return httpx.Response(200, json={"data": [{"v": "1.30"}]})
            elif "predictions" in url_str:
                return httpx.Response(200, json={"predictions": [
                    {"t": t_prev_str, "v": "1.00"},
                    {"t": now_str, "v": "1.30"},
                ]})
            elif "wind" in url_str:
                return httpx.Response(200, json={"data": []})
            elif "water_temperature" in url_str:
                return httpx.Response(200, json={"data": []})
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = BuoyService(client=client)
        obs = await service.fetch_coops_data("CAPL1")
        self.assertEqual(obs.tide_status, "rising")

        norm = BuoyService.to_normalized_reading(obs)
        self.assertEqual(norm.tide_status, "rising")


if __name__ == "__main__":
    unittest.main()
