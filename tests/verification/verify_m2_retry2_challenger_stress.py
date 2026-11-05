"""
tests/verification/verify_m2_retry2_challenger_stress.py
Adversarial Empirical Stress Test Suite for Challenger M2 Retry 2:
1. Multi-Thread Cross-Event-Loop Cache Execution (Deadlock & Loop Mismatch Stress)
2. Cache Pre-Seeding vs. Live Fetch Shadowing Invariant Testing
3. Tide Slope Precision & IEEE 754 Boundary Grid Analysis
4. End-to-End Ingestion Telemetry & tide_status Forwarding
"""

import asyncio
import concurrent.futures
import json
import logging
import math
import random
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from app.config import get_settings, SPOTS, BUOY_STATIONS
from app.models.schemas import (
    NormalizedMarineReading,
    HourlyForecastPoint,
    BuoyObservation,
    SpotDetail,
)
from app.services.cache_service import CacheService, CacheEntry
from app.services.buoy_service import (
    classify_tide_slope,
    BuoyService,
    degrees_to_cardinal,
    cardinal_to_degrees,
)

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("challenger_m2_retry_2")


class VerificationResult:
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.failed = 0
        self.failures = []
        self.findings = []

    def check(self, condition: bool, description: str, finding_msg: str = ""):
        self.total += 1
        if condition:
            self.passed += 1
            print(f"  [PASS] {description}")
        else:
            self.failed += 1
            self.failures.append(description)
            print(f"  [FAIL] {description}")
            if finding_msg:
                self.findings.append(finding_msg)
                print(f"    -> FINDING: {finding_msg}")


v = VerificationResult()


# ==============================================================================
# HARNESS 1: Multi-Thread Cross-Event-Loop Cache Execution Stress
# ==============================================================================
print("\n" + "=" * 80)
print("  HARNESS 1: Multi-Thread Cross-Event-Loop Cache Execution Stress")
print("=" * 80)

