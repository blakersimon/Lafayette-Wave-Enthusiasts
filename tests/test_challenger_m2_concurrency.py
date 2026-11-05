"""
tests/test_challenger_m2_concurrency.py
Pytest integration for Challenger M2.2 empirical concurrency, TTL caching,
Pydantic v2 boundary conditions, and Tide Slope Engine verification.
"""

import asyncio
import concurrent.futures
import json
import math
import random
import threading
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pydantic import ValidationError

from app.config import get_settings, SPOTS, BUOY_STATIONS
from app.models.schemas import (
    NormalizedMarineReading,
    HourlyForecastPoint,
    BuoyObservation,
    SpotDetail,
)
from app.services.cache_service import CacheService
from app.services.buoy_service import classify_tide_slope


class TestChallengerM2ConcurrencyAndBoundaries(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.cache = CacheService()

    def test_multithreaded_high_concurrency_50_threads(self):
        """Harness 1A: 50 concurrent native OS threads running 10,000 ops."""
        num_threads = 50
        ops_per_thread = 200
        exceptions = []
        keys = [f"key_{i}" for i in range(10)]

        def worker(thread_idx: int):
            try:
                for op_idx in range(ops_per_thread):
                    op = op_idx % 5
                    k = random.choice(keys)
                    if op == 0:
                        self.cache.set(k, f"val_{thread_idx}_{op_idx}", ttl=1.0)
                    elif op == 1:
                        self.cache.get(k)
                    elif op == 2:
                        self.cache.invalidate(k)
                    elif op == 3:
                        self.cache.get_stats()
                    elif op == 4:
                        self.cache.set(f"thread_private_{thread_idx}", op_idx, ttl=5.0)
            except Exception as e:
                exceptions.append((thread_idx, e))

        start = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, t) for t in range(num_threads)]
            concurrent.futures.wait(futures, timeout=15.0)
        elapsed = time.monotonic() - start

        self.assertEqual(len(exceptions), 0, f"Exceptions occurred during thread stress: {exceptions}")
        self.assertLess(elapsed, 5.0, f"Thread stress took too long: {elapsed}s")
        stats = self.cache.get_stats()
        self.assertGreaterEqual(stats["active_entries"], 0)

    async def test_async_double_checked_locking_50_tasks(self):
        """Harness 1B: 50 concurrent coroutines collapsing into 1 upstream fetch."""
        shared_key = "hot_spot:holly_beach"
        fetch_count = 0
        fetch_lock = threading.Lock()

        async def simulated_slow_api():
            nonlocal fetch_count
            with fetch_lock:
                fetch_count += 1
            await asyncio.sleep(0.02)
            return {"spot": "holly_beach", "breaking_height_ft": 3.2}

        tasks = [
            self.cache.get_or_set(shared_key, simulated_slow_api, ttl=10.0)
            for _ in range(50)
        ]
        results = await asyncio.gather(*tasks)

        self.assertEqual(fetch_count, 1, "Double checked locking must execute exactly 1 fetch")
        statuses = [status for _, status in results]
        self.assertEqual(statuses.count("MISS"), 1)
        self.assertEqual(statuses.count("HIT"), 49)
        for val, _ in results:
            self.assertEqual(val["breaking_height_ft"], 3.2)

    async def test_cache_ttl_expiry_and_multi_tier_degradation(self):
        """Harness 2A: Cache TTL expiry and graceful STALE / FAILED degradation."""
        async def fast_fetch():
            return {"data": "fresh_v1"}

        val, status = await self.cache.get_or_set("temp_key", fast_fetch, ttl=0.04, stale_grace=0.08)
        self.assertEqual(status, "MISS")

        await asyncio.sleep(0.05)

        async def failing_fetch():
            raise ConnectionResetError("Outage")

        # Expired during outage -> STALE
        val, status = await self.cache.get_or_set("temp_key", failing_fetch, ttl=0.04, stale_grace=0.08)
        self.assertEqual(status, "STALE")
        self.assertEqual(val["data"], "fresh_v1")

        # Past stale grace -> FAILED (zero crash)
        await asyncio.sleep(0.09)
        val, status = await self.cache.get_or_set("temp_key", failing_fetch, ttl=0.04, stale_grace=0.08)
        self.assertEqual(status, "FAILED")
        self.assertIsNone(val)

    def test_static_fallback_datasets_deserialization(self):
        """Harness 2B: Full Pydantic v2 validation of all bundled disk fallbacks."""
        self.cache.load_disk_fallbacks()
        spots = self.cache.get_fallback("spots_list")
        self.assertEqual(len(spots), 2)
        for s in spots:
            spot_obj = SpotDetail.model_validate(s)
            self.assertEqual(len(spot_obj.consensus.timeline_48h), 48)

        buoys = self.cache.get_fallback("buoys_list")
        self.assertEqual(len(buoys), 5)
        for b in buoys:
            b_obj = BuoyObservation.model_validate(b)
            self.assertIsNotNone(b_obj.station_id)

    def test_pydantic_true_north_and_degrees_normalization(self):
        """Harness 3A: Degree azimuth normalization (0-360) and modulo."""
        now = datetime.now(timezone.utc)

        # 360.0° and 0.0° preserved
        r360 = NormalizedMarineReading(source_id="s1", source_name="n1", source_type="buoy", timestamp=now, wind_direction_deg=360.0)
        self.assertEqual(r360.wind_direction_deg, 360.0)

        r0 = NormalizedMarineReading(source_id="s1", source_name="n1", source_type="buoy", timestamp=now, wind_direction_deg=0.0)
        self.assertEqual(r0.wind_direction_deg, 0.0)

        # > 360 normalized modulo
        r365 = NormalizedMarineReading(source_id="s1", source_name="n1", source_type="buoy", timestamp=now, wind_direction_deg=365.0)
        self.assertEqual(r365.wind_direction_deg, 5.0)

        # Negative normalized modulo
        r_neg = NormalizedMarineReading(source_id="s1", source_name="n1", source_type="buoy", timestamp=now, wind_direction_deg=-10.0)
        self.assertEqual(r_neg.wind_direction_deg, 350.0)

    def test_pydantic_wave_height_boundaries(self):
        """Harness 3B: Wave heights from 0.0 ft to 40.0 ft valid, >40 or <0 rejected."""
        now = datetime.now(timezone.utc)
        r_flat = NormalizedMarineReading(source_id="s1", source_name="n1", source_type="buoy", timestamp=now, wave_height_ft=0.0)
        self.assertEqual(r_flat.wave_height_ft, 0.0)

        r_storm = NormalizedMarineReading(source_id="s1", source_name="n1", source_type="buoy", timestamp=now, wave_height_ft=25.0)
        self.assertEqual(r_storm.wave_height_ft, 25.0)

        r_max = NormalizedMarineReading(source_id="s1", source_name="n1", source_type="buoy", timestamp=now, wave_height_ft=40.0)
        self.assertEqual(r_max.wave_height_ft, 40.0)

        with self.assertRaises(ValidationError):
            NormalizedMarineReading(source_id="s1", source_name="n1", source_type="buoy", timestamp=now, wave_height_ft=45.0)

        with self.assertRaises(ValidationError):
            NormalizedMarineReading(source_id="s1", source_name="n1", source_type="buoy", timestamp=now, wave_height_ft=-1.0)

    def test_spot_detail_serialization_roundtrip(self):
        """Harness 3C: SpotDetail export and JSON roundtrip deserialization."""
        self.cache.load_disk_fallbacks()
        raw_hb = self.cache.get_fallback("spot:holly_beach")
        spot_detail = SpotDetail.model_validate(raw_hb)

        # Roundtrip
        dumped_json = spot_detail.model_dump_json()
        rehydrated = SpotDetail.model_validate(json.loads(dumped_json))
        self.assertEqual(rehydrated.spot_id, spot_detail.spot_id)
        self.assertEqual(rehydrated.consensus.breaking_height_ft, spot_detail.consensus.breaking_height_ft)
        self.assertEqual(len(rehydrated.consensus.timeline_48h), 48)

    def test_tide_slope_threshold_boundaries(self):
        """Harness 4A: Discrete threshold classification for Tide Slope Engine."""
        t0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=1)

        # Rising
        res_rising = classify_tide_slope([(t0, 1.00), (t1, 1.15)], target_time=t1)
        self.assertEqual(res_rising["tide_state"], "Rising")

        # Falling
        res_falling = classify_tide_slope([(t0, 1.15), (t1, 1.00)], target_time=t1)
        self.assertEqual(res_falling["tide_state"], "Falling")

        # Slack High
        res_sh = classify_tide_slope([(t0, 1.80), (t0 + timedelta(minutes=30), 1.82), (t1, 1.81)], target_time=t1)
        self.assertEqual(res_sh["tide_state"], "Slack High")

        # Slack Low
        res_sl = classify_tide_slope([(t0, 0.20), (t0 + timedelta(minutes=30), 0.19), (t1, 0.18)], target_time=t1)
        self.assertEqual(res_sl["tide_state"], "Slack Low")

        # Empty & single point resilience
        res_empty = classify_tide_slope([])
        self.assertEqual(res_empty["tide_state"], "Slack High")
        self.assertEqual(res_empty["current_water_level_ft"], 0.0)

    def test_tide_slope_noisy_synthetic_stream(self):
        """Harness 4B: Continuous 48-hour noisy tide evaluation."""
        base_time = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
        num_samples = 480
        points = []
        for i in range(num_samples):
            t = base_time + timedelta(minutes=i * 6)
            clean_v = 1.0 + 1.2 * math.sin(2 * math.pi * (i * 6 / 60.0) / 12.0)
            noise = (random.random() - 0.5) * 0.04
            points.append((t, round(clean_v + noise, 3)))

        classifications = {}
        for i in range(10, num_samples):
            res = classify_tide_slope(points, target_time=points[i][0])
            st = res["tide_state"]
            classifications[st] = classifications.get(st, 0) + 1

        self.assertGreater(classifications.get("Rising", 0), 50)
        self.assertGreater(classifications.get("Falling", 0), 50)
        self.assertGreater(classifications.get("Slack High", 0), 10)
        self.assertGreater(classifications.get("Slack Low", 0), 10)


if __name__ == "__main__":
    unittest.main()
