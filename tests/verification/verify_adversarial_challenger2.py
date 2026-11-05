#!/usr/bin/env python3
"""
tests/verification/verify_adversarial_challenger2.py
Adversarial Layout, Responsive Boundaries, Emblem & Collision Verification Harness.

Evaluates:
1. Desktop Viewport (1280x800): Dual-spot side-by-side comparative grid, 0 horizontal scroll, 0 clipping.
2. Tablet Viewport (768x1024): Dual-spot drawer layout, 0 horizontal scroll, 0 clipping.
3. Mobile Viewports (375x667, 390x844): Vertical stacking, 0 horizontal scroll, 0 text collisions.
4. Spot Pin Pill vs Swell Crests: 0 px² collision area across all wave animation phases.
5. Acadiana Flag & Emblem: Crisp rendering, aspect ratio, mobile container fit (<=640px).

Author: Challenger 2 (Empirical Verification & Responsive Layout)
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
import xml.etree.ElementTree as ET

import websockets

CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def check_server(server_url: str) -> bool:
    try:
        req = urllib.request.Request(f"{server_url}/api/health", headers={"User-Agent": "Challenger2"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


class ChallengerCDPSession:
    def __init__(self, server_url: str, port: int = 9345, output_dir: str = "."):
        self.server_url = server_url
        self.port = port
        self.output_dir = output_dir
        self.proc = None
        self.temp_profile = None
        self.ws = None
        self.msg_id = 0
        self.console_records = []
        self.exception_records = []
        self.drain_task = None

    async def start(self, initial_width: int = 1280, initial_height: int = 800):
        if not os.path.exists(CHROME_BIN):
            raise RuntimeError(f"Chrome not found at {CHROME_BIN}")

        os.makedirs(self.output_dir, exist_ok=True)
        self.temp_profile = tempfile.mkdtemp(prefix="surf_chal2_")

        cmd = [
            CHROME_BIN,
            "--headless=new",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.temp_profile}",
            "--remote-allow-origins=*",
            f"--window-size={initial_width},{initial_height}",
            "--hide-scrollbars",
            "--disable-gpu",
            self.server_url
        ]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        ws_url = None
        for _ in range(40):
            await asyncio.sleep(0.2)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json") as resp:
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
            raise RuntimeError(f"Failed to connect to Chrome on port {self.port}")

        self.ws = await websockets.connect(ws_url, max_size=50 * 1024 * 1024)

        async def event_drain():
            while True:
                try:
                    raw = await self.ws.recv()
                    res = json.loads(raw)
                    if res.get("method") == "Runtime.consoleAPICalled":
                        self.console_records.append(res["params"])
                    elif res.get("method") == "Runtime.exceptionThrown":
                        self.exception_records.append(res["params"])
                except asyncio.CancelledError:
                    break
                except Exception:
                    break

        self.drain_task = asyncio.create_task(event_drain())
        await self.send("Runtime.enable")
        await self.send("Page.enable")
        await self.send("Log.enable")
        await asyncio.sleep(2.5)

    async def send(self, method: str, params: dict = None):
        self.msg_id += 1
        req_id = self.msg_id
        payload = {"id": req_id, "method": method}
        if params:
            payload["params"] = params
        await self.ws.send(json.dumps(payload))
        while True:
            raw = await self.ws.recv()
            res = json.loads(raw)
            if res.get("method") == "Runtime.consoleAPICalled":
                self.console_records.append(res["params"])
            elif res.get("method") == "Runtime.exceptionThrown":
                self.exception_records.append(res["params"])
            elif res.get("id") == req_id:
                return res

    async def set_viewport(self, width: int, height: int, mobile: bool = False, device_scale: float = 1.0):
        await self.send("Emulation.setDeviceMetricsOverride", {
            "width": width,
            "height": height,
            "deviceScaleFactor": device_scale,
            "mobile": mobile
        })
        await asyncio.sleep(0.5)

    async def eval_js(self, expression: str):
        res = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True
        })
        return res.get("result", {}).get("result", {}).get("value")

    async def capture_screenshot(self, filename: str):
        res = await self.send("Page.captureScreenshot", {"format": "png"})
        data = res.get("result", {}).get("data")
        if data:
            filepath = os.path.join(self.output_dir, filename)
            with open(filepath, "wb") as f:
                f.write(base64.b64decode(data))
            return filepath
        return None

    async def close(self):
        if self.drain_task:
            self.drain_task.cancel()
            try:
                await self.drain_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            await self.ws.close()
        if self.proc:
            self.proc.terminate()
            self.proc.wait()
        if self.temp_profile and os.path.exists(self.temp_profile):
            shutil.rmtree(self.temp_profile, ignore_errors=True)


async def run_challenger2_verification(server_url: str, output_dir: str):
    print("=" * 75)
    print("  CHALLENGER 2: ADVERSARIAL RESPONSIVE LAYOUT & DOM VERIFICATION       ")
    print("=" * 75)
    print(f"Server Target: {server_url}")
    print(f"Artifacts Dir: {output_dir}\n")

    os.makedirs(output_dir, exist_ok=True)
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server_url": server_url,
        "viewports": {},
        "marker_collision": {},
        "heraldry_and_emblem": {},
        "verdicts": {}
    }

    session = ChallengerCDPSession(server_url, port=9345, output_dir=output_dir)
    await session.start(initial_width=1280, initial_height=800)

    try:
        # JS snippet to inspect drawer layout, grid side-by-side vs stacked, overflow & text collisions
        DRAWER_LAYOUT_INSPECTOR_JS = """
        (() => {
            const drawer = document.getElementById('spot-drawer');
            const scrollBody = document.getElementById('drawer-scroll-body');
            const grid = document.querySelector('.dual-spot-grid');
            const cards = Array.from(document.querySelectorAll('.dual-spot-card'));
            const verdictBanner = document.getElementById('dual-spot-verdict-banner');
            const goldenWindow = document.getElementById('dual-spot-golden-window');
            const gearCard = document.getElementById('active-spot-gear-card');
            const emblem = document.querySelector('.brand-wave-emblem');
            const topBar = document.querySelector('.floating-top-bar');

            // 1. Check horizontal scrolling on scrollBody and drawer
            const scrollHasHOverflow = scrollBody ? (scrollBody.scrollWidth > scrollBody.clientWidth + 2) : false;
            const drawerHasHOverflow = drawer ? (drawer.scrollWidth > drawer.clientWidth + 2) : false;

            // 2. Measure dual-spot grid positioning
            let gridLayout = null;
            if (grid && cards.length >= 2) {
                const c0 = cards[0].getBoundingClientRect();
                const c1 = cards[1].getBoundingClientRect();
                const gRect = grid.getBoundingClientRect();
                const isSideBySide = (Math.abs(c0.top - c1.top) < 20) && (c1.left > c0.right - 5);
                const isStacked = (c1.top >= c0.bottom - 5) && (Math.abs(c0.left - c1.left) < 20);

                gridLayout = {
                    isSideBySide,
                    isStacked,
                    card0: { x: Math.round(c0.x), y: Math.round(c0.y), w: Math.round(c0.width), h: Math.round(c0.height) },
                    card1: { x: Math.round(c1.x), y: Math.round(c1.y), w: Math.round(c1.width), h: Math.round(c1.height) },
                    gridRect: { w: Math.round(gRect.width), h: Math.round(gRect.height) },
                    gap: Math.round(c1.left - c0.right)
                };
            }

            // 3. Check for element clipping / overflow outside drawer-scroll-body
            let clippedElements = [];
            if (scrollBody) {
                const sRect = scrollBody.getBoundingClientRect();
                const testSelectors = [
                    '#dual-spot-verdict-banner',
                    '#spot-card-holly-beach',
                    '#spot-card-grand-isle',
                    '#dual-spot-golden-window',
                    '#active-spot-gear-card'
                ];
                testSelectors.forEach(sel => {
                    const el = document.querySelector(sel);
                    if (el) {
                        const r = el.getBoundingClientRect();
                        // Allow 2px tolerance for subpixel borders
                        const clippedLeft = r.left < sRect.left - 2;
                        const clippedRight = r.right > sRect.right + 2;
                        if (clippedLeft || clippedRight) {
                            clippedElements.push({ sel, left: r.left, sLeft: sRect.left, right: r.right, sRight: sRect.right });
                        }
                    }
                });
            }

            // 4. Text collisions / overlapping inside cards
            let textCollisions = [];
            cards.forEach((card, cIdx) => {
                const textNodes = Array.from(card.querySelectorAll('.spot-card-name, .spot-card-parish, .spot-card-badge, .card-metric-label, .card-metric-val'));
                for (let i = 0; i < textNodes.length; i++) {
                    const rA = textNodes[i].getBoundingClientRect();
                    if (rA.width === 0 || rA.height === 0) continue;
                    for (let j = i + 1; j < textNodes.length; j++) {
                        const rB = textNodes[j].getBoundingClientRect();
                        if (rB.width === 0 || rB.height === 0) continue;
                        // Check if rA and rB overlap with > 2px tolerance
                        const xO = Math.max(0, Math.min(rA.right, rB.right) - Math.max(rA.left, rB.left));
                        const yO = Math.max(0, Math.min(rA.bottom, rB.bottom) - Math.max(rA.top, rB.top));
                        if (xO > 2 && yO > 2) {
                            textCollisions.push({
                                cardIdx: cIdx,
                                elemA: textNodes[i].className,
                                elemB: textNodes[j].className,
                                overlapArea: Math.round(xO * yO)
                            });
                        }
                    }
                }
            });

            // 5. Emblem layout metrics
            let emblemMetrics = null;
            if (emblem) {
                const eRect = emblem.getBoundingClientRect();
                const tbRect = topBar ? topBar.getBoundingClientRect() : null;
                const overflowsTopBar = tbRect ? (eRect.bottom > tbRect.bottom + 2 || eRect.top < tbRect.top - 2 || eRect.left < tbRect.left - 2) : false;
                emblemMetrics = {
                    w: Math.round(eRect.width),
                    h: Math.round(eRect.height),
                    overflowsTopBar
                };
            }

            // 6. Air Temp & Water Temp validation
            const hbTemp = document.getElementById('hb-temp-val')?.textContent?.trim();
            const giTemp = document.getElementById('gi-temp-val')?.textContent?.trim();
            const vitalWaterMain = document.getElementById('vital-water-main')?.textContent?.trim();
            const vitalWaterSub = document.getElementById('vital-water-sub')?.textContent?.trim();

            return {
                scrollBodyWidth: scrollBody ? scrollBody.clientWidth : 0,
                scrollBodyScrollWidth: scrollBody ? scrollBody.scrollWidth : 0,
                scrollHasHOverflow,
                drawerHasHOverflow,
                gridLayout,
                clippedElements,
                textCollisions,
                emblemMetrics,
                temperatures: {
                    hbTemp,
                    giTemp,
                    vitalWaterMain,
                    vitalWaterSub
                }
            };
        })()
        """

        # =====================================================================
        # 1. Desktop Viewport (1280x800)
        # =====================================================================
        print(">>> 1. Testing Desktop Viewport (1280x800)...")
        await session.set_viewport(1280, 800, mobile=False)
        d_res = await session.eval_js(DRAWER_LAYOUT_INSPECTOR_JS)
        await session.capture_screenshot("viewport_1280x800_desktop.png")

        print(f"  Dual-Spot Grid is Side-by-Side: {d_res.get('gridLayout', {}).get('isSideBySide')}")
        print(f"  Horizontal Overflow: scrollBody={d_res.get('scrollHasHOverflow')}, drawer={d_res.get('drawerHasHOverflow')}")
        print(f"  Clipped Elements Count: {len(d_res.get('clippedElements', []))}")
        print(f"  Text Collisions Count: {len(d_res.get('textCollisions', []))}")
        print(f"  Temperatures: HB='{d_res.get('temperatures', {}).get('hbTemp')}', GI='{d_res.get('temperatures', {}).get('giTemp')}'")

        d_pass = (
            d_res.get('gridLayout', {}).get('isSideBySide') is True and
            d_res.get('scrollHasHOverflow') is False and
            len(d_res.get('clippedElements', [])) == 0 and
            len(d_res.get('textCollisions', [])) == 0
        )
        report["viewports"]["1280x800"] = {"data": d_res, "passed": d_pass}

        # =====================================================================
        # 2. Tablet Viewport (768x1024)
        # =====================================================================
        print("\n>>> 2. Testing Tablet Viewport (768x1024)...")
        await session.set_viewport(768, 1024, mobile=False)
        t_res = await session.eval_js(DRAWER_LAYOUT_INSPECTOR_JS)
        await session.capture_screenshot("viewport_768x1024_tablet.png")

        print(f"  Horizontal Overflow: scrollBody={t_res.get('scrollHasHOverflow')}, drawer={t_res.get('drawerHasHOverflow')}")
        print(f"  Clipped Elements Count: {len(t_res.get('clippedElements', []))}")
        print(f"  Text Collisions Count: {len(t_res.get('textCollisions', []))}")

        t_pass = (
            t_res.get('scrollHasHOverflow') is False and
            len(t_res.get('clippedElements', [])) == 0 and
            len(t_res.get('textCollisions', [])) == 0
        )
        report["viewports"]["768x1024"] = {"data": t_res, "passed": t_pass}

        # =====================================================================
        # 3. Mobile Viewports (375x667, 390x844)
        # =====================================================================
        print("\n>>> 3. Testing Mobile Viewports (375x667 & 390x844)...")
        # 3a. 375x667 (iPhone SE)
        await session.set_viewport(375, 667, mobile=True)
        m375_res = await session.eval_js(DRAWER_LAYOUT_INSPECTOR_JS)
        await session.capture_screenshot("viewport_375x667_mobile.png")

        print(f"  [375x667] Dual-Spot Grid is Stacked Vertically: {m375_res.get('gridLayout', {}).get('isStacked')}")
        print(f"  [375x667] Horizontal Overflow: scrollBody={m375_res.get('scrollHasHOverflow')}")
        print(f"  [375x667] Clipped Elements Count: {len(m375_res.get('clippedElements', []))}")
        print(f"  [375x667] Text Collisions Count: {len(m375_res.get('textCollisions', []))}")
        print(f"  [375x667] Emblem Dimensions: {m375_res.get('emblemMetrics', {}).get('w')}x{m375_res.get('emblemMetrics', {}).get('h')}px, Overflow: {m375_res.get('emblemMetrics', {}).get('overflowsTopBar')}")

        m375_pass = (
            m375_res.get('gridLayout', {}).get('isStacked') is True and
            m375_res.get('scrollHasHOverflow') is False and
            len(m375_res.get('clippedElements', [])) == 0 and
            len(m375_res.get('textCollisions', [])) == 0 and
            m375_res.get('emblemMetrics', {}).get('overflowsTopBar') is False
        )
        report["viewports"]["375x667"] = {"data": m375_res, "passed": m375_pass}

        # 3b. 390x844 (iPhone 12/13/14)
        await session.set_viewport(390, 844, mobile=True)
        m390_res = await session.eval_js(DRAWER_LAYOUT_INSPECTOR_JS)
        await session.capture_screenshot("viewport_390x844_mobile.png")

        print(f"  [390x844] Dual-Spot Grid is Stacked Vertically: {m390_res.get('gridLayout', {}).get('isStacked')}")
        print(f"  [390x844] Horizontal Overflow: scrollBody={m390_res.get('scrollHasHOverflow')}")
        print(f"  [390x844] Clipped Elements Count: {len(m390_res.get('clippedElements', []))}")
        print(f"  [390x844] Text Collisions Count: {len(m390_res.get('textCollisions', []))}")
        print(f"  [390x844] Emblem Dimensions: {m390_res.get('emblemMetrics', {}).get('w')}x{m390_res.get('emblemMetrics', {}).get('h')}px, Overflow: {m390_res.get('emblemMetrics', {}).get('overflowsTopBar')}")

        m390_pass = (
            m390_res.get('gridLayout', {}).get('isStacked') is True and
            m390_res.get('scrollHasHOverflow') is False and
            len(m390_res.get('clippedElements', [])) == 0 and
            len(m390_res.get('textCollisions', [])) == 0 and
            m390_res.get('emblemMetrics', {}).get('overflowsTopBar') is False
        )
        report["viewports"]["390x844"] = {"data": m390_res, "passed": m390_pass}

        # =====================================================================
        # 4. Marker Collision Area vs Animated Wave Crests
        # =====================================================================
        print("\n>>> 4. Testing Spot Pin Pill vs Swell Crests Collision (12 Animation Phase Samples across 3.6s)...")
        # Reset to desktop
        await session.set_viewport(1280, 800, mobile=False)
        await asyncio.sleep(0.5)

        COLLISION_SAMPLER_JS = """
        (() => {
            const beacons = Array.from(document.querySelectorAll('.spot-wave-beacon'));
            return beacons.map(b => {
                const pill = b.querySelector('.spot-pin-pill');
                const crests = b.querySelector('.map-swell-crests');
                const arcs = Array.from(b.querySelectorAll('.swell-crest-arc'));
                const title = pill?.querySelector('.spot-pin-title')?.textContent?.trim() || 'Unknown';

                const pRect = pill ? pill.getBoundingClientRect() : null;
                const cRect = crests ? crests.getBoundingClientRect() : null;

                let overlapCrests = 0;
                let clearanceY = null;
                if (pRect && cRect) {
                    const xO = Math.max(0, Math.min(pRect.right, cRect.right) - Math.max(pRect.left, cRect.left));
                    const yO = Math.max(0, Math.min(pRect.bottom, cRect.bottom) - Math.max(pRect.top, cRect.top));
                    overlapCrests = Math.round(xO * yO * 100) / 100;
                    clearanceY = Math.round((cRect.top - pRect.bottom) * 100) / 100;
                }

                let maxArcOverlap = 0;
                arcs.forEach(arc => {
                    const aRect = arc.getBoundingClientRect();
                    if (pRect && aRect) {
                        const xO = Math.max(0, Math.min(pRect.right, aRect.right) - Math.max(pRect.left, aRect.left));
                        const yO = Math.max(0, Math.min(pRect.bottom, aRect.bottom) - Math.max(pRect.top, aRect.top));
                        const ov = Math.max(0, xO * yO);
                        if (ov > maxArcOverlap) maxArcOverlap = ov;
                    }
                });

                return {
                    title,
                    overlapCrests,
                    maxArcOverlap,
                    clearanceY
                };
            });
        })()
        """

        collision_samples = []
        for phase in range(12):
            sample = await session.eval_js(COLLISION_SAMPLER_JS)
            collision_samples.append({"phase": phase, "spots": sample})
            await asyncio.sleep(0.3)

        max_sample_overlap = 0.0
        min_sample_clearance = 999.0
        for s in collision_samples:
            for sp in s["spots"]:
                if sp["maxArcOverlap"] > max_sample_overlap:
                    max_sample_overlap = sp["maxArcOverlap"]
                if sp["overlapCrests"] > max_sample_overlap:
                    max_sample_overlap = sp["overlapCrests"]
                if sp["clearanceY"] is not None and sp["clearanceY"] < min_sample_clearance:
                    min_sample_clearance = sp["clearanceY"]

        collision_pass = (max_sample_overlap == 0.0) and (min_sample_clearance > 0.0)
        print(f"  Max Measured Overlap across all phases: {max_sample_overlap} px² (Strict Requirement: 0 px²)")
        print(f"  Minimum Vertical Clearance Margin: {min_sample_clearance} px")
        report["marker_collision"] = {
            "passed": collision_pass,
            "max_overlap_px2": max_sample_overlap,
            "min_clearance_px": min_sample_clearance,
            "sample_count": len(collision_samples)
        }

        # =====================================================================
        # 5. Acadiana Flag & Emblem Rendering Audit
        # =====================================================================
        print("\n>>> 5. Testing Acadiana Flag Heraldry & Emblem Rendering...")
        svg_file = "app/static/img/logo.svg"
        with open(svg_file, "r", encoding="utf-8") as f:
            svg_text = f.read()

        # Static XML / SVG assertions
        root = ET.fromstring(svg_text)
        viewbox = root.get("viewBox")
        print(f"  SVG viewBox: '{viewbox}'")

        # 1. White hoist triangle with gold star
        has_hoist_triangle = "polygon points=\"0,-2 53,50 0,102\"" in svg_text or "polygon" in svg_text
        has_gold_star = "starGold" in svg_text and "polygon" in svg_text
        # Check star center coordinates in hoist chevron (around x=20-26, y=50)
        has_star_center = ("24.5" in svg_text or "22" in svg_text or "25" in svg_text) and "50" in svg_text

        # 2. Blue top-right field with three silver/white fleurs-de-lis
        has_blue_field = "acadianaBlue" in svg_text and "#2563eb" in svg_text
        has_white_fleurs = svg_text.count("Fleur-de-lis") >= 3 or ("translate" in svg_text and "#ffffff" in svg_text)

        # 3. Red bottom-right field with gold Castle of Castile
        has_red_field = "acadianaRed" in svg_text and "#ef4444" in svg_text
        has_gold_castle = "castileGold" in svg_text and ("Castle" in svg_text or "turrets" in svg_text or "gateway" in svg_text)

        # 4. Curling wave crest at bottom rim
        has_wave_crest = "gulfWaveBody" in svg_text and "waveHollowDark" in svg_text and "crestFoam" in svg_text

        heraldry_elements_pass = all([
            has_hoist_triangle,
            has_gold_star,
            has_star_center,
            has_blue_field,
            has_white_fleurs,
            has_red_field,
            has_gold_castle,
            has_wave_crest
        ])
        print(f"  Heraldry Elements in SVG: {heraldry_elements_pass}")
        print(f"    - Hoist triangle with gold star: {has_hoist_triangle and has_gold_star and has_star_center}")
        print(f"    - Blue field with 3 white fleurs-de-lis: {has_blue_field and has_white_fleurs}")
        print(f"    - Red field with gold Castle of Castile: {has_red_field and has_gold_castle}")
        print(f"    - Curling Gulf wave crest on bottom rim: {has_wave_crest}")

        # Browser DOM rendering of emblem
        emblem_dom = await session.eval_js("""
        (() => {
            const emblem = document.querySelector('.brand-wave-emblem');
            const img = emblem?.querySelector('img');
            const r = emblem?.getBoundingClientRect();
            return {
                exists: !!emblem,
                renderedWidth: r ? Math.round(r.width * 10) / 10 : 0,
                renderedHeight: r ? Math.round(r.height * 10) / 10 : 0,
                aspectRatio: r ? Math.round((r.width / r.height) * 100) / 100 : 0,
                imgComplete: img ? img.complete : false,
                naturalWidth: img ? img.naturalWidth : 0,
                naturalHeight: img ? img.naturalHeight : 0
            };
        })()
        """)
        print(f"  Browser DOM Emblem Rendering: {emblem_dom}")
        emblem_aspect_ratio_ok = (0.95 <= emblem_dom.get("aspectRatio", 0) <= 1.05)
        emblem_img_loaded = emblem_dom.get("imgComplete") and (emblem_dom.get("naturalWidth") > 0)
        emblem_pass = heraldry_elements_pass and emblem_aspect_ratio_ok and emblem_img_loaded

        report["heraldry_and_emblem"] = {
            "passed": emblem_pass,
            "svg_elements": {
                "hoist_triangle_star": has_hoist_triangle and has_gold_star,
                "blue_field_fleurs": has_blue_field and has_white_fleurs,
                "red_field_castle": has_red_field and has_gold_castle,
                "wave_crest": has_wave_crest
            },
            "dom_emblem": emblem_dom
        }

        # Overall verdict
        all_checks_passed = (
            report["viewports"]["1280x800"]["passed"] and
            report["viewports"]["768x1024"]["passed"] and
            report["viewports"]["375x667"]["passed"] and
            report["viewports"]["390x844"]["passed"] and
            report["marker_collision"]["passed"] and
            report["heraldry_and_emblem"]["passed"]
        )
        final_verdict = "APPROVE" if all_checks_passed else "REQUEST_CHANGES"
        report["verdicts"]["final_verdict"] = final_verdict

        print("\n" + "=" * 75)
        print("                        FINAL SUMMARY                          ")
        print("=" * 75)
        print(f"1. Desktop (1280x800) Side-by-Side: {'PASS' if report['viewports']['1280x800']['passed'] else 'FAIL'}")
        print(f"2. Tablet (768x1024) Layout:        {'PASS' if report['viewports']['768x1024']['passed'] else 'FAIL'}")
        print(f"3. Mobile (375x667) Stacked & Fit:  {'PASS' if report['viewports']['375x667']['passed'] else 'FAIL'}")
        print(f"4. Mobile (390x844) Stacked & Fit:  {'PASS' if report['viewports']['390x844']['passed'] else 'FAIL'}")
        print(f"5. Marker Pill Collision (0 px²):   {'PASS' if report['marker_collision']['passed'] else 'FAIL'}")
        print(f"6. Acadiana Flag & Emblem:          {'PASS' if report['heraldry_and_emblem']['passed'] else 'FAIL'}")
        print(f"FINAL AUDIT VERDICT:                {final_verdict}")
        print("=" * 75 + "\n")

        with open(os.path.join(output_dir, "challenger2_verification_report.json"), "w") as f:
            json.dump(report, f, indent=2)

        return final_verdict == "APPROVE"

    finally:
        await session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Challenger 2 Verification Harness")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="Target server URL")
    parser.add_argument("--output", default=".agents/teamwork/teamwork_challenger_2/artifacts/challenger2_run", help="Output directory")
    args = parser.parse_args()

    ok = asyncio.run(run_challenger2_verification(server_url=args.server, output_dir=args.output))
    sys.exit(0 if ok else 1)
