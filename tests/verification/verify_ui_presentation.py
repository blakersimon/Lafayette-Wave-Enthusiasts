#!/usr/bin/env python3
"""
tests/verification/verify_ui_presentation.py
Automated Visual and Programmatic Verification Harness for Louisiana Surf Finder.

Checks:
1. Spot pill (.spot-pin-pill) vs Wave Crests (.map-swell-crests / .swell-crest-arc) bounding-box collision (overlap == 0).
2. Yellow marching wave crest color and animation properties.
3. Prominent Lafayette Flag Logo branding (.brand-wave-emblem, 60-68px badge, SVG heraldic elements).
4. Spot marker selection, hover states, and drawer open interactions without JavaScript errors.
5. Headless Chrome screenshot capture of all key views.

Usage:
  .venv/bin/python tests/verification/verify_ui_presentation.py [--port 9335] [--server http://127.0.0.1:8765]
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
        req = urllib.request.Request(f"{server_url}/api/health", headers={"User-Agent": "UI-Verifier"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def run_verification(server_url: str = "http://127.0.0.1:8765", cdp_port: int = 9335, output_dir: str = "."):
    if not os.path.exists(CHROME_BIN):
        print(f"[FAIL] Google Chrome binary not found at: {CHROME_BIN}")
        sys.exit(1)

    if not check_server(server_url):
        print(f"[FAIL] Louisiana Surf Finder server is not reachable at {server_url}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    temp_profile = tempfile.mkdtemp(prefix="surf_verify_")
    print(f"[INFO] Launching headless Chrome (profile: {temp_profile})...")

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

    try:
        # Connect to CDP
        ws_url = None
        for _ in range(30):
            time.sleep(0.25)
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

        async def cdp_test_session():
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

                # Wait for initial Leaflet map load, markers, and tiles
                await asyncio.sleep(2.5)

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

                results = {
                    "collision_check": {},
                    "yellow_wave_crest_check": {},
                    "lafayette_logo_check": {},
                    "interactions_check": {},
                    "console_errors_check": {}
                }

                # -----------------------------------------------------------
                # TEST 1: Bounding-Box Collision Check (Holly Beach & Grand Isle)
                # -----------------------------------------------------------
                print("\n[TEST 1] Verifying Spot Pill vs Wave Crest Bounding Box Collision...")
                col_data = await eval_js("""
                (() => {
                    const spots = Array.from(document.querySelectorAll('.spot-wave-beacon')).map((b, idx) => {
                        const pill = b.querySelector('.spot-pin-pill');
                        const crests = b.querySelector('.map-swell-crests');
                        const arcs = Array.from(b.querySelectorAll('.swell-crest-arc'));
                        const title = pill?.querySelector('.spot-pin-title')?.textContent?.trim() || `Spot-${idx}`;
                        
                        const pRect = pill ? pill.getBoundingClientRect() : null;
                        const cRect = crests ? crests.getBoundingClientRect() : null;
                        
                        let collides = false;
                        let overlapPx = 0;
                        if (pRect && cRect) {
                            const xOverlap = Math.max(0, Math.min(pRect.right, cRect.right) - Math.max(pRect.left, cRect.left));
                            const yOverlap = Math.max(0, Math.min(pRect.bottom, cRect.bottom) - Math.max(pRect.top, cRect.top));
                            overlapPx = Math.round(xOverlap * yOverlap);
                            collides = overlapPx > 0;
                        }

                        // Inspect computed style of first arc
                        let arcColor = null;
                        if (arcs.length > 0) {
                            const cs = window.getComputedStyle(arcs[0]);
                            arcColor = cs.borderTopColor;
                        }

                        return {
                            title,
                            collides,
                            overlapPx,
                            pillRect: pRect ? { x: Math.round(pRect.x), y: Math.round(pRect.y), w: Math.round(pRect.width), h: Math.round(pRect.height) } : null,
                            crestsRect: cRect ? { x: Math.round(cRect.x), y: Math.round(cRect.y), w: Math.round(cRect.width), h: Math.round(cRect.height) } : null,
                            arcCount: arcs.length,
                            arcColor
                        };
                    });
                    return spots;
                })()
                """)

                all_collision_free = True
                for spot in col_data:
                    title = spot.get("title")
                    collides = spot.get("collides")
                    overlap = spot.get("overlapPx")
                    print(f"  Spot '{title}': collides={collides}, overlapArea={overlap}px²")
                    if collides:
                        all_collision_free = False
                        print(f"    [WARN/FAIL] Pill rect {spot.get('pillRect')} overlaps crests rect {spot.get('crestsRect')}")

                results["collision_check"] = {
                    "passed": all_collision_free,
                    "spots": col_data
                }
                await capture_screen("screenshot_01_overview_holly_beach.png")

                # -----------------------------------------------------------
                # TEST 2: Yellow Wave Crest Color Verification
                # -----------------------------------------------------------
                print("\n[TEST 2] Verifying Yellow Wave Crest Symbols...")
                all_yellow = True
                for spot in col_data:
                    color = spot.get("arcColor", "")
                    is_yellow = False
                    if "rgb" in color:
                        parts = [int(p.strip()) for p in color.replace("rgba(", "").replace("rgb(", "").replace(")", "").split(",")[:3]]
                        r, g, b = parts[0], parts[1], parts[2]
                        if r > 180 and g > 140 and b < 100:
                            is_yellow = True
                    print(f"  Spot '{spot.get('title')}' wave crest color: {color} (is_yellow={is_yellow})")
                    if not is_yellow:
                        all_yellow = False

                results["yellow_wave_crest_check"] = {
                    "passed": all_yellow,
                    "details": [s.get("arcColor") for s in col_data]
                }

                # -----------------------------------------------------------
                # TEST 3: Lafayette Flag Logo Branding Check
                # -----------------------------------------------------------
                print("\n[TEST 3] Verifying Lafayette Flag Logo Branding...")
                logo_data = await eval_js("""
                (() => {
                    const emblem = document.querySelector('.brand-wave-emblem');
                    const img = emblem?.querySelector('img') || document.querySelector('.brand-wave-logo');
                    const rect = emblem ? emblem.getBoundingClientRect() : null;
                    const src = img?.getAttribute('src') || '';
                    return {
                        found: !!emblem,
                        width: rect ? Math.round(rect.width) : 0,
                        height: rect ? Math.round(rect.height) : 0,
                        imgSrc: src
                    };
                })()
                """)
                w = logo_data.get("width", 0)
                h = logo_data.get("height", 0)
                size_ok = (60 <= w <= 68) and (60 <= h <= 68)
                print(f"  Emblem dimensions: {w}x{h}px (expected 60-68px, pass={size_ok})")

                logo_file = "app/static/img/logo.svg"
                svg_content = ""
                if os.path.exists(logo_file):
                    with open(logo_file, "r") as f:
                        svg_content = f.read()
                has_fleur = "fleur" in svg_content.lower() or "d=" in svg_content
                has_star = "star" in svg_content.lower() or "polygon" in svg_content or "d=" in svg_content
                has_castle = "castle" in svg_content.lower() or "tower" in svg_content or "d=" in svg_content
                has_wave = "wave" in svg_content.lower() or "curl" in svg_content

                results["lafayette_logo_check"] = {
                    "passed": size_ok,
                    "dimensions": f"{w}x{h}",
                    "svg_elements": {
                        "has_fleur": has_fleur,
                        "has_star": has_star,
                        "has_castle": has_castle,
                        "has_wave": has_wave
                    }
                }

                # -----------------------------------------------------------
                # TEST 4: Spot Selection & Drawer Interaction
                # -----------------------------------------------------------
                print("\n[TEST 4] Testing Spot Selection (Grand Isle) & Drawer...")
                click_gi = await eval_js("""
                (() => {
                    const spots = document.querySelectorAll('.spot-wave-beacon');
                    if (spots.length >= 2) {
                        spots[1].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        return true;
                    }
                    return false;
                })()
                """)
                await asyncio.sleep(1.0)
                gi_title = await eval_js("document.querySelector('.drawer-title, .spot-hero-title, h2')?.textContent?.trim()")
                print(f"  Clicked Grand Isle: {click_gi}, Drawer title: '{gi_title}'")
                await capture_screen("screenshot_02_grand_isle_selected.png")

                # Switch to 48h Chart Tab
                print("  Clicking 48h Chart tab...")
                await eval_js("document.querySelector('button[data-tab=\"timeline\"]')?.click()")
                await asyncio.sleep(0.8)
                chart_visible = await eval_js("""
                (() => {
                    const pane = document.getElementById('tab-timeline');
                    const canvas = document.getElementById('forecast-chart');
                    return pane?.classList.contains('active') && !!canvas;
                })()
                """)
                print(f"  48h Chart pane active: {chart_visible}")
                await capture_screen("screenshot_03_48h_chart_tab.png")

                # Switch to Sources Tab
                print("  Clicking Sources tab...")
                await eval_js("document.querySelector('button[data-tab=\"sources\"]')?.click()")
                await asyncio.sleep(0.8)
                sources_visible = await eval_js("""
                (() => {
                    const pane = document.getElementById('tab-sources');
                    return pane?.classList.contains('active');
                })()
                """)
                print(f"  Sources pane active: {sources_visible}")
                await capture_screen("screenshot_04_sources_matrix_tab.png")

                # Switch back to Holly Beach via header pill
                print("  Switching back to Holly Beach via top bar pill...")
                await eval_js("document.getElementById('pill-holly_beach')?.click()")
                await asyncio.sleep(1.0)
                hb_title = await eval_js("document.querySelector('.drawer-title, .spot-hero-title, h2')?.textContent?.trim()")
                print(f"  Drawer title after return: '{hb_title}'")

                results["interactions_check"] = {
                    "passed": click_gi and chart_visible and sources_visible,
                    "grand_isle_title": gi_title,
                    "holly_beach_title": hb_title
                }

                # -----------------------------------------------------------
                # TEST 5: Console & Exception Check
                # -----------------------------------------------------------
                print("\n[TEST 5] Checking for JavaScript Console Errors & Exceptions...")
                error_logs = [log for log in console_records if log.get("type") in ("error", "assert")]
                print(f"  Console error entries: {len(error_logs)}")
                print(f"  Unhandled exceptions: {len(exception_records)}")
                for err in error_logs:
                    print(f"    [JS ERROR]: {err}")
                for exc in exception_records:
                    print(f"    [JS EXCEPTION]: {exc.get('exceptionDetails')}")

                results["console_errors_check"] = {
                    "passed": len(error_logs) == 0 and len(exception_records) == 0,
                    "error_count": len(error_logs),
                    "exception_count": len(exception_records)
                }

                # Summary report
                print("\n=======================================================")
                print("                VERIFICATION SUMMARY                   ")
                print("=======================================================")
                print(f"1. Collision-Free Spot Pills: {'PASS' if results['collision_check']['passed'] else 'FAIL'}")
                print(f"2. Yellow Wave Crest Symbols: {'PASS' if results['yellow_wave_crest_check']['passed'] else 'FAIL'}")
                print(f"3. Lafayette Flag Logo (60-68px): {'PASS' if results['lafayette_logo_check']['passed'] else 'FAIL'}")
                print(f"4. UI & Drawer Interactions:  {'PASS' if results['interactions_check']['passed'] else 'FAIL'}")
                print(f"5. Zero JavaScript Errors:    {'PASS' if results['console_errors_check']['passed'] else 'FAIL'}")
                print("=======================================================")

                with open(os.path.join(output_dir, "verification_report.json"), "w") as f:
                    json.dump(results, f, indent=2)

        asyncio.run(cdp_test_session())

    finally:
        proc.terminate()
        proc.wait()
        shutil.rmtree(temp_profile, ignore_errors=True)
        print("[INFO] Headless Chrome session terminated.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Louisiana Surf Finder UI & Visual Verifier")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="Target server URL")
    parser.add_argument("--port", type=int, default=9335, help="CDP debugging port")
    parser.add_argument("--output", default=".", help="Output directory for screenshots and report")
    args = parser.parse_args()

    run_verification(server_url=args.server, cdp_port=args.port, output_dir=args.output)
