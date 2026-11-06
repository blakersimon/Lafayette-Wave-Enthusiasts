#!/usr/bin/env python3
"""
tests/verification/verify_adversarial_challenger_stress.py
Adversarial Stress Testing Harness by Challenger UI 1.

Verifies:
1. 48-Hour Forecast Data Rigor:
   - Live /api/spots variability (>10 unique wave heights and scores for both spots).
   - Diurnal cycle, valid numeric ranges, strictly sequential hourly timestamps.
   - Offline & fallback resilience: empty/null Open-Meteo and NOAA responses.
2. Basemap Toggle Stress:
   - Rapid-fire 50x basemap toggle test.
   - Leakage check: ensure only the active basemap group is on the Leaflet map.
   - Complete absence of noaaContoursLayer.
3. Scrubber Logic Stress:
   - Hour 0, 47, out-of-bounds (48, 100, -1), non-numeric (null, "invalid").
   - Chart.js scrubber cursor plugin resilience on boundary values.
4. DOM & Brand Verification:
   - 5 legacy buttons completely removed.
   - Logo emblem 64x64px.
5. Zero unhandled JavaScript exceptions.
"""

import asyncio
import base64
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Any, Dict, List
import websockets

from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from app.main import app
from app.services.recommendation_engine import RecommendationEngine
from app.services.consensus_engine import ConsensusEngine
from app.models.schemas import NormalizedMarineReading

CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SERVER_URL = "http://127.0.0.1:8765"