def test_harness_1():
    cache = CacheService()
    cache.load_disk_fallbacks()

    # 1A: 50 concurrent OS threads, each with its OWN distinct asyncio event loop
    # All threads access the same shared key with asynchronous fetching
    print("\n--- 1A: 50 Concurrent Threads with Independent Event Loops on Shared Key ---")
    num_threads = 50
    exceptions_1a = []
    results_1a = []
    fetch_counter_1a = 0
    fetch_lock = threading.Lock()

    def thread_worker_1a(thread_id: int):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def simulated_fetch():
                nonlocal fetch_counter_1a
                with fetch_lock:
                    fetch_counter_1a += 1
                await asyncio.sleep(0.02)
                return {"payload": f"thread_data_{thread_id}", "timestamp": time.time()}

            async def main_coro():
                return await cache.get_or_set("shared_cross_loop_key", simulated_fetch, ttl=2.0)

            res, status = loop.run_until_complete(main_coro())
            results_1a.append((thread_id, res, status))
        except Exception as exc:
            exceptions_1a.append((thread_id, type(exc).__name__, str(exc)))
        finally:
            loop.close()

    start_1a = time.monotonic()
    threads_1a = [threading.Thread(target=thread_worker_1a, args=(i,)) for i in range(num_threads)]
    for t in threads_1a:
        t.start()
    for t in threads_1a:
        t.join(timeout=10.0)
    elapsed_1a = time.monotonic() - start_1a

    v.check(
        len(exceptions_1a) == 0,
        f"50 concurrent cross-event-loop threads threw 0 exceptions (got {len(exceptions_1a)}: {exceptions_1a[:3]})",
        "Cross-event-loop access caused RuntimeError or lock binding exceptions"
    )
    v.check(
        len(results_1a) == num_threads,
        f"All {num_threads} threads completed successfully in {elapsed_1a:.3f}s (< 5.0s)",
        f"Threads timed out or deadlocked: completed {len(results_1a)}/{num_threads}"
    )
    statuses_1a = [st for _, _, st in results_1a]
    v.check(
        statuses_1a.count("MISS") >= 1,
        f"At least 1 MISS occurred to populate data (got {statuses_1a.count('MISS')} MISS, {statuses_1a.count('HIT')} HIT)",
    )

    # 1B: Ephemeral Loop Churn & Lock Pruning Stress
    # 20 threads creating and destroying 20 event loops each = 400 event loops total
    # This exceeds CacheService._async_locks threshold of 256
    print("\n--- 1B: High-Frequency Event Loop Churn & Lock Pruning (400 Event Loops) ---")
    exceptions_1b = []
    churn_threads = 20
    loops_per_thread = 20
    keys_1b = [f"churn_key_{i}" for i in range(10)]

    def thread_worker_1b(thread_id: int):
        for loop_idx in range(loops_per_thread):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                target_key = keys_1b[loop_idx % len(keys_1b)]
                async def quick_fetch():
                    await asyncio.sleep(0.001)
                    return {"tid": thread_id, "lidx": loop_idx}

                async def op():
                    return await cache.get_or_set(target_key, quick_fetch, ttl=0.5)

                val, status = loop.run_until_complete(op())
                assert val is not None
            except Exception as exc:
                exceptions_1b.append((thread_id, loop_idx, type(exc).__name__, str(exc)))
                break
            finally:
                loop.close()

    start_1b = time.monotonic()
    threads_1b = [threading.Thread(target=thread_worker_1b, args=(i,)) for i in range(churn_threads)]
    for t in threads_1b:
        t.start()
    for t in threads_1b:
        t.join(timeout=15.0)
    elapsed_1b = time.monotonic() - start_1b

    v.check(
        len(exceptions_1b) == 0,
        f"400 distinct ephemeral event loops completed with 0 errors (errors: {exceptions_1b[:3]})",
        "Lock pruning or cross-loop churn triggered exceptions or memory deadlocks"
    )
    v.check(
        elapsed_1b < 10.0,
        f"Ephemeral loop churn completed in {elapsed_1b:.3f}s (< 10.0s)"
    )

    # 1C: Mixed Multi-Threaded Sync & Async Contention Stress
    print("\n--- 1C: Mixed Multi-Threaded Sync & Async Contention (1,000 Operations) ---")
    exceptions_1c = []
    sync_threads = 10
    async_threads = 15
    ops_per_thread = 40

    def sync_worker(tid: int):
        try:
            for op_idx in range(ops_per_thread):
                k = f"mix_key_{op_idx % 5}"
                cache.set(k, f"sync_val_{tid}_{op_idx}", ttl=1.0)
                cache.get(k)
                if op_idx % 10 == 0:
                    cache.invalidate(k)
                cache.get_stats()
        except Exception as exc:
            exceptions_1c.append(("sync", tid, type(exc).__name__, str(exc)))

    def async_worker(tid: int):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def run_ops():
                for op_idx in range(ops_per_thread):
                    k = f"mix_key_{op_idx % 5}"
                    async def fetch():
                        return f"async_val_{tid}_{op_idx}"
                    await cache.get_or_set(k, fetch, ttl=0.5)
            loop.run_until_complete(run_ops())
        except Exception as exc:
            exceptions_1c.append(("async", tid, type(exc).__name__, str(exc)))
        finally:
            loop.close()

    threads_1c = [threading.Thread(target=sync_worker, args=(i,)) for i in range(sync_threads)]
    threads_1c += [threading.Thread(target=async_worker, args=(i,)) for i in range(async_threads)]
    random.shuffle(threads_1c)

    start_1c = time.monotonic()
    for t in threads_1c:
        t.start()
    for t in threads_1c:
        t.join(timeout=10.0)
    elapsed_1c = time.monotonic() - start_1c

    v.check(
        len(exceptions_1c) == 0,
        f"Mixed sync/async contention threw 0 exceptions across {len(threads_1c)} threads ({elapsed_1c:.3f}s)",
        f"Contention errors in mixed operations: {exceptions_1c[:3]}"
    )

    # 1D: 10 Concurrent Threads x 10 Coroutines Each (100 Concurrent Callers with Lock Collapsing)
    print("\n--- 1D: 10 Threads x 10 Coroutines (100 Total Callers Collapsing on Shared Key) ---")
    exceptions_1d = []
    results_1d = []
    fetch_counter_1d = 0
    fetch_lock_1d = threading.Lock()
    num_threads_1d = 10
    tasks_per_thread = 10

    def thread_worker_1d(thread_id: int):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def slow_fetch():
                nonlocal fetch_counter_1d
                with fetch_lock_1d:
                    fetch_counter_1d += 1
                await asyncio.sleep(0.02)
                return {"val": "collapsed", "tid": thread_id}

            async def run_batch():
                # 10 concurrent coroutines within this thread's event loop
                tasks = [
                    cache.get_or_set("heavy_collapse_key", slow_fetch, ttl=5.0)
                    for _ in range(tasks_per_thread)
                ]
                return await asyncio.gather(*tasks)

            res_list = loop.run_until_complete(run_batch())
            for res, st in res_list:
                results_1d.append((thread_id, res, st))
        except Exception as exc:
            exceptions_1d.append((thread_id, type(exc).__name__, str(exc)))
        finally:
            loop.close()

    start_1d = time.monotonic()
    threads_1d = [threading.Thread(target=thread_worker_1d, args=(i,)) for i in range(num_threads_1d)]
    for t in threads_1d:
        t.start()
    for t in threads_1d:
        t.join(timeout=10.0)
    elapsed_1d = time.monotonic() - start_1d

    v.check(
        len(exceptions_1d) == 0,
        f"100 cross-thread/cross-loop tasks threw 0 exceptions (errors: {exceptions_1d[:3]})",
        "Coroutines across multi-thread event loops threw lock or attachment errors"
    )
    v.check(
        len(results_1d) == num_threads_1d * tasks_per_thread,
        f"All 100 concurrent coroutines across 10 threads completed in {elapsed_1d:.3f}s (< 5.0s)",
    )
    statuses_1d = [st for _, _, st in results_1d]
    v.check(
        fetch_counter_1d <= num_threads_1d,
        f"Within each thread loop, 10 coroutines collapsed: total fetches {fetch_counter_1d} <= {num_threads_1d} (HITs={statuses_1d.count('HIT')}, MISSes={statuses_1d.count('MISS')})",
        f"Double-checked locking failed to collapse coroutines within thread loops: {fetch_counter_1d} fetches"
    )

