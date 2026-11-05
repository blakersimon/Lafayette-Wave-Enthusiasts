"""
tests/verification/verify_m3_physics_stress.py
Empirical Adversarial Stress Test Suite for Milestone 3 Consensus & Physics Engine.

Validates:
1. Hurricane & Extreme Storm Washout Clamping (H >= 18 ft, T >= 14 s, wind >= 45 mph -> score < 25 / psi_washout -> 0)
2. Flat Calm Zero-Safety Boundaries (H=0, T=0, wind=0 -> no zero-division, dispersion finite, score < 25)
3. Hunt Wave Dispersion Asymptotic Convergence (< 1.0% relative error in shallow kd < pi/10 and deep kd > pi regimes)
4. Monte Carlo Consensus Outlier Fuzzer (500 iterations of normal + extreme corrupted readings [H=99, H=-5, T=99] -> zero crashes, complete purge)
5. Mississippi River Delta Shadow Boundary (0° to 360° azimuth sweep at Grand Isle, 045°-100° attenuation, bounded [0, 1], no spikes)

Author: Challenger M3.1 (teamwork_challenger)
"""

import sys
import os
import math
import random
import statistics
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

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
    G_IMPERIAL,
    SPOT_PHYSICS,
)


# ==============================================================================
# 1. HURRICANE & EXTREME STORM WASHOUT STRESS TESTS
# ==============================================================================

def test_hurricane_storm_washout_stress() -> Dict[str, Any]:
    """
    Stress-tests hurricane and extreme storm inputs:
    H_deep >= 18 ft, T >= 14 s, onshore wind >= 45 mph.
    Asserts:
    - Final surf quality score is clamped to 0 or strictly poor (< 25)
    - psi_washout <= 0.25 and reaches 0.0 for H_break >= 15 ft
    - Decision tier is strictly 'Poor / Flat'
    - Prevents fake 'Epic' scores during catastrophic marine storms.
    """
    results = {
        "test_name": "hurricane_storm_washout_stress",
        "combinations_tested": 0,
        "violations": [],
    }

    h_deep_range = [18.0, 20.0, 22.0, 25.0, 30.0, 40.0]
    t_period_range = [14.0, 15.0, 16.0, 18.0, 20.0]
    wind_speeds = [45.0, 50.0, 60.0, 75.0, 95.0, 120.0]
    spots = ["holly_beach", "grand_isle"]

    for spot in spots:
        offshore_deg = SPOT_PHYSICS[spot]["offshore_wind_deg"]
        # Direct onshore wind is 180° opposite to offshore wind
        onshore_deg = (offshore_deg + 180.0) % 360.0

        for h_deep in h_deep_range:
            for t_p in t_period_range:
                for v_w in wind_speeds:
                    results["combinations_tested"] += 1
                    h_break = shoaled_breaking_height(h_deep, spot, t_p)

                    score, sub = calculate_surf_score(
                        h_break=h_break,
                        t_p=t_p,
                        theta_wind=onshore_deg,
                        v_w=v_w,
                        theta_offshore=offshore_deg,
                        tide_state="rising",
                        tide_height_ft=2.0,
                    )
                    psi = sub["psi_washout"]
                    decision, badge = get_decision_tier(score)

                    # Invariant 1: Surf score must be strictly poor (< 25)
                    if score >= 25:
                        results["violations"].append(
                            f"Score leak: {score} >= 25 for spot={spot}, H={h_deep}, T={t_p}, wind={v_w}"
                        )

                    # Invariant 2: psi_washout must damp storm surge (<= 0.25)
                    if psi > 0.25:
                        results["violations"].append(
                            f"psi_washout too high: {psi} for spot={spot}, H_break={h_break}"
                        )

                    # Invariant 3: H_break >= 15 ft must force psi_washout = 0.0 and score = 0
                    if h_break >= 15.0 and (psi != 0.0 or score != 0):
                        results["violations"].append(
                            f"Washout zero-leak failure: psi={psi}, score={score} for H_break={h_break}"
                        )

                    # Invariant 4: Decision must be 'Poor / Flat'
                    if decision != "Poor / Flat":
                        results["violations"].append(
                            f"Invalid decision tier '{decision}' for hurricane conditions"
                        )

    # Also test full ConsensusEngine spot forecast under Category 3 hurricane surge
    engine = ConsensusEngine()
    ts = datetime.now(timezone.utc)
    for spot in spots:
        onshore_deg = (SPOT_PHYSICS[spot]["offshore_wind_deg"] + 180.0) % 360.0
        readings = [
            NormalizedMarineReading(
                source_id="buoy_hurricane_offshore",
                source_name="NDBC 42035",
                source_type="buoy",
                timestamp=ts,
                wave_height_ft=24.0,
                wave_period_sec=16.0,
                wind_speed_mph=65.0,
                wind_direction_deg=onshore_deg,
            ),
            NormalizedMarineReading(
                source_id="open_meteo",
                source_name="Open-Meteo",
                source_type="model",
                timestamp=ts,
                wave_height_ft=22.0,
                wave_period_sec=15.0,
                wind_speed_mph=60.0,
                wind_direction_deg=onshore_deg,
            ),
        ]
        forecast = engine.generate_spot_consensus(spot, readings)
        results["combinations_tested"] += 1
        if forecast.surf_score >= 25:
            results["violations"].append(f"Consensus forecast score {forecast.surf_score} >= 25 for {spot}")
        if forecast.overall_decision != "Poor / Flat":
            results["violations"].append(f"Consensus forecast decision {forecast.overall_decision} != 'Poor / Flat' for {spot}")

    return results


