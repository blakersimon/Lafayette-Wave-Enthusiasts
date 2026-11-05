"""
tests/verification/verify_consensus_engine.py
Empirical Verification & Stress Test Harness for Louisiana Surf Finder
Algorithms, Scoring Formulas, Shoaling Models, and Consensus Logic
(docs/ARCHITECTURE_PLAN.md Sections 7.1 - 7.4)

Author: Challenger M1.2 (Algorithmic & Consensus Formula Challenger)
"""

import math
import statistics
import random
from typing import List, Dict, Any, Optional, Tuple


# ==============================================================================
# SECTION 1: REFERENCE ALGORITHMIC IMPLEMENTATIONS FROM ARCHITECTURE PLAN
# ==============================================================================

def calculate_base_score(h_break: float) -> float:
    """
    Section 7.2.1: Breaking Wave Height Base Score BaseScore(H).
    """
    if h_break < 0.0:
        h_break = 0.0
    if h_break < 1.0:
        return h_break * 15.0
    elif h_break < 2.0:
        return 15.0 + (h_break - 1.0) * 25.0
    elif h_break < 3.5:
        return 40.0 + (h_break - 2.0) * 20.0
    elif h_break < 5.5:
        return 70.0 + (h_break - 3.5) * 10.0
    elif h_break <= 8.0:
        return 90.0 - (h_break - 5.5) * 5.0
    else:
        # H > 8.0 ft
        return max(40.0, 77.5 - (h_break - 8.0) * 6.0)


def calculate_period_multiplier(t_p: float) -> float:
    """
    Section 7.2.2: Swell Period Multiplier M_period(T_p).
    """
    if t_p < 4.0:
        return 0.55
    elif t_p < 5.0:
        return 0.75
    elif t_p < 6.2:
        return 0.95
    elif t_p < 7.8:
        return 1.15
    elif t_p < 9.5:
        return 1.30
    else:
        return 1.40


def calculate_delta_theta(theta_wind: float, theta_offshore: float) -> float:
    """
    Section 7.2.3: Angular divergence from ideal offshore axis.
    Delta theta = |(theta_wind - theta_offshore + 180) % 360 - 180|
    """
    return abs((theta_wind - theta_offshore + 180.0) % 360.0 - 180.0)


def calculate_wind_factor(v_w: float, delta_theta: float) -> float:
    """
    Section 7.2.3: Wind Alignment & Velocity Factor W_wind(V_w, Delta theta).
    """
    if v_w < 4.0:
        return 1.15
    elif delta_theta <= 45.0:
        if v_w <= 15.0:
            return 1.25
        elif v_w <= 22.0:
            return 1.05
        else:
            return 0.85
    elif delta_theta <= 90.0:
        return max(0.45, 1.0 - (v_w / 35.0))
    else:
        # delta_theta > 90.0
        return max(0.18, 0.72 - (v_w / 22.0))


def calculate_tide_modifier(tide_state: str) -> float:
    """
    Section 7.2.4: Tide State Modifier T_tide.
    """
    state_map = {
        "rising": 1.05,
        "rising_mid_high": 1.05,
        "slack_high": 1.00,
        "high": 1.00,
        "falling": 0.95,
        "falling_mid": 0.95,
        "slack_low": 0.90,
        "extreme_low": 0.90,
        "low": 0.90
    }
    normalized = tide_state.lower().strip().replace(" ", "_").replace("-", "_")
    return state_map.get(normalized, 1.00)


def calculate_surf_score(
    h_break: float,
    t_p: float,
    theta_wind: float,
    v_w: float,
    theta_offshore: float,
    tide_state: str = "slack_high"
) -> int:
    """
    Section 7.2: Objective 0-100 Surf Quality Score Q.
    """
    base = calculate_base_score(h_break)
    m_p = calculate_period_multiplier(t_p)
    delta_th = calculate_delta_theta(theta_wind, theta_offshore)
    w_factor = calculate_wind_factor(v_w, delta_th)
    t_factor = calculate_tide_modifier(tide_state)

    raw_q = base * m_p * w_factor * t_factor
    clamped_q = max(0, min(100, round(raw_q)))
    return clamped_q


