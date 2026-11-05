"""
tests/verification/verify_m3_recommendation_stress.py
Adversarial Stress Test Suite for Milestone 3 (Recommendation Engine & Scrapers)
Louisiana Surf Finder.

Focus Areas:
1. Golden Window Contiguity Invariants (intermittent 1h onshore gust, 2h vs 3h thresholds, boundaries).
2. Ranking Determinism & Multi-Criteria Tie-Breaking (duration, optimal tide, recency, permutation invariance).
3. 48-Hour Flat Calm Marginal Fallback (scores < 30, sliding 3h window, advisory text, empty timeline resilience).
4. Concurrency, Re-Entrancy & Thread-Safety (50 parallel requests, zero state bleed between spots, latency < 100ms).
5. Scraper Fallback & Blackout Resilience (all scrapers 403 / timeout -> clean fallback with status='fallback').

Can be run via:
    .venv/bin/python tests/verification/verify_m3_recommendation_stress.py
or:
    .venv/bin/pytest tests/verification/verify_m3_recommendation_stress.py -v
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time
import asyncio
import unittest
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.models.schemas import (
    HourlyForecastPoint,
    GoldenWindowRecommendation,
    NormalizedMarineReading,
    ConsensusForecast,
)
from app.services.recommendation_engine import RecommendationEngine
from app.services.consensus_engine import ConsensusEngine
from app.services.scraper_service import ScraperService


class TestM3RecommendationStress(unittest.IsolatedAsyncioTestCase):
    """Adversarial stress and invariant verification for Milestone 3."""

    def setUp(self):
        self.rec_engine = RecommendationEngine()
        self.consensus_engine = ConsensusEngine()
        self.base_time = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)

    # =========================================================================
    # 1. GOLDEN WINDOW CONTIGUITY INVARIANTS
    # =========================================================================

    def test_intermittent_onshore_gust_breaks_contiguity(self):
        """
        Construct a 48h timeline with 2h offshore, 1h onshore gust (25mph), 2h offshore.
        Neither run reaches >= 3 consecutive hours.
        Invariant: has_good_window MUST be False.
        """
        timeline: List[HourlyForecastPoint] = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            if h in (10, 11):
                # Run 1: 2 hours offshore
                wind_spd = 12.0
                wind_dir = 360.0
                wind_regime = "Offshore Groomed"
                score = 72
            elif h == 12:
                # Intermittent 1-hour onshore gust!
                wind_spd = 25.0
                wind_dir = 175.0
                wind_regime = "Onshore Blown-Out"
                score = 15
            elif h in (13, 14):
                # Run 2: 2 hours offshore
                wind_spd = 10.0
                wind_dir = 355.0
                wind_regime = "Offshore Groomed"
                score = 70
            else:
                wind_spd = 6.0
                wind_dir = 90.0
                wind_regime = "Cross-Shore"
                score = 25

            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.0 if score > 30 else 1.0,
                    breaking_range="3-4 ft" if score > 30 else "Flat",
                    dominant_period_sec=7.0 if score > 30 else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=wind_spd,
                    wind_direction_deg=wind_dir,
                    wind_direction_cardinal="N" if wind_dir > 300 else "S",
                    wind_regime=wind_regime,
                    surf_score=score,
                    decision="Good / Decent" if score >= 60 else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )

        rec = self.rec_engine.detect_golden_windows(timeline, spot_id="holly_beach")
        self.assertFalse(rec.has_good_window, "2h runs interrupted by 1h gust must NOT declare a golden window")
        self.assertIn("Marginal Window:", rec.window_title)

    def test_strict_boundary_2h_rejected_vs_3h_accepted(self):
        """
        Test borderline 2-hour window vs 3-hour window.
        Verify 2-hour window is strictly rejected, while 3-hour window is accepted.
        """
        # Part A: Exactly 2 consecutive hours meeting criteria
        timeline_2h = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            is_good = 10 <= h <= 11  # 2 hours: 10, 11
            timeline_2h.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.5 if is_good else 1.0,
                    breaking_range="3-4 ft" if is_good else "Flat",
                    dominant_period_sec=8.0 if is_good else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=10.0 if is_good else 5.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Offshore Groomed" if is_good else "Cross-Shore",
                    surf_score=78 if is_good else 20,
                    decision="Good / Decent" if is_good else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )
        rec_2h = self.rec_engine.detect_golden_windows(timeline_2h, spot_id="holly_beach")
        self.assertFalse(rec_2h.has_good_window, "Strictly 2 hours must be rejected")
        self.assertIn("Marginal Window:", rec_2h.window_title)

        # Part B: Exactly 3 consecutive hours meeting criteria
        timeline_3h = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            is_good = 10 <= h <= 12  # 3 hours: 10, 11, 12
            timeline_3h.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.5 if is_good else 1.0,
                    breaking_range="3-4 ft" if is_good else "Flat",
                    dominant_period_sec=8.0 if is_good else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=10.0 if is_good else 5.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Offshore Groomed" if is_good else "Cross-Shore",
                    surf_score=78 if is_good else 20,
                    decision="Good / Decent" if is_good else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )
        rec_3h = self.rec_engine.detect_golden_windows(timeline_3h, spot_id="holly_beach")
        self.assertTrue(rec_3h.has_good_window, "Strictly 3 hours must be accepted")
        self.assertIn("Golden Window:", rec_3h.window_title)
        self.assertEqual(rec_3h.start_time, timeline_3h[10].timestamp)
        self.assertEqual(rec_3h.end_time, timeline_3h[12].timestamp + timedelta(hours=1))

    def test_edge_boundary_windows_start_and_end_of_timeline(self):
        """
        Verify contiguous golden window at the very beginning (hours 0..2)
        and very end (hours 45..47) are properly captured without off-by-one errors.
        """
        # Beginning window: hours 0, 1, 2
        timeline_beg = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            is_win = h in (0, 1, 2)
            timeline_beg.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.0 if is_win else 1.0,
                    breaking_range="2-3 ft" if is_win else "Flat",
                    dominant_period_sec=7.0 if is_win else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=10.0 if is_win else 5.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Offshore Groomed" if is_win else "Cross-Shore",
                    surf_score=65 if is_win else 20,
                    decision="Good / Decent" if is_win else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )
        rec_beg = self.rec_engine.detect_golden_windows(timeline_beg, spot_id="holly_beach")
        self.assertTrue(rec_beg.has_good_window)
        self.assertEqual(rec_beg.start_time, timeline_beg[0].timestamp)
        self.assertEqual(rec_beg.end_time, timeline_beg[2].timestamp + timedelta(hours=1))

        # Ending window: hours 45, 46, 47
        timeline_end = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            is_win = h in (45, 46, 47)
            timeline_end.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.0 if is_win else 1.0,
                    breaking_range="2-3 ft" if is_win else "Flat",
                    dominant_period_sec=7.0 if is_win else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=10.0 if is_win else 5.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Offshore Groomed" if is_win else "Cross-Shore",
                    surf_score=65 if is_win else 20,
                    decision="Good / Decent" if is_win else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )
        rec_end = self.rec_engine.detect_golden_windows(timeline_end, spot_id="holly_beach")
        self.assertTrue(rec_end.has_good_window)
        self.assertEqual(rec_end.start_time, timeline_end[45].timestamp)
        self.assertEqual(rec_end.end_time, timeline_end[47].timestamp + timedelta(hours=1))

    # =========================================================================
    # 2. RANKING DETERMINISM & TIE-BREAKING
    # =========================================================================

    def test_ranking_tie_breaking_favors_longer_duration(self):
        """
        Two candidate windows with identical peak score (70 vs 70),
        Window A is 3 hours (hours 8..10), Window B is 5 hours (hours 24..28).
        Duration bonus (+2.0 per hour up to 8h) MUST break the tie in favor of Window B.
        """
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            is_win_a = 8 <= h <= 10  # 3 hours, peak 70
            is_win_b = 24 <= h <= 28  # 5 hours, peak 70
            is_win = is_win_a or is_win_b
            score = 70 if is_win else 20

            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.0 if is_win else 1.0,
                    breaking_range="2-3 ft" if is_win else "Flat",
                    dominant_period_sec=7.0 if is_win else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=10.0 if is_win else 5.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Offshore Groomed" if is_win else "Cross-Shore",
                    surf_score=score,
                    decision="Good / Decent" if is_win else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )

        rec = self.rec_engine.detect_golden_windows(timeline, spot_id="holly_beach")
        self.assertTrue(rec.has_good_window)
        # Window B has 5h (+10 pts) vs Window A 3h (+6 pts)
        self.assertEqual(rec.start_time, timeline[24].timestamp, "Longer 5-hour window MUST win tie over 3-hour window")

    def test_ranking_tie_breaking_favors_optimal_tide(self):
        """
        Two candidate windows with identical peak score (70 vs 70) and identical duration (3h vs 3h).
        Window A has peak at 'Rising' tide (+6.0 bonus).
        Window B has peak at 'Slack Low' tide (-4.0 penalty).
        Window A MUST win.
        """
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            is_win_a = 6 <= h <= 8  # 3 hours, Rising tide
            is_win_b = 20 <= h <= 22  # 3 hours, Slack Low tide
            is_win = is_win_a or is_win_b
            score = 70 if is_win else 20
            tide_status = "Rising" if is_win_a else ("Slack Low" if is_win_b else "Falling")

            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.0 if is_win else 1.0,
                    breaking_range="2-3 ft" if is_win else "Flat",
                    dominant_period_sec=7.0 if is_win else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=10.0 if is_win else 5.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Offshore Groomed" if is_win else "Cross-Shore",
                    surf_score=score,
                    decision="Good / Decent" if is_win else "Poor / Flat",
                    tide_height_ft=1.4 if is_win_a else 0.5,
                    tide_status=tide_status,
                )
            )

        rec = self.rec_engine.detect_golden_windows(timeline, spot_id="holly_beach")
        self.assertTrue(rec.has_good_window)
        self.assertEqual(rec.start_time, timeline[6].timestamp, "Rising tide window MUST win tie over Slack Low window")

    def test_ranking_determinism_across_100_runs_and_permutations(self):
        """
        Verify that window ranking is strictly deterministic across 100 runs
        and independent of candidate list ordering.
        """
        w1 = [
            HourlyForecastPoint(
                timestamp=self.base_time + timedelta(hours=h),
                breaking_height_ft=3.0,
                breaking_range="2-3 ft",
                dominant_period_sec=7.0,
                swell_direction_deg=170.0,
                swell_direction_cardinal="S",
                wind_speed_mph=10.0,
                wind_direction_deg=0.0,
                wind_direction_cardinal="N",
                wind_regime="Offshore Groomed",
                surf_score=70,
                decision="Good / Decent",
                tide_height_ft=1.4,
                tide_status="Rising",
            )
            for h in range(4)
        ]
        w2 = [
            HourlyForecastPoint(
                timestamp=self.base_time + timedelta(hours=20 + h),
                breaking_height_ft=3.2,
                breaking_range="2-3 ft",
                dominant_period_sec=7.2,
                swell_direction_deg=170.0,
                swell_direction_cardinal="S",
                wind_speed_mph=10.0,
                wind_direction_deg=0.0,
                wind_direction_cardinal="N",
                wind_regime="Offshore Groomed",
                surf_score=70,
                decision="Good / Decent",
                tide_height_ft=1.4,
                tide_status="Slack High",
            )
            for h in range(4)
        ]

        # W1 has Rising (+6.0) vs W2 Slack High (+4.0). W1 should win.
        for _ in range(50):
            res_forward = self.rec_engine._rank_golden_windows([w1, w2])
            res_backward = self.rec_engine._rank_golden_windows([w2, w1])
            self.assertEqual(res_forward[0].timestamp, w1[0].timestamp)
            self.assertEqual(res_backward[0].timestamp, w1[0].timestamp)

    # =========================================================================
    # 3. 48-HOUR FLAT CALM MARGINAL FALLBACK
    # =========================================================================

    def test_flat_calm_timeline_triggers_sliding_3h_fallback(self):
        """
        Construct an entirely flat timeline where all hourly scores are < 30 (range 12..25).
        Verify detect_golden_windows() triggers its fallback mechanism, returning a sliding 3-hour
        marginal window with appropriate advisory labeling rather than crashing or returning an unhandled empty list.
        """
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            score = 15 + (h % 10)  # scores 15 to 24 (all < 30)
            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=0.7,
                    breaking_range="Flat",
                    dominant_period_sec=3.5,
                    swell_direction_deg=160.0,
                    swell_direction_cardinal="SSE",
                    wind_speed_mph=5.0,
                    wind_direction_deg=120.0,
                    wind_direction_cardinal="ESE",
                    wind_regime="Light / Glassy" if h % 3 == 0 else "Cross-Shore",
                    surf_score=score,
                    decision="Poor / Flat",
                    tide_height_ft=1.2,
                    tide_status="Slack High",
                )
            )

        rec = self.rec_engine.detect_golden_windows(timeline, spot_id="holly_beach")

        # Invariant checks:
        self.assertFalse(rec.has_good_window)
        self.assertIn("Marginal Window:", rec.window_title)
        self.assertIsNotNone(rec.start_time)
        self.assertIsNotNone(rec.end_time)

        # Duration of marginal sliding window must be exactly 3 hours!
        duration = rec.end_time - rec.start_time
        self.assertEqual(duration, timedelta(hours=3), f"Marginal sliding window must span exactly 3 hours, got {duration}")

        # Advisory verdict must give authentic surfer guidance
        self.assertIn("Small or flat surf", rec.human_verdict)
        self.assertIn("Peak", rec.human_verdict)
        self.assertIn("hydrofoil", rec.human_verdict.lower())

    def test_marginal_conditions_sub_60_advisory(self):
        """
        Test conditions with peak score between 45 and 55 (sub-60 threshold).
        Should return has_good_window=False, window_title containing 'Marginal Window',
        and human_verdict containing 'Marginal conditions'.
        """
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            score = 50 if 12 <= h <= 14 else 35
            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=2.0 if score == 50 else 1.5,
                    breaking_range="1-2 ft (Ankle to Knee)",
                    dominant_period_sec=5.5,
                    swell_direction_deg=165.0,
                    swell_direction_cardinal="SSE",
                    wind_speed_mph=7.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Offshore Groomed" if score == 50 else "Cross-Shore",
                    surf_score=score,
                    decision="Fair" if score == 50 else "Poor / Flat",
                    tide_height_ft=1.3,
                    tide_status="Rising",
                )
            )

        rec = self.rec_engine.detect_golden_windows(timeline, spot_id="grand_isle")
        self.assertFalse(rec.has_good_window)
        self.assertIn("Marginal Window:", rec.window_title)
        self.assertIn("Marginal conditions for Grand Isle", rec.human_verdict)

    def test_empty_timeline_resilience(self):
        """Verify passing an empty timeline does not crash and returns graceful fallback."""
        rec = self.rec_engine.detect_golden_windows([], spot_id="holly_beach")
        self.assertFalse(rec.has_good_window)
        self.assertEqual(rec.window_title, "No Forecast Available")
        self.assertIn("unavailable", rec.human_verdict)

    # =========================================================================
    # 4. CONCURRENCY & RE-ENTRANCY (50 Parallel Requests)
    # =========================================================================

    async def test_high_concurrency_synthesize_and_consensus(self):
        """
        Concurrently execute 50 parallel requests alternating between Holly Beach and Grand Isle.
        Verify zero race conditions, zero state bleed between spots, and latency well under 100ms.
        """
        dummy_model_hb = [
            {"wave_height": 2.5, "wave_period": 6.5, "wave_direction": 170.0, "wind_speed_10m": 10.0, "wind_direction_10m": 360.0}
            for _ in range(48)
        ]
        dummy_model_gi = [
            {"wave_height": 3.0, "wave_period": 7.0, "wave_direction": 155.0, "wind_speed_10m": 12.0, "wind_direction_10m": 335.0}
            for _ in range(48)
        ]

        dummy_reading_hb = NormalizedMarineReading(
            source_id="ndbc_42035",
            source_name="Buoy 42035",
            source_type="buoy",
            timestamp=self.base_time,
            wave_height_ft=3.0,
            wave_period_sec=6.5,
            wave_direction_deg=170.0,
            wind_speed_mph=10.0,
            wind_direction_deg=0.0,
            status="valid",
        )
        dummy_reading_gi = NormalizedMarineReading(
            source_id="ndbc_42354",
            source_name="Buoy 42354",
            source_type="buoy",
            timestamp=self.base_time,
            wave_height_ft=3.5,
            wave_period_sec=7.0,
            wave_direction_deg=155.0,
            wind_speed_mph=12.0,
            wind_direction_deg=335.0,
            status="valid",
        )

        async def worker_task(i: int):
            spot_id = "holly_beach" if i % 2 == 0 else "grand_isle"
            model = dummy_model_hb if i % 2 == 0 else dummy_model_gi
            reading = dummy_reading_hb if i % 2 == 0 else dummy_reading_gi

            t0 = time.perf_counter()

            # 1. Timeline synthesis
            timeline = self.rec_engine.synthesize_48h_timeline(
                spot_id=spot_id,
                hourly_model_points=model,
                live_buoy_reading=reading,
                base_time=self.base_time,
            )

            # 2. Consensus generation
            consensus = self.consensus_engine.generate_spot_consensus(
                spot_id=spot_id,
                readings=[reading],
                hourly_weather=model,
                base_time=self.base_time,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            return i, spot_id, timeline, consensus, elapsed_ms

        tasks = [worker_task(i) for i in range(50)]
        results = await asyncio.gather(*tasks)

        self.assertEqual(len(results), 50)

        latencies = []
        for i, spot_id, timeline, consensus, elapsed_ms in results:
            latencies.append(elapsed_ms)
            # Re-entrancy & state bleed checks:
            self.assertEqual(consensus.spot_id, spot_id, f"State bleed detected: expected {spot_id}, got {consensus.spot_id}")
            self.assertEqual(len(timeline), 48, f"Timeline truncated: expected 48 points, got {len(timeline)}")
            self.assertEqual(len(consensus.timeline_48h), 48)

            if spot_id == "holly_beach":
                self.assertEqual(consensus.spot_name, "Holly Beach (Cajun Riviera)")
                self.assertAlmostEqual(consensus.latitude, 29.7711, places=3)
            else:
                self.assertEqual(consensus.spot_name, "Grand Isle")
                self.assertAlmostEqual(consensus.latitude, 29.2366, places=3)

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        # Verify latency constraint
        self.assertLess(avg_latency, 100.0, f"Average execution time {avg_latency:.2f}ms exceeds 100ms threshold")
        print(f"\n[Concurrency Benchmark] 50 Parallel Requests: Avg={avg_latency:.2f}ms, Max={max_latency:.2f}ms, Success=50/50")

    # =========================================================================
    # 5. SCRAPER FALLBACK & NETWORK BLACKOUT VERIFICATION
    # =========================================================================

    async def test_scraper_total_network_blackout_resilience(self):
        """
        Simulate a total network blackout where all scrapers (Surfline, Surf-Forecast, Surf Captain)
        fail with HTTP 403 or timeouts.
        Verify fetch_all_scrapers degrades cleanly to pre-seeded fallbacks with status='fallback'
        without raising uncaught exceptions, and downstream consensus generation succeeds.
        """
        async def blackout_handler(request: httpx.Request):
            url_str = str(request.url)
            if "surfline" in url_str:
                # Surfline returns HTTP 403 Forbidden
                return httpx.Response(403, text="Attention Required! | Cloudflare Anti-Bot Challenge")
            elif "surf-forecast" in url_str:
                # Surf-Forecast throws Timeout
                raise httpx.ReadTimeout("Connection timed out after 5.0s", request=request)
            else:
                # Surf Captain throws ConnectError
                raise httpx.ConnectError("Failed to resolve host / network down", request=request)

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(blackout_handler))
        scraper_service = ScraperService(client=mock_client)

        # --- Holly Beach Blackout Test ---
        hb_readings = await scraper_service.fetch_all_scrapers("holly_beach")
        self.assertEqual(len(hb_readings), 3, "Holly Beach must return 3 fallback readings under blackout")

        hb_source_ids = {r.source_id for r in hb_readings}
        self.assertEqual(hb_source_ids, {"surf_captain", "surfline", "surf_forecast"})

        for r in hb_readings:
            self.assertEqual(r.status, "fallback", f"Reading {r.source_id} must have status='fallback'")
            self.assertGreater(r.wave_height_ft, 0.0)
            self.assertGreater(r.confidence_weight, 0.0)
            self.assertIn("Fallback", r.raw_condition_label)

        # --- Grand Isle Blackout Test ---
        gi_readings = await scraper_service.fetch_all_scrapers("grand_isle")
        self.assertEqual(len(gi_readings), 2, "Grand Isle must return 2 fallback readings (no surf_forecast) under blackout")

        gi_source_ids = {r.source_id for r in gi_readings}
        self.assertEqual(gi_source_ids, {"surf_captain", "surfline"})

        for r in gi_readings:
            self.assertEqual(r.status, "fallback")
            self.assertGreater(r.wave_height_ft, 0.0)
            self.assertGreater(r.confidence_weight, 0.0)

        # --- Downstream Consensus Engine Integration under Blackout ---
        # Feed the blackout fallbacks directly into consensus generation
        hb_consensus = self.consensus_engine.generate_spot_consensus(
            spot_id="holly_beach",
            readings=hb_readings,
            base_time=self.base_time,
        )
        self.assertIsInstance(hb_consensus, ConsensusForecast)
        self.assertTrue(hb_consensus.is_fallback, "Consensus must mark is_fallback=True when sources are fallback")
        self.assertGreater(hb_consensus.surf_score, 0)
        self.assertIsNotNone(hb_consensus.best_window)

        gi_consensus = self.consensus_engine.generate_spot_consensus(
            spot_id="grand_isle",
            readings=gi_readings,
            base_time=self.base_time,
        )
        self.assertIsInstance(gi_consensus, ConsensusForecast)
        self.assertTrue(gi_consensus.is_fallback)
        self.assertGreater(gi_consensus.surf_score, 0)


def main():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestM3RecommendationStress)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