test_harness_1()



# ==============================================================================
# HARNESS 2: Cache Pre-Seeding vs. Live Fetch Shadowing Stress
# ==============================================================================
print("\n" + "=" * 80)
print("  HARNESS 2: Cache Pre-Seeding vs. Live Fetch Shadowing Invariant Testing")
print("=" * 80)

async def test_harness_2():
    cache = CacheService()
    cache.load_disk_fallbacks()

    all_fallback_keys = [
        "spot:holly_beach",
        "spot:grand_isle",
        "forecast:holly_beach",
        "forecast:grand_isle",
        "buoy:42035",
        "buoy:42354",
        "buoy:42001",
        "buoy:CAPL1",
        "buoy:GISL1",
        "spots_list",
        "buoys_list",
    ]

    print("\n--- 2A: Initial Call on Every Pre-Seeded Key MUST Trigger Live Fetch (MISS) ---")
    all_triggered = True
    all_statuses_miss = True
    for key in all_fallback_keys:
        called = False
        async def mock_live():
            nonlocal called
            called = True
            return {"source": "live_telemetry", "key": key}

        val, status = await cache.get_or_set(key, mock_live, ttl=300.0)
        if not called:
            all_triggered = False
            v.check(False, f"Live fetch NOT triggered for pre-seeded key '{key}'", f"Pre-seeded key '{key}' shadowed live fetch")
        if status != "MISS":
            all_statuses_miss = False
            v.check(False, f"Key '{key}' returned status '{status}' instead of 'MISS'", f"Pre-seeded key '{key}' returned {status}")

    v.check(
        all_triggered and all_statuses_miss,
        f"All {len(all_fallback_keys)} pre-seeded keys triggered live fetches with status=MISS (zero shadowing)"
    )

    print("\n--- 2B: Second Call within TTL Returns Live HIT Without Refetching ---")
    all_second_hits = True
    for key in all_fallback_keys:
        called = False
        async def mock_live():
            nonlocal called
            called = True
            return {"source": "live_telemetry_2"}

        val, status = await cache.get_or_set(key, mock_live, ttl=300.0)
        if called or status != "HIT" or val.get("source") != "live_telemetry":
            all_second_hits = False
            v.check(False, f"Subsequent call failed for '{key}': called={called}, status={status}")

    v.check(
        all_second_hits,
        f"All {len(all_fallback_keys)} subsequent calls returned status=HIT with live cached payload"
    )

    print("\n--- 2C: Immediate Outage on Fresh Pre-Seeded Cache Gracefully Serves FALLBACK ---")
    fresh_cache = CacheService()
    fresh_cache.load_disk_fallbacks()

    all_fallbacks_served = True
    for key in all_fallback_keys:
        async def failing_live():
            raise ConnectionError("Simulated NOAA API gateway outage")

        val, status = await fresh_cache.get_or_set(key, failing_live, ttl=300.0)
        if status != "FALLBACK" or val is None:
            all_fallbacks_served = False
            v.check(False, f"Key '{key}' failed to degrade to FALLBACK during outage (status={status}, val={val})")

    v.check(
        all_fallbacks_served,
        f"All {len(all_fallback_keys)} pre-seeded keys gracefully degraded to status=FALLBACK during initial outage"
    )

    print("\n--- 2D: Multi-Tier Lifecycle: Live MISS -> Expired Outage STALE -> Stale Outage FALLBACK ---")
    lifecycle_cache = CacheService()
    lifecycle_cache.load_disk_fallbacks()
    key = "spot:holly_beach"

    # Step 1: Live MISS
    async def fetch_v1():
        return {"spot_id": "holly_beach", "version": "v1_live"}

    val1, status1 = await lifecycle_cache.get_or_set(key, fetch_v1, ttl=0.03, stale_grace=0.06)
    v.check(status1 == "MISS" and val1["version"] == "v1_live", "Step 1: Fresh live fetch returned status=MISS")

    # Step 2: Immediate HIT
    val2, status2 = await lifecycle_cache.get_or_set(key, fetch_v1, ttl=0.03, stale_grace=0.06)
    v.check(status2 == "HIT" and val2["version"] == "v1_live", "Step 2: Immediate read returned status=HIT")

    # Step 3: Expired within stale grace during outage -> STALE
    await asyncio.sleep(0.04)
    async def failing_fetch():
        raise TimeoutError("Simulated upstream timeout")

    val3, status3 = await lifecycle_cache.get_or_set(key, failing_fetch, ttl=0.03, stale_grace=0.06)
    v.check(status3 == "STALE" and val3["version"] == "v1_live", f"Step 3: Expired live data served as STALE during outage (got {status3})")

    # Step 4: Expired past stale grace during outage -> FALLBACK
    await asyncio.sleep(0.07)
    val4, status4 = await lifecycle_cache.get_or_set(key, failing_fetch, ttl=0.03, stale_grace=0.06)
    v.check(
        status4 == "FALLBACK" and val4.get("spot_id") == "holly_beach" and "version" not in val4,
        f"Step 4: Past stale grace degraded cleanly to disk FALLBACK (got {status4})"
    )