def get_decision_tier(score: int) -> Tuple[str, str]:
    """
    Section 7.2.5: Decision Tiers & Badge Tokens.
    """
    if score >= 80:
        return "Firing / Epic", "purple"
    elif score >= 60:
        return "Good / Decent", "emerald"
    elif score >= 35:
        return "Fair / Marginal", "amber"
    else:
        return "Poor / Flat", "crimson"


# ------------------------------------------------------------------------------
# SECTION 7.1: SHOALING TRANSLATION
# ------------------------------------------------------------------------------

def shoaled_breaking_height(h_buoy: float, spot_id: str) -> float:
    """
    Section 7.1: Coastal Shoaling Attenuation.
    Holly Beach K_net = 0.65; Grand Isle K_net = 0.82.
    """
    k_net = 0.65 if "holly" in spot_id.lower() else 0.82
    return round(h_buoy * k_net, 2)


# ------------------------------------------------------------------------------
# SECTION 7.3: CONSENSUS & ANOMALY REJECTION
# ------------------------------------------------------------------------------

def reconcile_consensus(
    sources: List[Dict[str, Any]],
    time_step_hours: int = 0
) -> Dict[str, Any]:
    """
    Section 7.3: Multi-Source Consensus Reconciliation & Anomaly Rejection.
    sources is a list of dicts with:
      {'source_id': str, 'type': str, 'h_break': float, 'weight': float}
    """
    if not sources:
        return {"consensus_h": 0.0, "confidence_score": 30, "rejected": [], "valid": []}

    # Pre-filter sources with missing or None wave heights
    candidate_sources = [s for s in sources if s.get("h_break") is not None]
    if not candidate_sources:
        return {"consensus_h": 0.0, "confidence_score": 30, "rejected": [], "valid": []}

    h_values = [s["h_break"] for s in candidate_sources]
    
    # Stage 1: Median Outlier Filtering (if N >= 3)
    rejected = []
    valid = []
    
    if len(h_values) >= 3:
        med = statistics.median(h_values)
        for s in candidate_sources:
            h = s["h_break"]
            if abs(h - med) > 2.5:
                rejected.append(s)
            else:
                valid.append(s)
    else:
        valid = list(candidate_sources)

    if not valid:
        valid = list(candidate_sources)
        rejected = []

    # Dynamic temporal weights (Section 7.3.2)
    # At t=0: buoy: 0.40, open_meteo: 0.30, scraper: 0.20, nws: 0.10
    # 1 <= t <= 24: open_meteo: 0.45, scraper: 0.35, nws: 0.20
    # 25 <= t <= 72: open_meteo: 0.50, scraper: 0.30, nws: 0.20
    base_weight_map = {}
    if time_step_hours == 0:
        base_weight_map = {"buoy": 0.40, "model": 0.30, "scraper": 0.20, "nws": 0.10}
    elif time_step_hours <= 24:
        base_weight_map = {"buoy": 0.00, "model": 0.45, "scraper": 0.35, "nws": 0.20}
    else:
        base_weight_map = {"buoy": 0.00, "model": 0.50, "scraper": 0.30, "nws": 0.20}

    # Assign weights and renormalize
    total_assigned_weight = 0.0
    for s in valid:
        stype = s.get("type", "model")
        assigned = s.get("weight", base_weight_map.get(stype, 0.25))
        s["_assigned_weight"] = assigned
        total_assigned_weight += assigned

    if total_assigned_weight > 0:
        for s in valid:
            s["_norm_weight"] = s["_assigned_weight"] / total_assigned_weight
    else:
        for s in valid:
            s["_norm_weight"] = 1.0 / len(valid)

    # Weighted consensus height
    consensus_h = sum(s["h_break"] * s["_norm_weight"] for s in valid)

    # Stage 3: Variance-Derived Confidence Scoring
    # C = clamp(round(100 - (sigma_wave * 35)), 30, 98)
    valid_heights = [s["h_break"] for s in valid]
    if len(valid_heights) >= 2:
        sigma_wave = statistics.stdev(valid_heights)
    elif len(valid_heights) == 1:
        sigma_wave = 0.0
    else:
        sigma_wave = 2.0

    raw_conf = round(100.0 - (sigma_wave * 35.0))
    conf_score = max(30, min(98, raw_conf))

    if conf_score >= 85:
        conf_label = "High"
    elif conf_score >= 65:
        conf_label = "Moderate"
    else:
        conf_label = "Low (Divergent)"

    return {
        "consensus_h": round(consensus_h, 2),
        "sigma_wave": round(sigma_wave, 3),
        "confidence_score": conf_score,
        "confidence_label": conf_label,
        "valid": valid,
        "rejected": rejected
    }