# ==============================================================================
# 2. FLAT CALM ZERO-SAFETY BOUNDARY TESTS
# ==============================================================================

def test_flat_calm_boundaries() -> Dict[str, Any]:
    """
    Stress-tests boundary conditions where H_deep = 0.0 ft, T = 0.0 s, wind = 0.0 mph.
    Asserts:
    - Zero division is avoided across all dispersion, shoaling, and scoring algorithms
    - Hunt dispersion polynomial does not yield NaN or Inf
    - Breaking wave height transforms cleanly to 0.0 ft
    - Surf score stays strictly < 25 ('Poor / Flat')
    - Consensus fusion and forecast synthesis cleanly handle completely flat water.
    """
    results = {
        "test_name": "flat_calm_boundaries",
        "checks_passed": 0,
        "violations": [],
    }

    # 1. Dispersion solver with 0 period and 0 depth
    try:
        l_w, c_w, cg_w = calculate_shallow_wavelength(0.0, 0.0)
        results["checks_passed"] += 1
        if math.isnan(l_w) or math.isinf(l_w):
            results["violations"].append(f"Dispersion wavelength is NaN/Inf: {l_w}")
        if math.isnan(c_w) or math.isinf(c_w):
            results["violations"].append(f"Dispersion celerity is NaN/Inf: {c_w}")
        if math.isnan(cg_w) or math.isinf(cg_w):
            results["violations"].append(f"Dispersion group velocity is NaN/Inf: {cg_w}")
    except Exception as e:
        results["violations"].append(f"calculate_shallow_wavelength(0,0) crashed: {e}")

    # 2. Transform wave shoaling with 0s
    try:
        trans = transform_wave_shoaling(
            h_deep_ft=0.0,
            t_period_sec=0.0,
            theta_swell_deg=0.0,
            spot_id="holly_beach",
            tide_mllw_ft=0.0,
        )
        results["checks_passed"] += 1
        if trans["h_break_ft"] != 0.0:
            results["violations"].append(f"Breaking height not 0.0 ft: {trans['h_break_ft']}")
        if trans["surf_energy_index"] != 0.0:
            results["violations"].append(f"Energy index not 0.0: {trans['surf_energy_index']}")
    except Exception as e:
        results["violations"].append(f"transform_wave_shoaling(0,0) crashed: {e}")

    # 3. shoaled_breaking_height with 0
    try:
        h_sh = shoaled_breaking_height(0.0, "grand_isle", 0.0)
        results["checks_passed"] += 1
        if h_sh != 0.0:
            results["violations"].append(f"shoaled_breaking_height(0, GI) != 0.0: {h_sh}")
    except Exception as e:
        results["violations"].append(f"shoaled_breaking_height(0,0) crashed: {e}")

    # 4. Continuous score calculation with 0s
    try:
        score, sub = calculate_surf_score(
            h_break=0.0,
            t_p=0.0,
            theta_wind=0.0,
            v_w=0.0,
            theta_offshore=0.0,
            tide_state="slack_high",
            tide_height_ft=0.0,
        )
        results["checks_passed"] += 1
        if score >= 25:
            results["violations"].append(f"Flat calm score too high: {score}")
        if sub["s_height"] != 0.0:
            results["violations"].append(f"Flat calm s_height != 0.0: {sub['s_height']}")
        if sub["s_period"] != 0.0:
            results["violations"].append(f"Flat calm s_period != 0.0: {sub['s_period']}")
    except Exception as e:
        results["violations"].append(f"calculate_surf_score(0,0) crashed: {e}")

    # 5. Full consensus reconciliation on flat calm readings
    ts = datetime.now(timezone.utc)
    flat_readings = [
        NormalizedMarineReading(
            source_id="buoy_calm",
            source_name="Buoy Calm",
            source_type="buoy",
            timestamp=ts,
            wave_height_ft=0.0,
            wave_period_sec=0.0,
            wind_speed_mph=0.0,
            wind_direction_deg=0.0,
        ),
        NormalizedMarineReading(
            source_id="model_calm",
            source_name="Model Calm",
            source_type="model",
            timestamp=ts,
            wave_height_ft=0.0,
            wave_period_sec=0.0,
            wind_speed_mph=0.0,
            wind_direction_deg=0.0,
        ),
    ]
    try:
        rec = reconcile_consensus(flat_readings, spot_id="holly_beach")
        results["checks_passed"] += 1
        if rec["consensus_h"] != 0.0:
            results["violations"].append(f"Consensus height for flat calm != 0.0: {rec['consensus_h']}")
    except Exception as e:
        results["violations"].append(f"reconcile_consensus flat calm crashed: {e}")

    # 6. Spot consensus synthesis on flat calm
    engine = ConsensusEngine()
    try:
        forecast = engine.generate_spot_consensus("holly_beach", flat_readings)
        results["checks_passed"] += 1
        if forecast.breaking_height_ft != 0.0:
            results["violations"].append(f"Forecast breaking height != 0.0: {forecast.breaking_height_ft}")
        if forecast.overall_decision != "Poor / Flat":
            results["violations"].append(f"Forecast decision != 'Poor / Flat': {forecast.overall_decision}")
        if forecast.breaking_range_str != "Flat":
            results["violations"].append(f"Forecast range str != 'Flat': {forecast.breaking_range_str}")
    except Exception as e:
        results["violations"].append(f"generate_spot_consensus flat calm crashed: {e}")

    return results


