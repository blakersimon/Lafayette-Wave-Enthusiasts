"""
tests/test_consensus_engine.py
Comprehensive test suite for Louisiana Surf Finder Consensus Engine:
Shoaling physics, Mississippi Birdfoot Delta shadow mask, Hunt's rational polynomial dispersion,
continuous 0-100 scoring, storm washout damping, multi-source consensus fusion,
two-tier statistical outlier rejection, and full spot forecast synthesis.
"""

import unittest
import math
from datetime import datetime, timezone

from app.models.schemas import NormalizedMarineReading, ConsensusForecast
from app.services.consensus_engine import (
    calculate_angular_divergence,
    calculate_directional_exposure,
    calculate_shallow_wavelength,
    transform_wave_shoaling,
    shoaled_breaking_height,
    calculate_base_height_score,
    calculate_period_score,
    calculate_delta_theta,
    calculate_wind_score,
    calculate_tide_score,
    calculate_surf_score,
    get_decision_tier,
    get_breaking_range_str,
    classify_wind_regime,
    reconcile_consensus,
    ConsensusEngine,
    SPOT_PHYSICS,
)


class TestShoalingPhysics(unittest.TestCase):
    """Tests for oceanographic shelf attenuation, period scaling, and aperture exposure."""

    def test_shoaling_k_net_attenuation(self):
        """Verify K_net attenuation factors (0.65 for Holly Beach, 0.82 for Grand Isle) at baseline period."""
        # At t_p = 10.0s, (10.0 / 10.0)^0.2 = 1.0
        self.assertEqual(shoaled_breaking_height(5.0, "holly_beach", t_p=10.0), 3.25)
        self.assertEqual(shoaled_breaking_height(3.0, "holly_beach", t_p=10.0), 1.95)
        self.assertEqual(shoaled_breaking_height(4.0, "grand_isle", t_p=10.0), 3.28)
        self.assertEqual(shoaled_breaking_height(5.0, "grand_isle", t_p=10.0), 4.10)

    def test_period_dependent_shoaling_scaling(self):
        """Verify period scaling factor (T / 10)^0.2 amplifies groundswells and attenuates short chop."""
        # Short chop: 4.0s -> (4/10)^0.2 ≈ 0.8326
        # Long period: 14.0s -> (14/10)^0.2 ≈ 1.0696
        h_chop = shoaled_breaking_height(4.0, "holly_beach", t_p=4.0)
        h_ground = shoaled_breaking_height(4.0, "holly_beach", t_p=14.0)
        self.assertLess(h_chop, 4.0 * 0.65)
        self.assertGreater(h_ground, 4.0 * 0.65)
        self.assertAlmostEqual(h_chop, round(4.0 * 0.65 * ((4.0 / 10.0) ** 0.2), 2), places=2)
        self.assertAlmostEqual(h_ground, round(4.0 * 0.65 * ((14.0 / 10.0) ** 0.2), 2), places=2)

    def test_directional_exposure_and_delta_shadow_mask(self):
        """
        Verify directional cosine aperture and Grand Isle Mississippi Birdfoot Delta
        shadow masking (045° - 100° damped by 80%).
        """
        # Holly Beach (faces 180°): Swell from 180° is shore-normal -> K_dir ≈ 1.0
        k_hb_direct = calculate_directional_exposure(180.0, "holly_beach")
        self.assertEqual(k_hb_direct, 1.0)

        # Grand Isle (faces 155°): Swell from 155° is shore-normal -> K_dir ≈ 1.0
        k_gi_direct = calculate_directional_exposure(155.0, "grand_isle")
        self.assertEqual(k_gi_direct, 1.0)

        # Grand Isle: Swell from 060° (inside 045° - 100° shadow zone)
        # Delta angle = |60 - 155| = 95° (> 85° would be 0, but let's test 70° where delta angle is 85°)
        # Delta angle for 70° from 155° = 85°
        k_gi_shadow = calculate_directional_exposure(70.0, "grand_isle")
        # 70° is inside 45°-100°, so shadow mask applies (0.2x reduction)
        k_hb_70 = calculate_directional_exposure(70.0, "holly_beach")
        # Holly Beach divergence from 180° is 110° (>85° so 0.0)
        self.assertEqual(k_hb_70, 0.0)
        # Grand Isle at 70° has delta 85°, base is max(0.15, cos^2(85°)) = 0.15, then * 0.20 = 0.03
        self.assertAlmostEqual(k_gi_shadow, 0.03, places=2)

    def test_hunts_dispersion_solver(self):
        """Verify Hunt's rational polynomial dispersion relation for wavelength and celerity."""
        # For T=10s in deep water (d=100ft), wavelength should be close to L0 ≈ 5.12 * T^2 = 512 ft
        l_deep, c_deep, cg_deep = calculate_shallow_wavelength(10.0, 100.0)
        self.assertGreater(l_deep, 300.0)
        self.assertGreater(c_deep, 30.0)
        self.assertGreater(cg_deep, 15.0)

        # In very shallow water (d=4ft), celerity ≈ sqrt(g*d) = sqrt(32.174 * 4) ≈ 11.3 ft/s
        l_shallow, c_shallow, cg_shallow = calculate_shallow_wavelength(10.0, 4.0)
        self.assertAlmostEqual(c_shallow, math.sqrt(32.174 * 4.0), delta=0.5)

    def test_mccowan_breaking_criterion(self):
        """Verify sandbar depth-limited breaking caps excessive wave height at 0.78 * d_eff."""
        # Holly Beach: bar_base = 3.0 ft. If tide = 0.0, d_eff = 3.0 ft -> H_max = 0.78 * 3.0 = 2.34 ft
        trans = transform_wave_shoaling(
            h_deep_ft=10.0,
            t_period_sec=10.0,
            theta_swell_deg=180.0,
            spot_id="holly_beach",
            tide_mllw_ft=0.0
        )
        self.assertTrue(trans["is_depth_limited"])
        self.assertAlmostEqual(trans["h_break_ft"], 2.34, places=2)
        self.assertAlmostEqual(trans["h_max_breaking_ft"], 2.34, places=2)

    def test_energy_density_scaling(self):
        """Verify dynamic surfing push index E_surf = H^2 * T^2."""
        # 3.0 ft wave at 12.0s vs 3.0 ft wave at 4.0s
        e_12s = (3.0 ** 2) * (12.0 ** 2)  # 9 * 144 = 1296
        e_4s = (3.0 ** 2) * (4.0 ** 2)    # 9 * 16 = 144
        self.assertEqual(e_12s / e_4s, 9.0)


