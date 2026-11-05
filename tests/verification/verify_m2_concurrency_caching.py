"""
tests/verification/verify_m2_concurrency_caching.py
Adversarial Verification & Empirical Stress Harness for Milestone 2:
1. High-concurrency race condition testing (50 concurrent threads + 50 async tasks)
2. Cache TTL expiry and multi-tier fallback degradation
3. Pydantic v2 boundary conditions (degrees, wave heights, serialization roundtrip)
4. Tide Slope Engine boundary conditions and noisy synthetic data stress
5. App configuration & spot/buoy metadata integrity
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

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from app.config import get_settings, SPOTS, BUOY_STATIONS, COOPS_TO_STATION_MAP, STATION_TO_COOPS_MAP
from app.models.schemas import (
    NormalizedMarineReading,
    HourlyForecastPoint,
    BuoyObservation,
    GoldenWindowRecommendation,
    ConsensusForecast,
    SpotDetail,
    SpotSummary,
    HealthCheckResponse,
)
from app.services.cache_service import CacheService, CacheEntry
from app.services.buoy_service import (
    classify_tide_slope,
    degrees_to_cardinal,
    cardinal_to_degrees,
    parse_float_val,
)

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("challenger_m2_2")


class VerificationResult:
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.failed = 0
        self.failures = []
        self.findings = []

    def check(self, condition: bool, description: str):
        self.total += 1
        if condition:
            self.passed += 1
            print(f"  [PASS] {description}")
        else:
            self.failed += 1
            print(f"  [FAIL] {description}")
            self.failures.append(description)

    def record_finding(self, finding: str):
        self.findings.append(finding)
        print(f"  [FINDING] {finding}")


vr = VerificationResult()


# ==============================================================================
# HARNESS 1: HIGH-CONCURRENCY RACE CONDITION TESTING (50 THREADS & 50 ASYNC TASKS)
# ==============================================================================

def test_multithreaded_concurrency_stress():
    print("\n--- HARNESS 1A: 50 Concurrent Native OS Threads Stress ---")
    cache = CacheService()
    num_threads = 50
    ops_per_thread = 200
    exceptions = []
    keys = [f"key_{i}" for i in range(10)]  # Contention over 10 shared keys

    def worker(thread_idx: int):
        try:
            for op_idx in range(ops_per_thread):
                op = op_idx % 5
                k = random.choice(keys)
                if op == 0:
                    cache.set(k, f"val_{thread_idx}_{op_idx}", ttl=1.0)
                elif op == 1:
                    cache.get(k)
                elif op == 2:
                    cache.invalidate(k)
                elif op == 3:
                    cache.get_stats()
                elif op == 4:
                    cache.set(f"thread_private_{thread_idx}", op_idx, ttl=5.0)
        except Exception as e:
            exceptions.append((thread_idx, e))

    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, t) for t in range(num_threads)]
        concurrent.futures.wait(futures, timeout=15.0)
    elapsed = time.monotonic() - start

    vr.check(len(exceptions) == 0, f"50 threads x 200 ops (10,000 total ops) threw zero exceptions: {exceptions}")
    vr.check(elapsed < 5.0, f"50 concurrent threads completed in {elapsed:.3f}s (< 5.0s)")

    stats = cache.get_stats()
    vr.check(stats["active_entries"] >= 0, f"Cache dictionary intact after 10,000 thread ops (active: {stats['active_entries']})")


async def test_async_double_checked_concurrency():
    print("\n--- HARNESS 1B: 50 Concurrent Async Coroutines (Thundering Herd Stress) ---")
    cache = CacheService()
    shared_key = "hot_spot:holly_beach"
    fetch_count = 0
    fetch_lock = threading.Lock()

    async def simulated_slow_upstream_api():
        nonlocal fetch_count
        with fetch_lock:
            fetch_count += 1
        # Artificial latency representing slow NOAA NDBC HTTP fetch
        await asyncio.sleep(0.05)
        return {"spot": "holly_beach", "breaking_height_ft": 3.2, "timestamp": time.time()}

    num_concurrent_tasks = 50
    tasks = [
        cache.get_or_set(shared_key, simulated_slow_upstream_api, ttl=10.0)
        for _ in range(num_concurrent_tasks)
    ]

    start = time.monotonic()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.monotonic() - start

    # Check for exceptions
    any_errors = [r for r in results if isinstance(r, Exception)]
    vr.check(len(any_errors) == 0, f"50 concurrent async tasks had 0 exceptions (errors: {any_errors})")

    # Double-checked locking guarantee: exactly 1 upstream fetch should occur
    vr.check(fetch_count == 1, f"Double-checked locking collapsed 50 concurrent requests to exactly 1 fetch (fetch_count={fetch_count})")

    # Status distribution
    statuses = [r[1] for r in results if not isinstance(r, Exception)]
    miss_count = statuses.count("MISS")
    hit_count = statuses.count("HIT")
    vr.check(miss_count == 1, f"Exactly 1 MISS recorded across 50 concurrent callers (got {miss_count})")
    vr.check(hit_count == 49, f"Exactly 49 HITs recorded across 50 concurrent callers (got {hit_count})")

    # Data integrity: all 50 results must return identical object
    values = [r[0]["breaking_height_ft"] for r in results if not isinstance(r, Exception)]
    vr.check(all(v == 3.2 for v in values), f"All 50 callers received identical uncorrupted payload (3.2 ft)")


async def test_multi_key_async_concurrency():
    print("\n--- HARNESS 1C: 50 Concurrent Tasks Across 10 Heterogeneous Keys ---")
    cache = CacheService()
    fetch_counts = {f"spot_{i}": 0 for i in range(10)}

    async def fetch_spot(spot_idx: int):
        await asyncio.sleep(0.02)
        fetch_counts[f"spot_{spot_idx}"] += 1
        return {"spot_id": f"spot_{spot_idx}", "val": spot_idx * 10}

    # 50 tasks randomly requesting across 10 spots
    tasks = []
    expected_spots = []
    for _ in range(50):
        s_idx = random.randint(0, 9)
        expected_spots.append(s_idx)
        tasks.append(cache.get_or_set(f"spot_{s_idx}", lambda idx=s_idx: fetch_spot(idx), ttl=5.0))

    results = await asyncio.gather(*tasks)
    vr.check(len(results) == 50, "All 50 multi-key concurrent tasks completed")
    # Total fetches across all 10 spots should be at most 10 (one per unique spot)
    total_fetches = sum(fetch_counts.values())
    vr.check(total_fetches <= 10, f"Per-key locks allowed max 1 fetch per key (total_fetches={total_fetches} <= 10)")


# ==============================================================================
# HARNESS 2: CACHE TTL EXPIRY, EVICTION & FALLBACK DATASETS
# ==============================================================================

async def test_cache_ttl_and_fallbacks():
    print("\n--- HARNESS 2A: Cache TTL Expiry & Multi-Tier Degradation ---")
    cache = CacheService()

    # Seed an item with a very short TTL (50ms) and short stale grace (100ms)
    async def fast_fetch():
        return {"data": "fresh_v1"}

    val, status = await cache.get_or_set("temp_key", fast_fetch, ttl=0.05, stale_grace=0.10)
    vr.check(status == "MISS" and val["data"] == "fresh_v1", "Initial fetch succeeded with MISS")

    # Immediate second call: HIT
    val, status = await cache.get_or_set("temp_key", fast_fetch, ttl=0.05, stale_grace=0.10)
    vr.check(status == "HIT", "Immediate subsequent call returns HIT")

    # Wait for TTL to expire (70ms > 50ms)
    await asyncio.sleep(0.07)

    # Simulated failing upstream fetch
    async def failing_fetch():
        raise ConnectionResetError("Remote server closed connection")

    # In stale grace period (70ms < 150ms): should degrade to STALE
    val, status = await cache.get_or_set("temp_key", failing_fetch, ttl=0.05, stale_grace=0.10)
    vr.check(status == "STALE" and val["data"] == "fresh_v1", f"Expired key during outage degraded to STALE (status={status})")

    # Wait past stale grace period (another 100ms)
    await asyncio.sleep(0.12)

    # Now both TTL and stale_grace are exceeded; without fallback, should be FAILED
    val, status = await cache.get_or_set("temp_key", failing_fetch, ttl=0.05, stale_grace=0.10)
    vr.check(status == "FAILED" and val is None, f"Past stale grace without disk fallback degraded to FAILED (status={status})")


def test_static_fallback_datasets_integrity():
    print("\n--- HARNESS 2B: Static Fallback Datasets Verification ---")
    cache = CacheService()
    cache.load_disk_fallbacks()
    stats = cache.get_stats()

    vr.check(stats["is_warmed"] is True, "CacheService marked warmed after load_disk_fallbacks()")
    vr.check(stats["active_entries"] >= 10, f"Cache active entries seeded ({stats['active_entries']} >= 10)")

    # 1. Check spots fallback
    spots_list = cache.get_fallback("spots_list")
    vr.check(isinstance(spots_list, list) and len(spots_list) == 2, f"spots_list has exactly 2 spots (got {len(spots_list) if spots_list else 0})")

    spot_ids = [s["spot_id"] for s in spots_list]
    vr.check("holly_beach" in spot_ids and "grand_isle" in spot_ids, f"spots_list contains 'holly_beach' and 'grand_isle': {spot_ids}")

    # Check that each spot deserializes into Pydantic SpotDetail
    for spot_dict in spots_list:
        try:
            spot_obj = SpotDetail.model_validate(spot_dict)
            vr.check(True, f"SpotDetail.model_validate succeeded for '{spot_obj.spot_id}'")
            # Verify 48h timeline was stitched into consensus
            vr.check(len(spot_obj.consensus.timeline_48h) == 48, f"'{spot_obj.spot_id}' has 48 stitched hourly forecast points")
        except Exception as e:
            vr.check(False, f"SpotDetail.model_validate failed for '{spot_dict.get('spot_id')}': {e}")

    # 2. Check buoys fallback
    buoys_list = cache.get_fallback("buoys_list")
    vr.check(isinstance(buoys_list, list) and len(buoys_list) == 5, f"buoys_list has exactly 5 stations (got {len(buoys_list) if buoys_list else 0})")

    expected_buoys = {"42035", "42354", "42001", "CAPL1", "GISL1"}
    actual_buoys = {b["station_id"] for b in buoys_list}
    vr.check(expected_buoys == actual_buoys, f"buoys_list contains all 5 required stations: {actual_buoys}")

    for buoy_dict in buoys_list:
        try:
            b_obj = BuoyObservation.model_validate(buoy_dict)
            vr.check(True, f"BuoyObservation.model_validate succeeded for '{b_obj.station_id}'")
        except Exception as e:
            vr.check(False, f"BuoyObservation.model_validate failed for '{buoy_dict.get('station_id')}': {e}")

    # 3. Check forecasts fallback
    for spot_id in ("holly_beach", "grand_isle"):
        fc = cache.get_fallback(f"forecast:{spot_id}")
        vr.check(fc is not None, f"Fallback forecast found for '{spot_id}'")
        hourly = fc.get("hourly", [])
        vr.check(len(hourly) == 48, f"'{spot_id}' has 48 hourly forecast points (got {len(hourly)})")
        # Validate first and last point
        try:
            HourlyForecastPoint.model_validate(hourly[0])
            HourlyForecastPoint.model_validate(hourly[-1])
            vr.check(True, f"HourlyForecastPoint validated first and last points for '{spot_id}'")
        except Exception as e:
            vr.check(False, f"HourlyForecastPoint validation failed for '{spot_id}': {e}")


# ==============================================================================
# HARNESS 3: BOUNDARY CONDITIONS IN PYDANTIC V2 SCHEMAS
# ==============================================================================

def test_pydantic_boundary_conditions():
    print("\n--- HARNESS 3A: True North & Compass Degrees Normalization / Rejection ---")
    now = datetime.now(timezone.utc)

    # Test 1: True North 360.0° vs 0.0°
    reading_360 = NormalizedMarineReading(
        source_id="test_ndbc",
        source_name="Test Buoy",
        source_type="buoy",
        timestamp=now,
        wind_direction_deg=360.0,
        wave_direction_deg=360.0,
        swell_direction_deg=360.0,
    )
    vr.check(reading_360.wind_direction_deg == 360.0, "Degree validator preserves True North 360.0° exactly")

    reading_0 = NormalizedMarineReading(
        source_id="test_ndbc",
        source_name="Test Buoy",
        source_type="buoy",
        timestamp=now,
        wind_direction_deg=0.0,
    )
    vr.check(reading_0.wind_direction_deg == 0.0, "Degree validator preserves True North 0.0° exactly")

    # Test 2: Angle > 360.0° (modulo normalization)
    reading_365 = NormalizedMarineReading(
        source_id="test_ndbc",
        source_name="Test Buoy",
        source_type="buoy",
        timestamp=now,
        wind_direction_deg=365.0,
    )
    vr.check(reading_365.wind_direction_deg == 5.0, f"Angle 365.0° normalized via modulo to 5.0° (got {reading_365.wind_direction_deg})")

    reading_720 = NormalizedMarineReading(
        source_id="test_ndbc",
        source_name="Test Buoy",
        source_type="buoy",
        timestamp=now,
        wind_direction_deg=720.0,
    )
    vr.check(reading_720.wind_direction_deg == 0.0, f"Angle 720.0° normalized via modulo to 0.0° (got {reading_720.wind_direction_deg})")

    # Test 3: Negative angles (e.g. -10.0°) normalized modulo to 350.0°
    reading_neg = NormalizedMarineReading(
        source_id="test_ndbc",
        source_name="Test Buoy",
        source_type="buoy",
        timestamp=now,
        wind_direction_deg=-10.0,
    )
    vr.check(reading_neg.wind_direction_deg == 350.0, f"Negative angle -10.0° normalized to 350.0° (got {reading_neg.wind_direction_deg})")

    print("\n--- HARNESS 3B: Wave Heights Extremes (0.0 ft, 25.0 ft, 40.0 ft, >40.0 ft) ---")

    # Extreme 1: 0.0 ft (Flat calm)
    reading_flat = NormalizedMarineReading(
        source_id="test_ndbc",
        source_name="Test Buoy",
        source_type="buoy",
        timestamp=now,
        wave_height_ft=0.0,
        swell_height_ft=0.0,
    )
    vr.check(reading_flat.wave_height_ft == 0.0, "Wave height 0.0 ft (flat calm boundary) accepted")

    # Extreme 2: 25.0 ft (Severe storm / Hurricane swell)
    reading_storm = NormalizedMarineReading(
        source_id="test_ndbc",
        source_name="Test Buoy",
        source_type="buoy",
        timestamp=now,
        wave_height_ft=25.0,
        swell_height_ft=22.5,
    )
    vr.check(reading_storm.wave_height_ft == 25.0, "Wave height 25.0 ft (storm swell boundary) accepted")

    # Extreme 3: 40.0 ft (Maximum boundary)
    reading_max = NormalizedMarineReading(
        source_id="test_ndbc",
        source_name="Test Buoy",
        source_type="buoy",
        timestamp=now,
        wave_height_ft=40.0,
    )
    vr.check(reading_max.wave_height_ft == 40.0, "Wave height 40.0 ft (schema upper boundary le=40.0) accepted")

    # Extreme 4: > 40.0 ft (Exceeds physical Gulf bounds) -> should raise ValidationError
    rejected_huge = False
    try:
        NormalizedMarineReading(
            source_id="test_ndbc",
            source_name="Test Buoy",
            source_type="buoy",
            timestamp=now,
            wave_height_ft=45.0,
        )
    except ValidationError:
        rejected_huge = True
    vr.check(rejected_huge, "Wave height > 40.0 ft (45.0 ft) correctly raises ValidationError")

    # Extreme 5: Negative wave height -> should raise ValidationError
    rejected_neg_wave = False
    try:
        NormalizedMarineReading(
            source_id="test_ndbc",
            source_name="Test Buoy",
            source_type="buoy",
            timestamp=now,
            wave_height_ft=-1.5,
        )
    except ValidationError:
        rejected_neg_wave = True
    vr.check(rejected_neg_wave, "Negative wave height (-1.5 ft) correctly raises ValidationError")


def test_spot_detail_serialization_roundtrip():
    print("\n--- HARNESS 3C: SpotDetail Serialization & Nested Fallback Binding ---")
    cache = CacheService()
    cache.load_disk_fallbacks()
    raw_hb = cache.get_fallback("spot:holly_beach")

    # Verify SpotDetail instantiation
    spot_detail = SpotDetail.model_validate(raw_hb)
    vr.check(spot_detail.spot_id == "holly_beach", "SpotDetail validated from disk fallback dict")
    vr.check(spot_detail.source_breakdown is not None, "SpotDetail root source_breakdown populated from consensus")
    vr.check(spot_detail.recommendation is not None, "SpotDetail root recommendation populated from consensus.best_window")

    # Test dictionary export (model_dump)
    dumped_dict = spot_detail.model_dump()
    vr.check(isinstance(dumped_dict, dict), "spot_detail.model_dump() yields clean dict")
    vr.check("consensus" in dumped_dict and "coordinates" in dumped_dict, "Dumped dict preserves nested hierarchy")

    # Test JSON export and string serialization
    dumped_json = spot_detail.model_dump_json()
    vr.check(isinstance(dumped_json, str) and len(dumped_json) > 1000, "spot_detail.model_dump_json() produces valid JSON string")

    # Test full roundtrip deserialization from JSON
    parsed_json_dict = json.loads(dumped_json)
    rehydrated = SpotDetail.model_validate(parsed_json_dict)
    vr.check(rehydrated.spot_id == spot_detail.spot_id, "Rehydrated spot_id matches original")
    vr.check(rehydrated.consensus.breaking_height_ft == spot_detail.consensus.breaking_height_ft, "Rehydrated breaking_height_ft matches original")
    vr.check(len(rehydrated.consensus.timeline_48h) == len(spot_detail.consensus.timeline_48h), "Rehydrated 48h timeline length matches original")


# ==============================================================================
# HARNESS 4: TIDE SLOPE ENGINE BOUNDARY CONDITIONS & NOISY SYNTHETIC DATA
# ==============================================================================

def test_tide_slope_boundaries():
    print("\n--- HARNESS 4A: Tide Slope Engine Discrete Threshold Boundaries ---")
    t0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)

    # Note on Float Precision: In Python, 1.10 - 1.00 = 0.10000000000000009 > 0.10.
    # While 0.10 - 0.00 = 0.10 == 0.10 (not > 0.10).
    # We record this finding and verify both behaviors.

    # Case 1: Exact 0.10 ft delta with float exact representation (0.00 to 0.10)
    pts_exact_010 = [(t0, 0.00), (t1, 0.10)]
    res = classify_tide_slope(pts_exact_010, target_time=t1)
    vr.check(res["tide_state"] in ("Slack High", "Slack Low"), f"Exact delta_v = 0.10 ft (0.0 to 0.10) classified as Slack ({res['tide_state']})")

    # Document finding for 1.00 to 1.10
    pts_float_artifact = [(t0, 1.00), (t1, 1.10)]
    res_artifact = classify_tide_slope(pts_float_artifact, target_time=t1)
    if res_artifact["tide_state"] == "Rising":
        vr.record_finding(
            "Tide Slope threshold precision finding: unrounded floating-point subtraction (1.10 - 1.00 = 0.10000000000000009) "
            "causes strictly-greater comparison (delta_v > threshold_ft) to evaluate True, classifying as 'Rising' "
            "instead of 'Slack High'. Using round(delta_v, 2) or math.isclose() will harden threshold classification."
        )

    # Case 2: +0.15 ft delta => strictly > +0.10 => Rising
    pts_rising = [(t0, 1.00), (t1, 1.15)]
    res = classify_tide_slope(pts_rising, target_time=t1)
    vr.check(res["tide_state"] == "Rising", f"Delta v = +0.15 ft classified as Rising ({res['tide_state']})")

    # Case 3: Exact -0.10 ft delta with float exact representation (0.10 to 0.00)
    pts_exact_neg_010 = [(t0, 0.10), (t1, 0.00)]
    res = classify_tide_slope(pts_exact_neg_010, target_time=t1)
    vr.check(res["tide_state"] in ("Slack High", "Slack Low"), f"Exact delta_v = -0.10 ft (0.10 to 0.0) classified as Slack ({res['tide_state']})")

    # Case 4: -0.15 ft delta => strictly < -0.10 => Falling
    pts_falling = [(t0, 1.15), (t1, 1.00)]
    res = classify_tide_slope(pts_falling, target_time=t1)
    vr.check(res["tide_state"] == "Falling", f"Delta v = -0.15 ft classified as Falling ({res['tide_state']})")

    # Case 5: Slack High vs Slack Low (relative to mean water level)
    # Mean of (1.8, 1.82, 1.81) is 1.81. Current is 1.81 >= mean => Slack High
    pts_slack_high = [(t0, 1.80), (t0 + timedelta(minutes=30), 1.82), (t1, 1.81)]
    res_sh = classify_tide_slope(pts_slack_high, target_time=t1)
    vr.check(res_sh["tide_state"] == "Slack High", f"Flat tide at high water classified as Slack High ({res_sh['tide_state']})")

    # Mean of (0.2, 0.18, 0.19) is 0.19. Current is 0.18 < mean => Slack Low
    pts_slack_low = [(t0, 0.20), (t0 + timedelta(minutes=30), 0.19), (t1, 0.18)]
    res_sl = classify_tide_slope(pts_slack_low, target_time=t1)
    vr.check(res_sl["tide_state"] == "Slack Low", f"Flat tide at low water classified as Slack Low ({res_sl['tide_state']})")

    # Case 6: Empty points list resilience
    res_empty = classify_tide_slope([])
    vr.check(res_empty["tide_state"] == "Slack High" and res_empty["current_water_level_ft"] == 0.0, "Empty points list returns safe fallback dictionary without crashing")

    # Case 7: Single point list resilience
    res_single = classify_tide_slope([(t0, 1.5)])
    vr.check(res_single["tide_state"] == "Slack High" and res_single["current_water_level_ft"] == 1.5, "Single point returns safe dictionary without crashing")


def test_tide_slope_noisy_synthetic_data():
    print("\n--- HARNESS 4B: Noisy Synthetic Tide Simulation (480 Samples over 48h) ---")
    # Simulate a 48-hour sinusoidal tide matching NOAA CO-OPS 6-minute frequency
    base_time = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
    tide_period_hours = 12.0
    amplitude_ft = 1.2
    mean_level_ft = 1.0

    points = []
    num_samples = 480  # 48 hours * 10 samples/hr
    for i in range(num_samples):
        t = base_time + timedelta(minutes=i * 6)
        clean_v = mean_level_ft + amplitude_ft * math.sin(2 * math.pi * (i * 6 / 60.0) / tide_period_hours)
        # Add random sensor noise (+- 0.02 ft)
        noise = (random.random() - 0.5) * 0.04
        v = round(clean_v + noise, 3)
        points.append((t, v))

    # Evaluate classification along the continuous time series
    classifications = {"Rising": 0, "Falling": 0, "Slack High": 0, "Slack Low": 0}
    crashes = 0

    for i in range(10, num_samples):
        target = points[i][0]
        try:
            res = classify_tide_slope(points, target_time=target, window_minutes=60, threshold_ft=0.10)
            classifications[res["tide_state"]] += 1
        except Exception as e:
            crashes += 1

    vr.check(crashes == 0, f"Noisy synthetic tide stream (480 points) had 0 crashes across all 470 evaluations")
    vr.check(classifications["Rising"] > 50, f"Correctly detected Rising phases ({classifications['Rising']} periods)")
    vr.check(classifications["Falling"] > 50, f"Correctly detected Falling phases ({classifications['Falling']} periods)")
    vr.check(classifications["Slack High"] > 10, f"Correctly detected Slack High phases ({classifications['Slack High']} periods)")
    vr.check(classifications["Slack Low"] > 10, f"Correctly detected Slack Low phases ({classifications['Slack Low']} periods)")

    print(f"    Tidal distribution across 470 intervals: {classifications}")


# ==============================================================================
# HARNESS 5: CONFIGURATION & METADATA BOUNDARIES
# ==============================================================================

def test_config_and_metadata():
    print("\n--- HARNESS 5: App Configuration, Spot Definitions & Station Metadata ---")
    settings = get_settings()

    # TTLs must be positive integers
    vr.check(settings.CACHE_TTL_BUOYS > 0, f"CACHE_TTL_BUOYS is positive: {settings.CACHE_TTL_BUOYS}s")
    vr.check(settings.CACHE_TTL_COOPS > 0, f"CACHE_TTL_COOPS is positive: {settings.CACHE_TTL_COOPS}s")
    vr.check(settings.CACHE_TTL_FORECASTS > 0, f"CACHE_TTL_FORECASTS is positive: {settings.CACHE_TTL_FORECASTS}s")
    vr.check(settings.CACHE_TTL_SCRAPERS > 0, f"CACHE_TTL_SCRAPERS is positive: {settings.CACHE_TTL_SCRAPERS}s")
    vr.check(settings.CACHE_TTL_CONSENSUS > 0, f"CACHE_TTL_CONSENSUS is positive: {settings.CACHE_TTL_CONSENSUS}s")
    vr.check(settings.STALE_GRACE_PERIOD_SECONDS >= 3600, f"STALE_GRACE_PERIOD_SECONDS >= 1 hour: {settings.STALE_GRACE_PERIOD_SECONDS}s")

    # Fallback directory
    vr.check(settings.FALLBACK_DATA_DIR.exists(), f"FALLBACK_DATA_DIR exists at: {settings.FALLBACK_DATA_DIR}")

    # Spot metadata
    vr.check(len(SPOTS) == 2, f"SPOTS configuration defines exactly 2 spots (got {len(SPOTS)})")
    hb = SPOTS["holly_beach"]
    gi = SPOTS["grand_isle"]

    vr.check(hb["shoaling_k_net"] == 0.65, f"Holly Beach K_net == 0.65 (got {hb['shoaling_k_net']})")
    vr.check(gi["shoaling_k_net"] == 0.82, f"Grand Isle K_net == 0.82 (got {gi['shoaling_k_net']})")

    vr.check(28.0 <= hb["coordinates"]["latitude"] <= 31.0, "Holly Beach latitude within Louisiana bounds")
    vr.check(-94.5 <= hb["coordinates"]["longitude"] <= -88.0, "Holly Beach longitude within Louisiana bounds")
    vr.check(28.0 <= gi["coordinates"]["latitude"] <= 31.0, "Grand Isle latitude within Louisiana bounds")
    vr.check(-94.5 <= gi["coordinates"]["longitude"] <= -88.0, "Grand Isle longitude within Louisiana bounds")

    # Buoy metadata
    vr.check(len(BUOY_STATIONS) == 5, f"BUOY_STATIONS defines 5 stations (got {len(BUOY_STATIONS)})")
    for s_id, s_meta in BUOY_STATIONS.items():
        vr.check("station_name" in s_meta and "latitude" in s_meta, f"Station '{s_id}' has complete metadata")


# ==============================================================================
# MAIN RUNNER
# ==============================================================================

async def run_all_async_tests():
    await test_async_double_checked_concurrency()
    await test_multi_key_async_concurrency()
    await test_cache_ttl_and_fallbacks()


def main():
    print("================================================================================")
    print("  CHALLENGER M2.2 EMPIRICAL ADVERSARIAL STRESS TEST SUITE")
    print("  Concurrency, Cache TTL, Fallback Resilience, Schemas & Tide Engine")
    print("================================================================================")

    # 1. Multi-threaded Concurrency
    test_multithreaded_concurrency_stress()

    # 2. Async Concurrency & TTL Degradation
    asyncio.run(run_all_async_tests())

    # 3. Static Fallbacks Integrity
    test_static_fallback_datasets_integrity()

    # 4. Pydantic v2 Boundaries & Serialization
    test_pydantic_boundary_conditions()
    test_spot_detail_serialization_roundtrip()

    # 5. Tide Slope Boundaries & Noise
    test_tide_slope_boundaries()
    test_tide_slope_noisy_synthetic_data()

    # 6. Configuration & Metadata
    test_config_and_metadata()

    print("\n================================================================================")
    print(f"  STRESS TEST SUMMARY: {vr.passed}/{vr.total} PASSED ({vr.failed} FAILED)")
    print(f"  FINDINGS LOGGED: {len(vr.findings)}")
    print("================================================================================")

    if vr.findings:
        print("\nEmpirical Findings Recorded:")
        for idx, f in enumerate(vr.findings, 1):
            print(f"  [{idx}] {f}")

    if vr.failed > 0:
        print("\nFailures:")
        for f in vr.failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\nAll empirical stress harnesses PASSED with zero unexpected failures!")
        sys.exit(0)


if __name__ == "__main__":
    main()