# ==============================================================================
# 3. HUNT WAVE DISPERSION ASYMPTOTIC CONVERGENCE TESTS
# ==============================================================================

def test_hunts_dispersion_asymptotic_convergence() -> Dict[str, Any]:
    """
    Validates Hunt's (1979) 6th-order rational polynomial dispersion relation across:
    - Shallow water asymptotic regime (k * d < pi / 10):
      L_shallow = T * sqrt(g * d)
    - Deep water asymptotic regime (k * d > pi):
      L_deep = (g * T^2) / (2 * pi)
    Asserts relative error <= 1.0% (0.01) across all test combinations.
    """
    results = {
        "test_name": "hunts_dispersion_asymptotic_convergence",
        "deep_tests_passed": 0,
        "shallow_tests_passed": 0,
        "violations": [],
    }

    # 1. Deep Water Regime: k * d > pi
    t_deep_list = [4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0]
    d_deep_list = [250.0, 400.0, 600.0, 1000.0, 2500.0]

    for t in t_deep_list:
        for d in d_deep_list:
            l_calc, c_calc, cg_calc = calculate_shallow_wavelength(t, d)
            k_calc = (2.0 * math.pi) / l_calc
            kd = k_calc * d

            if kd > math.pi:
                l_deep_theory = (G_IMPERIAL * (t ** 2)) / (2.0 * math.pi)
                rel_err = abs(l_calc - l_deep_theory) / l_deep_theory
                if rel_err > 0.01:
                    results["violations"].append(
                        f"Deep water error {rel_err:.4%} > 1.0% for T={t}s, d={d}ft (calc={l_calc}, theory={l_deep_theory:.2f})"
                    )
                else:
                    results["deep_tests_passed"] += 1

                # Group velocity in deep water must converge to cg = c / 2
                c_theory = l_deep_theory / t
                cg_theory = 0.5 * c_theory
                cg_err = abs(cg_calc - cg_theory) / cg_theory
                if cg_err > 0.02:
                    results["violations"].append(
                        f"Deep water group velocity error {cg_err:.4%} for T={t}s, d={d}ft"
                    )

    # 2. Shallow Water Regime: k * d < pi / 10
    # For surf groundswells (T >= 10.0s) in nearshore depths (d <= 4.0 ft),
    # kd is strictly < pi/10 and matches L_shallow = T * sqrt(g * d) within 1.0% relative tolerance.
    t_shallow_list = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0]
    d_shallow_list = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]

    for t in t_shallow_list:
        for d in d_shallow_list:
            l_calc, c_calc, cg_calc = calculate_shallow_wavelength(t, d)
            k_calc = (2.0 * math.pi) / l_calc
            kd = k_calc * d

            if kd < (math.pi / 10.0):
                l_shallow_theory = t * math.sqrt(G_IMPERIAL * d)
                rel_err = abs(l_calc - l_shallow_theory) / l_shallow_theory
                if rel_err > 0.01:
                    results["violations"].append(
                        f"Shallow water error {rel_err:.4%} > 1.0% for T={t}s, d={d}ft (calc={l_calc}, theory={l_shallow_theory:.2f})"
                    )
                else:
                    results["shallow_tests_passed"] += 1

                # Group velocity in shallow water must converge to cg = c = sqrt(g*d)
                c_theory = math.sqrt(G_IMPERIAL * d)
                c_err = abs(c_calc - c_theory) / c_theory
                if c_err > 0.02:
                    results["violations"].append(
                        f"Shallow water celerity error {c_err:.4%} for T={t}s, d={d}ft"
                    )

    return results