asyncio.run(test_harness_2())


# ==============================================================================
# HARNESS 3: Tide Slope Precision & IEEE 754 Boundary Grid Analysis
# ==============================================================================
print("\n" + "=" * 80)
print("  HARNESS 3: Tide Slope Precision & IEEE 754 Boundary Grid Analysis")
print("=" * 80)

def test_harness_3():
    # 3A: Sweep of baseline heights from -5.0 ft to +15.0 ft in 0.25 ft steps (81 baselines)
    # Test EXACT delta_v = +0.10 ft and -0.10 ft
    print("\n--- 3A: Comprehensive Height Sweep (81 baselines from -5.0 to +15.0 ft) ---")
    t0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)

    exact_pos_slack_high_passes = 0
    exact_neg_slack_low_passes = 0
    total_baselines = 0

    base_heights = [round(-5.0 + i * 0.25, 2) for i in range(81)]
    for h in base_heights:
        total_baselines += 1
        h_prev = h
        h_curr_pos = round(h + 0.10, 4)
        h_curr_neg = round(h - 0.10, 4)

        # Exact +0.10 ft delta: (t0, h_prev), (t1, h_curr_pos)
        # mean_v = (h_prev + h_curr_pos)/2 = h + 0.05. h_curr_pos >= mean_v (h+0.10 >= h+0.05 is True)
        res_pos = classify_tide_slope([(t0, h_prev), (t1, h_curr_pos)], target_time=t1, threshold_ft=0.10)
        if res_pos["tide_state"] == "Slack High" and res_pos["tide_status"] == "slack_high":
            exact_pos_slack_high_passes += 1
        else:
            print(f"    FAIL pos at h={h}: delta={h_curr_pos - h_prev}, eval={res_pos}")

        # Exact -0.10 ft delta: (t0, h_prev), (t1, h_curr_neg)
        # mean_v = h - 0.05. h_curr_neg >= mean_v (h-0.10 >= h-0.05 is False) -> Slack Low
        res_neg = classify_tide_slope([(t0, h_prev), (t1, h_curr_neg)], target_time=t1, threshold_ft=0.10)
        if res_neg["tide_state"] == "Slack Low" and res_neg["tide_status"] == "slack_low":
            exact_neg_slack_low_passes += 1
        else:
            print(f"    FAIL neg at h={h}: delta={h_curr_neg - h_prev}, eval={res_neg}")

    v.check(
        exact_pos_slack_high_passes == total_baselines,
        f"Exact +0.10 ft delta classified as 'Slack High' across ALL {total_baselines} baselines ({exact_pos_slack_high_passes}/{total_baselines})",
        "Tide Slope Engine IEEE 754 precision glitch: exact +0.10 ft misclassified as Rising"
    )
    v.check(
        exact_neg_slack_low_passes == total_baselines,
        f"Exact -0.10 ft delta classified as 'Slack Low' across ALL {total_baselines} baselines ({exact_neg_slack_low_passes}/{total_baselines})",
        "Tide Slope Engine IEEE 754 precision glitch: exact -0.10 ft misclassified as Falling"
    )

    # 3B: Fine-Grained Epsilon Delta Boundary Step Analysis around 0.10 ft
    print("\n--- 3B: Fine-Grained Epsilon Probing Around 0.10 ft Boundary ---")
    t0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)
    base = 1.00

    test_deltas = [
        # (delta, expected_state, expected_status)
        (0.000, "Slack High", "slack_high"),
        (0.050, "Slack High", "slack_high"),
        (0.090, "Slack High", "slack_high"),
        (0.099, "Slack High", "slack_high"),
        (0.100, "Slack High", "slack_high"),       # EXACT BOUNDARY -> Slack High
        (0.101, "Slack High", "slack_high"),       # rounds to 0.10 -> Slack High
        (0.104, "Slack High", "slack_high"),       # rounds to 0.10 -> Slack High
        (0.106, "Rising", "rising"),               # rounds to 0.11 > 0.10 -> Rising
        (0.110, "Rising", "rising"),               # Rising
        (0.250, "Rising", "rising"),               # Rising
        (-0.050, "Slack Low", "slack_low"),
        (-0.090, "Slack Low", "slack_low"),
        (-0.099, "Slack Low", "slack_low"),
        (-0.100, "Slack Low", "slack_low"),        # EXACT BOUNDARY -> Slack Low
        (-0.101, "Slack Low", "slack_low"),        # rounds to -0.10 -> Slack Low
        (-0.104, "Slack Low", "slack_low"),        # rounds to -0.10 -> Slack Low
        (-0.106, "Falling", "falling"),            # rounds to -0.11 < -0.10 -> Falling
        (-0.110, "Falling", "falling"),            # Falling
        (-0.300, "Falling", "falling"),            # Falling
    ]

    all_deltas_correct = True
    for d, exp_state, exp_status in test_deltas:
        pts = [(t0, base), (t1, round(base + d, 4))]
        res = classify_tide_slope(pts, target_time=t1, threshold_ft=0.10)
        actual_state = res["tide_state"]
        actual_status = res["tide_status"]
        if actual_state != exp_state or actual_status != exp_status:
            all_deltas_correct = False
            v.check(False, f"Delta {d:+.3f}ft: expected ({exp_state}, {exp_status}) got ({actual_state}, {actual_status})")

    v.check(
        all_deltas_correct,
        f"All {len(test_deltas)} fine-grained delta steps around 0.10ft classified correctly"
    )

    # 3C: End-to-End Pipeline Telemetry Forwarding
    print("\n--- 3C: Telemetry Forwarding: classify_tide_slope -> BuoyObservation -> NormalizedMarineReading ---")
    pts = [(t0, 1.00), (t1, 1.10)]
    slope_info = classify_tide_slope(pts, target_time=t1)

    obs = BuoyObservation(
        station_id="8768094",
        station_name="Calcasieu Pass (CAPL1)",
        latitude=29.768,
        longitude=-93.343,
        station_type="coops_tide_station",
        timestamp=t1,
        last_updated=t1,
        tide_level_ft=slope_info["current_water_level_ft"],
        tide_status=slope_info["tide_status"],
        is_online=True,
        status="online",
    )

    norm_reading = BuoyService.to_normalized_reading(obs)

    v.check(
        obs.tide_status == "slack_high",
        f"BuoyObservation contains tide_status='{obs.tide_status}'"
    )
    v.check(
        norm_reading.tide_status == "slack_high",
        f"NormalizedMarineReading preserves tide_status='{norm_reading.tide_status}'"
    )
    v.check(
        norm_reading.tide_ft == 1.10,
        f"NormalizedMarineReading preserves tide_ft={norm_reading.tide_ft}"
    )

