#!/usr/bin/env python3
"""
tests/verification/stress_test_interactions.py
Adversarial Stress-Testing Harness for Louisiana Surf Finder (Gen 2).

Tests:
1. Rapid Spot Switching Stress Test (Holly Beach <-> Grand Isle via top-bar, markers, drawer).
2. 48-Hour Timeline Scrubbing & Chart.js Click Synchronization Stress Test.
3. Drawer Toggle, Tab Transitions & Sticky Header Scroll Stress Test.
4. Strict Console Log Audit: 0 Errors, 0 Warnings, 0 Unhandled Exceptions.

Author: Challenger Gen 2.2 (UI Interaction and Stress Challenger)
"""

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import websockets

CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def check_server(server_url: str) -> bool:
    try:
        req = urllib.request.Request(f"{server_url}/api/health", headers={"User-Agent": "Stress-Tester"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def run_stress_test(server_url: str = "http://127.0.0.1:8765", cdp_port: int = 9336, output_dir: str = ".agents/teamwork/challenger_gen2_2/stress_run"):
    if not os.path.exists(CHROME_BIN):
        print(f"[FAIL] Google Chrome binary not found at: {CHROME_BIN}")
        sys.exit(1)

    if not check_server(server_url):
        print(f"[FAIL] Server is not reachable at {server_url}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    temp_profile = tempfile.mkdtemp(prefix="surf_stress_")
    print(f"[INFO] Launching headless Chrome for stress testing (profile: {temp_profile})...")

    proc = subprocess.Popen([
        CHROME_BIN,
        "--headless=new",
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={temp_profile}",
        "--remote-allow-origins=*",
        "--window-size=1440,900",
        "--hide-scrollbars",
        "--disable-gpu",
        server_url
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    console_records = []
    exception_records = []
    log_records = []

    try:
        # Connect to CDP
        ws_url = None
        for _ in range(40):
            time.sleep(0.2)
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

        async def stress_session():
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
                        elif res.get("method") == "Log.entryAdded":
                            log_records.append(res["params"])
                        elif res.get("id") == msg_id:
                            return res

                # Background drain for unsolicited events
                async def event_drain():
                    while True:
                        try:
                            raw = await ws.recv()
                            res = json.loads(raw)
                            if res.get("method") == "Runtime.consoleAPICalled":
                                console_records.append(res["params"])
                            elif res.get("method") == "Runtime.exceptionThrown":
                                exception_records.append(res["params"])
                            elif res.get("method") == "Log.entryAdded":
                                log_records.append(res["params"])
                        except asyncio.CancelledError:
                            break
                        except Exception:
                            break

                drain_task = asyncio.create_task(event_drain())

                await send("Runtime.enable")
                await send("Page.enable")
                await send("Log.enable")

                # Wait for initial page hydration, Leaflet map, tiles, and telemetry fetch
                print("[INFO] Waiting for page hydration and initial telemetry sync...")
                await asyncio.sleep(3.0)

                async def eval_js(code: str):
                    res = await send("Runtime.evaluate", {
                        "expression": code,
                        "returnByValue": True,
                        "awaitPromise": True
                    })
                    return res.get("result", {}).get("result", {}).get("value")

                async def capture_screen(name: str):
                    res = await send("Page.captureScreenshot", {"format": "png"})
                    data = res.get("result", {}).get("data")
                    if data:
                        path = os.path.join(output_dir, name)
                        with open(path, "wb") as f:
                            f.write(base64.b64decode(data))
                        print(f"  [SCREENSHOT] Saved: {path}")
                        return path
                    return None

                stress_results = {
                    "phase1_spot_switching": {},
                    "phase2_timeline_scrubbing": {},
                    "phase3_drawer_and_tabs": {},
                    "phase4_console_audit": {}
                }

                # =========================================================================
                # STRESS TEST 1: RAPID SPOT SWITCHING
                # =========================================================================
                print("\n=======================================================")
                print(">>> STRESS TEST 1: Rapid Spot Switching (60 Toggles)")
                print("=======================================================")

                initial_heap = await eval_js("window.performance?.memory?.usedJSHeapSize || 0")
                print(f"  Initial JS Heap Size: {initial_heap / (1024*1024):.2f} MB" if initial_heap else "  Heap metrics unavailable")

                toggle_errors = []
                num_switches = 60

                # 1. Top bar button rapid toggling (30 switches)
                for i in range(30):
                    target_spot = "grand_isle" if i % 2 == 0 else "holly_beach"
                    pill_id = f"pill-{target_spot}"
                    expected_title = "Grand Isle" if target_spot == "grand_isle" else "Holly Beach"

                    await eval_js(f"document.getElementById('{pill_id}')?.click()")
                    # Minimal delay: test high frequency without crashing
                    if i % 5 == 0:
                        await asyncio.sleep(0.05)

                # Check state after top-bar toggles
                current_state = await eval_js("""
                (() => {
                    const activePill = document.querySelector('.spot-pill.active')?.getAttribute('data-spot-id');
                    const drawerTitle = document.getElementById('drawer-spot-name')?.textContent?.trim();
                    const markerCount = document.querySelectorAll('.spot-wave-beacon').length;
                    const selectedMarker = document.querySelector('.spot-wave-beacon.selected .spot-pin-title')?.textContent?.trim();
                    return { activePill, drawerTitle, markerCount, selectedMarker };
                })()
                """)
                print(f"  State after 30 top-bar switches: {current_state}")
                if current_state.get("markerCount") != 2:
                    toggle_errors.append(f"Unexpected marker count: {current_state.get('markerCount')} (expected 2)")

                # 2. Map marker click rapid toggling (20 switches)
                for i in range(20):
                    target_idx = 1 if i % 2 == 0 else 0
                    await eval_js(f"""
                    (() => {{
                        const markers = document.querySelectorAll('.spot-wave-beacon');
                        if (markers[{target_idx}]) {{
                            markers[{target_idx}].dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true }}));
                        }}
                    }})()
                    """)
                    if i % 4 == 0:
                        await asyncio.sleep(0.05)

                # 3. High-speed burst toggling (10 bursts back-and-forth in 100ms)
                print("  Executing high-speed burst toggle batch (10 clicks in quick succession)...")
                burst_result = await eval_js("""
                (() => {
                    const p1 = document.getElementById('pill-holly_beach');
                    const p2 = document.getElementById('pill-grand_isle');
                    for (let j = 0; j < 10; j++) {
                        if (j % 2 === 0) p2?.click();
                        else p1?.click();
                    }
                    return {
                        activePill: document.querySelector('.spot-pill.active')?.getAttribute('data-spot-id'),
                        drawerTitle: document.getElementById('drawer-spot-name')?.textContent?.trim(),
                        heroWave: document.getElementById('hero-breaking-ft')?.textContent?.trim()
                    };
                })()
                """)
                await asyncio.sleep(0.5)

                # Verify final state consistency
                final_spot_state = await eval_js("""
                (() => {
                    const activePill = document.querySelector('.spot-pill.active')?.getAttribute('data-spot-id');
                    const drawerTitle = document.getElementById('drawer-spot-name')?.textContent?.trim();
                    const heroWave = document.getElementById('hero-breaking-ft')?.textContent?.trim();
                    const markerCount = document.querySelectorAll('.spot-wave-beacon').length;
                    const selectedMarkers = document.querySelectorAll('.spot-wave-beacon.selected').length;
                    const selectedMarkerTitle = document.querySelector('.spot-wave-beacon.selected .spot-pin-title')?.textContent?.trim();
                    
                    const isConsistent = (activePill === 'holly_beach' && drawerTitle.includes('Holly Beach') && selectedMarkerTitle.includes('Holly Beach')) ||
                                         (activePill === 'grand_isle' && drawerTitle.includes('Grand Isle') && selectedMarkerTitle.includes('Grand Isle'));
                    
                    return {
                        activePill,
                        drawerTitle,
                        heroWave,
                        markerCount,
                        selectedMarkers,
                        selectedMarkerTitle,
                        isConsistent
                    };
                })()
                """)
                print(f"  Final spot state consistency: {final_spot_state}")

                if not final_spot_state.get("isConsistent"):
                    toggle_errors.append(f"Inconsistent state after rapid toggling: {final_spot_state}")
                if final_spot_state.get("selectedMarkers") != 1:
                    toggle_errors.append(f"Expected exactly 1 selected marker, found {final_spot_state.get('selectedMarkers')}")

                final_heap = await eval_js("window.performance?.memory?.usedJSHeapSize || 0")
                heap_growth_mb = (final_heap - initial_heap) / (1024 * 1024) if (final_heap and initial_heap) else 0.0
                print(f"  Post-stress JS Heap Size: {final_heap / (1024*1024):.2f} MB (Delta: {heap_growth_mb:+.2f} MB)")

                stress_results["phase1_spot_switching"] = {
                    "passed": len(toggle_errors) == 0,
                    "total_switches": num_switches,
                    "errors": toggle_errors,
                    "final_state": final_spot_state,
                    "heap_delta_mb": heap_growth_mb
                }
                await capture_screen("stress_01_after_rapid_switching.png")

                # =========================================================================
                # STRESS TEST 2: TIMELINE SCRUBBING & CHART.JS CLICKS
                # =========================================================================
                print("\n=======================================================")
                print(">>> STRESS TEST 2: 48-Hour Timeline Scrubbing & Chart Clicks")
                print("=======================================================")

                scrub_errors = []

                # Part A: Sweep through all 48 hours (0 to 47) and verify sync
                print("  Sweeping all 48 hours sequentially on #forecast-time-slider...")
                sweep_sample_hours = list(range(48))
                sweep_failures = 0

                for h in sweep_sample_hours:
                    res = await eval_js(f"""
                    (() => {{
                        const slider = document.getElementById('forecast-time-slider');
                        if (!slider) return {{ error: 'No slider found' }};
                        slider.value = String({h});
                        slider.dispatchEvent(new Event('input', {{ bubbles: true }}));

                        // Query updated values
                        const badgeText = document.getElementById('scrubber-mode-badge')?.textContent?.trim();
                        const timeText = document.getElementById('scrubber-time-display')?.textContent?.trim();
                        const heroWave = document.getElementById('hero-breaking-ft')?.textContent?.trim();
                        const period = document.getElementById('vital-swell-period')?.textContent?.trim();
                        const windSpeed = document.getElementById('vital-wind-speed')?.textContent?.trim();
                        const windRegime = document.getElementById('vital-wind-regime')?.textContent?.trim();
                        const tideHt = document.getElementById('vital-tide-height')?.textContent?.trim();
                        const score = document.getElementById('hero-surf-score')?.textContent?.trim();
                        
                        // Check for any NaN or undefined strings
                        const fields = [heroWave, period, windSpeed, windRegime, tideHt, score];
                        const hasNaN = fields.some(f => f && (f.includes('NaN') || f.includes('undefined')));
                        
                        return {{
                            h: {h},
                            badgeText,
                            heroWave,
                            period,
                            windSpeed,
                            windRegime,
                            score,
                            hasNaN
                        }};
                    }})()
                    """)

                    if res.get("hasNaN"):
                        sweep_failures += 1
                        scrub_errors.append(f"Hour {h} rendered NaN/undefined: {res}")
                    if h == 0 and res.get("badgeText") != "LIVE NOW":
                        scrub_errors.append(f"Hour 0 expected 'LIVE NOW' badge, got: {res.get('badgeText')}")
                    elif h > 0 and not (res.get("badgeText") or "").startswith(f"+{h}H"):
                        scrub_errors.append(f"Hour {h} expected '+{h}H FORECAST', got: {res.get('badgeText')}")

                print(f"  Sequential 48h sweep complete. Hour anomalies: {sweep_failures}")

                # Part B: Rapid oscillating scrubbing (100 rapid random jumps)
                print("  Executing high-speed oscillating scrubbing (100 rapid hops)...")
                oscillate_res = await eval_js("""
                (() => {
                    const slider = document.getElementById('forecast-time-slider');
                    const hops = [0, 12, 24, 36, 47, 5, 18, 42, 1, 30, 0, 47, 23, 8, 15, 0];
                    for (let k = 0; k < 100; k++) {
                        const target = hops[k % hops.length];
                        slider.value = String(target);
                        slider.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                    // Return final state after 100 hops
                    return {
                        sliderVal: slider.value,
                        badgeText: document.getElementById('scrubber-mode-badge')?.textContent?.trim(),
                        heroWave: document.getElementById('hero-breaking-ft')?.textContent?.trim()
                    };
                })()
                """)
                print(f"  Oscillating scrubber test final state: {oscillate_res}")

                # Part C: Jump to Peak Golden Window button
                print("  Testing Jump to Peak Golden Window button...")
                golden_state = await eval_js("""
                (() => {
                    const btnGolden = document.getElementById('btn-open-best-spot');
                    btnGolden?.click();
                    const slider = document.getElementById('forecast-time-slider');
                    return {
                        sliderVal: Number(slider?.value || 0),
                        badgeText: document.getElementById('scrubber-mode-badge')?.textContent?.trim(),
                        timeText: document.getElementById('scrubber-time-display')?.textContent?.trim(),
                        score: document.getElementById('hero-surf-score')?.textContent?.trim()
                    };
                })()
                """)
                print(f"  Peak Golden Window state: {golden_state}")
                if golden_state.get("sliderVal") == 0 and "LIVE NOW" in (golden_state.get("badgeText") or ""):
                    # Unless hour 0 is highest score, golden window should be > 0
                    print(f"  Note: Peak golden window selected hour {golden_state.get('sliderVal')}")

                await capture_screen("stress_02_scrubbed_peak_golden_hour.png")

                # Part D: Chart.js Canvas Click Simulation
                # Test clicking on the 48h Chart tab and simulating point clicks
                print("  Testing Chart.js Canvas Click Simulation & Bidirectional Scrubbing...")
                # Switch to 48h chart tab first
                await eval_js("document.querySelector('button[data-tab=\"timeline\"]')?.click()")
                await asyncio.sleep(0.5)

                chart_click_test = await eval_js("""
                (() => {
                    const canvas = document.getElementById('forecast-chart');
                    const slider = document.getElementById('forecast-time-slider');
                    if (!canvas || !slider) return { error: 'Canvas or slider missing' };

                    // Get chart instance
                    const chart = window.Chart?.getChart(canvas);
                    if (!chart) return { error: 'No Chart.js instance attached to canvas' };

                    const testIndices = [4, 16, 28, 40];
                    const testResults = [];

                    for (const idx of testIndices) {
                        // Simulate chart onClick option handler directly with activeElements
                        chart.options.onClick(null, [{ index: idx }]);
                        
                        testResults.push({
                            targetIdx: idx,
                            sliderValue: Number(slider.value),
                            badgeText: document.getElementById('scrubber-mode-badge')?.textContent?.trim(),
                            heroWave: document.getElementById('hero-breaking-ft')?.textContent?.trim()
                        });
                    }

                    return {
                        hasChart: true,
                        pointsCount: chart.data?.labels?.length || 0,
                        testResults
                    };
                })()
                """)
                print(f"  Chart.js click synchronization results: {chart_click_test}")

                for tr in chart_click_test.get("testResults", []):
                    if tr.get("sliderValue") != tr.get("targetIdx"):
                        scrub_errors.append(f"Chart click on index {tr.get('targetIdx')} did not update slider: {tr}")

                await capture_screen("stress_03_chart_click_synchronization.png")

                # Reset to Live Now
                await eval_js("document.getElementById('btn-scrub-now')?.click()")
                await asyncio.sleep(0.3)

                stress_results["phase2_timeline_scrubbing"] = {
                    "passed": len(scrub_errors) == 0,
                    "errors": scrub_errors,
                    "sweep_count": len(sweep_sample_hours),
                    "golden_state": golden_state,
                    "chart_click_test": chart_click_test
                }

                # =========================================================================
                # STRESS TEST 3: DRAWER TOGGLE, TABS & STICKY HEADER SCROLL
                # =========================================================================
                print("\n=======================================================")
                print(">>> STRESS TEST 3: Drawer Toggle, Tabs & Sticky Table Headers")
                print("=======================================================")

                drawer_errors = []

                # Part A: Rapid drawer minimize / open toggle (20 rapid clicks)
                print("  Stress testing drawer minimize toggle (20 rapid clicks)...")
                drawer_toggle_res = await eval_js("""
                (() => {
                    const btn = document.getElementById('btn-drawer-toggle');
                    const drawer = document.getElementById('spot-drawer');
                    if (!btn || !drawer) return { error: 'Drawer elements missing' };
                    
                    const states = [];
                    for (let i = 0; i < 20; i++) {
                        btn.click();
                        states.push(drawer.classList.contains('card-minimized'));
                    }
                    // Ensure drawer is open at end
                    if (drawer.classList.contains('card-minimized')) {
                        btn.click();
                    }
                    return {
                        toggleCount: 20,
                        isMinimizedEnd: drawer.classList.contains('card-minimized'),
                        ariaExpanded: btn.getAttribute('aria-expanded')
                    };
                })()
                """)
                print(f"  Drawer toggle results: {drawer_toggle_res}")
                if drawer_toggle_res.get("isMinimizedEnd"):
                    drawer_errors.append("Drawer remained minimized at end of toggle test")

                # Part B: Rapid Tab Switching (30 cycles between Overview, 48h Chart, Sources)
                print("  Stress testing tab switching (30 rapid cycles)...")
                tab_switch_res = await eval_js("""
                (() => {
                    const tabs = ['overview', 'timeline', 'sources'];
                    const errors = [];
                    
                    for (let i = 0; i < 30; i++) {
                        const t = tabs[i % tabs.length];
                        const btn = document.querySelector(`button[data-tab="${t}"]`);
                        btn?.click();
                        
                        const pane = document.getElementById(`tab-${t}`);
                        if (!pane?.classList.contains('active')) {
                            errors.push(`Tab ${t} did not activate pane`);
                        }
                    }
                    
                    return {
                        cycles: 30,
                        errors,
                        currentTab: document.querySelector('.card-tab.active')?.getAttribute('data-tab')
                    };
                })()
                """)
                print(f"  Tab switching results: {tab_switch_res}")
                if tab_switch_res.get("errors"):
                    drawer_errors.extend(tab_switch_res.get("errors"))

                # Part C: Sources Table Sticky Header & Smooth Scrolling
                print("  Testing Sources Table Sticky Header under scroll...")
                # Ensure on sources tab
                await eval_js("document.querySelector('button[data-tab=\"sources\"]')?.click()")
                await asyncio.sleep(0.5)

                sticky_header_res = await eval_js("""
                (() => {
                    const tableWrapper = document.querySelector('.table-wrapper');
                    const scrollBody = document.getElementById('drawer-scroll-body');
                    const thead = document.querySelector('.source-comparison-table thead');
                    const thFirst = thead?.querySelector('th');
                    const tbody = document.getElementById('source-matrix-tbody');
                    const rows = tbody?.querySelectorAll('tr')?.length || 0;

                    if (!thFirst || !thead) return { error: 'Table or header th missing' };

                    const cs = window.getComputedStyle(thFirst);
                    const position = cs.position;
                    const top = cs.top;
                    const zIndex = cs.zIndex;

                    // Measure bounding rect initially
                    const r0 = thFirst.getBoundingClientRect();

                    // Scroll the container
                    const scrollTarget = tableWrapper || scrollBody;
                    scrollTarget.scrollTop = 120;

                    const rScrolled = thFirst.getBoundingClientRect();
                    const containerRect = scrollTarget.getBoundingClientRect();

                    // For position: sticky, the th should stay pinned near container top
                    const stickyPinned = Math.abs(rScrolled.top - containerRect.top) <= 15 || position === 'sticky';

                    return {
                        position,
                        top,
                        zIndex,
                        rowsCount: rows,
                        initialHeaderTop: Math.round(r0.top),
                        scrolledHeaderTop: Math.round(rScrolled.top),
                        containerTop: Math.round(containerRect.top),
                        stickyPinned
                    };
                })()
                """)
                print(f"  Sticky header inspection: {sticky_header_res}")

                if sticky_header_res.get("position") != "sticky":
                    drawer_errors.append(f"Expected th position: sticky, got: {sticky_header_res.get('position')}")
                if sticky_header_res.get("top") not in ("0px", "0"):
                    drawer_errors.append(f"Expected th top: 0px, got: {sticky_header_res.get('top')}")

                await capture_screen("stress_04_sources_sticky_header_scrolled.png")

                stress_results["phase3_drawer_and_tabs"] = {
                    "passed": len(drawer_errors) == 0,
                    "errors": drawer_errors,
                    "drawer_toggle_res": drawer_toggle_res,
                    "tab_switch_res": tab_switch_res,
                    "sticky_header_res": sticky_header_res
                }

                # =========================================================================
                # STRESS TEST 5: CONCURRENT CHAOS & VIEWPORT RESIZE STRESS TEST
                # =========================================================================
                print("\n=======================================================")
                print(">>> STRESS TEST 5: Concurrent Chaos & Viewport Resize")
                print("=======================================================")

                chaos_errors = []

                # Part A: Concurrent Interleaved Operations in rapid JS loop
                print("  Running 50 interleaved chaos operations (spot click + slider scrub + tab switch)...")
                chaos_res = await eval_js("""
                (() => {
                    const spots = ['holly_beach', 'grand_isle'];
                    const tabs = ['overview', 'timeline', 'sources'];
                    const slider = document.getElementById('forecast-time-slider');
                    let exceptions = 0;

                    for (let c = 0; c < 50; c++) {
                        try {
                            // 1. Toggle spot
                            const spotId = spots[c % spots.length];
                            document.getElementById(`pill-${spotId}`)?.click();

                            // 2. Scrub slider
                            if (slider) {
                                slider.value = String(c % 48);
                                slider.dispatchEvent(new Event('input', { bubbles: true }));
                            }

                            // 3. Switch tab
                            const tab = tabs[c % tabs.length];
                            document.querySelector(`button[data-tab="${tab}"]`)?.click();
                        } catch (e) {
                            exceptions++;
                        }
                    }

                    return {
                        iterations: 50,
                        exceptions,
                        activeSpot: document.querySelector('.spot-pill.active')?.getAttribute('data-spot-id'),
                        activeTab: document.querySelector('.card-tab.active')?.getAttribute('data-tab'),
                        sliderValue: slider?.value
                    };
                })()
                """)
                print(f"  Chaos test result: {chaos_res}")
                if chaos_res.get("exceptions", 0) > 0:
                    chaos_errors.append(f"Chaos test encountered {chaos_res.get('exceptions')} client exceptions")

                # Part B: Viewport Resize Stress Test (Mobile, Tablet, Desktop)
                print("  Testing responsive viewport resize resilience...")
                viewports = [
                    {"width": 375, "height": 667, "name": "mobile_iphone"},
                    {"width": 768, "height": 1024, "name": "tablet_ipad"},
                    {"width": 1440, "height": 900, "name": "desktop_mac"}
                ]

                for vp in viewports:
                    await send("Emulation.setDeviceMetricsOverride", {
                        "width": vp["width"],
                        "height": vp["height"],
                        "deviceScaleFactor": 1,
                        "mobile": vp["width"] < 768
                    })
                    await asyncio.sleep(0.3)
                    # Trigger spot click on mobile
                    await eval_js("document.getElementById('pill-grand_isle')?.click()")
                    await asyncio.sleep(0.2)
                    await eval_js("document.getElementById('pill-holly_beach')?.click()")
                    await asyncio.sleep(0.2)

                # Reset to default desktop viewport
                await send("Emulation.clearDeviceMetricsOverride")
                await asyncio.sleep(0.4)

                # Final memory check
                chaos_heap = await eval_js("window.performance?.memory?.usedJSHeapSize || 0")
                chaos_heap_mb = chaos_heap / (1024 * 1024) if chaos_heap else 0
                print(f"  Final JS Heap Size after Chaos & Viewport tests: {chaos_heap_mb:.2f} MB")

                stress_results["phase5_chaos_and_resize"] = {
                    "passed": len(chaos_errors) == 0,
                    "errors": chaos_errors,
                    "chaos_res": chaos_res,
                    "final_heap_mb": chaos_heap_mb
                }

                # =========================================================================
                # STRESS TEST 4: CONSOLE ERROR & LOG AUDIT
                # =========================================================================
                print("\n=======================================================")
                print(">>> STRESS TEST 4: Console Log & Exception Audit")
                print("=======================================================")

                drain_task.cancel()
                try:
                    await drain_task
                except asyncio.CancelledError:
                    pass

                # Filter console calls
                errors = [log for log in console_records if log.get("type") in ("error", "assert")]
                warnings = [log for log in console_records if log.get("type") in ("warn", "warning")]
                exceptions = exception_records
                browser_log_errors = [entry for entry in log_records if entry.get("entry", {}).get("level") in ("error", "warning")]

                print(f"  Console Log Entries: {len(console_records)}")
                print(f"  Console Errors:     {len(errors)}")
                print(f"  Console Warnings:   {len(warnings)}")
                print(f"  Exceptions Thrown:  {len(exceptions)}")
                print(f"  Browser Log Issues: {len(browser_log_errors)}")

                if errors:
                    print("  [CRITICAL] Detected Console Errors:")
                    for e in errors:
                        print(f"    - {e}")
                if warnings:
                    print("  [WARN] Detected Console Warnings:")
                    for w in warnings:
                        print(f"    - {w}")
                if exceptions:
                    print("  [CRITICAL] Detected Exceptions:")
                    for exc in exceptions:
                        print(f"    - {exc}")

                console_clean = (len(errors) == 0 and len(warnings) == 0 and len(exceptions) == 0)

                stress_results["phase4_console_audit"] = {
                    "passed": console_clean,
                    "error_count": len(errors),
                    "warning_count": len(warnings),
                    "exception_count": len(exceptions),
                    "browser_log_issues": len(browser_log_errors),
                    "errors": errors,
                    "warnings": warnings,
                    "exceptions": exceptions
                }

                # =========================================================================
                # FINAL OVERALL VERDICT
                # =========================================================================
                all_passed = (
                    stress_results["phase1_spot_switching"]["passed"] and
                    stress_results["phase2_timeline_scrubbing"]["passed"] and
                    stress_results["phase3_drawer_and_tabs"]["passed"] and
                    stress_results["phase4_console_audit"]["passed"] and
                    stress_results.get("phase5_chaos_and_resize", {}).get("passed", True)
                )

                verdict = "APPROVE" if all_passed else "REQUEST_CHANGES"

                print("\n=======================================================")
                print("              STRESS TEST FINAL VERDICT                ")
                print("=======================================================")
                print(f"Phase 1 Spot Switching:    {'PASS' if stress_results['phase1_spot_switching']['passed'] else 'FAIL'}")
                print(f"Phase 2 Timeline Scrubbing:{'PASS' if stress_results['phase2_timeline_scrubbing']['passed'] else 'FAIL'}")
                print(f"Phase 3 Drawer & Tabs:     {'PASS' if stress_results['phase3_drawer_and_tabs']['passed'] else 'FAIL'}")
                print(f"Phase 4 Console Audit:     {'PASS' if stress_results['phase4_console_audit']['passed'] else 'FAIL'}")
                print(f"Phase 5 Chaos & Resize:    {'PASS' if stress_results.get('phase5_chaos_and_resize', {}).get('passed', True) else 'FAIL'}")
                print(f"FINAL VERDICT:             {verdict}")
                print("=======================================================\n")

                report_path = os.path.join(output_dir, "stress_test_report.json")
                with open(report_path, "w") as f:
                    json.dump({
                        "verdict": verdict,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "results": stress_results
                    }, f, indent=2)
                print(f"[INFO] Stress test report saved to: {report_path}")

                return verdict == "APPROVE"

        success = asyncio.run(stress_session())
        return success

    finally:
        proc.terminate()
        proc.wait()
        shutil.rmtree(temp_profile, ignore_errors=True)
        print("[INFO] Headless Chrome stress session finished.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adversarial Stress Tester for Louisiana Surf Finder")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="Target server URL")
    parser.add_argument("--port", type=int, default=9336, help="CDP port")
    parser.add_argument("--output", default=".agents/teamwork/challenger_gen2_2/stress_run", help="Output directory")
    args = parser.parse_args()

    ok = run_stress_test(server_url=args.server, cdp_port=args.port, output_dir=args.output)
    sys.exit(0 if ok else 1)
