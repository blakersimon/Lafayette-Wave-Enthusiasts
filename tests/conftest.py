"""
tests/conftest.py
Shared Pytest fixtures for Louisiana Surf Finder.
Provides clean cache fixtures, mock async HTTP clients, and raw test samples.
"""

import sys
from pathlib import Path
import pytest

# Ensure repository root is on sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.services.cache_service import CacheService, get_cache_service


@pytest.fixture
def clean_cache():
    """Provides a fresh, unpopulated CacheService instance."""
    cache = CacheService()
    return cache


@pytest.fixture
def warmed_cache():
    """Provides a CacheService instance pre-seeded with disk fallbacks."""
    cache = CacheService()
    cache.load_disk_fallbacks()
    return cache