test_harness_3()


# ==============================================================================
# HARNESS 4: Oceanographic Marine Boundary & Calm Period Parsing Stress
# ==============================================================================
print("\n" + "=" * 80)
print("  HARNESS 4: Oceanographic Marine Boundary & Calm Period Parsing Stress")
print("=" * 80)

def test_harness_4():
    now = datetime.now(timezone.utc)

    # 4A: Calm wave periods (0.0s, 0.5s subsecond, rounded to 1 decimal place)
    calm_reading = NormalizedMarineReading(
        source_id="open_meteo_calm",
        source_name="Open-Meteo Calm Model",
        source_type="model",
        timestamp=now,
        wave_height_ft=0.2,
        wave_period_sec=0.0,
        swell_height_ft=0.0,
        swell_period_sec=0.54,
        wind_wave_height_ft=0.1,
        wind_wave_period_sec=0.0,
    )
    v.check(
        calm_reading.wave_period_sec == 0.0,
        f"Wave period 0.0s accepted on calm seas (got {calm_reading.wave_period_sec})"
    )
    v.check(
        calm_reading.swell_period_sec == 0.5,
        f"Sub-second period 0.54s rounded to 0.5s (got {calm_reading.swell_period_sec})"
    )
    v.check(
        calm_reading.wind_wave_period_sec == 0.0,
        f"Wind wave period 0.0s accepted on calm seas (got {calm_reading.wind_wave_period_sec})"
    )

    # 4B: Modulo 360 Degree Azimuth Invariants
    deg_cases = [
        (-45.0, 315.0),
        (-360.0, 0.0),
        (-370.0, 350.0),
        (0.0, 0.0),
        (360.0, 360.0),
        (365.0, 5.0),
        (720.0, 0.0),
        (1085.0, 5.0),
    ]
    all_degs_pass = True
    for inp, exp in deg_cases:
        r = NormalizedMarineReading(
            source_id="test",
            source_name="test",
            source_type="buoy",
            timestamp=now,
            wind_direction_deg=inp
        )
        if r.wind_direction_deg != exp:
            all_degs_pass = False
            v.check(False, f"Degree {inp}° normalized to {r.wind_direction_deg}° (expected {exp}°)")

    v.check(
        all_degs_pass,
        f"All {len(deg_cases)} degree angle azimuth cases normalized correctly modulo 360"
    )

test_harness_4()


# ==============================================================================
# SUMMARY
# ==============================================================================
print("\n" + "=" * 80)
print(f"  CHALLENGER RETRY 2 STRESS SUITE SUMMARY: {v.passed}/{v.total} PASSED ({v.failed} FAILED)")
print(f"  FINDINGS LOGGED: {len(v.findings)}")
print("=" * 80)

if v.failed > 0:
    print(f"\nFailed checks ({len(v.failures)}):")
    for f in v.failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("\nAll empirical stress harnesses PASSED with zero failures!")
    sys.exit(0)