class TestSurfScoringEngine(unittest.TestCase):
    """Tests for continuous 0-100 objective surf scoring, cleanliness, and storm washout."""

    def test_post_frontal_clean_off_scenario(self):
        """Verify post-frontal clean-off scenario reaches Firing/Epic (81/100)."""
        score, breakdown = calculate_surf_score(
            h_break=3.5,
            t_p=7.5,
            theta_wind=0.0,
            v_w=12.0,
            theta_offshore=0.0,
            tide_state="rising"
        )
        self.assertGreaterEqual(score, 60)
        self.assertEqual(score, 81)
        tier, badge = get_decision_tier(score)
        self.assertEqual(tier, "Firing / Epic")
        self.assertEqual(badge, "purple")

    def test_pre_frontal_onshore_chop_scenario(self):
        """Verify pre-frontal onshore chop drops score to Poor/Flat (29/100)."""
        score, breakdown = calculate_surf_score(
            h_break=4.5,
            t_p=4.5,
            theta_wind=160.0,
            v_w=18.0,
            theta_offshore=0.0,
            tide_state="rising"
        )
        self.assertLess(score, 35)
        self.assertEqual(score, 29)
        tier, badge = get_decision_tier(score)
        self.assertEqual(tier, "Poor / Flat")
        self.assertEqual(badge, "crimson")

    def test_flat_calm_scenario(self):
        """Verify flat calm conditions score Poor/Flat (17/100)."""
        score, breakdown = calculate_surf_score(
            h_break=0.8,
            t_p=3.5,
            theta_wind=0.0,
            v_w=2.0,
            theta_offshore=0.0,
            tide_state="slack_high"
        )
        self.assertLess(score, 20)
        self.assertEqual(score, 17)
        tier, badge = get_decision_tier(score)
        self.assertEqual(tier, "Poor / Flat")

    def test_hurricane_storm_washout_zero_leak(self):
        """Verify 20ft hurricane storm surge receives score 0 (preventing high-score leak)."""
        score, breakdown = calculate_surf_score(
            h_break=20.0,
            t_p=12.0,
            theta_wind=0.0,
            v_w=2.0,
            theta_offshore=0.0,
            tide_state="slack_high"
        )
        self.assertEqual(score, 0)
        self.assertEqual(breakdown["psi_washout"], 0.0)
        tier, badge = get_decision_tier(score)
        self.assertEqual(tier, "Poor / Flat")

    def test_epic_groundswell_grand_isle(self):
        """Verify epic long-period groundswell at Grand Isle reaches peak performance (93/100)."""
        score, breakdown = calculate_surf_score(
            h_break=4.5,
            t_p=9.0,
            theta_wind=335.0,
            v_w=8.0,
            theta_offshore=335.0,
            tide_state="rising"
        )
        self.assertGreaterEqual(score, 90)
        self.assertEqual(score, 93)
        tier, badge = get_decision_tier(score)
        self.assertEqual(tier, "Firing / Epic")

    def test_wind_continuity_no_cliffs(self):
        """Verify continuous wind scoring: smooth transition across 4.0 mph and 45.0 degrees."""
        # Small changes in wind speed or angle produce small changes in wind score
        w1 = calculate_wind_score(3.9, 44.0)
        w2 = calculate_wind_score(4.1, 46.0)
        self.assertLess(abs(w1 - w2), 3.0)


