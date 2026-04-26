"""
Admin / diagnostics endpoints — cache stats, version, runtime info.

Read-only. No mutations. In production these should be gated behind auth or
a private network; for dev they're open so the parquet cache hit-rate is
trivially observable.
"""

from __future__ import annotations

import os
import sys

from fastapi import APIRouter

from backend.app.services.data_cache import cache_info, cache_clear


router = APIRouter()


@router.get("/cache")
def cache_stats() -> dict:
    """Parquet read cache hit rate. Hit rate <90% sustained suggests the
    LRU is undersized for the working set — bump `set_cache_maxsize()` at
    startup."""
    info = cache_info()
    total = info["lru_hits"] + info["lru_misses"]
    info["hit_rate"] = info["lru_hits"] / total if total else None
    return info


@router.post("/cache/clear")
def cache_clear_endpoint() -> dict:
    """Drop all cached parquet frames. Used in tests; rarely needed in
    prod (mtime-based invalidation handles fresh-write cases)."""
    cache_clear()
    return {"cleared": True, "info": cache_info()}


@router.get("/runtime")
def runtime_info() -> dict:
    """PID + worker hint for debugging multi-worker setups. Each uvicorn
    worker has its own cache — if you bounce between PIDs across requests,
    cache hit rates per request will be lower than the per-worker rate."""
    return {
        "pid": os.getpid(),
        "python": sys.version,
        "worker_count_hint": os.environ.get("WEB_CONCURRENCY", "unset"),
    }
