"""
tests/test_cache_service.py
Comprehensive unit tests for CacheService: TTL expiration, stale-while-revalidate,
double-checked async locking, static fallback JSON loading, and zero-crash guarantees.
"""

import asyncio
import time
import unittest
from pathlib import Path

from app.services.cache_service import CacheService, CacheEntry


class TestCacheService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.cache = CacheService()

    def test_cache_entry_expiration(self):
        entry = CacheEntry(key="test", value="val", ttl=0.1, stale_grace=0.5)
        self.assertFalse(entry.is_expired())
        self.assertTrue(entry.is_stale_valid())

        # Simulate time passing
        now = time.monotonic() + 0.15
        self.assertTrue(entry.is_expired(now))
        self.assertTrue(entry.is_stale_valid(now))

        # Stale expired
        past_stale = time.monotonic() + 0.7
        self.assertTrue(entry.is_expired(past_stale))
        self.assertFalse(entry.is_stale_valid(past_stale))

    def test_load_disk_fallbacks(self):
        self.cache.load_disk_fallbacks()
        stats = self.cache.get_stats()
        self.assertTrue(stats["is_warmed"])
        self.assertGreater(stats["active_entries"], 5)

        # Check spot fallback
        hb_spot = self.cache.get_fallback("spot:holly_beach")
        self.assertIsNotNone(hb_spot)
        self.assertEqual(hb_spot["spot_id"], "holly_beach")

        # Verify 48h timeline stitching into spot consensus
        consensus = hb_spot.get("consensus", {})
        timeline = consensus.get("timeline_48h", [])
        self.assertEqual(len(timeline), 48)

        # Check buoy fallback
        buoy_42035 = self.cache.get_fallback("buoy:42035")
        self.assertIsNotNone(buoy_42035)
        self.assertEqual(buoy_42035["station_id"], "42035")

    async def test_get_or_set_hit_and_miss(self):
        call_count = 0

        async def fetch_data():
            nonlocal call_count
            call_count += 1
            return {"data": 42}

        # First call: MISS -> fetches live data
        val, status = await self.cache.get_or_set("key1", fetch_data, ttl=10.0)
        self.assertEqual(status, "MISS")
        self.assertEqual(val, {"data": 42})
        self.assertEqual(call_count, 1)

        # Second call: HIT -> returns from cache
        val, status = await self.cache.get_or_set("key1", fetch_data, ttl=10.0)
        self.assertEqual(status, "HIT")
        self.assertEqual(val, {"data": 42})
        self.assertEqual(call_count, 1)

    async def test_double_checked_locking_concurrency(self):
        call_count = 0

        async def slow_fetch():
            nonlocal call_count
            await asyncio.sleep(0.05)
            call_count += 1
            return "computed_val"

        # Launch 5 concurrent calls for the same key
        tasks = [
            self.cache.get_or_set("concurrent_key", slow_fetch, ttl=10.0)
            for _ in range(5)
        ]
        results = await asyncio.gather(*tasks)

        # Exactly ONE should miss and fetch, other 4 should HIT
        statuses = [status for val, status in results]
        self.assertEqual(statuses.count("MISS"), 1)
        self.assertEqual(statuses.count("HIT"), 4)
        self.assertEqual(call_count, 1)
        for val, _ in results:
            self.assertEqual(val, "computed_val")

    async def test_fallback_degradation_on_outage(self):
        self.cache.load_disk_fallbacks()

        # Seed key with an expired entry within stale grace
        self.cache.set("spot:holly_beach", {"spot_id": "holly_beach", "stale": True}, ttl=-1.0, stale_grace=100.0)

        async def failing_fetch():
            raise ConnectionError("Upstream API unreachable")

        # Should degrade to STALE
        val, status = await self.cache.get_or_set("spot:holly_beach", failing_fetch, ttl=10.0)
        self.assertEqual(status, "STALE")
        self.assertEqual(val.get("stale"), True)

        # Invalidate so no stale exists, should degrade to static FALLBACK
        self.cache.invalidate("spot:holly_beach")
        val, status = await self.cache.get_or_set("spot:holly_beach", failing_fetch, ttl=10.0)
        self.assertEqual(status, "FALLBACK")
        self.assertIsNotNone(val)
        self.assertEqual(val.get("spot_id"), "holly_beach")

    async def test_zero_crash_on_unknown_failing_key(self):
        async def failing_fetch():
            raise TimeoutError("Timeout")

        val, status = await self.cache.get_or_set("unknown_key_999", failing_fetch, ttl=10.0)
        self.assertEqual(status, "FAILED")
        self.assertIsNone(val)

    def test_observability_stats(self):
        stats = self.cache.get_stats()
        self.assertIn("hits", stats)
        self.assertIn("misses", stats)
        self.assertIn("stale_hits", stats)
        self.assertIn("fallback_hits", stats)
        self.assertIn("uptime_seconds", stats)

    async def test_fallback_pre_seeding_does_not_shadow_live_fetch(self):
        """Verifies that pre-seeded fallbacks do not block live fetch execution."""
        self.cache.load_disk_fallbacks()
        called = False

        async def live_fetch():
            nonlocal called
            called = True
            return {"spot_id": "holly_beach", "live": True}

        # First call: must invoke live_fetch and return MISS
        val, status = await self.cache.get_or_set("spot:holly_beach", live_fetch, ttl=600.0)
        self.assertTrue(called, "live_fetch must be called when cache contains only static fallback")
        self.assertEqual(status, "MISS")
        self.assertEqual(val.get("live"), True)

        # Subsequent call within TTL: must return HIT from live cache without calling live_fetch again
        called = False
        val2, status2 = await self.cache.get_or_set("spot:holly_beach", live_fetch, ttl=600.0)
        self.assertFalse(called, "live_fetch must not be called on valid cache hit")
        self.assertEqual(status2, "HIT")
        self.assertEqual(val2.get("live"), True)

    async def test_outage_fallback_served_when_pre_seeded(self):
        """Verifies that pre-seeded fallbacks are returned as FALLBACK during upstream outage."""
        self.cache.load_disk_fallbacks()

        async def failing_fetch():
            raise ConnectionError("Upstream API unreachable")

        val, status = await self.cache.get_or_set("spot:holly_beach", failing_fetch, ttl=600.0)
        self.assertEqual(status, "FALLBACK")
        self.assertIsNotNone(val)
        self.assertEqual(val.get("spot_id"), "holly_beach")

    def test_cross_event_loop_concurrency_no_runtime_error(self):
        """Verifies that accessing cache across distinct event loops causes no RuntimeError or deadlock."""
        import threading
        exceptions = []

        def worker(thread_idx: int):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            async def task():
                async def fetch():
                    await asyncio.sleep(0.01)
                    return {"thread": thread_idx}
                return await self.cache.get_or_set("shared_key", fetch, ttl=1.0)
            try:
                loop.run_until_complete(task())
            except Exception as e:
                exceptions.append((thread_idx, e))
            finally:
                loop.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(len(exceptions), 0, f"Cross-event-loop concurrency threw exceptions: {exceptions}")


if __name__ == "__main__":
    unittest.main()
