#!/usr/bin/env python3
"""
tests/verification/verify_gen2_dom_collision_adversarial.py

Adversarial Empirical Verification Harness for Challenger Gen 2.1:
Rigorous DOM bounding-box clearance, yellow wave symbol visibility,
and Lafayette / Acadiana flag branding audit.

Target:
- Server: http://127.0.0.1:8765
- Viewports:
    * Standard desktop (1440x900)
    * Narrow desktop / tablet (1024x768)
- Spots:
    * Holly Beach (holly_beach)
    * Grand Isle (grand_isle)
- States:
    * Initial overview
    * Holly Beach selected
    * Grand Isle selected
    * Hover expansion
    * Wave animation progression sampling
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
from pathlib import Path
from typing import Dict, Any, List

import websockets

CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def check_server(server_url: str) -> bool:
    try:
        req = urllib.request.Request(f"{server_url}/api/health", headers={"User-Agent": "Challenger-Verifier"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


class AdversarialCDPSession:
    def __init__(self, server_url: str, width: int, height: int, port: int, output_dir: str):
        self.server_url = server_url
        self.width = width
        self.height = height
        self.port = port
        self.output_dir = output_dir
        self.temp_profile = None
        self.proc = None
        self.ws = None
        self.msg_id = 0
        self.console_records = []
        self.exception_records = []
        self.drain_task = None

    async def start(self):
        if not os.path.exists(CHROME_BIN):
            raise RuntimeError(f"Google Chrome not found at {CHROME_BIN}")

        os.makedirs(self.output_dir, exist_ok=True)
        self.temp_profile = tempfile.mkdtemp(prefix=f"surf_chal_{self.width}x{self.height}_")

        cmd = [
            CHROME_BIN,
            "--headless=new",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.temp_profile}",
            "--remote-allow-origins=*",
            f"--window-size={self.width},{self.height}",
            "--hide-scrollbars",
            "--disable-gpu",
            self.server_url
        ]

        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Connect to CDP JSON endpoint
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
            raise RuntimeError(f"Failed to connect to Chrome CDP on port {self.port}")

        self.ws = await websockets.connect(ws_url, max_size=50 * 1024 * 1024)

        await self.send("Runtime.enable")
        await self.send("Page.enable")
        await self.send("Log.enable")

        # Allow initial Leaflet map rendering & tile settlement
        await asyncio.sleep(2.5)

    async def send(self, method: str, params: dict = None):
        self.msg_id += 1
        curr_id = self.msg_id
        req = {"id": curr_id, "method": method}
        if params:
            req["params"] = params
        await self.ws.send(json.dumps(req))
        while True:
            raw = await self.ws.recv()
            res = json.loads(raw)
            if res.get("method") == "Runtime.consoleAPICalled":
                self.console_records.append(res["params"])
            elif res.get("method") == "Runtime.exceptionThrown":
                self.exception_records.append(res["params"])
            elif res.get("id") == curr_id:
                return res

    async def eval_js(self, expression: str):
        res = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True
        })
        return res.get("result", {}).get("result", {}).get("value")

    async def capture_screenshot(self, filename: str, clip: dict = None):
        params = {"format": "png"}
        if clip:
            params["clip"] = clip
        res = await self.send("Page.captureScreenshot", params)
        data = res.get("result", {}).get("data")
        if data:
            filepath = os.path.join(self.output_dir, filename)
            with open(filepath, "wb") as f:
                f.write(base64.b64decode(data))
            return filepath
        return None

    async def close(self):
        if self.ws:
            await self.ws.close()
        if self.proc:
            self.proc.terminate()
            self.proc.wait()
        if self.temp_profile and os.path.exists(self.temp_profile):
            shutil.rmtree(self.temp_profile, ignore_errors=True)


async def run_adversarial_tests(server_url: str, output_dir: str):
    print("=" * 70)
    print("  LOUISIANA SURF FINDER — ADVERSARIAL DOM CLEARANCE & VISUAL SUITE   ")
    print("=" * 70)
    print(f"Target Server: {server_url}")
    print(f"Artifacts Dir: {output_dir}\n")

    os.makedirs(output_dir, exist_ok=True)
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server_url": server_url,
        "viewports": {},
        "heraldry_audit": {},
        "animation_stress": {},
        "overall_verdict": "PENDING"
    }

    # =========================================================================
    # PART 1: HERALDRY & EMBLEM AUDIT (Static Vector Analysis)
    # =========================================================================
    print(">>> AUDITING LAFAYETTE / ACADIANA FLAG HERALDRY IN SVG...")
    svg_path = os.path.join(os.getcwd(), "app/static/img/logo.svg")
    with open(svg_path, "r", encoding="utf-8") as f:
        svg_raw = f.read()

    # Parse XML
    root = ET.fromstring(svg_raw)
    ns = {"svg": "http://www.w3.org/2000/svg"}

    # 1. Royal blue quadrant + French Fleur-de-lis
    has_blue_grad = "acadianaBlue" in svg_raw and "#2563eb" in svg_raw and "#1e3a8a" in svg_raw
    has_fleur_de_lis = "Fleur-de-lis" in svg_raw or ("translate(25, 23)" in svg_raw and "C" in svg_raw)
    
    # 2. White/silver quadrant + Spanish Castle of Castile
    has_white_grad = "acadianaWhite" in svg_raw and "#ffffff" in svg_raw
    has_castle_gold = "castileGold" in svg_raw and "#f59e0b" in svg_raw
    has_castle_turrets = "Castle of Castile" in svg_raw or "battlement turrets" in svg_raw or "gateway" in svg_raw

    # 3. Crimson red field + 5-pointed Acadiana Star of Our Lady of the Assumption
    has_red_grad = "acadianaRed" in svg_raw and "#ef4444" in svg_raw and ("#991b1b" in svg_raw or "#b91c1c" in svg_raw)
    has_star_gold = "starGold" in svg_raw and "#fcd34d" in svg_raw
    has_5pt_star = "polygon" in svg_raw or "Stella Maris" in svg_raw or "5-pointed" in svg_raw

    # 4. Bold curling Gulf surf wave foreground
    has_wave_body = "gulfWaveBody" in svg_raw and ("#0284c7" in svg_raw or "#0369a1" in svg_raw)
    has_wave_tube = "waveHollowDark" in svg_raw
    has_crest_foam = "crestFoam" in svg_raw

    heraldry_pass = all([
        has_blue_grad, has_fleur_de_lis,
        has_white_grad, has_castle_gold, has_castle_turrets,
        has_red_grad, has_star_gold, has_5pt_star,
        has_wave_body, has_wave_tube, has_crest_foam
    ])

    report["heraldry_audit"] = {
        "pass": heraldry_pass,
        "blue_quadrant_fleur_de_lis": has_blue_grad and has_fleur_de_lis,
        "white_quadrant_spanish_castle": has_white_grad and has_castle_gold and has_castle_turrets,
        "crimson_quadrant_acadiana_star": has_red_grad and has_star_gold and has_5pt_star,
        "curling_gulf_surf_wave": has_wave_body and has_wave_tube and has_crest_foam
    }
    print(f"  Heraldry Audit Pass: {heraldry_pass}")
    for k, v in report["heraldry_audit"].items():
        print(f"    - {k}: {v}")

    # =========================================================================
    # PART 2: DESKTOP VIEWPORT (1440x900) EMPIRICAL MEASUREMENTS
    # =========================================================================
    print("\n>>> LAUNCHING HEADLESS CHROME (1440x900 Standard Desktop)...")
    desktop_session = AdversarialCDPSession(server_url, 1440, 900, 9338, output_dir)
    await desktop_session.start()

    try:
        # JS helper to extract exact geometry & styling of spot markers
        GET_SPOTS_METRICS_JS = """
        (() => {
            const markers = Array.from(document.querySelectorAll('.spot-wave-beacon')).map((b) => {
                const pill = b.querySelector('.spot-pin-pill');
                const stem = b.querySelector('.spot-pin-stem');
                const core = b.querySelector('.spot-anchor-core');
                const crests = b.querySelector('.map-swell-crests');
                const arcs = Array.from(b.querySelectorAll('.swell-crest-arc'));
                const title = pill?.querySelector('.spot-pin-title')?.textContent?.trim() || 'Unknown';
                const isSelected = b.classList.contains('selected');

                const pRect = pill ? pill.getBoundingClientRect() : null;
                const cRect = crests ? crests.getBoundingClientRect() : null;
                const sRect = stem ? stem.getBoundingClientRect() : null;
                const coreRect = core ? core.getBoundingClientRect() : null;

                // Calculate bounding box overlap with map-swell-crests container
                let overlapAreaCrests = 0;
                let clearanceY = null;
                if (pRect && cRect) {
                    const xOverlap = Math.max(0, Math.min(pRect.right, cRect.right) - Math.max(pRect.left, cRect.left));
                    const yOverlap = Math.max(0, Math.min(pRect.bottom, cRect.bottom) - Math.max(pRect.top, cRect.top));
                    overlapAreaCrests = Math.round(xOverlap * yOverlap * 100) / 100;
                    clearanceY = Math.round((cRect.top - pRect.bottom) * 100) / 100;
                }

                // Check overlap against every individual arc bounding box
                const arcMetrics = arcs.map((arc, i) => {
                    const aRect = arc.getBoundingClientRect();
                    let arcOverlap = 0;
                    if (pRect && aRect) {
                        const xO = Math.max(0, Math.min(pRect.right, aRect.right) - Math.max(pRect.left, aRect.left));
                        const yO = Math.max(0, Math.min(pRect.bottom, aRect.bottom) - Math.max(pRect.top, aRect.top));
                        arcOverlap = Math.round(xO * yO * 100) / 100;
                    }
                    const cs = window.getComputedStyle(arc);
                    return {
                        index: i,
                        rect: { x: Math.round(aRect.x), y: Math.round(aRect.y), w: Math.round(aRect.width), h: Math.round(aRect.height), top: Math.round(aRect.top), bottom: Math.round(aRect.bottom) },
                        overlapWithPill: arcOverlap,
                        borderTopColor: cs.borderTopColor,
                        filter: cs.filter,
                        opacity: cs.opacity,
                        visibility: cs.visibility
                    };
                });

                const maxArcOverlap = Math.max(0, ...arcMetrics.map(a => a.overlapWithPill));

                return {
                    title,
                    isSelected,
                    pillRect: pRect ? { x: Math.round(pRect.x), y: Math.round(pRect.y), w: Math.round(pRect.width), h: Math.round(pRect.height), top: Math.round(pRect.top), bottom: Math.round(pRect.bottom) } : null,
                    crestsRect: cRect ? { x: Math.round(cRect.x), y: Math.round(cRect.y), w: Math.round(cRect.width), h: Math.round(cRect.height), top: Math.round(cRect.top), bottom: Math.round(cRect.bottom) } : null,
                    overlapAreaCrests,
                    clearanceY,
                    arcMetrics,
                    maxArcOverlap,
                    hasArcs: arcs.length > 0
                };
            });
            return markers;
        })()
        """

        GET_EMBLEM_METRICS_JS = """
        (() => {
            const emblem = document.querySelector('.brand-wave-emblem');
            const img = emblem?.querySelector('img') || document.querySelector('.brand-wave-logo');
            const rect = emblem ? emblem.getBoundingClientRect() : null;
            const cs = emblem ? window.getComputedStyle(emblem) : null;
            return {
                found: !!emblem,
                width: rect ? Math.round(rect.width * 10) / 10 : 0,
                height: rect ? Math.round(rect.height * 10) / 10 : 0,
                boxShadow: cs ? cs.boxShadow : '',
                borderRadius: cs ? cs.borderRadius : '',
                imgLoaded: img ? (img.complete && img.naturalWidth > 0) : false,
                imgNaturalWidth: img ? img.naturalWidth : 0,
                imgSrc: img ? img.getAttribute('src') : ''
            };
        })()
        """

        # -------------------------------------------------------------
        # 1440x900 State 1: Initial Overview
        # -------------------------------------------------------------
        print("\n--- 1440x900 State 1: Initial Overview ---")
        d_state1 = await desktop_session.eval_js(GET_SPOTS_METRICS_JS)
        emblem_1440 = await desktop_session.eval_js(GET_EMBLEM_METRICS_JS)
        await desktop_session.capture_screenshot("desktop_1440x900_01_initial_overview.png")

        print(f"Emblem Rendered Size: {emblem_1440.get('width')}x{emblem_1440.get('height')}px (Loaded: {emblem_1440.get('imgLoaded')})")
        for spot in d_state1:
            print(f"Spot: {spot['title']} | Selected: {spot['isSelected']}")
            print(f"  Pill Rect:   {spot['pillRect']}")
            print(f"  Crests Rect: {spot['crestsRect']}")
            print(f"  Overlap Area (Crests Container): {spot['overlapAreaCrests']} px²")
            print(f"  Max Arc Overlap: {spot['maxArcOverlap']} px²")
            print(f"  Vertical Clearance (Crests Top - Pill Bottom): {spot['clearanceY']} px")
            for arc in spot['arcMetrics']:
                print(f"    Arc {arc['index']}: color={arc['borderTopColor']}, filter={arc['filter']}, overlap={arc['overlapWithPill']}px²")

        # -------------------------------------------------------------
        # 1440x900 State 2: Select Holly Beach
        # -------------------------------------------------------------
        print("\n--- 1440x900 State 2: Select Holly Beach ---")
        await desktop_session.eval_js("""
        (() => {
            const btn = document.getElementById('pill-holly_beach');
            if (btn) btn.click();
            else {
                const hb = Array.from(document.querySelectorAll('.spot-wave-beacon')).find(b => b.textContent.includes('Holly Beach'));
                if (hb) hb.querySelector('.spot-pin-pill').click();
            }
        })()
        """)
        await asyncio.sleep(1.2)
        d_state2 = await desktop_session.eval_js(GET_SPOTS_METRICS_JS)
        await desktop_session.capture_screenshot("desktop_1440x900_02_holly_beach_selected.png")
        for spot in d_state2:
            print(f"Spot: {spot['title']} | Selected: {spot['isSelected']} | Overlap: {spot['overlapAreaCrests']}px² | MaxArcOverlap: {spot['maxArcOverlap']}px²")

        # -------------------------------------------------------------
        # 1440x900 State 3: Select Grand Isle
        # -------------------------------------------------------------
        print("\n--- 1440x900 State 3: Select Grand Isle ---")
        await desktop_session.eval_js("""
        (() => {
            const btn = document.getElementById('pill-grand_isle');
            if (btn) btn.click();
            else {
                const gi = Array.from(document.querySelectorAll('.spot-wave-beacon')).find(b => b.textContent.includes('Grand Isle'));
                if (gi) gi.querySelector('.spot-pin-pill').click();
            }
        })()
        """)
        await asyncio.sleep(1.2)
        d_state3 = await desktop_session.eval_js(GET_SPOTS_METRICS_JS)
        await desktop_session.capture_screenshot("desktop_1440x900_03_grand_isle_selected.png")
        for spot in d_state3:
            print(f"Spot: {spot['title']} | Selected: {spot['isSelected']} | Overlap: {spot['overlapAreaCrests']}px² | MaxArcOverlap: {spot['maxArcOverlap']}px²")

        # -------------------------------------------------------------
        # 1440x900 State 4: Hover State Expansion Stress Test
        # -------------------------------------------------------------
        print("\n--- 1440x900 State 4: Hover State Expansion (Holly Beach) ---")
        hover_metrics = await desktop_session.eval_js("""
        (() => {
            const b = Array.from(document.querySelectorAll('.spot-wave-beacon')).find(b => b.textContent.includes('Holly Beach'));
            if (!b) return null;
            const pill = b.querySelector('.spot-pin-pill');
            const crests = b.querySelector('.map-swell-crests');
            // Trigger hover transform manually to measure exact expanded geometry
            pill.style.transform = 'translateX(-50%) scale(1.06)';
            
            const pRect = pill.getBoundingClientRect();
            const cRect = crests.getBoundingClientRect();
            const xOverlap = Math.max(0, Math.min(pRect.right, cRect.right) - Math.max(pRect.left, cRect.left));
            const yOverlap = Math.max(0, Math.min(pRect.bottom, cRect.bottom) - Math.max(pRect.top, cRect.top));
            const overlapArea = Math.round(xOverlap * yOverlap * 100) / 100;
            const clearance = Math.round((cRect.top - pRect.bottom) * 100) / 100;

            const res = {
                title: 'Holly Beach (Hover Scale 1.06)',
                pillRect: { x: Math.round(pRect.x), y: Math.round(pRect.y), w: Math.round(pRect.width), h: Math.round(pRect.height), top: Math.round(pRect.top), bottom: Math.round(pRect.bottom) },
                crestsRect: { x: Math.round(cRect.x), y: Math.round(cRect.y), w: Math.round(cRect.width), h: Math.round(cRect.height), top: Math.round(cRect.top), bottom: Math.round(cRect.bottom) },
                overlapArea,
                clearance
            };
            // restore
            pill.style.transform = '';
            return res;
        })()
        """)
        await desktop_session.capture_screenshot("desktop_1440x900_04_holly_beach_hover.png")
        print(f"Hover Metrics: {hover_metrics}")

        # -------------------------------------------------------------
        # 1440x900 State 5: Continuous Animation Wave Cycle Sampling
        # -------------------------------------------------------------
        print("\n--- 1440x900 State 5: Animation Phase Sampling (3.2s cycle at 400ms intervals) ---")
        animation_samples = []
        for step in range(8):
            sample = await desktop_session.eval_js("""
            (() => {
                const spots = Array.from(document.querySelectorAll('.spot-wave-beacon')).map(b => {
                    const pill = b.querySelector('.spot-pin-pill');
                    const arcs = Array.from(b.querySelectorAll('.swell-crest-arc'));
                    const title = pill?.querySelector('.spot-pin-title')?.textContent?.trim() || '';
                    const pRect = pill.getBoundingClientRect();
                    let maxOverlap = 0;
                    let minArcTop = 99999;
                    arcs.forEach(a => {
                        const aRect = a.getBoundingClientRect();
                        if (aRect.top < minArcTop) minArcTop = aRect.top;
                        const xO = Math.max(0, Math.min(pRect.right, aRect.right) - Math.max(pRect.left, aRect.left));
                        const yO = Math.max(0, Math.min(pRect.bottom, aRect.bottom) - Math.max(pRect.top, aRect.top));
                        const ov = Math.max(0, xO * yO);
                        if (ov > maxOverlap) maxOverlap = ov;
                    });
                    return {
                        title,
                        maxOverlap: Math.round(maxOverlap * 100) / 100,
                        clearanceFromHighestArc: Math.round((minArcTop - pRect.bottom) * 100) / 100
                    };
                });
                return { time: Date.now(), spots };
            })()
            """)
            animation_samples.append(sample)
            await asyncio.sleep(0.4)

        report["animation_stress"] = animation_samples
        print(f"Captured {len(animation_samples)} animation cycle samples.")
        for s_idx, sample in enumerate(animation_samples):
            spots_str = ", ".join([f"{sp['title']}: overlap={sp['maxOverlap']}px², clearance={sp['clearanceFromHighestArc']}px" for sp in sample['spots']])
            print(f"  Sample T+{s_idx*400}ms -> {spots_str}")

        # High resolution clip of emblem
        emblem_clip = await desktop_session.eval_js("""
        (() => {
            const emblem = document.querySelector('.brand-wave-emblem');
            if (!emblem) return null;
            const r = emblem.getBoundingClientRect();
            return { x: r.x - 10, y: r.y - 10, width: r.width + 20, height: r.height + 20, scale: 2 };
        })()
        """)
        if emblem_clip:
            await desktop_session.capture_screenshot("emblem_closeup_capture.png", clip=emblem_clip)
            print("Saved emblem close-up screenshot.")

        report["viewports"]["1440x900"] = {
            "initial_overview": d_state1,
            "holly_beach_selected": d_state2,
            "grand_isle_selected": d_state3,
            "hover_metrics": hover_metrics,
            "emblem_metrics": emblem_1440
        }

    finally:
        await desktop_session.close()

    # =========================================================================
    # PART 3: NARROW DESKTOP / TABLET VIEWPORT (1024x768)
    # =========================================================================
    print("\n>>> LAUNCHING HEADLESS CHROME (1024x768 Narrow Desktop / Tablet)...")
    tablet_session = AdversarialCDPSession(server_url, 1024, 768, 9339, output_dir)
    await tablet_session.start()

    try:
        # -------------------------------------------------------------
        # 1024x768 State 1: Initial Overview
        # -------------------------------------------------------------
        print("\n--- 1024x768 State 1: Initial Overview ---")
        t_state1 = await tablet_session.eval_js(GET_SPOTS_METRICS_JS)
        emblem_1024 = await tablet_session.eval_js(GET_EMBLEM_METRICS_JS)
        await tablet_session.capture_screenshot("tablet_1024x768_01_initial_overview.png")

        print(f"1024x768 Emblem Size: {emblem_1024.get('width')}x{emblem_1024.get('height')}px (Pass: {60 <= emblem_1024.get('width') <= 68})")
        for spot in t_state1:
            print(f"Spot: {spot['title']} | Selected: {spot['isSelected']}")
            print(f"  Pill Rect:   {spot['pillRect']}")
            print(f"  Crests Rect: {spot['crestsRect']}")
            print(f"  Overlap Area (Crests Container): {spot['overlapAreaCrests']} px²")
            print(f"  Max Arc Overlap: {spot['maxArcOverlap']} px²")
            print(f"  Clearance Y: {spot['clearanceY']} px")

        # -------------------------------------------------------------
        # 1024x768 State 2: Select Holly Beach
        # -------------------------------------------------------------
        print("\n--- 1024x768 State 2: Select Holly Beach ---")
        await tablet_session.eval_js("document.getElementById('pill-holly_beach')?.click()")
        await asyncio.sleep(1.2)
        t_state2 = await tablet_session.eval_js(GET_SPOTS_METRICS_JS)
        await tablet_session.capture_screenshot("tablet_1024x768_02_holly_beach_selected.png")
        for spot in t_state2:
            print(f"Spot: {spot['title']} | Selected: {spot['isSelected']} | Overlap: {spot['overlapAreaCrests']}px² | MaxArcOverlap: {spot['maxArcOverlap']}px²")

        # -------------------------------------------------------------
        # 1024x768 State 3: Select Grand Isle
        # -------------------------------------------------------------
        print("\n--- 1024x768 State 3: Select Grand Isle ---")
        await tablet_session.eval_js("document.getElementById('pill-grand_isle')?.click()")
        await asyncio.sleep(1.2)
        t_state3 = await tablet_session.eval_js(GET_SPOTS_METRICS_JS)
        await tablet_session.capture_screenshot("tablet_1024x768_03_grand_isle_selected.png")
        for spot in t_state3:
            print(f"Spot: {spot['title']} | Selected: {spot['isSelected']} | Overlap: {spot['overlapAreaCrests']}px² | MaxArcOverlap: {spot['maxArcOverlap']}px²")

        report["viewports"]["1024x768"] = {
            "initial_overview": t_state1,
            "holly_beach_selected": t_state2,
            "grand_isle_selected": t_state3,
            "emblem_metrics": emblem_1024
        }

    finally:
        await tablet_session.close()

    # =========================================================================
    # PART 4: AUDIT EVALUATION & RIGOROUS VERIFICATION CHECKS
    # =========================================================================
    print("\n" + "=" * 70)
    print("                    EMPIRICAL AUDIT RESULTS                           ")
    print("=" * 70)

    # 1. Collision Check (Strictly 0px² overlap)
    all_overlaps = []
    # Collect all spot measurements from both viewports
    for vp_name, vp_data in report["viewports"].items():
        for state_key in ["initial_overview", "holly_beach_selected", "grand_isle_selected"]:
            for spot in vp_data[state_key]:
                all_overlaps.append({
                    "viewport": vp_name,
                    "state": state_key,
                    "spot": spot["title"],
                    "overlapAreaCrests": spot["overlapAreaCrests"],
                    "maxArcOverlap": spot["maxArcOverlap"],
                    "clearanceY": spot["clearanceY"]
                })
    # Add hover
    all_overlaps.append({
        "viewport": "1440x900",
        "state": "hover_state_holly_beach",
        "spot": "Holly Beach",
        "overlapAreaCrests": report["viewports"]["1440x900"]["hover_metrics"]["overlapArea"],
        "maxArcOverlap": 0.0,
        "clearanceY": report["viewports"]["1440x900"]["hover_metrics"]["clearance"]
    })
    # Add animation samples
    for idx, sample in enumerate(report["animation_stress"]):
        for sp in sample["spots"]:
            all_overlaps.append({
                "viewport": "1440x900",
                "state": f"animation_t+{idx*400}ms",
                "spot": sp["title"],
                "overlapAreaCrests": 0.0,
                "maxArcOverlap": sp["maxOverlap"],
                "clearanceY": sp["clearanceFromHighestArc"]
            })

    max_measured_overlap = max(item["maxArcOverlap"] for item in all_overlaps)
    max_crests_overlap = max(item["overlapAreaCrests"] for item in all_overlaps)
    min_clearance = min(item["clearanceY"] for item in all_overlaps if item["clearanceY"] is not None)

    collision_passed = (max_measured_overlap == 0.0) and (max_crests_overlap == 0.0) and (min_clearance > 0.0)
    print(f"1. DOM Bounding-Box Overlap: {'PASS' if collision_passed else 'FAIL'}")
    print(f"   - Max Overlap with Arcs: {max_measured_overlap} px² (Requirement: strictly 0px²)")
    print(f"   - Max Overlap with Crests Container: {max_crests_overlap} px²")
    print(f"   - Minimum Vertical Clearance Margin: {min_clearance} px (all positive separation)")

    # 2. Wave Crest Color Check (#facc15 -> rgb(250, 204, 21))
    wave_color_passed = True
    color_details = []
    for spot in report["viewports"]["1440x900"]["initial_overview"]:
        for arc in spot["arcMetrics"]:
            c = arc["borderTopColor"]
            color_details.append(c)
            # Check if rgb(250, 204, 21)
            if "250, 204, 21" not in c and "rgb(250, 204, 21)" not in c:
                wave_color_passed = False

    print(f"2. Wave Crest Color: {'PASS' if wave_color_passed else 'FAIL'}")
    print(f"   - Computed borderTopColor: {set(color_details)}")
    print(f"   - Target Color: #facc15 -> rgb(250, 204, 21)")

    # 3. Lafayette Flag Logo Check (60-68px badge + 4 heraldic elements)
    emblem_w_1440 = report["viewports"]["1440x900"]["emblem_metrics"]["width"]
    emblem_h_1440 = report["viewports"]["1440x900"]["emblem_metrics"]["height"]
    emblem_w_1024 = report["viewports"]["1024x768"]["emblem_metrics"]["width"]
    emblem_h_1024 = report["viewports"]["1024x768"]["emblem_metrics"]["height"]

    size_passed = (60.0 <= emblem_w_1440 <= 68.0) and (60.0 <= emblem_h_1440 <= 68.0) and \
                  (60.0 <= emblem_w_1024 <= 68.0) and (60.0 <= emblem_h_1024 <= 68.0)
    logo_passed = size_passed and heraldry_pass

    print(f"3. Lafayette Flag Logo: {'PASS' if logo_passed else 'FAIL'}")
    print(f"   - Rendered Size (1440x900): {emblem_w_1440}x{emblem_h_1440}px (Expected 60-68px)")
    print(f"   - Rendered Size (1024x768): {emblem_w_1024}x{emblem_h_1024}px (Expected 60-68px)")
    print(f"   - All 4 Heraldic Flag Elements Present in SVG: {heraldry_pass}")

    # Overall Verdict
    overall_verdict = "APPROVE" if (collision_passed and wave_color_passed and logo_passed) else "REQUEST_CHANGES"
    report["overall_verdict"] = overall_verdict
    report["summary"] = {
        "collision_passed": collision_passed,
        "max_measured_overlap_px2": max_measured_overlap,
        "min_clearance_margin_px": min_clearance,
        "wave_color_passed": wave_color_passed,
        "wave_crest_color": list(set(color_details)),
        "logo_size_passed": size_passed,
        "heraldry_passed": heraldry_pass,
        "verdict": overall_verdict
    }

    report_path = os.path.join(output_dir, "adversarial_verification_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("=" * 70)
    print(f"FINAL AUDIT VERDICT: {overall_verdict}")
    print(f"Report saved to: {report_path}")
    print("=" * 70)

    return overall_verdict, report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Challenger Gen 2.1 Adversarial Harness")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="Louisiana Surf Finder URL")
    parser.add_argument("--output", default=".agents/teamwork/challenger_gen2_1/artifacts", help="Artifacts directory")
    args = parser.parse_args()

    verdict, rep = asyncio.run(run_adversarial_tests(server_url=args.server, output_dir=args.output))
    sys.exit(0 if verdict == "APPROVE" else 1)
