"""
tests/test_scraper_service.py
Unit tests for ScraperService: Surf Captain & Surf-Forecast resilient HTML parsing,
BeautifulSoup and regex fallbacks, spot routing, and outage shielding.
"""

import unittest
from datetime import datetime, timezone
import httpx

from app.services.scraper_service import ScraperService
from app.models.schemas import NormalizedMarineReading


SAMPLE_SURF_CAPTAIN_HTML_RANGE = """
<!DOCTYPE html>
<html>
<head><title>Surf Captain Holly Beach</title></head>
<body>
  <div class="fcst-header">
    <h1 id="fcst-current-title">The surf forecast is 2-3 ft and clean right now for Holly Beach, LA</h1>
    <span class="badge fcst-current-cond clean">Clean</span>
  </div>
  <ul class="fcst-current-data">
    <li class="fcst-current-item">
      <span class="label">WEATHER</span>
      <div class="current-data-desc">74° N @ 12 → 16mph</div>
    </li>
  </ul>
</body>
</html>
"""

SAMPLE_SURF_CAPTAIN_HTML_PLUS = """
<!DOCTYPE html>
<html>
<body>
  <h1 id="fcst-current-title">The surf forecast is 1+ ft and semi glassy/bumpy right now for Grand Isle, LA</h1>
  <div class="fcst-current-cond fair">Fair</div>
  <div class="current-data-desc">78° SSE @ 8mph</div>
</body>
</html>
"""

SAMPLE_SURF_CAPTAIN_HTML_FLAT = """
<!DOCTYPE html>
<html>
<body>
  <h1 id="fcst-current-title">The surf forecast is Flat and calm right now</h1>
  <div class="fcst-current-cond flat">Flat</div>
</body>
</html>
"""

SAMPLE_SURF_FORECAST_HTML = """
<!DOCTYPE html>
<html>
<body>
  <table class="forecast-table__table">
    <tr data-row="rating">
      <td><div class="star-rating__rating star-rating__rating--5">5</div></td>
    </tr>
    <tr data-row="swell">
      <td>
        <div class="swell-icon" data-height="0.8"></div>
        <div class="forecast-table__swell-period">6</div>
        <div>SSE</div>
      </td>
    </tr>
    <tr data-row="wind">
      <td>
        <div class="wind-icon" data-speed="16.0"></div>
        <div class="wind-icon__letters">NNW</div>
      </td>
    </tr>
    <tr data-row="wind-state">
      <td><div class="wind-state wind-state--offshore">offshore</div></td>
    </tr>
  </table>
</body>
</html>
"""

SAMPLE_SURFLINE_JSON = {
    "data": {
        "wave": [
            {
                "timestamp": 1699000000,
                "surf": {
                    "min": 2,
                    "max": 3,
                    "optimalScore": 1,
                    "plus": False,
                    "humanRelation": "Thigh to waist",
                },
                "swells": [
                    {
                        "height": 2.5,
                        "period": 7.0,
                        "direction": 165.0,
                        "directionMin": 150.0,
                    }
                ],
            }
        ],
        "wind": [
            {
                "timestamp": 1699000000,
                "speed": 11.0,
                "direction": 355.0,
                "directionType": "Offshore",
            }
        ],
    }
}

SAMPLE_SURFLINE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Holly Beach Surf Report & Forecast - Surfline</title></head>
<body>
  <div class="quiver-surf-headline">2-3 FT</div>
  <div class="quiver-colored-condition">Fair</div>
  <div class="sl-spot-report__wind">
    <span class="wind-speed">11 kts</span>
    <span class="wind-direction">N</span>
  </div>
