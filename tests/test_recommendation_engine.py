"""
tests/test_recommendation_engine.py
Comprehensive test suite for Louisiana Surf Finder Recommendation Engine:
48-Hour Golden Window Detector, Contiguous Run Filter, Multi-Criteria Ranking (WQS),
Post-Frontal Clean-Off Tagging, Marginal Sliding Fallback, Quiver Selector,
Wetsuit / Rubber Advisor, Cross-Spot Comparison (Holly Beach vs Grand Isle),
and 48-Hour Timeline Synthesizer schema compliance.
"""

import unittest
from datetime import datetime, timezone, timedelta

from app.models.schemas import (
    HourlyForecastPoint,
    GoldenWindowRecommendation,
    NormalizedMarineReading,
    ConsensusForecast,
)
from app.services.recommendation_engine import RecommendationEngine


class TestRecommendationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RecommendationEngine()
        self.base_time = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

    # --------------------------------------------------------------------------
    # 1. Golden Window Detector & Contiguous Filtering Tests
    # --------------------------------------------------------------------------
    def test_contiguous_golden_window_detection(self):
        """T1: Verify >= 3 consecutive hours with score >= 60 and offshore winds triggers has_good_window=True."""
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            is_window = 10 <= h <= 13  # 4 consecutive hours
            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.2 if is_window else 1.2,
                    breaking_range="3-4 ft (Waist to Chest)" if is_window else "1-2 ft (Ankle to Knee)",
                    dominant_period_sec=7.5 if is_window else 4.5,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=12.0 if is_window else 6.0,
                    wind_direction_deg=355.0 if is_window else 160.0,
                    wind_direction_cardinal="N" if is_window else "SSE",
                    wind_regime="Offshore Groomed" if is_window else "Onshore Blown-Out",
                    surf_score=75 if is_window else 25,
                    decision="Good / Decent" if is_window else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )

        rec = self.engine.detect_golden_windows(timeline, spot_id="holly_beach")
        self.assertTrue(rec.has_good_window)
        self.assertIn("Golden Window:", rec.window_title)
        self.assertEqual(rec.peak_score, 75)
        self.assertEqual(rec.peak_wave_range, "3-4 ft (Waist to Chest)")
        self.assertEqual(rec.start_time, timeline[10].timestamp)
        self.assertEqual(rec.end_time, timeline[13].timestamp + timedelta(hours=1))

    def test_short_window_rejection(self):
        """T2: Verify 2-hour window is rejected as below the 3-hour minimum threshold."""
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            is_window = 10 <= h <= 11  # Only 2 hours
            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.5 if is_window else 1.0,
                    breaking_range="3-4 ft (Waist to Chest)" if is_window else "Flat",
                    dominant_period_sec=7.5 if is_window else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=12.0 if is_window else 5.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Offshore Groomed" if is_window else "Light / Glassy",
                    surf_score=80 if is_window else 20,
                    decision="Firing / Epic" if is_window else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )

        rec = self.engine.detect_golden_windows(timeline, spot_id="holly_beach")
        # 2 hours does not meet >= 3h contiguous threshold
        self.assertFalse(rec.has_good_window)
        self.assertIn("Marginal Window:", rec.window_title)

    def test_onshore_interruption_rejection(self):
        """T3: Verify that an onshore wind hour splits/invalidates a contiguous block."""
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            # 2h offshore (h=10,11) + 1h onshore blown-out (h=12) + 2h offshore (h=13,14)
            is_offshore = h in (10, 11, 13, 14)
            is_onshore = h == 12
            score = 70 if (is_offshore or is_onshore) else 20
            wind_regime = "Onshore Blown-Out" if is_onshore else ("Offshore Groomed" if is_offshore else "Light / Glassy")

            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.0 if score > 20 else 1.0,
                    breaking_range="2-3 ft (Thigh to Waist)" if score > 20 else "Flat",
                    dominant_period_sec=7.0 if score > 20 else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=15.0 if is_onshore else 10.0,
                    wind_direction_deg=170.0 if is_onshore else 0.0,
                    wind_direction_cardinal="S" if is_onshore else "N",
                    wind_regime=wind_regime,
                    surf_score=score,
                    decision="Good / Decent" if score > 20 else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )

        rec = self.engine.detect_golden_windows(timeline, spot_id="holly_beach")
        # Neither segment reaches 3 consecutive hours (2h and 2h)
        self.assertFalse(rec.has_good_window)

    def test_competing_window_ranking(self):
        """T4: Verify multi-window ranking prioritizes peak score, duration bonus, and tide bonus."""
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            # Window A: hours 6-8 (3 hours), peak score 68
            # Window B: hours 20-24 (5 hours), peak score 82
            if 6 <= h <= 8:
                score = 68
                is_win = True
            elif 20 <= h <= 24:
                score = 82
                is_win = True
            else:
                score = 25
                is_win = False

            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.5 if is_win else 1.0,
                    breaking_range="3-4 ft" if is_win else "Flat",
                    dominant_period_sec=7.5 if is_win else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=10.0 if is_win else 5.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Offshore Groomed" if is_win else "Light / Glassy",
                    surf_score=score,
                    decision="Firing / Epic" if score >= 80 else ("Good / Decent" if score >= 60 else "Poor / Flat"),
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )

        rec = self.engine.detect_golden_windows(timeline, spot_id="holly_beach")
        self.assertTrue(rec.has_good_window)
        # Window B should win because of higher peak score (82 vs 68) and longer duration (5h vs 3h)
        self.assertEqual(rec.peak_score, 82)
        self.assertEqual(rec.start_time, timeline[20].timestamp)

    def test_post_frontal_clean_off_tagging(self):
        """T5: Verify post-frontal clean-off is detected and tagged in human verdict."""
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            is_win = 10 <= h <= 14  # 5 hours
            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=3.8 if is_win else 1.0,
                    breaking_range="3-4 ft (Waist to Chest)" if is_win else "Flat",
                    dominant_period_sec=8.0 if is_win else 4.0,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=14.0 if is_win else 5.0,
                    wind_direction_deg=350.0 if is_win else 0.0,
                    wind_direction_cardinal="N" if is_win else "N",
                    wind_regime="Offshore Groomed" if is_win else "Light / Glassy",
                    surf_score=82 if is_win else 20,
                    decision="Firing / Epic" if is_win else "Poor / Flat",
                    tide_height_ft=1.4,
                    tide_status="Rising",
                )
            )

        rec = self.engine.detect_golden_windows(timeline, spot_id="holly_beach")
        self.assertTrue(rec.has_good_window)
        self.assertIn("Post-Frontal Clean-Off Alert!", rec.human_verdict)

    def test_flat_spells_marginal_fallback(self):
        """T6: Verify 3-hour sliding window fallback provides authentic advice when completely flat."""
        timeline = []
        for h in range(48):
            dt = self.base_time + timedelta(hours=h)
            timeline.append(
                HourlyForecastPoint(
                    timestamp=dt,
                    breaking_height_ft=0.8,
                    breaking_range="Flat",
                    dominant_period_sec=3.5,
                    swell_direction_deg=170.0,
                    swell_direction_cardinal="S",
                    wind_speed_mph=4.0,
                    wind_direction_deg=0.0,
                    wind_direction_cardinal="N",
                    wind_regime="Light / Glassy",
                    surf_score=18,
                    decision="Poor / Flat",
                    tide_height_ft=1.2,
                    tide_status="Slack High",
                )
            )

        rec = self.engine.detect_golden_windows(timeline, spot_id="holly_beach")
        self.assertFalse(rec.has_good_window)
        self.assertIn("Marginal Window:", rec.window_title)
        self.assertIn("Longboard", rec.human_verdict)

    # --------------------------------------------------------------------------
    # 2. Board Quiver Selector Tests
    # --------------------------------------------------------------------------
    def test_quiver_recommendation_tiers(self):
        """T7: Verify board selector outputs appropriate boards across wave height tiers."""
        # Tier 1: Flat (<1.5 ft)
        b_flat = self.engine.recommend_board(1.0, 4.0, "Light / Glassy")
        self.assertIn("Longboard", b_flat["primary"])

        # Tier 2: Knee to Waist (1.5 - 2.5 ft)
        b_small = self.engine.recommend_board(2.0, 6.0, "Offshore Groomed")
        self.assertIn("Twin Fin Fish", b_small["primary"])

        # Tier 3: Waist to Chest (2.5 - 4.0 ft)
        b_mid = self.engine.recommend_board(3.5, 7.5, "Offshore Groomed")
        self.assertIn("Performance Fish", b_mid["primary"])

        # Tier 4: Chest to Head High (4.0 - 6.0 ft)
        b_head = self.engine.recommend_board(5.0, 8.5, "Offshore Groomed")
        self.assertIn("Performance Shortboard", b_head["primary"])

        # Tier 5: Overhead+ (> 6.0 ft)
        b_large = self.engine.recommend_board(7.0, 10.0, "Offshore Groomed")
        self.assertIn("Step-Up", b_large["primary"])

    # --------------------------------------------------------------------------
    # 3. Wetsuit Advisor Tests
    # --------------------------------------------------------------------------
    def test_wetsuit_recommendation_temperatures(self):
        """T8: Verify wetsuit advisor recommends correct neoprene thickness across temperatures."""
        # Tropical >= 78°F
        w_warm = self.engine.recommend_wetsuit(82.0)
        self.assertEqual(w_warm["wetsuit"], "Boardshorts / Bikini")

        # 71 - 77°F
        w_mild = self.engine.recommend_wetsuit(74.0)
        self.assertIn("Top", w_mild["wetsuit"])

        # 64 - 70°F
        w_spring = self.engine.recommend_wetsuit(68.0)
        self.assertIn("3/2mm", w_spring["wetsuit"])

        # 57 - 63°F
        w_cool = self.engine.recommend_wetsuit(60.0)
        self.assertIn("3/2mm GBS", w_cool["wetsuit"])

        # 51 - 56°F
        w_cold = self.engine.recommend_wetsuit(53.0)
        self.assertIn("4/3mm", w_cold["wetsuit"])
        self.assertIn("Booties mandatory", w_cold["accessories"])

        # < 51°F Arctic freeze
        w_freeze = self.engine.recommend_wetsuit(48.0)
        self.assertIn("Hooded", w_freeze["wetsuit"])
        self.assertIn("Gloves mandatory", w_freeze["accessories"])

        # Missing temperature -> seasonal fallback
        w_missing = self.engine.recommend_wetsuit(None)
        self.assertIsNotNone(w_missing["wetsuit"])

    # --------------------------------------------------------------------------
    # 4. Cross-Spot Comparison Tests
    # --------------------------------------------------------------------------
    def test_cross_spot_comparison(self):
        """T9: Verify head-to-head comparison correctly identifies winner with driving logistics."""
        dt = datetime.now(timezone.utc)
        hb_window = GoldenWindowRecommendation(
            has_good_window=True,
            window_title="Golden Window: Tomorrow 6:00 AM – 11:00 AM",
            peak_score=68,
            peak_wave_range="2-3 ft (Thigh to Waist)",
            human_verdict="Fun peelers on Holly Beach.",
        )
        gi_window = GoldenWindowRecommendation(
            has_good_window=True,
            window_title="Golden Window: Tomorrow 6:00 AM – 11:00 AM",
            peak_score=82,
            peak_wave_range="4-5 ft (Chest to Head)",
            human_verdict="Punchy groin wedges on Grand Isle.",
        )

        hb_consensus = ConsensusForecast(
            spot_id="holly_beach",
            spot_name="Holly Beach (Cajun Riviera)",
            latitude=29.7711,
            longitude=-93.4475,
            timestamp=dt,
            surf_score=68,
            overall_decision="Good / Decent",
            badge_color="emerald",
            breaking_height_ft=2.5,
            breaking_range_str="2-3 ft (Thigh to Waist)",
            primary_swell_period_sec=7.0,
            primary_swell_direction_deg=170.0,
            primary_swell_compass="S",
            wind_speed_mph=12.0,
            wind_direction_deg=0.0,
            wind_compass="N",
            wind_character="Offshore Groomed",
            tide_state="Rising",
            confidence_score_pct=85,
            confidence_label="High",
            best_window=hb_window,
        )

        gi_consensus = ConsensusForecast(
            spot_id="grand_isle",
            spot_name="Grand Isle",
            latitude=29.2366,
            longitude=-89.9873,
            timestamp=dt,
            surf_score=82,
            overall_decision="Firing / Epic",
            badge_color="purple",
            breaking_height_ft=4.2,
            breaking_range_str="4-5 ft (Chest to Head)",
            primary_swell_period_sec=8.5,
            primary_swell_direction_deg=155.0,
            primary_swell_compass="SSE",
            wind_speed_mph=10.0,
            wind_direction_deg=340.0,
            wind_compass="NNW",
            wind_character="Offshore Groomed",
            tide_state="Rising",
            confidence_score_pct=88,
            confidence_label="High",
            best_window=gi_window,
        )

        comparison = self.engine.generate_spot_comparison(hb_consensus, gi_consensus)
        # Grand Isle score is 82, Holly Beach is 68 -> GI wins by 14 points (>= 8 threshold)
        self.assertEqual(comparison["winner_spot"], "grand_isle")
        self.assertIn("Grand Isle is the standout choice", comparison["comparison_verdict"])
        self.assertIn("LA-1", comparison["grand_isle_logistics"])
        self.assertIn("Cameron Ferry", comparison["holly_beach_logistics"])

    # --------------------------------------------------------------------------
    # 5. Timeline Synthesizer Schema Compliance
    # --------------------------------------------------------------------------
    def test_timeline_synthesizer_schema_compliance(self):
        """T10: Verify synthesized 48h timeline strictly adheres to HourlyForecastPoint schema."""
        hourly_model = [
            {
                "wave_height": 3.0,
                "wave_period": 7.0,
                "wave_direction": 160.0,
                "wind_speed_10m": 12.0,
                "wind_direction_10m": 0.0,
            }
            for _ in range(48)
        ]
        live_buoy = NormalizedMarineReading(
            source_id="ndbc_42035",
            source_name="Buoy 42035",
            source_type="buoy",
            timestamp=self.base_time,
            wave_height_ft=3.2,
            wave_period_sec=7.0,
        )

        timeline = self.engine.synthesize_48h_timeline(
            spot_id="holly_beach",
            hourly_model_points=hourly_model,
            live_buoy_reading=live_buoy,
            base_time=self.base_time,
        )

        self.assertEqual(len(timeline), 48)
        for pt in timeline:
            self.assertIsInstance(pt, HourlyForecastPoint)
            self.assertGreaterEqual(pt.surf_score, 0)
            self.assertLessEqual(pt.surf_score, 100)
            self.assertIn(pt.wind_regime, ["Offshore Groomed", "Cross-Shore", "Onshore Blown-Out", "Light / Glassy"])
            self.assertIn(pt.decision, ["Poor / Flat", "Fair", "Good / Decent", "Firing / Epic"])


if __name__ == "__main__":
    unittest.main()
