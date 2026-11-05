"""
app/services/cache_service.py
Thread-safe and async-safe in-memory TTL cache with double-checked locking,
background cache warming, and disk JSON fallback loading.
"""

from __future__ import annotations
import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable, Awaitable, List

from app.config import get_settings

logger = logging.getLogger("louisiana_surf.cache")


class CacheEntry:
    """Represents a cached item with TTL and stale grace period."""
    __slots__ = ("key", "value", "created_at", "expires_at", "stale_until", "source")

    def __init__(
        self,
        key: str,
        value: Any,
        ttl: float,
        stale_grace: float = 86400.0,
        source: str = "live"
    ):
        now = time.monotonic()
        self.key = key
        self.value = value
        self.created_at = now
        self.expires_at = now + ttl
        self.stale_until = now + ttl + stale_grace
        self.source = source

    def is_expired(self, now: Optional[float] = None) -> bool:
        curr = now if now is not None else time.monotonic()
        return curr >= self.expires_at

    def is_stale_valid(self, now: Optional[float] = None) -> bool:
        curr = now if now is not None else time.monotonic()
        return curr < self.stale_until


class CacheService:
    """
    High-performance in-memory cache supporting:
    - Thread-safety across concurrent workers
    - Async-safety with fine-grained per-key double-checked locking
    - Immediate startup warming via bundled static fallback JSONs
    - Stale-while-revalidate safety tier
    - Zero-crash fallback guarantees
    """

    def __init__(self, fallback_dir: Optional[Path] = None):
        settings = get_settings()
        self._fallback_dir = fallback_dir or settings.FALLBACK_DATA_DIR
        self._cache: Dict[str, CacheEntry] = {}
        self._fallbacks: Dict[str, Any] = {}
        self._thread_lock = threading.Lock()
        self._async_locks: Dict[str, asyncio.Lock] = {}

        # Observability Metrics
        self._hits = 0
        self._misses = 0
        self._stale_hits = 0
        self._fallback_hits = 0
        self._errors = 0
        self._is_warmed = False
        self._start_time = time.monotonic()

    def _get_async_lock(self, key: str) -> asyncio.Lock:
        """Returns or creates a key-specific async lock scoped to current event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
        except RuntimeError:
            loop = None
            loop_id = 0

        lock_key = f"{loop_id}:{key}"
        with self._thread_lock:
            # Prevent unbounded lock accumulation across short-lived event loops
            if len(self._async_locks) > 256:
                prefix = f"{loop_id}:"
                self._async_locks = {
                    k: v for k, v in self._async_locks.items()
                    if k.startswith(prefix)
                }

            existing = self._async_locks.get(lock_key)
            if existing is None or (loop is not None and getattr(existing, "_loop", None) not in (None, loop)):
                existing = asyncio.Lock()
                self._async_locks[lock_key] = existing
            return existing

    def get(self, key: str) -> Optional[CacheEntry]:
        """Thread-safe retrieval of a raw cache entry."""
        with self._thread_lock:
            return self._cache.get(key)

    def set(
        self,
        key: str,
        value: Any,
        ttl: float,
        stale_grace: float = 86400.0,
        source: str = "live"
    ) -> None:
        """Thread-safe storage of a cache entry."""
        with self._thread_lock:
            self._cache[key] = CacheEntry(
                key=key,
                value=value,
                ttl=ttl,
                stale_grace=stale_grace,
                source=source
            )

    def invalidate(self, key: str) -> None:
        """Removes a specific key from the cache."""
        with self._thread_lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clears all entries from the in-memory cache."""
        with self._thread_lock:
            self._cache.clear()
            self._async_locks.clear()
            self._is_warmed = False

    def load_disk_fallbacks(self) -> None:
        """
        Synchronously loads fallback JSON snapshots from disk into memory.
        Merges 48-hour forecasts into spot consensus objects and pre-seeds the cache.
        """
        try:
            logger.info(f"Loading disk fallbacks from {self._fallback_dir}...")
            # 1. Forecasts fallback (load first so hourly points can be merged)
            forecasts_map: Dict[str, List[Dict[str, Any]]] = {}
            forecasts_path = self._fallback_dir / "forecasts_fallback.json"
            if forecasts_path.exists():
                with open(forecasts_path, "r", encoding="utf-8") as f:
                    forecasts_json = json.load(f)
                    self._fallbacks["forecasts_raw"] = forecasts_json
                    for spot_id, fc in forecasts_json.items():
                        self._fallbacks[f"forecast:{spot_id}"] = fc
                        forecasts_map[spot_id] = fc.get("hourly", [])

            # 2. Spots fallback
            spots_path = self._fallback_dir / "spots_fallback.json"
            if spots_path.exists():
                with open(spots_path, "r", encoding="utf-8") as f:
                    spots_json = json.load(f)
                    self._fallbacks["spots_raw"] = spots_json
                    spots_list = spots_json.get("spots", [])
                    for s in spots_list:
                        spot_id = s.get("spot_id")
                        if spot_id:
                            # Merge 48h timeline into spot consensus
                            if spot_id in forecasts_map and "consensus" in s:
                                s["consensus"]["timeline_48h"] = forecasts_map[spot_id]
                            self._fallbacks[f"spot:{spot_id}"] = s
                            self._fallbacks[f"consensus:{spot_id}"] = s.get("consensus")
                    self._fallbacks["spots_list"] = spots_list

            # 3. Buoys fallback
            buoys_path = self._fallback_dir / "buoys_fallback.json"
            if buoys_path.exists():
                with open(buoys_path, "r", encoding="utf-8") as f:
                    buoys_list = json.load(f)
                    self._fallbacks["buoys_list"] = buoys_list
                    for b in buoys_list:
                        station_id = b.get("station_id")
                        if station_id:
                            self._fallbacks[f"buoy:{station_id}"] = b

            # Seed cache with fallbacks so immediate startup queries succeed with zero latency
            # ttl=0.0 ensures live fetches via get_or_set() are not shadowed
            with self._thread_lock:
                for k, v in self._fallbacks.items():
                    self._cache[k] = CacheEntry(
                        key=k,
                        value=v,
                        ttl=0.0,
                        stale_grace=86400.0,
                        source="fallback"
                    )
                self._is_warmed = True

            logger.info(f"Disk fallbacks loaded successfully. {len(self._fallbacks)} keys cached.")

        except Exception as exc:
            with self._thread_lock:
                self._errors += 1
            logger.error(f"Failed to load disk fallbacks: {exc}", exc_info=True)

    def get_fallback(self, key: str) -> Optional[Any]:
        """Retrieves a pre-loaded fallback payload for the given key."""
        with self._thread_lock:
            return self._fallbacks.get(key)

    async def get_or_set(
        self,
        key: str,
        fetch_coro: Callable[[], Awaitable[Any]],
        ttl: float,
        stale_grace: float = 86400.0,
        fallback_key: Optional[str] = None
    ) -> Tuple[Any, str]:
        """
        Thread-safe and async-safe double-checked locking getter.
        Returns a tuple: (value, status) where status is:
        - 'HIT': Cache hit within valid TTL
        - 'MISS': Successfully refreshed live data
        - 'STALE': Served expired in-memory data during upstream outage
        - 'FALLBACK': Served disk JSON fallback data during upstream outage
        - 'FAILED': Catastrophic failure (returns None, never raises exception)
        """
        # Step 1: Fast Path (No async lock) — live cache hit only
        entry = self.get(key)
        if entry is not None and not entry.is_expired() and entry.source != "fallback":
            with self._thread_lock:
                self._hits += 1
            return entry.value, "HIT"

        # Step 2: Acquire per-key async lock
        lock = self._get_async_lock(key)
        async with lock:
            # Step 3: Double-checked second evaluation — live cache hit only
            entry = self.get(key)
            if entry is not None and not entry.is_expired() and entry.source != "fallback":
                with self._thread_lock:
                    self._hits += 1
                return entry.value, "HIT"

            # Step 4: Execute asynchronous fetch
            try:
                new_value = await fetch_coro()
                self.set(
                    key=key,
                    value=new_value,
                    ttl=ttl,
                    stale_grace=stale_grace,
                    source="live"
                )
                with self._thread_lock:
                    self._misses += 1
                return new_value, "MISS"
            except Exception as exc:
                with self._thread_lock:
                    self._errors += 1
                logger.warning(f"Upstream fetch failed for key '{key}': {type(exc).__name__}: {exc}")

                # Step 5: Graceful degradation to stale in-memory data (must be previous live data)
                if entry is not None and entry.is_stale_valid() and entry.source != "fallback":
                    with self._thread_lock:
                        self._stale_hits += 1
                    return entry.value, "STALE"

                # Step 6: Graceful degradation to static JSON disk fallback
                f_key = fallback_key or key
                fallback_val = self.get_fallback(f_key)
                if fallback_val is None and entry is not None and entry.source == "fallback":
                    fallback_val = entry.value
                if fallback_val is not None:
                    with self._thread_lock:
                        self._fallback_hits += 1
                    return fallback_val, "FALLBACK"

                # Step 7: Final fail-safe return None (guaranteed zero unhandled crash)
                return None, "FAILED"

    def get_stats(self) -> Dict[str, Any]:
        """Returns cache telemetry matching /api/health specification."""
        with self._thread_lock:
            return {
                "backend": "memory",
                "active_entries": len(self._cache),
                "hits": self._hits,
                "misses": self._misses,
                "stale_hits": self._stale_hits,
                "fallback_hits": self._fallback_hits,
                "errors": self._errors,
                "is_warmed": self._is_warmed,
                "uptime_seconds": round(time.monotonic() - self._start_time, 2)
            }


# Singleton cache instance
_global_cache: Optional[CacheService] = None
_global_lock = threading.Lock()


def get_cache_service() -> CacheService:
    """Returns singleton CacheService instance."""
    global _global_cache
    with _global_lock:
        if _global_cache is None:
            _global_cache = CacheService()
            _global_cache.load_disk_fallbacks()
        return _global_cache