</body>
</html>
"""


class TestScraperService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.service = ScraperService()

    # --------------------------------------------------------------------------
    # Surf Captain Parser Tests
    # --------------------------------------------------------------------------
    def test_parse_surf_captain_range(self):
        reading = self.service.parse_surf_captain_html("holly_beach", SAMPLE_SURF_CAPTAIN_HTML_RANGE)
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertEqual(reading.source_id, "surf_captain")
        # 2-3 ft => average 2.5 ft
        self.assertEqual(reading.wave_height_ft, 2.5)
        # Weather: 74° N @ 12 → 16mph
        self.assertEqual(reading.air_temp_f, 74.0)
        self.assertEqual(reading.wind_speed_mph, 12.0)
        self.assertEqual(reading.wind_gust_mph, 16.0)
        self.assertEqual(reading.wind_direction_cardinal, "N")
        self.assertEqual(reading.wind_direction_deg, 0.0)
        self.assertEqual(reading.status, "valid")

    def test_parse_surf_captain_plus(self):
        reading = self.service.parse_surf_captain_html("grand_isle", SAMPLE_SURF_CAPTAIN_HTML_PLUS)
        self.assertIsInstance(reading, NormalizedMarineReading)
        # 1+ ft => 1.5 ft
        self.assertEqual(reading.wave_height_ft, 1.5)
        self.assertEqual(reading.air_temp_f, 78.0)
        self.assertEqual(reading.wind_speed_mph, 8.0)
        self.assertEqual(reading.wind_direction_cardinal, "SSE")

    def test_parse_surf_captain_flat(self):
        reading = self.service.parse_surf_captain_html("holly_beach", SAMPLE_SURF_CAPTAIN_HTML_FLAT)
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertEqual(reading.wave_height_ft, 0.5)

    # --------------------------------------------------------------------------
    # Surf-Forecast Parser Tests
    # --------------------------------------------------------------------------
    def test_parse_surf_forecast(self):
        reading = self.service.parse_surf_forecast_html("holly_beach", SAMPLE_SURF_FORECAST_HTML)
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertEqual(reading.source_id, "surf_forecast")
        # Swell: 0.8 m * 3.28084 = 2.62 ft
        self.assertAlmostEqual(reading.wave_height_ft, 2.62, delta=0.05)
        self.assertEqual(reading.wave_period_sec, 6.0)
        self.assertEqual(reading.swell_direction_cardinal, "SSE")
        # Wind: 16.0 km/h * 0.621371 = 9.9 mph
        self.assertAlmostEqual(reading.wind_speed_mph, 9.9, delta=0.2)
        self.assertEqual(reading.wind_direction_cardinal, "NNW")
        self.assertIn("Rating: 5/10", reading.raw_condition_label)
        self.assertIn("State: offshore", reading.raw_condition_label)

    # --------------------------------------------------------------------------
    # Routing & Mocked Network Tests
    # --------------------------------------------------------------------------
    async def test_grand_isle_safely_bypasses_surf_forecast(self):
        # Grand Isle has no page on Surf-Forecast.com and should return None gracefully
        res = await self.service.fetch_surf_forecast("grand_isle")
        self.assertIsNone(res)

    async def test_fetch_surf_captain_mock_success(self):
        async def handler(request: httpx.Request):
            return httpx.Response(200, text=SAMPLE_SURF_CAPTAIN_HTML_RANGE)

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = ScraperService(client=mock_client)

        reading = await service.fetch_surf_captain("holly_beach")
        self.assertEqual(reading.source_id, "surf_captain")
        self.assertEqual(reading.status, "valid")
        self.assertEqual(reading.wave_height_ft, 2.5)

    async def test_fetch_surf_captain_outage_fallback(self):
        async def handler(request: httpx.Request):
            return httpx.Response(500, text="Internal Server Error")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = ScraperService(client=mock_client)

        # 500 should degrade cleanly to static fallback with status="fallback"
        reading = await service.fetch_surf_captain("holly_beach")
        self.assertEqual(reading.source_id, "surf_captain")
        self.assertEqual(reading.status, "fallback")
        self.assertIsNotNone(reading.wave_height_ft)

    async def test_fetch_surf_forecast_outage_fallback(self):
        async def handler(request: httpx.Request):
            raise httpx.ConnectTimeout("Connection timed out")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = ScraperService(client=mock_client)

        # Timeout should degrade cleanly to fallback
        reading = await service.fetch_surf_forecast("holly_beach")
        self.assertEqual(reading.source_id, "surf_forecast")
        self.assertEqual(reading.status, "fallback")

    # --------------------------------------------------------------------------
    # Surfline Parser Tests
    # --------------------------------------------------------------------------
    def test_parse_surfline_json(self):
        reading = self.service.parse_surfline_json("holly_beach", SAMPLE_SURFLINE_JSON)
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertEqual(reading.source_id, "surfline")
        self.assertEqual(reading.wave_height_ft, 2.5)
        self.assertEqual(reading.wave_period_sec, 7.0)
        self.assertEqual(reading.swell_direction_deg, 165.0)
        self.assertEqual(reading.swell_direction_cardinal, "SSE")
        self.assertEqual(reading.wind_speed_mph, 11.0)
        self.assertEqual(reading.wind_direction_deg, 355.0)
        self.assertEqual(reading.wind_direction_cardinal, "N")
        self.assertIn("Thigh to waist", reading.raw_condition_label)
        self.assertEqual(reading.status, "valid")

    def test_parse_surfline_html(self):
        reading = self.service.parse_surfline_html("holly_beach", SAMPLE_SURFLINE_HTML)
        self.assertIsInstance(reading, NormalizedMarineReading)
        self.assertEqual(reading.source_id, "surfline")
        self.assertEqual(reading.wave_height_ft, 2.5)
        self.assertEqual(reading.wind_speed_mph, 11.0)
        self.assertEqual(reading.wind_direction_cardinal, "N")
        self.assertIn("Fair", reading.raw_condition_label)
        self.assertEqual(reading.status, "valid")

    async def test_fetch_surfline_mock_success(self):
        async def handler(request: httpx.Request):
            return httpx.Response(200, json=SAMPLE_SURFLINE_JSON)

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = ScraperService(client=mock_client)

        reading = await service.fetch_surfline("holly_beach")
        self.assertEqual(reading.source_id, "surfline")
        self.assertEqual(reading.status, "valid")
        self.assertEqual(reading.wave_height_ft, 2.5)

    async def test_fetch_surfline_403_cloudflare_fallback(self):
        async def handler(request: httpx.Request):
            return httpx.Response(403, text="Attention Required! | Cloudflare")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = ScraperService(client=mock_client)

        reading = await service.fetch_surfline("holly_beach")
        self.assertEqual(reading.source_id, "surfline")
        self.assertEqual(reading.status, "fallback")
        self.assertEqual(reading.wave_height_ft, 2.5)

    async def test_fetch_surfline_timeout_fallback(self):
        async def handler(request: httpx.Request):
            raise httpx.ConnectTimeout("Connection timed out to Surfline")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = ScraperService(client=mock_client)

        reading = await service.fetch_surfline("grand_isle")
        self.assertEqual(reading.source_id, "surfline")
        self.assertEqual(reading.status, "fallback")
        self.assertEqual(reading.wave_height_ft, 3.0)

    async def test_fetch_all_scrapers_holly_beach(self):
        async def handler(request: httpx.Request):
            url_str = str(request.url)
            if "surfcaptain" in url_str:
                return httpx.Response(200, text=SAMPLE_SURF_CAPTAIN_HTML_RANGE)
            elif "surf-forecast" in url_str:
                return httpx.Response(200, text=SAMPLE_SURF_FORECAST_HTML)
            elif "surfline" in url_str:
                return httpx.Response(200, json=SAMPLE_SURFLINE_JSON)
            return httpx.Response(404)

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = ScraperService(client=mock_client)

        readings = await service.fetch_all_scrapers("holly_beach")
        self.assertEqual(len(readings), 3)
        source_ids = [r.source_id for r in readings]
        self.assertIn("surf_captain", source_ids)
        self.assertIn("surf_forecast", source_ids)
        self.assertIn("surfline", source_ids)

    async def test_fetch_all_scrapers_grand_isle(self):
        async def handler(request: httpx.Request):
            url_str = str(request.url)
            if "surfcaptain" in url_str:
                return httpx.Response(200, text=SAMPLE_SURF_CAPTAIN_HTML_PLUS)
            elif "surfline" in url_str:
                return httpx.Response(200, json=SAMPLE_SURFLINE_JSON)
            return httpx.Response(404)

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = ScraperService(client=mock_client)

        readings = await service.fetch_all_scrapers("grand_isle")
        self.assertEqual(len(readings), 2)
        source_ids = [r.source_id for r in readings]
        self.assertIn("surf_captain", source_ids)
        self.assertIn("surfline", source_ids)

    def test_parse_surf_forecast_header_tooltip_isolation(self):
        """Verify wind-state parser isolates cell data and ignores header tooltip legend."""
        html_with_legend = """
        <table class="forecast-table__table">
          <tr data-row="wind-state">
            <th class="forecast-table__header">
              <div class="forecast-table__header-container forecast-table__header-container--wind-state">
                Wind State
                <div class="tooltip">
                  <div class="wind-state wind-state--on">on-shore</div>
                  <div class="wind-state wind-state--cross-on">cross-onshore</div>
                  <div class="wind-state wind-state--cross">cross-shore</div>
                  <div class="wind-state wind-state--cross-off">cross-offshore</div>
                  <div class="wind-state wind-state--off">off-shore</div>
                  <div class="wind-state wind-state--glassy">glassy</div>
                </div>
              </div>
            </th>
            <td class="forecast-table__cell">
              <div class="forecast-table__container forecast-table__container--wind-state">
                <div class="wind-state wind-state--cross-on">cross-on</div>
              </div>
            </td>
          </tr>
        </table>
        """
        reading = self.service.parse_surf_forecast_html("holly_beach", html_with_legend)
        self.assertIn("State: cross-on", reading.raw_condition_label)
        self.assertNotIn("on-shorecross-onshore", reading.raw_condition_label)


if __name__ == "__main__":
    unittest.main()