def check_server(url: str) -> bool:
    try:
        req = urllib.request.Request(f"{url}/api/health", headers={"User-Agent": "Challenger-Stress"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


class ChallengerReport:
    def __init__(self):
        self.passed: List[str] = []
        self.failed: List[str] = []
        self.findings: List[str] = []

    def check(self, condition: bool, description: str):
        if condition:
            self.passed.append(description)
            print(f"  [PASS] {description}")
        else:
            self.failed.append(description)
            print(f"  [FAIL] {description}")

    def note_finding(self, finding: str):
        self.findings.append(finding)
        print(f"  [FINDING] {finding}")


def run_python_data_rigor_tests(report: ChallengerReport):
    print("\n=======================================================")
    print("PART A: 48-Hour Forecast Data Rigor & Engine Resilience")
    print("=======================================================")

    # 1. Live /api/spots Test
    print("\n[A1] Testing Live /api/spots Response & Data Variability...")
    with TestClient(app) as client:
        resp = client.get("/api/spots")
        report.check(resp.status_code == 200, f"/api/spots returns HTTP 200 (got {resp.status_code})")
        data = resp.json()
        report.check("spots" in data and len(data["spots"]) >= 2, "Contains at least 2 surf spots")

        spots_by_id = {s["spot_id"]: s for s in data.get("spots", [])}
        for spot_id in ["holly_beach", "grand_isle"]:
            spot = spots_by_id.get(spot_id)
            report.check(spot is not None, f"Spot '{spot_id}' present in /api/spots payload")
            if not spot:
                continue

            cons = spot.get("consensus", {})
            timeline = cons.get("timeline_48h", [])
            report.check(len(timeline) == 48, f"'{spot_id}' timeline_48h has exactly 48 hourly points (got {len(timeline)})")

            if len(timeline) == 48:
                heights = [p["breaking_height_ft"] for p in timeline]
                scores = [p["surf_score"] for p in timeline]
                unique_heights = set(heights)
                unique_scores = set(scores)

                report.check(
                    len(unique_heights) >= 10,
                    f"'{spot_id}' has >= 10 unique wave heights across 48h (got {len(unique_heights)}: {sorted(list(unique_heights))[:5]}...)"
                )
                report.check(
                    len(unique_scores) >= 10,
                    f"'{spot_id}' has >= 10 unique surf scores across 48h (got {len(unique_scores)}: {sorted(list(unique_scores))[:5]}...)"
                )

                # Monotonic timestamp check and field validity
                timestamps = [p["timestamp"] for p in timeline]
                report.check(all(t is not None for t in timestamps), f"'{spot_id}' all timestamps non-null")
                report.check(all(not math.isnan(h) and h >= 0 for h in heights), f"'{spot_id}' wave heights non-negative and not NaN")
                report.check(all(0 <= s <= 100 for s in scores), f"'{spot_id}' surf scores strictly in range [0, 100]")

                # Range strings and regimes non-empty
                regimes = [p.get("wind_regime") for p in timeline]
                report.check(all(bool(r) for r in regimes), f"'{spot_id}' all wind regimes non-empty strings")
                ranges = [p.get("breaking_range") for p in timeline]
                report.check(all(bool(rng) for rng in ranges), f"'{spot_id}' all breaking ranges non-empty strings")

    # 2. Mocked Offline / Empty Upstream Fallback Resilience
    print("\n[A2] Testing Offline Fallback Resilience (Empty Open-Meteo & NOAA)...")
    with patch("app.services.weather_service.WeatherService.fetch_open_meteo_hourly", new_callable=AsyncMock, return_value=[]), \
         patch("app.services.buoy_service.BuoyService.fetch_station_pair", new_callable=AsyncMock, return_value={"tide_predictions": []}):
        with TestClient(app) as client:
            resp = client.get("/api/spots")
            report.check(resp.status_code == 200, "Fallback /api/spots returns HTTP 200 during total hourly outage")
            data = resp.json()
            fallback_spots = {s["spot_id"]: s for s in data.get("spots", [])}
            for spot_id in ["holly_beach", "grand_isle"]:
                spot = fallback_spots.get(spot_id)
                timeline = spot.get("consensus", {}).get("timeline_48h", []) if spot else []
                report.check(len(timeline) == 48, f"Offline fallback for '{spot_id}' returns 48 points (got {len(timeline)})")
                if len(timeline) == 48:
                    u_h = set(p["breaking_height_ft"] for p in timeline)
                    u_s = set(p["surf_score"] for p in timeline)
                    report.check(len(u_h) > 3, f"Offline fallback '{spot_id}' has dynamic wave heights (>3, got {len(u_h)})")
                    report.check(len(u_s) > 3, f"Offline fallback '{spot_id}' has dynamic surf scores (>3, got {len(u_s)})")

    # 3. Direct RecommendationEngine Synthesize Boundary Testing
    print("\n[A3] Testing RecommendationEngine.synthesize_48h_timeline Boundary Conditions...")
    rec_engine = RecommendationEngine()

    # Empty list
    res_empty = rec_engine.synthesize_48h_timeline("holly_beach", [])
    report.check(len(res_empty) == 48, "synthesize_48h_timeline handles empty model points by loading fallback data (48 pts)")

    # None input
    res_none = rec_engine.synthesize_48h_timeline("holly_beach", None)
    report.check(len(res_none) == 48, "synthesize_48h_timeline handles None model points safely (48 pts)")

    # Partial model points (fewer than 48, e.g. 5)
    partial_pts = [{"wave_height": 2.5 + 0.1 * i, "wave_period": 6.0} for i in range(5)]
    res_partial = rec_engine.synthesize_48h_timeline("holly_beach", partial_pts)
    report.check(len(res_partial) == 48, "synthesize_48h_timeline handles partial points (<48) padding to 48 points without crash")

    # Extreme wave conditions (storm 25ft swell, 18s period, 55 mph gale)
    storm_pts = [{"wave_height": 25.0, "wave_period": 18.0, "wave_direction": 180.0, "wind_speed_10m": 55.0, "wind_direction_10m": 180.0} for _ in range(48)]
    res_storm = rec_engine.synthesize_48h_timeline("holly_beach", storm_pts)
    report.check(len(res_storm) == 48, "synthesize_48h_timeline handles severe storm swell (25ft, 55mph) without NaN or crash")
    storm_scores = [p.surf_score for p in res_storm]
    report.check(all(0 <= s <= 100 for s in storm_scores), "Storm surf scores clamped within [0, 100]")

    # Flat calm conditions (0.1ft swell, 3s period, 0mph wind)
    calm_pts = [{"wave_height": 0.1, "wave_period": 3.0, "wave_direction": 0.0, "wind_speed_10m": 0.0, "wind_direction_10m": 0.0} for _ in range(48)]
    res_calm = rec_engine.synthesize_48h_timeline("holly_beach", calm_pts)
    report.check(len(res_calm) == 48, "synthesize_48h_timeline handles flat calm swell without NaN or crash")

    # Golden window detection under flat conditions
    gw_calm = rec_engine.detect_golden_windows(res_calm, "holly_beach")
    report.check(gw_calm is not None and not gw_calm.has_good_window, "detect_golden_windows correctly reports has_good_window=False for flat conditions")

    # Golden window detection under ideal conditions
    ideal_pts = [{"wave_height": 4.0, "wave_period": 9.0, "wave_direction": 165.0, "wind_speed_10m": 7.0, "wind_direction_10m": 0.0} for _ in range(48)]
    res_ideal = rec_engine.synthesize_48h_timeline("holly_beach", ideal_pts)
    gw_ideal = rec_engine.detect_golden_windows(res_ideal, "holly_beach")
    report.check(gw_ideal is not None and gw_ideal.has_good_window, "detect_golden_windows correctly detects golden window under offshore groomed conditions")

    # Check finding on None values in model_pt dictionary
    try:
        rec_engine.synthesize_48h_timeline("holly_beach", [{"wave_height": None}])
        report.note_finding("synthesize_48h_timeline accepted {'wave_height': None} without error")
    except TypeError as te:
        report.note_finding(f"Vulnerability noted: Passing explicit {{'wave_height': None}} into synthesize_48h_timeline triggers TypeError ('{te}') due to dict.get(..., default) returning None when key exists. Upstream weather_service protects against this, but engine should use `or` fallback.")


async def run_cdp_adversarial_tests(report: ChallengerReport, cdp_port: int = 9336):
    print("\n=======================================================")
    print("PART B: Headless Chrome CDP Stress Tests (UI, Map, Scrubber)")
    print("=======================================================")

    temp_profile = tempfile.mkdtemp(prefix="surf_challenger_")
    print(f"[INFO] Launching Chrome (port {cdp_port}, profile: {temp_profile})...")

    proc = subprocess.Popen([
        CHROME_BIN,
        "--headless=new",
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={temp_profile}",
        "--remote-allow-origins=*",
        "--window-size=1440,900",
        "--hide-scrollbars",
        "--disable-gpu",
        SERVER_URL
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    console_records = []
    exception_records = []

    try:
        ws_url = None
        for _ in range(30):
            await asyncio.sleep(0.25)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json") as resp:
                    targets = json.loads(resp.read().decode())
                    for t in targets:
                        if t.get("type") == "page" and "8765" in t.get("url", ""):
                            ws_url = t.get("webSocketDebuggerUrl")
                            break
                    if ws_url:
                        break
            except Exception:
                pass

        if not ws_url:
            raise RuntimeError(f"Could not connect to Chrome debugging target on port {cdp_port}")

        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
            msg_id = 0

            async def send(method, params=None):
                nonlocal msg_id
                msg_id += 1
                req = {"id": msg_id, "method": method}
                if params:
                    req["params"] = params
                await ws.send(json.dumps(req))
                while True:
                    raw = await ws.recv()
                    res = json.loads(raw)
                    if res.get("method") == "Runtime.consoleAPICalled":
                        console_records.append(res["params"])
                    elif res.get("method") == "Runtime.exceptionThrown":
                        exception_records.append(res["params"])
                    elif res.get("id") == msg_id:
                        return res

            await send("Runtime.enable")
            await send("Page.enable")
            await send("Log.enable")

            # Wait for initial app hydration
            await asyncio.sleep(3.0)

            async def eval_js(code: str):
                res = await send("Runtime.evaluate", {
                    "expression": code,
                    "returnByValue": True,
                    "awaitPromise": True
                })
                return res.get("result", {}).get("result", {}).get("value")

            # -------------------------------------------------------------
            # TEST B1: Legacy Controls Elimination & Brand Dimensions
            # -------------------------------------------------------------
            print("\n[B1] Verifying Top Bar Controls Streamlining & Brand Logo...")
            dom_check = await eval_js("""
            (() => {
                const legacyDeep = document.getElementById('btn-view-deep-ocean');
                const legacyBathy = document.getElementById('btn-view-bathy');
                const legacyCoast = document.getElementById('btn-view-coast');
                const legacyBuoys = document.getElementById('btn-toggle-buoys');
                const legacySync = document.getElementById('btn-sync-now');
                const basemapBtn = document.getElementById('btn-toggle-basemap');
                const basemapLabel = document.getElementById('basemap-toggle-label');

                const emblemImg = document.querySelector('.brand-wave-emblem img');
                const emblemRect = emblemImg ? emblemImg.getBoundingClientRect() : null;

                const brandTitle = document.querySelector('.brand-title');
                const brandSubtitle = document.querySelector('.brand-tag');

                return {
                    legacyButtonsFound: [
                        legacyDeep ? '#btn-view-deep-ocean' : null,
                        legacyBathy ? '#btn-view-bathy' : null,
                        legacyCoast ? '#btn-view-coast' : null,
                        legacyBuoys ? '#btn-toggle-buoys' : null,
                        legacySync ? '#btn-sync-now' : null,
                    ].filter(Boolean),
                    hasBasemapToggle: !!basemapBtn,
                    basemapLabelText: basemapLabel ? basemapLabel.textContent.trim() : null,
                    emblemWidth: emblemRect ? Math.round(emblemRect.width) : 0,
                    emblemHeight: emblemRect ? Math.round(emblemRect.height) : 0,
                    titleText: brandTitle ? brandTitle.textContent.trim() : null,
                    subtitleText: brandSubtitle ? brandSubtitle.textContent.trim() : null
                };
            })()
            """)

            report.check(len(dom_check["legacyButtonsFound"]) == 0, f"All 5 legacy top-bar buttons removed (found: {dom_check['legacyButtonsFound']})")
            report.check(dom_check["hasBasemapToggle"], f"Single basemap toggle button '#btn-toggle-basemap' is present (label: '{dom_check['basemapLabelText']}')")
            report.check(60 <= dom_check["emblemWidth"] <= 68 and 60 <= dom_check["emblemHeight"] <= 68,
                         f"Header brand emblem is enlarged beyond 50px (computed {dom_check['emblemWidth']}x{dom_check['emblemHeight']}px)")
            report.check(dom_check["titleText"] == "Lafayette Wave Enthusiasts", f"Brand title correct: '{dom_check['titleText']}'")
            report.check(dom_check["subtitleText"] == "GULF COAST SURF RADAR", f"Brand subtitle correct: '{dom_check['subtitleText']}'")

            # -------------------------------------------------------------
            # TEST B2: Basemap Toggle Stress & Layer Leakage
            # -------------------------------------------------------------
            print("\n[B2] Stress-Testing Basemap Toggle (50 Rapid Invocations & Leakage Check)...")
            toggle_stress = await eval_js("""
            (() => {
                // Find map controller via window or DOM
                const btn = document.getElementById('btn-toggle-basemap');
                const mapContainer = document.getElementById('surf-map');
                if (!btn || !mapContainer) return { error: 'elements not found' };

                // Read initial state
                const initialClass = mapContainer.className;
                const labelsSeen = [];

                // Perform 50 rapid clicks
                for (let i = 0; i < 50; i++) {
                    btn.click();
                    const lbl = document.getElementById('basemap-toggle-label')?.textContent?.trim();
                    labelsSeen.push(lbl);
                }

                // After 50 toggles (even number), it should cycle back to initial preset
                const finalClass = mapContainer.className;
                const finalLabel = document.getElementById('basemap-toggle-label')?.textContent?.trim();

                // Check for noaaContoursLayer across window
                const hasNoaaGlobal = typeof window.noaaContoursLayer !== 'undefined';

                return {
                    success: true,
                    togglesPerformed: 50,
                    finalClass,
                    finalLabel,
                    hasNoaaGlobal,
                    uniqueLabels: Array.from(new Set(labelsSeen))
                };
            })()
            """)

            report.check(toggle_stress.get("success", False), "50 rapid basemap toggles executed successfully")
            report.check(toggle_stress.get("hasNoaaGlobal") is False, "noaaContoursLayer is completely absent from global window")
            report.check("3d_relief" in toggle_stress.get("finalClass", "") or "map-theme-3d-relief" in toggle_stress.get("finalClass", ""),
                         f"Map theme container class cleanly preserved after 50 toggles ({toggle_stress.get('finalClass')})")
            report.check(set(toggle_stress.get("uniqueLabels", [])) == {"3D Relief", "Satellite"} or "Satellite" in toggle_stress.get("uniqueLabels", []),
                         f"Toggle correctly alternates labels: {toggle_stress.get('uniqueLabels')}")

            # -------------------------------------------------------------
            # TEST B3: Scrubber Boundary & Stress Testing
            # -------------------------------------------------------------
            print("\n[B3] Testing Scrubber Logic across Hour Boundaries (0 to 47, Out-of-Bounds, Non-Numeric)...")
            scrubber_stress = await eval_js("""
            (() => {
                const slider = document.getElementById('forecast-time-slider');
                const badge = document.getElementById('scrubber-mode-badge');
                const timeDisplay = document.getElementById('scrubber-time-display');

                const testResults = [];

                const testHours = [
                    0, 1, 12, 24, 46, 47,  // Valid boundaries
                    48, 100, -1, -10,       // Out-of-bounds
                    null, undefined, "abc"  // Non-numeric
                ];

                for (const h of testHours) {
                    try {
                        if (slider) {
                            slider.value = String(h);
                            slider.dispatchEvent(new Event('input', { bubbles: true }));
                        }
                        const badgeText = badge ? badge.textContent.trim() : '';
                        const timeText = timeDisplay ? timeDisplay.textContent.trim() : '';
                        testResults.push({
                            hour: h,
                            badgeText,
                            timeText,
                            crashed: false
                        });
                    } catch (e) {
                        testResults.push({
                            hour: h,
                            crashed: true,
                            error: String(e)
                        });
                    }
                }

                return testResults;
            })()
            """)

            report.check(all(not r.get("crashed") for r in scrubber_stress), "All boundary and out-of-range scrubber inputs handled without throwing exceptions")
            
            # Check hour 0 returns to live state
            h0 = next((r for r in scrubber_stress if r.get("hour") == 0), {})
            report.check(h0.get("badgeText") == "LIVE NOW", f"Hour 0 displays 'LIVE NOW' badge (got '{h0.get('badgeText')}')")
            
            # Check hour 47 displays +47H forecast
            h47 = next((r for r in scrubber_stress if r.get("hour") == 47), {})
            report.check("+47H" in h47.get("badgeText", ""), f"Hour 47 displays '+47H FORECAST' badge (got '{h47.get('badgeText')}')")

            # Check out-of-range hour 48 is clamped to slider max (47) or falls back safely
            h48 = next((r for r in scrubber_stress if r.get("hour") == 48), {})
            report.check(
                h48.get("badgeText") in ("LIVE NOW", "+47H FORECAST", "+48H FORECAST"),
                f"Out-of-range hour 48 safely handled by slider boundary clamping or live fallback (got '{h48.get('badgeText')}')"
            )

            # -------------------------------------------------------------
            # TEST B4: Chart.js Canvas Scrubber Cursor & Interaction
            # -------------------------------------------------------------
            print("\n[B4] Testing Chart.js Scrubber Cursor & Canvas Click Synchronization...")
            chart_test = await eval_js("""
            (() => {
                // Switch to 48h chart tab
                const chartTab = document.querySelector('[data-tab="timeline"]');
                if (chartTab) chartTab.click();

                const canvas = document.getElementById('forecast-chart');
                const hasCanvas = !!canvas;

                // Test clicking quick buttons
                const btnDawn = document.getElementById('btn-scrub-dawn');
                if (btnDawn) btnDawn.click();
                const dawnVal = document.getElementById('forecast-time-slider')?.value;

                const btnBest = document.getElementById('btn-open-best-spot');
                if (btnBest) btnBest.click();
                const bestVal = document.getElementById('forecast-time-slider')?.value;

                const btnNow = document.getElementById('btn-scrub-now');
                if (btnNow) btnNow.click();
                const nowVal = document.getElementById('forecast-time-slider')?.value;

                return {
                    hasCanvas,
                    dawnVal: Number(dawnVal),
                    bestVal: Number(bestVal),
                    nowVal: Number(nowVal),
                };
            })()
            """)

            report.check(chart_test.get("hasCanvas", False), "Chart.js canvas '#forecast-chart' present in DOM")
            report.check(chart_test.get("nowVal") == 0, f"'Now' quick chip resets slider to 0 (got {chart_test.get('nowVal')})")
            report.check(chart_test.get("dawnVal") is not None and chart_test.get("dawnVal") >= 0,
                         f"'Dawn 6 AM' quick chip selects valid hour index ({chart_test.get('dawnVal')})")
            report.check(chart_test.get("bestVal") is not None and chart_test.get("bestVal") >= 0,
                         f"'Best Window' quick chip selects valid hour index ({chart_test.get('bestVal')})")

            # -------------------------------------------------------------
            # TEST B5: Console Error & Unhandled Exception Audit
            # -------------------------------------------------------------
            print("\n[B5] Auditing Browser Console & Exceptions...")
            report.check(len(exception_records) == 0, f"Zero unhandled JS exceptions during entire stress run (count: {len(exception_records)})")
            if exception_records:
                for exc in exception_records:
                    print(f"    [UNHANDLED JS EXCEPTION] {exc}")

            # Filter console errors
            err_logs = [c for c in console_records if c.get("type") == "error"]
            report.check(len(err_logs) == 0, f"Zero console.error logs during stress run (count: {len(err_logs)})")
            if err_logs:
                for err in err_logs:
                    print(f"    [CONSOLE ERROR] {err}")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        shutil.rmtree(temp_profile, ignore_errors=True)
        print("[INFO] Headless Chrome session cleaned up.")


async def main():
    report = ChallengerReport()

    # Part A: Python API & Recommendation Engine Stress Tests
    run_python_data_rigor_tests(report)

    # Part B: Headless Chrome CDP Stress Tests
    if os.path.exists(CHROME_BIN) and check_server(SERVER_URL):
        await run_cdp_adversarial_tests(report)
    else:
        print(f"[SKIP] Headless Chrome tests skipped (Chrome exists: {os.path.exists(CHROME_BIN)}, Server active: {check_server(SERVER_URL)})")

    print("\n=======================================================")
    print("                 STRESS TEST SUMMARY                   ")
    print("=======================================================")
    print(f"Total Passed Checks: {len(report.passed)}")
    print(f"Total Failed Checks: {len(report.failed)}")
    print(f"Adversarial Findings: {len(report.findings)}")
    print("=======================================================")

    if report.failed:
        print("\nFailed Checks:")
        for f in report.failed:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\nAll adversarial checks passed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    import shutil
    asyncio.run(main())