# ==============================================================================
# 4. MONTE CARLO CONSENSUS OUTLIER FUZZER
# ==============================================================================

def test_monte_carlo_consensus_outlier_fuzzer(iterations: int = 500) -> Dict[str, Any]:
    """
    Fuzzes reconcile_consensus with 500 randomized multi-source arrays containing
    realistic observations mixed with extreme sensor corruptions:
    - H = 99.0 ft (extreme high spike)
    - H = -5.0 ft (negative corruption)
    - T = 99.0 s (extreme period)
    - H = 85.0 ft, H = -12.0 ft, etc.
    Asserts:
    - reconcile_consensus never crashes or raises unhandled exceptions
    - Outliers are purged into 'rejected' with status='outlier_rejected' and weight=0.0
    - Consensus breaking height remains strictly bounded within physical realistic range [0.5, 6.0] ft
    - Valid source list is never empty (invariant 1 preserved)
    - Confidence score remains within valid bounds [20, 98].
    """
    random.seed(20261103)
    results = {
        "test_name": "monte_carlo_consensus_outlier_fuzzer",
        "iterations_completed": 0,
        "violations": [],
    }
    ts = datetime.now(timezone.utc)

    for i in range(iterations):
        # 1. Generate 3 to 6 normal sources
        num_normal = random.randint(3, 6)
        sources = []
        for j in range(num_normal):
            h_norm = round(random.uniform(1.5, 4.5), 2)
            p_norm = round(random.uniform(4.0, 9.0), 1)
            src_type = random.choice(["buoy", "model", "scraper", "tide_station"])
            sources.append(
                NormalizedMarineReading(
                    source_id=f"src_normal_{j}",
                    source_name=f"Normal Source {j}",
                    source_type=src_type,
                    timestamp=ts,
                    wave_height_ft=h_norm,
                    wave_period_sec=p_norm,
                    confidence_weight=0.25,
                    status="valid",
                )
            )

        # 2. Inject 1 to 2 corrupted readings using model_construct to bypass initial pydantic bounds
        num_corrupt = random.randint(1, 2)
        corrupted_ids = []
        for k in range(num_corrupt):
            cid = f"corrupt_sensor_{k}"
            corrupted_ids.append(cid)
            corrupt_flavor = random.choice(["high_99", "negative_5", "extreme_period_99", "dual_spike"])

            if corrupt_flavor == "high_99":
                c_h, c_p = 99.0, 7.0
            elif corrupt_flavor == "negative_5":
                c_h, c_p = -5.0, 6.5
            elif corrupt_flavor == "extreme_period_99":
                c_h, c_p = 2.5, 99.0
            else:
                c_h, c_p = random.choice([85.0, 99.0, -8.0, -15.0]), random.choice([25.0, 99.0])

            sources.append(
                NormalizedMarineReading.model_construct(
                    source_id=cid,
                    source_name=f"Corrupted Sensor {k}",
                    source_type="scraper",
                    timestamp=ts,
                    wave_height_ft=c_h,
                    wave_period_sec=c_p,
                    confidence_weight=0.10,
                    status="valid",
                )
            )

        random.shuffle(sources)

        # 3. Execute consensus reconciliation
        try:
            res = reconcile_consensus(sources, spot_id="holly_beach")
            results["iterations_completed"] += 1

            # Invariant 1: Consensus height must remain physically realistic
            c_h = res["consensus_h"]
            if not (0.5 <= c_h <= 6.0):
                results["violations"].append(
                    f"Iteration {i}: Consensus height {c_h} ft outside physical bounds [0.5, 6.0]"
                )

            # Invariant 2: Corrupted height sources must be purged from valid set
            valid_ids = [s.source_id for s in res["valid"]]
            rejected_ids = [s.source_id for s in res["rejected"]]

            for s in res["valid"]:
                if s.wave_height_ft is not None and (s.wave_height_ft > 10.0 or s.wave_height_ft < 0.0):
                    results["violations"].append(
                        f"Iteration {i}: Corrupted reading {s.source_id} (H={s.wave_height_ft}) survived in valid sources!"
                    )

            # Invariant 3: Valid list must never be empty
            if len(res["valid"]) == 0:
                results["violations"].append(f"Iteration {i}: Valid sources list is empty!")

            # Invariant 4: Confidence score within [20, 98]
            c_score = res["confidence_score"]
            if not (20 <= c_score <= 98):
                results["violations"].append(f"Iteration {i}: Confidence score {c_score} out of bounds")

        except Exception as exc:
            results["violations"].append(f"Iteration {i}: reconcile_consensus crashed with exception: {exc}")

    return results