# ------------------------------------------------------------------------------
# SECTION 7.4: GOLDEN WINDOW RECOMMENDATION
# ------------------------------------------------------------------------------

def detect_golden_windows(
    timeline_48h: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Section 7.4: 48-Hour Golden Window Recommendation Algorithm.
    timeline_48h contains 48 hourly dicts with keys:
    {'hour': int, 'score': int, 'h_break': float, 'v_w': float, 'wind_dir': float}
    """
    # Find all contiguous windows where score >= 60
    good_windows = []
    current_window = []

    for point in timeline_48h:
        if point["score"] >= 60:
            current_window.append(point)
        else:
            if len(current_window) >= 2:
                good_windows.append(current_window)
            current_window = []

    if len(current_window) >= 2:
        good_windows.append(current_window)

    if good_windows:
        # Pick the best window (highest peak score, tie break: longest window)
        best_window = max(good_windows, key=lambda w: (max(p["score"] for p in w), len(w)))
        peak_point = max(best_window, key=lambda p: p["score"])
        return {
            "has_good_window": True,
            "window_count": len(good_windows),
            "start_hour": best_window[0]["hour"],
            "end_hour": best_window[-1]["hour"],
            "duration_hours": len(best_window),
            "peak_hour": peak_point["hour"],
            "peak_score": peak_point["score"],
            "peak_h_break": peak_point["h_break"],
            "is_clean_off": any(p["v_w"] <= 15.0 and p["h_break"] >= 2.5 for p in best_window),
            "human_verdict": f"Golden Window detected from T+{best_window[0]['hour']}h to T+{best_window[-1]['hour']}h with peak score {peak_point['score']}/100."
        }
    else:
        # Compute best 3-hour sliding window
        best_avg = -1.0
        best_3h = []
        for i in range(len(timeline_48h) - 2):
            window_slice = timeline_48h[i:i+3]
            avg = sum(p["score"] for p in window_slice) / 3.0
            if avg > best_avg:
                best_avg = avg
                best_3h = window_slice

        return {
            "has_good_window": False,
            "window_count": 0,
            "best_3h_start": best_3h[0]["hour"] if best_3h else 0,
            "best_3h_avg_score": round(best_avg, 1) if best_3h else 0.0,
            "human_verdict": f"No high-quality golden window. Best 3h window averages {round(best_avg, 1)}/100 (Longboard / foil recommended)."
        }


# ==============================================================================
# SECTION 2: VERIFICATION & STRESS TEST SUITE
# ==============================================================================

class EmpiricalVerifier:
    def __init__(self):
        self.passes = 0
        self.failures = 0
        self.vulnerabilities = []

    def check(self, condition: bool, test_name: str, detail: str = ""):
        if condition:
            self.passes += 1
            print(f"  [PASS] {test_name}: {detail}")
        else:
            self.failures += 1
            print(f"  [FAIL] {test_name}: {detail}")

    def record_vulnerability(self, title: str, description: str, severity: str = "MEDIUM"):
        self.vulnerabilities.append({
            "title": title,
            "description": description,
            "severity": severity
        })
        print(f"  [!VULNERABILITY ({severity})] {title}: {description}")


def run_all_tests():
    verifier = EmpiricalVerifier()
    print("=" * 80)
    print("RUNNING EMPIRICAL VERIFICATION HARNESS FOR LOUISIANA SURF FINDER")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # TASK 1: SURF QUALITY SCORING EQUATION TESTS
    # --------------------------------------------------------------------------
    print("\n--- TEST GROUP 1: Surf Quality Scoring (Mandatory Dispatch Scenarios) ---")

    # 1.1 Post-frontal clean-off scenario (Holly Beach)
    # 3.5 ft breaking, 7.5s period, offshore N wind 12 mph (theta_wind=0, theta_offshore=0)
    score_cleanoff = calculate_surf_score(
        h_break=3.5,
        t_p=7.5,
        theta_wind=0.0,
        v_w=12.0,
        theta_offshore=0.0,
        tide_state="rising"
    )
    decision_cleanoff, badge_cleanoff = get_decision_tier(score_cleanoff)
    verifier.check(
        score_cleanoff >= 60,
        "1.1 Post-Frontal Clean-Off Score",
        f"Score = {score_cleanoff}/100 (Decision: {decision_cleanoff}, Badge: {badge_cleanoff}) >= 60"
    )

    # 1.2 Pre-frontal onshore chop scenario
    # 4.5 ft breaking, 4.5s period, onshore SSE wind 18 mph (theta_wind=160, theta_offshore=0)
    score_onshore = calculate_surf_score(
        h_break=4.5,
        t_p=4.5,
        theta_wind=160.0,
        v_w=18.0,
        theta_offshore=0.0,
        tide_state="rising"
    )
    decision_onshore, badge_onshore = get_decision_tier(score_onshore)
    verifier.check(
        score_onshore < 35,
        "1.2 Pre-Frontal Onshore Chop Score",
        f"Score = {score_onshore}/100 (Decision: {decision_onshore}, Badge: {badge_onshore}) < 35 (Poor)"
    )

    # 1.3 Flat calm scenario
    # 0.8 ft breaking, 3.5s period, light wind 2 mph
    score_flat = calculate_surf_score(
        h_break=0.8,
        t_p=3.5,
        theta_wind=0.0,
        v_w=2.0,
        theta_offshore=0.0,
        tide_state="slack_high"
    )
    decision_flat, badge_flat = get_decision_tier(score_flat)
    verifier.check(
        score_flat < 20,
        "1.3 Flat Calm Score",
        f"Score = {score_flat}/100 (Decision: {decision_flat}, Badge: {badge_flat}) < 20 (Poor)"
    )

    # --------------------------------------------------------------------------
    # TASK 2: SHOALING TRANSLATION TESTS
    # --------------------------------------------------------------------------
    print("\n--- TEST GROUP 2: Shoaling Translation (K_net) ---")

    # 2.1 Holly Beach K_net = 0.65
    hb_5ft = shoaled_breaking_height(5.0, "holly_beach")
    verifier.check(
        hb_5ft == 3.25,
        "2.1 Holly Beach 5ft Buoy Shoaling",
        f"5.0 ft buoy * 0.65 = {hb_5ft} ft (Expected: 3.25 ft)"
    )
    hb_3ft = shoaled_breaking_height(3.0, "holly_beach")
    verifier.check(
        hb_3ft == 1.95,
        "2.2 Holly Beach 3ft Buoy Shoaling",
        f"3.0 ft buoy * 0.65 = {hb_3ft} ft (Expected: 1.95 ft)"
    )

    # 2.2 Grand Isle K_net = 0.82
    gi_4ft = shoaled_breaking_height(4.0, "grand_isle")
    verifier.check(
        gi_4ft == 3.28,
        "2.3 Grand Isle 4ft Buoy Shoaling",
        f"4.0 ft buoy * 0.82 = {gi_4ft} ft (Expected: 3.28 ft)"
    )
    gi_5ft = shoaled_breaking_height(5.0, "grand_isle")
    verifier.check(
        gi_5ft == 4.10,
        "2.4 Grand Isle 5ft Buoy Shoaling",
        f"5.0 ft buoy * 0.82 = {gi_5ft} ft (Expected: 4.10 ft)"
    )

    # 2.3 Verify constituent attenuation factors K_s * K_r * K_f
    hb_calc_k = 1.15 * 0.91 * 0.62
    verifier.check(
        math.isclose(hb_calc_k, 0.65, abs_tol=0.01),
        "2.5 Holly Beach Constituent Shoaling Derivation",
        f"1.15 * 0.91 * 0.62 = {hb_calc_k:.4f} ~ 0.65"
    )
    gi_calc_k = 1.15 * 0.91 * 0.78
    verifier.check(
        math.isclose(gi_calc_k, 0.82, abs_tol=0.01),
        "2.6 Grand Isle Constituent Shoaling Derivation",
        f"1.15 * 0.91 * 0.78 = {gi_calc_k:.4f} ~ 0.82"
    )

    # --------------------------------------------------------------------------
    # TASK 3: CONSENSUS & OUTLIER REJECTION TESTS
    # --------------------------------------------------------------------------
    print("\n--- TEST GROUP 3: Consensus & Outlier Rejection ---")

    # 3.1 Mandatory Outlier Rejection Scenario:
    # One scraped source reports 8.5 ft, buoy reports 3.0 ft, weather API reports 3.2 ft
    test_sources = [
        {"source_id": "ndbc_42035", "type": "buoy", "h_break": 3.0, "weight": 0.40},
        {"source_id": "open_meteo", "type": "model", "h_break": 3.2, "weight": 0.30},
        {"source_id": "surf_captain", "type": "scraper", "h_break": 8.5, "weight": 0.20}
    ]
    res_outlier = reconcile_consensus(test_sources, time_step_hours=0)
    rejected_ids = [s["source_id"] for s in res_outlier["rejected"]]
    verifier.check(
        "surf_captain" in rejected_ids and len(rejected_ids) == 1,
        "3.1 8.5ft Outlier Rejected",
        f"Rejected: {rejected_ids}, Valid: {[s['source_id'] for s in res_outlier['valid']]}"
    )
    verifier.check(
        math.isclose(res_outlier["consensus_h"], 3.09, abs_tol=0.05),
        "3.2 Consensus Height After Rejection",
        f"Consensus H = {res_outlier['consensus_h']} ft (Expected ~3.09 ft)"
    )

    # 3.3 Confidence Score Calculation:
    # Tight cluster (low variance): [3.0, 3.1, 3.2] -> High Confidence
    tight_sources = [
        {"source_id": "ndbc_42035", "type": "buoy", "h_break": 3.0, "weight": 0.40},
        {"source_id": "open_meteo", "type": "model", "h_break": 3.1, "weight": 0.30},
        {"source_id": "surf_captain", "type": "scraper", "h_break": 3.2, "weight": 0.20}
    ]
    res_tight = reconcile_consensus(tight_sources, time_step_hours=0)
    verifier.check(
        res_tight["confidence_score"] >= 85 and res_tight["confidence_label"] == "High",
        "3.3 Low Variance -> High Confidence",
        f"Score = {res_tight['confidence_score']}%, Label = {res_tight['confidence_label']}, Stdev = {res_tight['sigma_wave']} ft"
    )

    # Divergent cluster (high variance): [1.5, 3.5, 5.0] -> Low Confidence (< 65%)
    # Note: median = 3.5. |1.5-3.5|=2.0 <= 2.5; |5.0-3.5|=1.5 <= 2.5. None rejected, but wide spread!
    div_sources = [
        {"source_id": "ndbc_42035", "type": "buoy", "h_break": 1.5, "weight": 0.40},
        {"source_id": "open_meteo", "type": "model", "h_break": 3.5, "weight": 0.30},
        {"source_id": "surf_captain", "type": "scraper", "h_break": 5.0, "weight": 0.20}
    ]
    res_div = reconcile_consensus(div_sources, time_step_hours=0)
    verifier.check(
        res_div["confidence_score"] < 65 and res_div["confidence_label"] == "Low (Divergent)",
        "3.4 High Variance -> Low Confidence",
        f"Score = {res_div['confidence_score']}%, Label = {res_div['confidence_label']}, Stdev = {res_div['sigma_wave']} ft"
    )

    # --------------------------------------------------------------------------
    # TASK 4: GOLDEN WINDOW DETECTOR TESTS
    # --------------------------------------------------------------------------
    print("\n--- TEST GROUP 4: Golden Window Detector ---")

    # 4.1 Synthetic 48-hour timeline with a 4-hour golden window at T+12 to T+15
    timeline_48 = []
    for h in range(48):
        if 12 <= h <= 15:
            # Golden window conditions
            timeline_48.append({
                "hour": h,
                "score": 65 + (h - 12) * 5,  # 65, 70, 75, 80
                "h_break": 3.5,
                "v_w": 10.0,
                "wind_dir": 0.0
            })
        else:
            # Marginal / poor conditions
            timeline_48.append({
                "hour": h,
                "score": 25,
                "h_break": 1.5,
                "v_w": 15.0,
                "wind_dir": 180.0
            })

    gw_res = detect_golden_windows(timeline_48)
    verifier.check(
        gw_res["has_good_window"] is True and gw_res["duration_hours"] == 4,
        "4.1 4-Hour Golden Window Detected",
        f"Detected window T+{gw_res['start_hour']}h to T+{gw_res['end_hour']}h (Duration: {gw_res['duration_hours']}h)"
    )
    verifier.check(
        gw_res["peak_score"] == 80 and gw_res["peak_hour"] == 15,
        "4.2 Golden Window Peak Detection",
        f"Peak score {gw_res['peak_score']} at T+{gw_res['peak_hour']}h"
    )

    # 4.2 Single-hour spike (boundary check: < 2 hours should NOT trigger golden window)
    timeline_spike = []
    for h in range(48):
        if h == 10:
            timeline_spike.append({"hour": h, "score": 75, "h_break": 3.5, "v_w": 10.0, "wind_dir": 0.0})
        else:
            timeline_spike.append({"hour": h, "score": 30, "h_break": 1.5, "v_w": 15.0, "wind_dir": 180.0})

    gw_spike = detect_golden_windows(timeline_spike)
    verifier.check(
        gw_spike["has_good_window"] is False,
        "4.3 Isolated 1-Hour Spike Rejected (< 2h minimum)",
        f"has_good_window = {gw_spike['has_good_window']}, Verdict: {gw_spike['human_verdict']}"
    )

    # 4.3 Flat calm timeline -> Best 3h fallback
    timeline_flat = [{"hour": h, "score": 15, "h_break": 0.8, "v_w": 5.0, "wind_dir": 90.0} for h in range(48)]
    gw_flat = detect_golden_windows(timeline_flat)
    verifier.check(
        gw_flat["has_good_window"] is False and "Longboard / foil recommended" in gw_flat["human_verdict"],
        "4.4 Flat Calm 48h Fallback Assessment",
        f"Verdict: {gw_flat['human_verdict']}"
    )

    # --------------------------------------------------------------------------
    # ADVERSARIAL STRESS TESTING & BOUNDARY VULNERABILITY MINING
    # --------------------------------------------------------------------------
    print("\n--- TEST GROUP 5: Adversarial Stress Testing & Vulnerability Mining ---")

    # 5.1 BaseScore Continuity at Piecewise Boundaries
    # Boundaries: 1.0, 2.0, 3.5, 5.5, 8.0
    epsilon = 1e-5
    boundaries = [1.0, 2.0, 3.5, 5.5, 8.0]
    for b in boundaries:
        left = calculate_base_score(b - epsilon)
        right = calculate_base_score(b + epsilon)
        diff = abs(left - right)
        verifier.check(
            diff < 0.01,
            f"5.1.{boundaries.index(b)+1} BaseScore Continuity at H={b}",
            f"Left = {left:.4f}, Right = {right:.4f}, Diff = {diff:.6f}"
        )

    # 5.2 Adversarial Check: Catastrophic Hurricane Storm Surge (H = 20 ft)
    # Physical reality: 20ft hurricane waves in Louisiana are life-threatening washouts.
    # What does the formula score for H=20ft, Tp=12s (Tp multiplier 1.40), calm eye wind (W=1.15)?
    score_hurricane = calculate_surf_score(
        h_break=20.0,
        t_p=12.0,
        theta_wind=0.0,
        v_w=2.0,
        theta_offshore=0.0,
        tide_state="high"
    )
    decision_hurricane, _ = get_decision_tier(score_hurricane)
    base_20 = calculate_base_score(20.0)
    print(f"  [DEBUG] H=20ft Hurricane: BaseScore={base_20}, TotalScore={score_hurricane}, Decision={decision_hurricane}")
    if score_hurricane >= 60:
        verifier.record_vulnerability(
            "Hurricane Storm Washout High-Score Leak",
            f"At H=20.0 ft (extreme hurricane surge), BaseScore floors at 40.0. With groundswell period (Tp=12s) and light eye winds, TotalScore = {score_hurricane}/100 ('{decision_hurricane}'). A hazardous storm surge is misclassified as Good/Decent surf!",
            "HIGH"
        )
    else:
        verifier.check(True, "5.2 Extreme Hurricane Wave Damped", f"Score = {score_hurricane}")

    # 5.3 Adversarial Check: Discontinuity in Wind Factor at Boundaries
    # Boundary 1: v_w = 4.0 mph vs 3.99 mph when delta_theta = 180 (onshore)
    w_sub4 = calculate_wind_factor(3.99, 180.0)  # Glassy: 1.15
    w_at4 = calculate_wind_factor(4.01, 180.0)   # Onshore: max(0.18, 0.72 - 4.01/22) = 0.538
    print(f"  [DEBUG] Wind discontinuity at 4mph onshore: 3.99mph -> {w_sub4}, 4.01mph -> {w_at4:.3f}")
    if abs(w_sub4 - w_at4) > 0.50:
        verifier.record_vulnerability(
            "Wind Factor Step Discontinuity at 4.0 mph",
            f"Onshore wind transitioning from 3.99 mph to 4.01 mph causes W_wind to drop instantly from {w_sub4:.2f} to {w_at4:.2f} (a {abs(w_sub4 - w_at4):.2f} drop or 53% decrease), creating sudden forecast score cliffs.",
            "MEDIUM"
        )

    # Boundary 2: delta_theta = 45 vs 46 at v_w = 12 mph
    w_offshore_edge = calculate_wind_factor(12.0, 45.0)  # 1.25
    w_cross_edge = calculate_wind_factor(12.0, 46.0)     # max(0.45, 1.0 - 12/35) = 0.657
    print(f"  [DEBUG] Wind angle discontinuity at 45 deg: 45deg -> {w_offshore_edge}, 46deg -> {w_cross_edge:.3f}")
    if abs(w_offshore_edge - w_cross_edge) > 0.40:
        verifier.record_vulnerability(
            "Wind Angle Step Discontinuity at 45.0 degrees",
            f"At 12 mph, a 1-degree wind shift from 45.0 deg to 46.0 deg causes W_wind to plummet from {w_offshore_edge:.2f} to {w_cross_edge:.2f} (a 47% drop).",
            "MEDIUM"
        )

    # 5.4 Adversarial Check: Outlier Rejection Edge Case: Single Remaining Source
    # What if 3 sources are [1.0, 4.0, 7.0]? Median is 4.0. Both 1.0 and 7.0 are > 2.5 ft away!
    # Does statistics.stdev crash when only [4.0] remains?
    edge_sources = [
        {"source_id": "s1", "type": "model", "h_break": 1.0, "weight": 0.33},
        {"source_id": "s2", "type": "model", "h_break": 4.0, "weight": 0.33},
        {"source_id": "s3", "type": "model", "h_break": 7.0, "weight": 0.33}
    ]
    try:
        res_edge = reconcile_consensus(edge_sources, time_step_hours=0)
        verifier.check(
            res_edge["consensus_h"] == 4.0 and len(res_edge["valid"]) == 1,
            "5.4 Dual Outlier Rejection Resilience",
            f"Remaining valid = {[s['source_id'] for s in res_edge['valid']]}, Consensus H = {res_edge['consensus_h']} ft"
        )
    except Exception as e:
        verifier.check(False, "5.4 Dual Outlier Rejection Resilience", f"Crashed with exception: {e}")
        verifier.record_vulnerability("Consensus Engine Crash on Single Valid Source", str(e), "CRITICAL")

    # 5.5 Adversarial Check: Missing or None values handling
    none_sources = [
        {"source_id": "s1", "type": "model", "h_break": None, "weight": 0.50},
        {"source_id": "s2", "type": "model", "h_break": 3.0, "weight": 0.50}
    ]
    try:
        res_none = reconcile_consensus(none_sources, time_step_hours=0)
        verifier.check(
            res_none["consensus_h"] == 3.0,
            "5.5 None/Missing Value Handling",
            f"Consensus H = {res_none['consensus_h']} ft"
        )
    except Exception as e:
        verifier.check(False, "5.5 None/Missing Value Handling", f"Crashed: {e}")

    # 5.6 Grand Isle Offshore Wind Angle Calculation Wrap-Around
    # Grand Isle offshore axis is 340 deg.
    # Wind at 350 deg should be 10 deg away (offshore).
    # Wind at 330 deg should be 10 deg away (offshore).
    # Wind at 160 deg should be 180 deg away (pure onshore).
    d_gi_350 = calculate_delta_theta(350.0, 340.0)
    d_gi_330 = calculate_delta_theta(330.0, 340.0)
    d_gi_160 = calculate_delta_theta(160.0, 340.0)
    verifier.check(
        math.isclose(d_gi_350, 10.0) and math.isclose(d_gi_330, 10.0) and math.isclose(d_gi_160, 180.0),
        "5.6 Grand Isle Angular Divergence Wrap-Around",
        f"350->{d_gi_350}deg, 330->{d_gi_330}deg, 160->{d_gi_160}deg"
    )

    # 5.7 Monte Carlo Fuzzing (10,000 Random Marine States)
    print("\n--- TEST GROUP 6: Monte Carlo Fuzzing (10,000 Iterations) ---")
    random.seed(42)
    fuzz_passes = 0
    fuzz_errors = 0
    for _ in range(10000):
        h = random.uniform(0.0, 25.0)
        tp = random.uniform(1.0, 20.0)
        wdir = random.uniform(0.0, 360.0)
        vw = random.uniform(0.0, 60.0)
        offshore_dir = random.choice([0.0, 340.0])
        tide = random.choice(["rising", "falling", "slack_high", "slack_low"])

        try:
            q = calculate_surf_score(h, tp, wdir, vw, offshore_dir, tide)
            if 0 <= q <= 100:
                fuzz_passes += 1
            else:
                fuzz_errors += 1
        except Exception:
            fuzz_errors += 1

    verifier.check(
        fuzz_errors == 0 and fuzz_passes == 10000,
        "6.1 10,000-Point Monte Carlo Fuzzing",
        f"{fuzz_passes}/10000 points safely evaluated within [0, 100], 0 errors"
    )

    # --------------------------------------------------------------------------
    # SUMMARY & VERDICT
    # --------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print(f"VERIFICATION SUMMARY: {verifier.passes} PASSED, {verifier.failures} FAILED")
    print(f"VULNERABILITIES FOUND: {len(verifier.vulnerabilities)}")
    for v in verifier.vulnerabilities:
        print(f"  - [{v['severity']}] {v['title']}: {v['description']}")
    print("=" * 80)

    return verifier


if __name__ == "__main__":
    run_all_tests()
