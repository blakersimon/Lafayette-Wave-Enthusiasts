"""
tests/test_challenger_m2_retry2.py
Challenger M2 Retry 2 Pytest Integration:
1. Multi-thread cross-event-loop execution and per-loop lock collapsing
2. Cache pre-seeding non-shadowing and graceful multi-tier degradation
3. Tide slope precision: IEEE 754 boundary test (exact 0.10 ft delta -> Slack High)
4. Telemetry forwarding of tide_status through BuoyObservation into NormalizedMarineReading
"""

import asyncio
import concurrent.futures
import threading
import time
import unittest
from datetime import datetime, timezone, timedelta

from app.models.schemas import (
    NormalizedMarineReading,
    BuoyObservation,
)
from app.services.cache_service import CacheService
from app.services.buoy_service import (
    classify_tide_slope,
    BuoyService,
)


class TestChallengerM2Retry2(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.cache = CacheService()

    def test_multi_thread_cross_event_loop_concurrency(self):
        """Stress test 20 concurrent threads with independent event loops accessing shared key."""
        num_threads = 20
        exceptions = []
        results = []
        fetch_counter = 0
        fetch_lock = threading.Lock()

        def worker(thread_id: int):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def slow_fetch():
                    nonlocal fetch_counter
                    with fetch_lock:
                        fetch_counter += 1
                    await asyncio.sleep(0.01)
                    return {"tid": thread_id, "data": "cross_loop_ok"}

                async def main():
                    return await self.cache.get_or_set("shared_cross_loop_key", slow_fetch, ttl=2.0)

                val, status = loop.run_until_complete(main())
                results.append((thread_id, val, status))
            except Exception as exc:
                exceptions.append((thread_id, type(exc).__name__, str(exc)))
            finally:
                loop.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(exceptions), 0, f"Exceptions in cross-event-loop threads: {exceptions}")
        self.assertEqual(len(results), num_threads)
        for _, val, status in results:
            self.assertEqual(val["data"], "cross_loop_ok")

    def test_10_threads_x_10_coroutines_in_loop_lock_collapsing(self):
        """Verify 10 threads x 10 coroutines collapse fetches per-loop without lock errors."""
        num_threads = 10
        tasks_per_thread = 10
        exceptions = []
        results = []
        fetch_counter = 0
        fetch_lock = threading.Lock()

        def worker(thread_id: int):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def slow_fetch():
                    nonlocal fetch_counter
                    with fetch_lock:
                        fetch_counter += 1
                    await asyncio.sleep(0.01)
                    return {"tid": thread_id, "status": "ok"}

                async def run_batch():
                    tasks = [
                        self.cache.get_or_set("collapsing_key", slow_fetch, ttl=5.0)
                        for _ in range(tasks_per_thread)
                    ]
                    return await asyncio.gather(*tasks)

                res_list = loop.run_until_complete(run_batch())
                results.extend(res_list)
            except Exception as exc:
                exceptions.append((thread_id, type(exc).__name__, str(exc)))
            finally:
                loop.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(exceptions), 0, f"Exceptions: {exceptions}")
        self.assertEqual(len(results), num_threads * tasks_per_thread)
        self.assertLessEqual(fetch_counter, num_threads)

    async def test_cache_pre_seeding_shadowing_prevention(self):
        """Verify all pre-seeded keys trigger live fetch on first call and HIT on second call."""
        self.cache.load_disk_fallbacks()
        all_keys = [
            "spot:holly_beach",
            "spot:grand_isle",
            "forecast:holly_beach",
            "forecast:grand_isle",
            "buoy:42035",
            "buoys_list",
            "spots_list",
        ]
        for k in all_keys:
            called = False
            async def live_fn():
                nonlocal called
                called = True
                return {"live": True, "k": k}

            val1, st1 = await self.cache.get_or_set(k, live_fn, ttl=300.0)
            self.assertTrue(called, f"Live fetch was shadowed for key: {k}")
            self.assertEqual(st1, "MISS", f"Expected MISS for key: {k}, got {st1}")
            self.assertEqual(val1.get("live"), True)

            called = False
            val2, st2 = await self.cache.get_or_set(k, live_fn, ttl=300.0)
            self.assertFalse(called, f"Live fetch was called on cache hit for key: {k}")
            self.assertEqual(st2, "HIT", f"Expected HIT for key: {k}, got {st2}")
            self.assertEqual(val2.get("live"), True)

    def test_tide_slope_exact_point_one_ft_delta_slack_high(self):
        """Verify exact 0.10 ft delta classifies as Slack High (not Rising) due to rounding."""
        t0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=1)

        # Baseline 1.00 -> 1.10 ft (unrounded delta is 0.10000000000000009)
        pts_pos = [(t0, 1.00), (t1, 1.10)]
        res_pos = classify_tide_slope(pts_pos, target_time=t1, threshold_ft=0.10)
        self.assertEqual(
            res_pos["tide_state"],
            "Slack High",
            f"Exact +0.10 ft delta misclassified: {res_pos}"
        )
        self.assertEqual(res_pos["tide_status"], "slack_high")

        # Baseline 2.00 -> 1.90 ft (unrounded delta is -0.10000000000000009)
        pts_neg = [(t0, 2.00), (t1, 1.90)]
        res_neg = classify_tide_slope(pts_neg, target_time=t1, threshold_ft=0.10)
        self.assertEqual(
            res_neg["tide_state"],
            "Slack Low",
            f"Exact -0.10 ft delta misclassified: {res_neg}"
        )
        self.assertEqual(res_neg["tide_status"], "slack_low")

    def test_tide_slope_pipeline_telemetry_forwarding(self):
        """Verify tide_status is forwarded from slope engine to BuoyObservation and NormalizedMarineReading."""
        t0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=1)
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
        self.assertEqual(obs.tide_status, "slack_high")

        norm = BuoyService.to_normalized_reading(obs)
        self.assertEqual(norm.tide_status, "slack_high")
        self.assertEqual(norm.tide_ft, 1.10)


if __name__ == "__main__":
    unittest.main()