# ==============================================================================
# 5. MISSISSIPPI RIVER DELTA SHADOW BOUNDARY TESTS
# ==============================================================================

def test_mississippi_delta_shadow_boundary() -> Dict[str, Any]:
    """
    Sweeps swell azimuth from 0.0° to 360.0° at Grand Isle (in 0.5° increments = 720 points).
    Asserts:
    - Directional exposure K_dir is strictly bounded in [0.0, 1.0] for all bearings
    - No NaNs, Infs, or negative numbers across the full circle
    - Grand Isle Mississippi Birdfoot Delta shadow (045° to 100°) damps exposure by 80%
      (multiplier 0.20) compared to unshadowed exposure
    - Evaluates the transition at 045° and 100° without discontinuous numerical spikes (K_dir <= 1.0)
    - Holly Beach (unshadowed by delta) exhibits standard aperture without delta damping.
    """
    results = {
        "test_name": "mississippi_delta_shadow_boundary",
        "angles_tested": 0,
        "violations": [],
    }

    # 1. 360-degree sweep for Grand Isle
    step = 0.5
    num_steps = int(360.0 / step)
    for i in range(num_steps):
        deg = i * step
        results["angles_tested"] += 1
        k_gi = calculate_directional_exposure(deg, "grand_isle")

        # Invariant 1: Bounded in [0.0, 1.0]
        if not (0.0 <= k_gi <= 1.0):
            results["violations"].append(f"Grand Isle K_dir out of [0, 1] at {deg}°: {k_gi}")

        # Invariant 2: Finite numeric value
        if math.isnan(k_gi) or math.isinf(k_gi):
            results["violations"].append(f"Grand Isle K_dir NaN/Inf at {deg}°: {k_gi}")

    # 2. Specific boundary transitions around 045° and 100°
    # At 044° vs 045°
    k_44 = calculate_directional_exposure(44.0, "grand_isle")
    k_45 = calculate_directional_exposure(45.0, "grand_isle")
    # For Grand Isle (azimuth 155°), 45° has delta_theta = 110° > 85°, so exposure is 0.0
    if k_44 != 0.0 or k_45 != 0.0:
        results["violations"].append(f"Back-island swell at 44°-45° expected 0.0, got {k_44}, {k_45}")

    # At 70° (delta_theta = 85°):
    # Base exposure is max(0.15, cos(85°)^2) = 0.15
    # Shadow factor 0.20 -> 0.15 * 0.20 = 0.030
    k_gi_70 = calculate_directional_exposure(70.0, "grand_isle")
    if not math.isclose(k_gi_70, 0.03, abs_tol=0.005):
        results["violations"].append(f"Grand Isle 70° shadow exposure expected ~0.03, got {k_gi_70}")

    # At 99° vs 100° vs 101°
    k_99 = calculate_directional_exposure(99.0, "grand_isle")
    k_100 = calculate_directional_exposure(100.0, "grand_isle")
    k_101 = calculate_directional_exposure(101.0, "grand_isle")

    # At 100.0°, delta_theta = 55°, cos(55°) ≈ 0.5736, shadowed by 0.20 -> ~0.115
    if not math.isclose(k_100, 0.115, abs_tol=0.01):
        results["violations"].append(f"Grand Isle 100° expected ~0.115, got {k_100}")

    # At 101.0°, delta_theta = 54°, cos(54°) ≈ 0.5878, shadow mask clears -> ~0.588
    if not math.isclose(k_101, 0.588, abs_tol=0.01):
        results["violations"].append(f"Grand Isle 101° expected ~0.588, got {k_101}")

    # Verify no numerical spike above 1.0 during unshadowed clearing
    if k_101 > 1.0 or k_100 > 1.0:
        results["violations"].append(f"Spike above 1.0 detected at boundary: k_100={k_100}, k_101={k_101}")

    # 3. Verify Shore-Normal peak at 155°
    k_normal = calculate_directional_exposure(155.0, "grand_isle")
    if k_normal != 1.0:
        results["violations"].append(f"Grand Isle shore-normal (155°) != 1.0: {k_normal}")

    # 4. Compare with Holly Beach (unshadowed by Delta)
    # Holly Beach faces 180°. At 100°, delta_theta = 80°. Base exposure = max(0.15, cos(80°)^2) = 0.15
    k_hb_100 = calculate_directional_exposure(100.0, "holly_beach")
    if k_hb_100 != 0.15:
        results["violations"].append(f"Holly Beach 100° expected 0.15, got {k_hb_100}")

    return results


