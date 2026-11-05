"""
app/services/consensus_engine.py
Louisiana Surf Finder Consensus & Wave Transformation Engine.
Provides oceanographic shelf shoaling physics (Holly Beach fluid mud K_net=0.65,
Grand Isle canyon shelf K_net=0.82), Hunt's rational dispersion wavelength solver,
directional aperture and Mississippi Delta shadow masking, multi-source weighted fusion,
two-tier statistical outlier rejection, continuous 0-100 surf scoring, and composite confidence calculation.
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple, Literal

from app.models.schemas import (
    NormalizedMarineReading,
    ConsensusForecast,
    HourlyForecastPoint,
    GoldenWindowRecommendation,
)
from app.config import SPOTS
from app.services.buoy_service import degrees_to_cardinal

logger = logging.getLogger("louisiana_surf.consensus_engine")

# ==============================================================================
# PHYSICAL CONSTANTS & CALIBRATION METRICS
# ==============================================================================

G_IMPERIAL = 32.174    # ft/s^2 (Standard gravitational acceleration)
G_METRIC = 9.80665     # m/s^2
GAMMA_BREAKING = 0.78  # McCowan solitary wave breaking index

SPOT_PHYSICS: Dict[str, Dict[str, Any]] = {
    "holly_beach": {
        "shoreline_azimuth_deg": 180.0,
        "offshore_wind_deg": 0.0,
        "shoaling_k_net": 0.65,
        "bar_depth_base_ft": 3.0,
        "delta_shadow_active": False,
        "shelf_mode": "fluid_mud_dissipation",
    },
    "grand_isle": {
        "shoreline_azimuth_deg": 155.0,
        "offshore_wind_deg": 335.0,
        "shoaling_k_net": 0.82,
        "bar_depth_base_ft": 4.5,
        "delta_shadow_active": True,
        "shelf_mode": "canyon_boundary_friction",
    },
}


# ==============================================================================
# 1. OCEANOGRAPHIC SHOALING PHYSICS & SHELF TRANSFORMATION
# ==============================================================================

def calculate_angular_divergence(angle1: float, angle2: float) -> float:
    """Computes minimum circular divergence between two compass bearings in [0, 180]."""
    return abs((angle1 - angle2 + 180.0) % 360.0 - 180.0)


def calculate_directional_exposure(theta_swell: float, spot_id: str) -> float:
    """
    Computes swell directional alignment and window exposure factor K_dir in [0.0, 1.0].
    Applies cosine-aperture refraction and Mississippi Birdfoot Delta shadow masking.
    """
    spot = SPOT_PHYSICS.get(spot_id, SPOT_PHYSICS["holly_beach"])
    azimuth = spot["shoreline_azimuth_deg"]

    delta_theta = calculate_angular_divergence(theta_swell, azimuth)

    # 1. Base Cosine Aperture
    if delta_theta <= 30.0:
        rad = math.radians(delta_theta)
        k_dir = math.sqrt(math.cos(rad))
    elif delta_theta <= 60.0:
        rad = math.radians(delta_theta)
        k_dir = math.cos(rad)
    elif delta_theta <= 85.0:
        rad = math.radians(delta_theta)
        k_dir = max(0.15, math.cos(rad) ** 2)
    else:
        k_dir = 0.0

    # 2. Grand Isle Mississippi Birdfoot Delta Shadow Masking (045° - 100°)
    if spot.get("delta_shadow_active", False):
        norm_swell = theta_swell % 360.0
        if 45.0 <= norm_swell <= 100.0:
            k_dir *= 0.20  # 80% shadow reduction from landmass blockage

    return round(k_dir, 3)


def calculate_shallow_wavelength(t_sec: float, d_ft: float) -> Tuple[float, float, float]:
    """
    Computes wavelength L (ft), celerity c (ft/s), and group velocity cg (ft/s)
    using Hunt's (1979) non-iterative 6th-order rational polynomial dispersion relation.
    Guarantees <0.1% relative error across all water depths without Newton-Raphson looping.
    """
    t_clamped = max(2.0, min(25.0, t_sec))
    d_clamped = max(0.5, d_ft)

    omega = (2.0 * math.pi) / t_clamped
    y = (omega ** 2 * d_clamped) / G_IMPERIAL

    # Hunt's rational polynomial for kd
    poly = (
        1.0
        + 0.6522 * y
        + 0.4670 * (y ** 2)
        + 0.0864 * (y ** 3)
        + 0.0675 * (y ** 4)
        + 0.0039 * (y ** 5)
        + 0.0004 * (y ** 6)
    )
    kd = math.sqrt(y ** 2 + y / poly)

    wavelength_ft = (2.0 * math.pi * d_clamped) / kd
    celerity_fps = wavelength_ft / t_clamped

    # Group velocity cg
    two_kd = 2.0 * kd
    sinh_val = math.sinh(min(50.0, two_kd))
    n = 0.5 * (1.0 + (two_kd / sinh_val))
    cg_fps = n * celerity_fps

    return round(wavelength_ft, 1), round(celerity_fps, 1), round(cg_fps, 1)


def transform_wave_shoaling(
    h_deep_ft: float,
    t_period_sec: float,
    theta_swell_deg: float,
    spot_id: str,
    tide_mllw_ft: Optional[float] = 0.0,
) -> Dict[str, Any]:
    """
    Full oceanographic transformation from deepwater buoy to nearshore breaking surf zone:
    1. Directional alignment & aperture exposure (K_dir)
    2. Dual-shelf attenuation (K_net = 0.65 HB, 0.82 GI)
    3. Non-linear period amplification factor (T/10)^0.2
    4. Sandbar depth-limited breaking cap (McCowan: 0.78 * d_eff)
    5. Dynamic surfing energy density scaling (E_surf = H^2 * T^2)
    """
    spot = SPOT_PHYSICS.get(spot_id, SPOT_PHYSICS["holly_beach"])
    k_net = spot["shoaling_k_net"]
    bar_base = spot["bar_depth_base_ft"]

    t_safe = max(2.0, min(25.0, t_period_sec))
    h_safe = max(0.0, h_deep_ft)
    tide_safe = tide_mllw_ft if tide_mllw_ft is not None else 0.0

    # Step 1: Directional Alignment & Window Aperture
    k_dir = calculate_directional_exposure(theta_swell_deg, spot_id)
    h_aligned = h_safe * k_dir

    # Step 2: Shelf Mud Dissipation & Period Amplification
    period_factor = (t_safe / 10.0) ** 0.2
    h_shoaled = h_aligned * k_net * period_factor

    # Step 3: Sandbar Depth-Limited Breaking Cap (McCowan Criterion)
    d_eff = bar_base + max(0.0, tide_safe)
    h_max_breaking = GAMMA_BREAKING * d_eff

    # Determine final breaking wave face
    is_depth_limited = h_shoaled > h_max_breaking
    h_break = min(h_shoaled, h_max_breaking)

    # Step 4: Surfing Energy Density Index (H^2 * T^2)
    surf_energy_index = (h_break ** 2) * (t_safe ** 2)

    # Step 5: Shallow Wavelength
    l_break, c_break, cg_break = calculate_shallow_wavelength(t_safe, d_eff)

    return {
        "h_deep_ft": round(h_safe, 2),
        "t_period_sec": round(t_safe, 1),
        "theta_swell_deg": round(theta_swell_deg % 360.0, 1),
        "k_dir": round(k_dir, 3),
        "k_net": k_net,
        "period_factor": round(period_factor, 3),
        "h_shoaled_ft": round(h_shoaled, 2),
        "effective_depth_ft": round(d_eff, 2),
        "h_max_breaking_ft": round(h_max_breaking, 2),
        "h_break_ft": round(h_break, 2),
        "is_depth_limited": is_depth_limited,
        "surf_energy_index": round(surf_energy_index, 1),
        "breaker_wavelength_ft": l_break,
        "breaker_celerity_fps": c_break,
        "breaker_group_vel_fps": cg_break,
    }


def shoaled_breaking_height(h_buoy: float, spot_id: str, t_p: float = 10.0) -> float:
    """Applies bathymetric shelf attenuation K_net and period amplification to deepwater buoy reading."""
    k_net = 0.65 if "holly" in spot_id.lower() else 0.82
    clamped_t = max(2.0, min(25.0, t_p))
    factor = (clamped_t / 10.0) ** 0.2
    return round(h_buoy * k_net * factor, 2)


# ==============================================================================
# 2. CONTINUOUS 0–100 SURF QUALITY SCORING FORMULATIONS
# ==============================================================================

def calculate_base_height_score(h_break: float) -> float:
    """
    Piecewise continuous breaking wave height sub-score S_height in [0, 35].
    Guarantees C^0 continuity and complete storm washout decay for extreme waves.
    """
    if h_break <= 0.0:
        return 0.0
    elif h_break <= 1.0:
        return 4.0 * h_break
    elif h_break <= 2.0:
        return 4.0 + 12.0 * (h_break - 1.0)
    elif h_break <= 2.5:
        return 16.0 + 12.0 * (h_break - 2.0)
    elif h_break <= 4.0:
        return 22.0 + 13.0 * ((h_break - 2.5) / 1.5)
    elif h_break <= 5.0:
        return 35.0
    elif h_break <= 6.5:
        return 35.0 - 10.0 * ((h_break - 5.0) / 1.5)
    elif h_break <= 8.0:
        return 25.0 - 15.0 * ((h_break - 6.5) / 1.5)
    elif h_break <= 11.0:
        return max(0.0, 10.0 - 3.33 * (h_break - 8.0))
    else:
        return 0.0


def calculate_period_score(t_p: float) -> float:
    """Exponential groundswell period sub-score S_period in [0, 25]."""
    if t_p <= 0.0:
        return 0.0
    return max(0.0, min(25.0, 25.0 / (1.0 + math.exp(-0.55 * (t_p - 6.8)))))


def calculate_delta_theta(theta_wind: float, theta_offshore: float) -> float:
    """Calculates minimal circular angular divergence |theta_wind - theta_offshore| in [0, 180]."""
    return abs((theta_wind - theta_offshore + 180.0) % 360.0 - 180.0)


def calculate_wind_score(v_w: float, delta_theta: float) -> float:
    """
    Continuous angular divergence and velocity wind sub-score S_wind in [0, 25].
    Eliminates all step-function cliffs across velocity and direction boundaries.
    """
    a_dir = (1.0 + math.cos(math.radians(delta_theta))) / 2.0

    # Continuous offshore velocity curve B(V_w)
    if v_w <= 8.0:
        b_vw = 20.0 + 5.0 * (v_w / 8.0)
    elif v_w <= 15.0:
        b_vw = 25.0 - 4.0 * ((v_w - 8.0) / 7.0)
    elif v_w <= 22.0:
        b_vw = 21.0 - 9.0 * ((v_w - 15.0) / 7.0)
    else:
        b_vw = max(0.0, 12.0 - 0.8 * (v_w - 22.0))

    # Continuous onshore deterioration curve O(V_w)
    o_vw = max(0.0, 18.0 * math.exp(-0.15 * v_w) - 1.5)

    return max(0.0, min(25.0, a_dir * b_vw + (1.0 - a_dir) * o_vw))


def calculate_tide_score(tide_state: str, tide_height_ft: Optional[float] = None) -> float:
    """Spot tide window sub-score S_tide in [0, 15]."""
    clean_state = tide_state.lower().strip().replace("-", "_").replace(" ", "_")
    base_map = {
        "rising": 14.0,
        "rising_mid_high": 14.0,
        "slack_high": 11.0,
        "high": 11.0,
        "falling": 8.0,
        "falling_mid": 8.0,
        "slack_low": 3.0,
        "extreme_low": 3.0,
        "low": 3.0,
    }
    score = base_map.get(clean_state, 10.0)
    if tide_height_ft is not None:
        if 0.8 <= tide_height_ft <= 1.6:
            score = min(15.0, score + 1.0)
        elif tide_height_ft < 0.2:
            score = max(0.0, score - 1.0)
    return score


def calculate_surf_score(
    h_break: float,
    t_p: float,
    theta_wind: float,
    v_w: float,
    theta_offshore: float,
    tide_state: str = "slack_high",
    tide_height_ft: Optional[float] = None,
) -> Tuple[int, Dict[str, float]]:
    """
    Computes total 0-100 objective surf quality score and sub-score component breakdown.
    Applies cleanliness modulation, flat-wave gating, and hurricane storm washout damping.
    Returns: (total_score_0_100, sub_scores_dict)
    """
    s_h = calculate_base_height_score(h_break)
    s_p = calculate_period_score(t_p)
    delta_th = calculate_delta_theta(theta_wind, theta_offshore)
    s_w = calculate_wind_score(v_w, delta_th)
    s_t = calculate_tide_score(tide_state, tide_height_ft)

    # 1. Cleanliness coupling factor
    eta_clean = max(0.20, min(1.0, 0.20 + 0.80 * ((s_w / 25.0) ** 0.75)))

    # 2. Flat wave gating factor (suppresses high wind scores when water is calm)
    gamma_wave = max(0.15, min(1.0, h_break / 2.0))

    # 3. Storm washout damping envelope (zero leak for massive storm surges)
    if h_break <= 6.5:
        psi_washout = 1.0
    elif h_break <= 10.0:
        psi_washout = 1.0 - 0.75 * ((h_break - 6.5) / 3.5)
    else:
        psi_washout = max(0.0, 0.25 - 0.05 * (h_break - 10.0))

    raw_score = (s_h * eta_clean + gamma_wave * (s_p + s_w + s_t)) * psi_washout
    total_score = round(max(0, min(100, raw_score)))

    sub_scores = {
        "s_height": round(s_h, 2),
        "s_period": round(s_p, 2),
        "s_wind": round(s_w, 2),
        "s_tide": round(s_t, 2),
        "eta_clean": round(eta_clean, 2),
        "gamma_wave": round(gamma_wave, 2),
        "psi_washout": round(psi_washout, 2),
    }
    return total_score, sub_scores


# ==============================================================================
# 3. CATEGORICAL OUTCOMES & FORMATTING
# ==============================================================================

def get_decision_tier(
    score: int
) -> Tuple[Literal["Poor / Flat", "Fair", "Good / Decent", "Firing / Epic"], Literal["crimson", "amber", "emerald", "purple"]]:
    """Returns categorical decision label and badge color token for score."""
    if score >= 80:
        return "Firing / Epic", "purple"
    elif score >= 56:
        return "Good / Decent", "emerald"
    elif score >= 31:
        return "Fair", "amber"
    else:
        return "Poor / Flat", "crimson"


def get_breaking_range_str(h_break: float) -> str:
    """Formats breaking wave height into surfer-friendly range string."""
    if h_break < 1.0:
        return "Flat"
    elif h_break < 1.8:
        return "1-2 ft (Ankle to Knee)"
    elif h_break < 2.8:
        return "2-3 ft (Thigh to Waist)"
    elif h_break < 3.8:
        return "3-4 ft (Waist to Chest)"
    elif h_break < 5.0:
        return "4-5 ft (Chest to Head)"
    elif h_break < 6.5:
        return "5-6 ft (Head to Overhead)"
    elif h_break < 8.5:
        return "6-8+ ft (Double Overhead)"
    else:
        return "8+ ft (Storm Washout)"


def classify_wind_regime(
    v_w: float, delta_theta: float
) -> Literal["Offshore Groomed", "Cross-Shore", "Onshore Blown-Out", "Light / Glassy"]:
    """Categorizes wind character regime."""
    if v_w < 4.0:
        return "Light / Glassy"
    elif delta_theta <= 45.0:
        return "Offshore Groomed" if v_w <= 22.0 else "Cross-Shore"
    elif delta_theta <= 105.0:
        return "Cross-Shore"
    else:
        return "Onshore Blown-Out"


# ==============================================================================
# 4. MULTI-SOURCE CONSENSUS FUSION & OUTLIER ANOMALY REJECTION
# ==============================================================================

def reconcile_consensus(
    sources: List[Any],
    spot_id: str = "holly_beach",
    time_step_hours: int = 0,
) -> Dict[str, Any]:
    """
    Executes multi-source weighted consensus fusion, two-tier outlier anomaly rejection,
    and variance-freshness confidence scoring across buoys, models, and scrapers.
    Accepts List[NormalizedMarineReading] or List[Dict[str, Any]].
    """
    normalized_sources: List[NormalizedMarineReading] = []
    for s in sources:
        if isinstance(s, dict):
            src_id = str(s.get("source_id", "src"))
            src_type = str(s.get("source_type", s.get("type", "model")))
            h = s.get("wave_height_ft", s.get("h_break"))
            w = float(s.get("confidence_weight", s.get("weight", 0.2)))
            valid_types = ("buoy", "model", "scraper", "tide_station", "buoy_observation", "forecast_model", "scraped_forecast", "tide_gauge")
            st = src_type if src_type in valid_types else "model"
            reading = NormalizedMarineReading(
                source_id=src_id,
                source_name=src_id.replace("_", " ").title(),
                source_type=st,
                timestamp=s.get("timestamp", datetime.now(timezone.utc)),
                wave_height_ft=h,
                wave_period_sec=s.get("wave_period_sec", s.get("period", 7.0)),
                confidence_weight=w,
                status=s.get("status", "valid"),
            )
            normalized_sources.append(reading)
        elif isinstance(s, NormalizedMarineReading):
            normalized_sources.append(s)

    candidate_sources = [s for s in normalized_sources if s.wave_height_ft is not None]
    if not candidate_sources:
        return {
            "consensus_h": 0.0,
            "sigma_wave": 0.0,
            "confidence_score": 30,
            "confidence_label": "Low (Divergent)",
            "divergence_warning": "No active wave sources available; using fallback snapshot.",
            "valid": [],
            "rejected": [],
        }

    # Normalize offshore buoys to breaking wave height if not already shoaled
    h_effective_map: Dict[str, float] = {}
    for s in candidate_sources:
        h = s.wave_height_ft or 0.0
        # If source is an offshore wave buoy, transform to breaking height via K_net
        is_offshore_buoy = (
            ("42035" in s.source_id or "42354" in s.source_id or "42001" in s.source_id)
            and ("tide" not in s.source_type)
        )
        if is_offshore_buoy:
            h_eff = shoaled_breaking_height(h, spot_id, s.wave_period_sec or 7.0)
        else:
            h_eff = h
        h_effective_map[s.source_id] = h_eff

    h_values = [h_effective_map[s.source_id] for s in candidate_sources]
    valid: List[NormalizedMarineReading] = []
    rejected: List[NormalizedMarineReading] = []

    # Two-tier outlier detection when N >= 3 candidate sources are present
    if len(h_values) >= 3:
        med = statistics.median(h_values)
        stdev = statistics.stdev(h_values) if len(h_values) >= 2 else 0.0

        for s in candidate_sources:
            h = h_effective_map[s.source_id]
            is_outlier = False

            # Check 1: Relative ratio > 2.5x median or < 0.2x median
            if med >= 0.5 and (h > 2.5 * med or (med >= 1.5 and h < 0.2 * med)):
                is_outlier = True
            # Check 2: 3-sigma rule or absolute distance > 2.5 ft for typical surf
            if abs(h - med) > 2.5 or (stdev > 0.0 and abs(h - med) > 3.0 * max(stdev, 0.5)):
                is_outlier = True

            if is_outlier:
                s.status = "outlier_rejected"
                s.confidence_weight = 0.0
                rejected.append(s)
            else:
                valid.append(s)
    else:
        valid = list(candidate_sources)

    # Invariant 1: Outlier rejection must never result in an empty valid set
    if not valid:
        med = statistics.median(h_values)
        closest = min(candidate_sources, key=lambda s: abs(h_effective_map[s.source_id] - med))
        closest.status = "valid"
        valid = [closest]
        rejected = [s for s in candidate_sources if s != closest]

    # Baseline weight hierarchy
    base_weight_map = {
        "coops": 0.35,
        "tide_gauge": 0.35,
        "tide_station": 0.35,
        "capl1": 0.35,
        "gisl1": 0.35,
        "ndbc": 0.25,
        "cdip": 0.25,
        "buoy": 0.25,
        "buoy_observation": 0.25,
        "open_meteo": 0.20,
        "forecast_model": 0.20,
        "model": 0.20,
        "surf_captain": 0.10,
        "surf_forecast": 0.10,
        "surfline": 0.10,
        "scraped_forecast": 0.10,
        "scraper": 0.10,
    }

    # Weight re-normalization
    total_assigned = 0.0
    for s in valid:
        weight = 0.15
        for key, w in base_weight_map.items():
            if key in s.source_id.lower() or key in s.source_type.lower():
                weight = w
                break
        s.confidence_weight = weight
        total_assigned += weight

    for s in valid:
        norm_w = s.confidence_weight / total_assigned if total_assigned > 0 else 1.0 / len(valid)
        setattr(s, "_norm_weight", norm_w)

    consensus_h = sum(h_effective_map[s.source_id] * getattr(s, "_norm_weight", 1.0 / len(valid)) for s in valid)
    valid_heights = [h_effective_map[s.source_id] for s in valid]
    sigma_wave = statistics.stdev(valid_heights) if len(valid_heights) >= 2 else 0.0

    # Cross-source agreement
    if len(valid_heights) >= 2:
        c_agreement = max(25.0, min(98.0, 100.0 - (sigma_wave * 35.0)))
    elif len(valid_heights) == 1:
        c_agreement = 55.0
    else:
        c_agreement = 30.0

    # Freshness calculation
    now = datetime.now(timezone.utc)
    newest_ts = max((s.timestamp for s in valid), default=now)
    age_minutes = max(0.0, (now - newest_ts).total_seconds() / 60.0)
    if age_minutes <= 30.0:
        f_freshness = 1.00
    elif age_minutes <= 60.0:
        f_freshness = 0.95
    elif age_minutes <= 120.0:
        f_freshness = 0.85
    elif age_minutes <= 360.0:
        f_freshness = 0.70
    else:
        f_freshness = 0.50

    # Source diversity factor
    b_sources = 1.00 if len(valid) >= 4 else (0.92 if len(valid) == 3 else (0.82 if len(valid) == 2 else 0.70))
    conf_score = round(max(20, min(98, c_agreement * f_freshness * b_sources)))

    conf_label: Literal["High", "Moderate", "Low (Divergent)"] = (
        "High" if conf_score >= 80 else ("Moderate" if conf_score >= 60 else "Low (Divergent)")
    )
    warning = None
    if conf_score < 65 or sigma_wave > 1.2:
        warning = f"Forecast divergence alert: Sources differ by ±{sigma_wave:.1f} ft across {len(valid)} reporting feeds."

    return {
        "consensus_h": round(consensus_h, 2),
        "sigma_wave": round(sigma_wave, 3),
        "confidence_score": conf_score,
        "confidence_label": conf_label,
        "divergence_warning": warning,
        "valid": valid,
        "rejected": rejected,
    }


# ==============================================================================
# 5. SPOT FORECAST COMPILATION ENGINE
# ==============================================================================

class ConsensusEngine:
    """
    Core Consensus & Wave Transformation Engine coordinating physics,
    multi-source data reconciliation, and forecast synthesis.
    """

    def __init__(self):
        pass

    def generate_spot_consensus(
        self,
        spot_id: str,
        readings: List[NormalizedMarineReading],
        hourly_weather: Optional[List[Dict[str, Any]]] = None,
        tide_predictions: Optional[List[Dict[str, Any]]] = None,
        base_time: Optional[datetime] = None,
    ) -> ConsensusForecast:
        """
        Synthesize full ConsensusForecast object for Holly Beach or Grand Isle,
        combining live readings, shoaling physics, multi-source consensus,
        48-hour timeline, and golden window recommendation.
        """
        meta = SPOTS.get(spot_id, SPOTS["holly_beach"])
        spot_name = meta["spot_name"]
        lat = meta["coordinates"]["latitude"]
        lon = meta["coordinates"]["longitude"]
        offshore_wind_deg = meta["offshore_wind_deg"]
        now = base_time or datetime.now(timezone.utc)

        # 1. Multi-source fusion and outlier rejection
        fusion = reconcile_consensus(readings, spot_id)
        breaking_h = fusion["consensus_h"]
        conf_score = fusion["confidence_score"]
        conf_label = fusion["confidence_label"]
        warning = fusion["divergence_warning"]

        # 2. Extract consensus period and direction
        valid_readings = fusion["valid"] if fusion["valid"] else readings
        periods = [r.wave_period_sec for r in valid_readings if r.wave_period_sec is not None]
        consensus_period = round(statistics.mean(periods), 1) if periods else 6.5

        swell_dirs = [r.swell_direction_deg or r.wave_direction_deg for r in valid_readings if (r.swell_direction_deg or r.wave_direction_deg) is not None]
        consensus_swell_dir = round(statistics.mean(swell_dirs), 1) if swell_dirs else 165.0
        swell_compass = degrees_to_cardinal(consensus_swell_dir)

        # 3. Extract wind metrics
        wind_speeds = [r.wind_speed_mph for r in valid_readings if r.wind_speed_mph is not None]
        consensus_wind_spd = round(statistics.mean(wind_speeds), 1) if wind_speeds else 10.0

        wind_dirs = [r.wind_direction_deg for r in valid_readings if r.wind_direction_deg is not None]
        consensus_wind_dir = round(statistics.mean(wind_dirs), 1) if wind_dirs else 0.0
        wind_compass = degrees_to_cardinal(consensus_wind_dir)

        # 4. Extract water temp & tide
        water_temps = [r.water_temp_f for r in readings if r.water_temp_f is not None]
        water_temp = round(statistics.mean(water_temps), 1) if water_temps else None

        tide_levels = [r.tide_ft for r in readings if r.tide_ft is not None]
        tide_height = round(tide_levels[0], 2) if tide_levels else None

        tide_states = [r.tide_status for r in readings if r.tide_status]
        raw_tide_state = tide_states[0] if tide_states else "Rising"

        # Standardize tide state to schema Literal
        clean_ts = raw_tide_state.lower()
        if "rising" in clean_ts:
            std_tide_state: Literal["Rising", "Falling", "Slack High", "Slack Low"] = "Rising"
        elif "falling" in clean_ts:
            std_tide_state = "Falling"
        elif "low" in clean_ts:
            std_tide_state = "Slack Low"
        else:
            std_tide_state = "Slack High"

        # 5. Calculate 0-100 Surf Score
        surf_score, _ = calculate_surf_score(
            h_break=breaking_h,
            t_p=consensus_period,
            theta_wind=consensus_wind_dir,
            v_w=consensus_wind_spd,
            theta_offshore=offshore_wind_deg,
            tide_state=std_tide_state,
            tide_height_ft=tide_height,
        )

        decision, badge_color = get_decision_tier(surf_score)
        breaking_range_str = get_breaking_range_str(breaking_h)
        delta_th = calculate_delta_theta(consensus_wind_dir, offshore_wind_deg)
        wind_character = classify_wind_regime(consensus_wind_spd, delta_th)

        # 6. Generate 48-Hour Timeline & Golden Window Recommendation
        from app.services.recommendation_engine import RecommendationEngine

        rec_engine = RecommendationEngine()
        primary_buoy = next((r for r in readings if "buoy" in r.source_type or "ndbc" in r.source_id), None)
        timeline_48h = rec_engine.synthesize_48h_timeline(
            spot_id=spot_id,
            hourly_model_points=hourly_weather or [],
            live_buoy_reading=primary_buoy,
            tide_predictions=tide_predictions,
            base_time=now,
        )
        best_window = rec_engine.detect_golden_windows(timeline_48h, spot_id)

        # Check fallback status
        is_fallback = any(r.status == "fallback" for r in readings) if readings else True

        # Ensure spot_id is strictly typed Literal["holly_beach", "grand_isle"]
        strict_spot_id: Literal["holly_beach", "grand_isle"] = (
            "grand_isle" if "grand" in spot_id.lower() else "holly_beach"
        )

        return ConsensusForecast(
            spot_id=strict_spot_id,
            spot_name=spot_name,
            latitude=lat,
            longitude=lon,
            timestamp=now,
            surf_score=surf_score,
            overall_decision=decision,
            badge_color=badge_color,
            breaking_height_ft=round(breaking_h, 2),
            breaking_range_str=breaking_range_str,
            primary_swell_period_sec=consensus_period,
            primary_swell_direction_deg=consensus_swell_dir,
            primary_swell_compass=swell_compass,
            wind_speed_mph=consensus_wind_spd,
            wind_direction_deg=consensus_wind_dir,
            wind_compass=wind_compass,
            wind_character=wind_character,
            water_temp_f=water_temp,
            tide_height_ft=tide_height,
            tide_state=std_tide_state,
            confidence_score_pct=conf_score,
            confidence_label=conf_label,
            divergence_warning=warning,
            is_fallback=is_fallback,
            best_window=best_window,
            source_breakdown=readings,
            timeline_48h=timeline_48h,
        )