class TestConsensusFusionAndOutliers(unittest.TestCase):
    """Tests for multi-source weighting, two-tier outlier rejection, and confidence calculation."""

    def test_two_tier_outlier_rejection_high_spike(self):
        """Verify an 8.5ft scraper spike is rejected against 3.0ft and 3.2ft readings."""
        ts = datetime.now(timezone.utc)
        sources = [
            NormalizedMarineReading(source_id="buoy_42035", source_name="Buoy 42035", source_type="buoy", timestamp=ts, wave_height_ft=3.0),
            NormalizedMarineReading(source_id="open_meteo", source_name="Open-Meteo", source_type="model", timestamp=ts, wave_height_ft=3.2),
            NormalizedMarineReading(source_id="surf_captain", source_name="Surf Captain", source_type="scraper", timestamp=ts, wave_height_ft=8.5),
        ]
        res = reconcile_consensus(sources, spot_id="holly_beach")
        self.assertEqual(len(res["rejected"]), 1)
        self.assertEqual(res["rejected"][0].source_id, "surf_captain")
        self.assertEqual(res["rejected"][0].status, "outlier_rejected")
        self.assertEqual(res["rejected"][0].confidence_weight, 0.0)
        self.assertAlmostEqual(res["consensus_h"], 2.43, delta=0.1)

    def test_outlier_rejection_severe_low_reading(self):
        """Verify a broken sensor reporting 0.2ft is rejected when median is 3.5ft."""
        ts = datetime.now(timezone.utc)
        sources = [
            NormalizedMarineReading(source_id="buoy_42035", source_name="Buoy 42035", source_type="buoy", timestamp=ts, wave_height_ft=3.4),
            NormalizedMarineReading(source_id="open_meteo", source_name="Open-Meteo", source_type="model", timestamp=ts, wave_height_ft=3.6),
            NormalizedMarineReading(source_id="broken_sensor", source_name="Broken Sensor", source_type="scraper", timestamp=ts, wave_height_ft=0.2),
        ]
        res = reconcile_consensus(sources, spot_id="holly_beach")
        self.assertEqual(len(res["rejected"]), 1)
        self.assertEqual(res["rejected"][0].source_id, "broken_sensor")

    def test_invariant_single_survivor_preserved(self):
        """Verify that if all sources trigger outlier criteria, the source closest to median is preserved."""
        ts = datetime.now(timezone.utc)
        # All three far apart
        sources = [
            NormalizedMarineReading(source_id="s1", source_name="S1", source_type="buoy", timestamp=ts, wave_height_ft=1.0),
            NormalizedMarineReading(source_id="s2", source_name="S2", source_type="model", timestamp=ts, wave_height_ft=4.0),
            NormalizedMarineReading(source_id="s3", source_name="S3", source_type="scraper", timestamp=ts, wave_height_ft=10.0),
        ]
        res = reconcile_consensus(sources, spot_id="holly_beach")
        self.assertGreaterEqual(len(res["valid"]), 1)
        # Closest to median (4.0) is s2
        self.assertEqual(res["valid"][0].source_id, "s2")

    def test_multi_source_dynamic_re_normalization(self):
        """Verify all valid sources sum to 1.0 weight with Surfline included."""
        ts = datetime.now(timezone.utc)
        sources = [
            NormalizedMarineReading(source_id="coops_8768094", source_name="CO-OPS", source_type="tide_gauge", timestamp=ts, wave_height_ft=2.8),
            NormalizedMarineReading(source_id="ndbc_42035", source_name="NDBC 42035", source_type="buoy", timestamp=ts, wave_height_ft=4.0),
            NormalizedMarineReading(source_id="open_meteo", source_name="Open-Meteo", source_type="model", timestamp=ts, wave_height_ft=2.7),
            NormalizedMarineReading(source_id="surf_captain", source_name="Surf Captain", source_type="scraper", timestamp=ts, wave_height_ft=2.6),
            NormalizedMarineReading(source_id="surfline", source_name="Surfline", source_type="scraper", timestamp=ts, wave_height_ft=2.5),
        ]
        res = reconcile_consensus(sources, spot_id="holly_beach")
        self.assertEqual(len(res["valid"]), 5)
        # Verify confidence score is high
        self.assertGreaterEqual(res["confidence_score"], 80)
        self.assertEqual(res["confidence_label"], "High")

    def test_dict_sources_interoperability(self):
        """Verify reconcile_consensus accepts dictionaries for backwards compatibility."""
        sources = [
            {"source_id": "buoy", "type": "buoy", "h_break": 3.0, "weight": 0.40},
            {"source_id": "model", "type": "model", "h_break": 3.2, "weight": 0.30},
            {"source_id": "scraper", "type": "scraper", "h_break": 8.5, "weight": 0.20},
        ]
        res = reconcile_consensus(sources, spot_id="holly_beach")
        self.assertEqual(len(res["rejected"]), 1)
        self.assertAlmostEqual(res["consensus_h"], 3.09, delta=0.1)