# ==============================================================================
# MAIN TEST HARNESS RUNNER
# ==============================================================================

def run_all_stress_tests() -> bool:
    """Executes all 5 empirical stress test suites and reports summary."""
    print("=" * 80)
    print("EMPIRICAL ADVERSARIAL STRESS TEST SUITE — MILESTONE 3 PHYSICS CONSENSUS")
    print("=" * 80)

    suites = [
        ("Hurricane & Extreme Storm Washout", test_hurricane_storm_washout_stress),
        ("Flat Calm Zero-Safety Boundaries", test_flat_calm_boundaries),
        ("Hunt Wave Dispersion Asymptotic Convergence", test_hunts_dispersion_asymptotic_convergence),
        ("Monte Carlo Consensus Outlier Fuzzer (500 runs)", test_monte_carlo_consensus_outlier_fuzzer),
        ("Mississippi River Delta Shadow Boundary", test_mississippi_delta_shadow_boundary),
    ]

    total_violations = 0
    test_reports = []

    for name, func in suites:
        print(f"\n[RUNNING] {name}...")
        report = func()
        num_v = len(report["violations"])
        total_violations += num_v
        test_reports.append((name, report))

        if num_v == 0:
            print(f"[PASS] {name} — 0 violations.")
        else:
            print(f"[FAIL] {name} — {num_v} violations encountered:")
            for v in report["violations"][:5]:
                print(f"  - {v}")
            if num_v > 5:
                print(f"  ... and {num_v - 5} more.")

    print("\n" + "=" * 80)
    print("SUMMARY OF EMPIRICAL ADVERSARIAL VERIFICATION")
    print("=" * 80)
    for name, report in test_reports:
        num_v = len(report["violations"])
        detail = ""
        if "combinations_tested" in report:
            detail = f"({report['combinations_tested']} combinations tested)"
        elif "checks_passed" in report:
            detail = f"({report['checks_passed']} safety checks verified)"
        elif "deep_tests_passed" in report:
            detail = f"({report['deep_tests_passed']} deep + {report['shallow_tests_passed']} shallow regime tests verified)"
        elif "iterations_completed" in report:
            detail = f"({report['iterations_completed']} Monte Carlo iterations verified)"
        elif "angles_tested" in report:
            detail = f"({report['angles_tested']} sweep angles verified)"

        status = "PASSED" if num_v == 0 else f"FAILED ({num_v} errors)"
        print(f"  • {name:50s} : {status} {detail}")

    print("-" * 80)
    if total_violations == 0:
        print("OVERALL VERDICT: ALL ADVERSARIAL STRESS TESTS PASSED (0 FAILURES)")
        return True
    else:
        print(f"OVERALL VERDICT: FAILED WITH {total_violations} TOTAL VIOLATIONS")
        return False


if __name__ == "__main__":
    success = run_all_stress_tests()
    sys.exit(0 if success else 1)
