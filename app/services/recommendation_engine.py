"""
app/services/recommendation_engine.py
Production 48-Hour Golden Window Detector, Timeline Synthesizer,
and Natural Language Recommendation Engine for Louisiana Surf Finder.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from app.models.schemas import (
    HourlyForecastPoint,
    GoldenWindowRecommendation,
    ConsensusForecast,
    NormalizedMarineReading,
)
from app.config import SPOTS
from app.services.buoy_service import degrees_to_cardinal

logger = logging.getLogger("louisiana_surf.recommendation_engine")


class RecommendationEngine:
    """
    Synthesizes 48-hour hourly timelines, detects contiguous golden windows,
    and generates natural language surf advice including board and wetsuit recommendations.
    """

    def __init__(self):
        pass

    # =========================================================================
    # 1. 48-HOUR TIMELINE SYNTHESIZER
    # =========================================================================

    def synthesize_48h_timeline(
        self,
        spot_id: str,
        hourly_model_points: List[Dict[str, Any]],
        live_buoy_reading: Optional[NormalizedMarineReading] = None,
        tide_predictions: Optional[List[Dict[str, Any]]] = None,
        base_time: Optional[datetime] = None,
    ) -> List[HourlyForecastPoint]:
        """
        Merge model forecast points, live buoy readings, and CO-OPS tides
        into an authoritative 48-hour timeline conforming to HourlyForecastPoint schema.
        """
        from app.services.consensus_engine import (
            calculate_surf_score,
            get_decision_tier,
            classify_wind_regime,
            get_breaking_range_str,
        )

        meta = SPOTS.get(spot_id, SPOTS["holly_beach"])
        k_net = meta.get("shoaling_k_net", 0.65)
        offshore_wind_deg = meta.get("offshore_wind_deg", 0.0)

        now = base_time or datetime.now(timezone.utc)
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        timeline: List[HourlyForecastPoint] = []

        # Build quick tide lookup by hour timestamp
        tide_map: Dict[str, float] = {}
        if tide_predictions:
            for tp in tide_predictions:
                t_str = tp.get("t") or tp.get("timestamp")
                val = tp.get("v") or tp.get("tide_ft") or tp.get("tide_level_ft")
                if t_str and val is not None:
                    try:
                        dt = datetime.fromisoformat(str(t_str).replace("Z", "+00:00"))
                        key = dt.strftime("%Y-%m-%dT%H:00")
                        tide_map[key] = float(val)
                    except Exception:
                        continue

        if not hourly_model_points:
            # Fallback to forecasts_fallback.json with dynamic re-baselined data
            try:
                from app.config import get_settings
                import json
                settings = get_settings()
                fc_path = settings.FALLBACK_DATA_DIR / "forecasts_fallback.json"
                if fc_path.exists():
                    with open(fc_path, "r", encoding="utf-8") as f:
                        fc_data = json.load(f)
                    spot_fc = fc_data.get(spot_id, {}).get("hourly", [])
                    if spot_fc:
                        hourly_model_points = []
                        for pt in spot_fc[:48]:
                            pt_copy = dict(pt)
                            if "wave_height" not in pt_copy and "breaking_height_ft" in pt_copy:
                                pt_copy["wave_height"] = round(pt_copy["breaking_height_ft"] / max(0.1, k_net), 2)
                            if "wave_period" not in pt_copy and "dominant_period_sec" in pt_copy:
                                pt_copy["wave_period"] = pt_copy["dominant_period_sec"]
                            hourly_model_points.append(pt_copy)
            except Exception as e:
                logger.warning(f"Could not load fallback forecasts for {spot_id}: {e}")

        if not tide_map and hourly_model_points:
            for h_idx in range(48):
                t_dt = current_hour + timedelta(hours=h_idx)
                h_k = t_dt.strftime("%Y-%m-%dT%H:00")
                if h_idx < len(hourly_model_points):
                    m_pt = hourly_model_points[h_idx]
                    t_val = m_pt.get("tide_height_ft") or m_pt.get("tide_level_ft")
                    if t_val is not None:
                        tide_map[h_k] = float(t_val)

        for hour_idx in range(48):
            target_dt = current_hour + timedelta(hours=hour_idx)
            hour_key = target_dt.strftime("%Y-%m-%dT%H:00")

            # Extract or default model data
            model_pt = hourly_model_points[hour_idx] if hour_idx < len(hourly_model_points) else {}
            deep_h = float(model_pt.get("wave_height", model_pt.get("wave_height_ft", 2.5)))
            period = float(model_pt.get("wave_period", model_pt.get("wave_period_sec", 6.0)))
            swell_dir = float(model_pt.get("wave_direction", model_pt.get("wave_direction_deg", 160.0))) % 360.0
            wind_spd = float(model_pt.get("wind_speed_10m", model_pt.get("wind_speed_mph", 10.0)))
            wind_dir = float(model_pt.get("wind_direction_10m", model_pt.get("wind_direction_deg", 0.0))) % 360.0

            # Period-dependent shoaling factor: (T / 10)^0.2
            clamped_t = max(2.0, min(25.0, period))
            shoal_factor = k_net * ((clamped_t / 10.0) ** 0.2)

            # Shoaled wave height
            h_model_break = deep_h * shoal_factor

            # Blend with live buoy at hour 0 if available and valid
            if hour_idx == 0 and live_buoy_reading and live_buoy_reading.wave_height_ft is not None:
                buoy_p = live_buoy_reading.wave_period_sec or period
                buoy_shoal = k_net * ((max(2.0, min(25.0, buoy_p)) / 10.0) ** 0.2)
                h_buoy_break = live_buoy_reading.wave_height_ft * buoy_shoal
                h_break = round((0.50 * h_buoy_break) + (0.50 * h_model_break), 2)
            else:
                h_break = round(h_model_break, 2)

            breaking_range = get_breaking_range_str(h_break)

            # Wind regime
            delta_theta = abs((wind_dir - offshore_wind_deg + 180.0) % 360.0 - 180.0)
            wind_regime = classify_wind_regime(wind_spd, delta_theta)

            # Tide calculation & derivative
            tide_h = tide_map.get(hour_key, 1.4)
            prev_key = (target_dt - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00")
            prev_tide = tide_map.get(prev_key, tide_h)
            delta_tide = tide_h - prev_tide
            tide_status = self.classify_tide_status(delta_tide, tide_h)

            # Surf score calculation
            score, _ = calculate_surf_score(
                h_break=h_break,
                t_p=period,
                theta_wind=wind_dir,
                v_w=wind_spd,
                theta_offshore=offshore_wind_deg,
                tide_state=tide_status,
                tide_height_ft=tide_h,
            )
            decision, _ = get_decision_tier(score)

            point = HourlyForecastPoint(
                timestamp=target_dt,
                breaking_height_ft=h_break,
                breaking_range=breaking_range,
                dominant_period_sec=round(period, 1),
                swell_direction_deg=round(swell_dir, 1),
                swell_direction_cardinal=degrees_to_cardinal(swell_dir),
                wind_speed_mph=round(wind_spd, 1),
                wind_direction_deg=round(wind_dir, 1),
                wind_direction_cardinal=degrees_to_cardinal(wind_dir),
                wind_regime=wind_regime,
                surf_score=score,
                decision=decision,
                tide_height_ft=round(tide_h, 2),
                tide_status=tide_status,
            )
            timeline.append(point)

        return timeline

    # =========================================================================
    # 2. GOLDEN WINDOW DETECTOR & RANKING
    # =========================================================================

    def detect_golden_windows(
        self,
        timeline_48h: List[HourlyForecastPoint],
        spot_id: str = "holly_beach",
    ) -> GoldenWindowRecommendation:
        """
        Identify contiguous blocks (>= 3 consecutive hours) where surf_score >= 60
        AND wind_regime is 'Offshore Groomed' or 'Light / Glassy'.
        Rank competing windows by peak score, duration, and tide alignment.
        Fallback to best 3-hour marginal window if none qualify.
        """
        if not timeline_48h:
            return GoldenWindowRecommendation(
                has_good_window=False,
                window_title="No Forecast Available",
                human_verdict="Timeline forecast data is currently unavailable.",
            )

        qualified_windows: List[List[HourlyForecastPoint]] = []
        current_run: List[HourlyForecastPoint] = []

        for pt in timeline_48h:
            # Condition: Score >= 60 AND Offshore / Light
            if pt.surf_score >= 60 and pt.wind_regime in ("Offshore Groomed", "Light / Glassy"):
                current_run.append(pt)
            else:
                if len(current_run) >= 3:
                    qualified_windows.append(current_run)
                current_run = []

        if len(current_run) >= 3:
            qualified_windows.append(current_run)

        # ---------------------------------------------------------------------
        # Case A: One or more Golden Windows Detected
        # ---------------------------------------------------------------------
        if qualified_windows:
            best_window = self._rank_golden_windows(qualified_windows)
            peak_point = max(best_window, key=lambda p: p.surf_score)

            start_t = best_window[0].timestamp
            end_t = best_window[-1].timestamp + timedelta(hours=1)

            time_title = self.format_window_times(start_t, end_t)
            window_title = f"Golden Window: {time_title}"

            wind_summary = f"{peak_point.wind_direction_cardinal} {round(peak_point.wind_speed_mph)} mph ({peak_point.wind_regime})"
            tide_summary = f"{peak_point.tide_status} ({round(peak_point.tide_height_ft or 1.4, 1)} ft)"

            is_clean_off = any(
                p.wind_direction_cardinal in ("N", "NNW", "NW")
                and p.wind_speed_mph >= 8.0
                and p.breaking_height_ft >= 2.5
                for p in best_window
            )

            human_verdict = self.generate_golden_window_verdict(
                spot_id=spot_id,
                best_window=best_window,
                peak_point=peak_point,
                is_clean_off=is_clean_off,
            )

            return GoldenWindowRecommendation(
                has_good_window=True,
                window_title=window_title,
                start_time=start_t,
                end_time=end_t,
                peak_time=peak_point.timestamp,
                peak_score=peak_point.surf_score,
                peak_wave_range=peak_point.breaking_range,
                wind_summary=wind_summary,
                tide_summary=tide_summary,
                human_verdict=human_verdict,
            )

        # ---------------------------------------------------------------------
        # Case B: Marginal Fallback (No window >= 3h at score >= 60)
        # ---------------------------------------------------------------------
        best_slice = self._find_best_marginal_window(timeline_48h)
        peak_pt = max(best_slice, key=lambda p: p.surf_score) if best_slice else timeline_48h[0]

        start_t = best_slice[0].timestamp if best_slice else timeline_48h[0].timestamp
        end_t = (best_slice[-1].timestamp + timedelta(hours=1)) if best_slice else timeline_48h[0].timestamp

        time_title = self.format_window_times(start_t, end_t)
        window_title = f"Marginal Window: {time_title}"

        wind_summary = f"{peak_pt.wind_direction_cardinal} {round(peak_pt.wind_speed_mph)} mph ({peak_pt.wind_regime})"
        tide_summary = f"{peak_pt.tide_status} ({round(peak_pt.tide_height_ft or 1.4, 1)} ft)"

        human_verdict = self.generate_marginal_verdict(spot_id, best_slice, peak_pt)

        return GoldenWindowRecommendation(
            has_good_window=False,
            window_title=window_title,
            start_time=start_t,
            end_time=end_t,
            peak_time=peak_pt.timestamp,
            peak_score=peak_pt.surf_score,
            peak_wave_range=peak_pt.breaking_range,
            wind_summary=wind_summary,
            tide_summary=tide_summary,
            human_verdict=human_verdict,
        )

    def _rank_golden_windows(
        self, windows: List[List[HourlyForecastPoint]]
    ) -> List[HourlyForecastPoint]:
        """Rank multiple qualified golden windows using composite Window Quality Score."""
        def score_window(w: List[HourlyForecastPoint]) -> float:
            peak_pt = max(w, key=lambda p: p.surf_score)
            s_peak = float(peak_pt.surf_score)
            s_duration = 2.0 * min(float(len(w)), 8.0)

            tide_bonus = 0.0
            if peak_pt.tide_status == "Rising":
                tide_bonus = 6.0
            elif peak_pt.tide_status == "Slack High":
                tide_bonus = 4.0
            elif peak_pt.tide_status == "Falling":
                tide_bonus = 1.0
            elif peak_pt.tide_status == "Slack Low":
                tide_bonus = -4.0

            start_offset = float(w[0].timestamp.hour)
            s_recency = -0.05 * start_offset

            return s_peak + s_duration + tide_bonus + s_recency

        return max(windows, key=score_window)

    def _find_best_marginal_window(
        self, timeline: List[HourlyForecastPoint]
    ) -> List[HourlyForecastPoint]:
        """Locate the best 3-hour sliding window in sub-60 or marginal conditions."""
        if len(timeline) < 3:
            return timeline

        best_score = -100.0
        best_slice = timeline[:3]

        for i in range(len(timeline) - 2):
            window_slice = timeline[i : i + 3]
            avg_score = sum(p.surf_score for p in window_slice) / 3.0

            wind_penalties = 0.0
            for p in window_slice:
                if p.wind_regime == "Onshore Blown-Out":
                    wind_penalties += 5.0
                elif p.wind_regime == "Cross-Shore":
                    wind_penalties += 2.0

            effective_score = avg_score - wind_penalties
            if effective_score > best_score:
                best_score = effective_score
                best_slice = window_slice

        return best_slice

    # =========================================================================
    # 3. NATURAL LANGUAGE ADVICE, BOARD & WETSUIT SELECTORS
    # =========================================================================

    def recommend_board(
        self,
        breaking_height_ft: float,
        period_sec: float,
        wind_regime: str,
        spot_id: str = "holly_beach",
    ) -> Dict[str, str]:
        """
        Formulate human board advice based on wave height, period, texture, and bathymetry.
        """
        if breaking_height_ft < 1.5:
            return {
                "primary": "9'0+ High-Volume Longboard or 8'0+ Foamie",
                "alternate": "Hydrofoil / Surf SUP",
                "rationale": "Soft ankle-to-knee rollers lack energy on the shallow mud shelf; maximum buoyancy and planing surface are essential.",
            }
        elif breaking_height_ft < 2.5:
            if wind_regime in ("Offshore Groomed", "Light / Glassy") and period_sec >= 5.5:
                return {
                    "primary": "5'6 - 5'10 Twin Fin Fish or Modern Mid-Length (6'8 - 7'4)",
                    "alternate": "Classic Single-Fin Log",
                    "rationale": "Clean knee-to-waist peelers with flat shoulders. A twin fish skates effortlessly across soft flat sections.",
                }
            else:
                return {
                    "primary": "High-Volume Groveler or Wide-Tail Foamie",
                    "alternate": "Longboard",
                    "rationale": "Short-period chop requires extra chest foam and low rocker to motor through crumbly washouts.",
                }
        elif breaking_height_ft < 4.0:
            if wind_regime in ("Offshore Groomed", "Light / Glassy"):
                return {
                    "primary": "Performance Fish (5'8 - 6'0) or High-Volume Hybrid Shortboard",
                    "alternate": "Performance Longboard or Mid-Length",
                    "rationale": "The Gulf sweet spot! Waves have decent pocket and steepness. A fish or modern hybrid shortboard will fly down the line.",
                }
            else:
                return {
                    "primary": "High-Volume Quad Groveler",
                    "alternate": "Modern Mid-Length",
                    "rationale": "Bumpy texture and side-chop require a quad fin setup for speed generation over dead sections.",
                }
        elif breaking_height_ft < 6.0:
            if wind_regime in ("Offshore Groomed", "Light / Glassy"):
                return {
                    "primary": "Standard Performance Shortboard (5'10 - 6'2 Thruster)",
                    "alternate": "Round-Tail Step-Up (6'2 - 6'4)",
                    "rationale": "Punchy chest-to-head high waves allow vertical snaps and steep takeoffs, particularly off the Grand Isle rock groins.",
                }
            else:
                return {
                    "primary": "Beefed-Up Shortboard (+2-3 Liters volume) or Step-Up",
                    "alternate": "Heavy Quad Shortboard",
                    "rationale": "Heavy longshore drift and churning washouts require serious paddle power and rail bite.",
                }
        else:
            return {
                "primary": "Step-Up / Semi-Gun (6'4 - 6'8) with heavy leash",
                "alternate": "Big-Wave Quad Shortboard",
                "rationale": "Overhead storm swell creates dangerous littoral currents and washing-machine conditions. Bring extra foam and inspect your leash string!",
            }

    def recommend_wetsuit(
        self,
        water_temp_f: Optional[float],
        air_temp_f: Optional[float] = None,
        wind_speed_mph: Optional[float] = None,
    ) -> Dict[str, str]:
        """
        Formulate wetsuit and rubber advice based on water temperature and wind chill.
        """
        if water_temp_f is None or water_temp_f <= 32.0 or water_temp_f > 105.0:
            month = datetime.now(timezone.utc).month
            seasonal_map = {
                1: 53.0, 2: 55.0, 3: 63.0, 4: 69.0, 5: 76.0, 6: 82.0,
                7: 85.0, 8: 86.0, 9: 82.0, 10: 74.0, 11: 66.0, 12: 58.0
            }
            water_temp_f = seasonal_map.get(month, 70.0)

        temp_str = f"{round(water_temp_f)}°F"

        if water_temp_f >= 78.0:
            return {
                "wetsuit": "Boardshorts / Bikini",
                "accessories": "Rashguard / UV Sun Hoodie (recommended for sun & jellyfish)",
                "temp_summary": f"Water: {temp_str}",
                "notes": "Tropical bathwater. Protect skin from blistering sun and occasional sea nettles.",
            }
        elif water_temp_f >= 71.0:
            return {
                "wetsuit": "Boardshorts with 1.5mm Neoprene Top, or 2mm Shorty",
                "accessories": "None required",
                "temp_summary": f"Water: {temp_str}",
                "notes": "Pleasant water, but brisk morning offshore breezes can produce slight wind chill during dawn patrol.",
            }
        elif water_temp_f >= 64.0:
            return {
                "wetsuit": "2/2mm Shorty or 3/2mm Fullsuit",
                "accessories": "None required",
                "temp_summary": f"Water: {temp_str}",
                "notes": "A 3/2mm fullsuit is recommended if air temperature is under 60°F or wind is over 15 mph.",
            }
        elif water_temp_f >= 57.0:
            return {
                "wetsuit": "3/2mm GBS (Glued & Blindstitched) Fullsuit",
                "accessories": "2mm/3mm Booties optional for early morning sessions",
                "temp_summary": f"Water: {temp_str}",
                "notes": "Gulf temperatures drop rapidly after northern cold fronts. A sealed 3/2mm will keep you comfortable.",
            }
        elif water_temp_f >= 51.0:
            return {
                "wetsuit": "4/3mm Sealed Fullsuit",
                "accessories": "3mm Booties mandatory; 2mm hood advised for windy sessions",
                "temp_summary": f"Water: {temp_str}",
                "notes": "Cold muddy runoff and biting north winds create severe wind chill. Do not skimp on neoprene.",
            }
        else:
            return {
                "wetsuit": "5/4mm or 4/3mm Hooded Fullsuit",
                "accessories": "5mm Booties & 3mm Gloves mandatory",
                "temp_summary": f"Water: {temp_str}",
                "notes": "Rare severe winter freeze event on the Gulf Coast. Full cold-water armor required.",
            }

    def generate_spot_comparison(
        self,
        holly_beach_consensus: ConsensusForecast,
        grand_isle_consensus: ConsensusForecast,
    ) -> Dict[str, Any]:
        """
        Compare Holly Beach vs Grand Isle across the 48-hour timeline and formulate advice.
        """
        hb_win = holly_beach_consensus.best_window
        gi_win = grand_isle_consensus.best_window

        hb_score = hb_win.peak_score if hb_win.has_good_window else holly_beach_consensus.surf_score
        gi_score = gi_win.peak_score if gi_win.has_good_window else grand_isle_consensus.surf_score

        if hb_win.has_good_window and gi_win.has_good_window:
            if gi_score >= hb_score + 8:
                winner = "grand_isle"
                advice = (
                    f"Grand Isle is the standout choice! Its narrow shelf and canyon proximity are delivering punchier "
                    f"{gi_win.peak_wave_range} faces (Peak {gi_score}/100) compared to softer {hb_win.peak_wave_range} lines at Holly Beach."
                )
            elif hb_score >= gi_score + 8:
                winner = "holly_beach"
                advice = (
                    f"Head to Holly Beach! The pure North wind is grooming clean {hb_win.peak_wave_range} peelers down Cameron Parish "
                    f"(Peak {hb_score}/100), while Grand Isle is experiencing cross-shore side texture."
                )
            else:
                winner = "both"
                advice = (
                    f"Both spots are firing! Choose Holly Beach ({hb_win.peak_wave_range}, Peak {hb_score}/100) for a relaxed tailgate "
                    f"driving directly on the sand with a fish or log. Choose Grand Isle ({gi_win.peak_wave_range}, Peak {gi_score}/100) "
                    f"if you want punchier groin wedges on a shortboard."
                )
        elif hb_win.has_good_window and not gi_win.has_good_window:
            winner = "holly_beach"
            advice = (
                f"Lock in on Holly Beach! Grand Isle has compromised wind conditions, but Holly Beach offers a clean "
                f"window ({hb_win.window_title}) with {hb_win.peak_wave_range} surf and peak score {hb_score}/100."
            )
        elif gi_win.has_good_window and not hb_win.has_good_window:
            winner = "grand_isle"
            advice = (
                f"Lock in on Grand Isle! Holly Beach lacks organized swell, but Grand Isle has a clean golden window "
                f"({gi_win.window_title}) with punchy {gi_win.peak_wave_range} surf and peak score {gi_score}/100."
            )
        else:
            winner = "neither"
            better_spot = "Grand Isle" if gi_score >= hb_score else "Holly Beach"
            better_range = gi_win.peak_wave_range if gi_score >= hb_score else hb_win.peak_wave_range
            advice = (
                f"Small and marginal across coastal Louisiana over the next 48 hours. If you're itching for a paddle, "
                f"{better_spot} offers the cleanest bump ({better_range}) on a high-volume longboard or foil."
            )

        return {
            "winner_spot": winner,
            "holly_beach_score": hb_score,
            "grand_isle_score": gi_score,
            "comparison_verdict": advice,
            "holly_beach_logistics": "Drive directly on the beach (4WD recommended); check Cameron Ferry schedule if arriving from the east.",
            "grand_isle_logistics": "LA-1 elevated toll bridge ($3.00); park at State Park or public groin crossovers; watch rip currents near rock groins.",
        }

    # =========================================================================
    # 4. VERDICT FORMATTING HELPERS
    # =========================================================================

    def generate_golden_window_verdict(
        self,
        spot_id: str,
        best_window: List[HourlyForecastPoint],
        peak_point: HourlyForecastPoint,
        is_clean_off: bool,
    ) -> str:
        spot_label = "Holly Beach (Cajun Riviera)" if spot_id == "holly_beach" else "Grand Isle"
        clean_off_str = "Post-Frontal Clean-Off Alert! " if is_clean_off else ""

        board_info = self.recommend_board(
            peak_point.breaking_height_ft,
            peak_point.dominant_period_sec,
            peak_point.wind_regime,
            spot_id,
        )

        return (
            f"{clean_off_str}{spot_label} lights up with clean {peak_point.breaking_range} surf "
            f"(Peak {peak_point.surf_score}/100) under {peak_point.wind_direction_cardinal} offshore winds ({round(peak_point.wind_speed_mph)} mph). "
            f"Tide aligns well ({peak_point.tide_status}). Quiver: {board_info['primary']}."
        )

    def generate_marginal_verdict(
        self,
        spot_id: str,
        best_slice: List[HourlyForecastPoint],
        peak_point: HourlyForecastPoint,
    ) -> str:
        spot_label = "Holly Beach" if spot_id == "holly_beach" else "Grand Isle"
        board_info = self.recommend_board(
            peak_point.breaking_height_ft,
            peak_point.dominant_period_sec,
            peak_point.wind_regime,
            spot_id,
        )

        if peak_point.surf_score >= 45:
            return (
                f"Marginal conditions for {spot_label}. Best opportunity offers {peak_point.breaking_range} surf "
                f"(Peak {peak_point.surf_score}/100) with {peak_point.wind_regime.lower()} winds. "
                f"Rideable for fun on a {board_info['primary']}."
            )
        else:
            return (
                f"Small or flat surf across {spot_label} over the next 48 hours ({peak_point.breaking_range}, Peak {peak_point.surf_score}/100). "
                f"Recommend a {board_info['primary']} or hydrofoil if desperate for water time."
            )

    def format_window_times(self, start_t: datetime, end_t: datetime) -> str:
        """Format window timestamps in readable format."""
        day_str = start_t.strftime("%A")
        start_str = start_t.strftime("%-I:%M %p") if hasattr(start_t, "strftime") else str(start_t)
        end_str = end_t.strftime("%-I:%M %p") if hasattr(end_t, "strftime") else str(end_t)
        return f"{day_str} {start_str} – {end_str}"

    def classify_tide_status(self, delta_tide: float, tide_h: float) -> str:
        if delta_tide > 0.10:
            return "Rising"
        elif delta_tide < -0.10:
            return "Falling"
        elif tide_h >= 1.2:
            return "Slack High"
        else:
            return "Slack Low"