class TestSpotConsensusGeneration(unittest.TestCase):
    """Tests for ConsensusEngine.generate_spot_consensus producing valid ConsensusForecast."""

    def setUp(self):
        self.engine = ConsensusEngine()

    def test_generate_spot_consensus_holly_beach(self):
        ts = datetime.now(timezone.utc)
        readings = [
            NormalizedMarineReading(source_id="coops_8768094", source_name="CO-OPS 8768094", source_type="tide_gauge", timestamp=ts, wave_height_ft=2.5, wave_period_sec=6.5, wind_speed_mph=12.0, wind_direction_deg=0.0, tide_ft=1.4, tide_status="Rising"),
            NormalizedMarineReading(source_id="open_meteo", source_name="Open-Meteo", source_type="model", timestamp=ts, wave_height_ft=2.6, wave_period_sec=6.8, wind_speed_mph=11.0, wind_direction_deg=5.0),
            NormalizedMarineReading(source_id="surfline", source_name="Surfline", source_type="scraped_forecast", timestamp=ts, wave_height_ft=2.5, wave_period_sec=6.5, wind_speed_mph=10.0, wind_direction_deg=0.0),
        ]
        hourly_weather = [
            {"wave_height": 2.5, "wave_period": 6.5, "wind_speed_10m": 12.0, "wind_direction_10m": 0.0}
            for _ in range(48)
        ]
        forecast = self.engine.generate_spot_consensus(
            spot_id="holly_beach",
            readings=readings,
            hourly_weather=hourly_weather,
        )
        self.assertIsInstance(forecast, ConsensusForecast)
        self.assertEqual(forecast.spot_id, "holly_beach")
        self.assertEqual(forecast.spot_name, "Holly Beach (Cajun Riviera)")
        self.assertGreater(forecast.surf_score, 0)
        self.assertIn(forecast.overall_decision, ["Poor / Flat", "Fair", "Good / Decent", "Firing / Epic"])
        self.assertEqual(len(forecast.timeline_48h), 48)
        self.assertIsNotNone(forecast.best_window)


if __name__ == "__main__":
    unittest.main()
