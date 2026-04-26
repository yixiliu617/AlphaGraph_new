"""
Mtime-keyed LRU cache for parquet reads.

The API routers re-read the same parquet files on every request — at our
current 18-ticker scale that's invisible (each read is 25-115 ms), but the
cost grows linearly with file count. At the 2000-ticker target a single
heatmap that scans 100 tickers would do 100 cold reads = ~2.5 s of pure I/O.

This module wraps `pandas.read_parquet` with `functools.lru_cache`, keyed by
the file's path AND its mtime_ns. When an extractor writes a new parquet,
the mtime changes, the cache key changes, the next request gets the fresh
data — no manual invalidation needed. Stale entries fall out via LRU.

The cache is process-local. With `uvicorn --workers N`, each worker has its
own cache (no inter-process communication needed; parquet reads are
idempotent). For a multi-process Redis-backed cache, see Phase 2 plans.

Usage:
    from backend.app.services.data_cache import read_parquet_cached
    df = read_parquet_cached("backend/data/financials/quarterly_facts/2330.TW.parquet")

Important: callers must NOT mutate the returned DataFrame in place — it's
shared across requests. The standard endpoint pattern (`df[mask]`, `.copy()`
before any modification, `groupby`, `pivot`) is already safe. We rely on
pandas' copy-on-write mode (enabled below) as a safety net for accidental
in-place writes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd


logger = logging.getLogger(__name__)


# Enable pandas copy-on-write so any accidental in-place modification of a
# cached DataFrame creates a divergent copy rather than corrupting the cache.
# Available since pandas 2.0; will be the default in pandas 3.0.
try:
    pd.set_option("mode.copy_on_write", True)
except Exception:  # pragma: no cover - older pandas
    pass


# ---------------------------------------------------------------------------
# Core cache
# ---------------------------------------------------------------------------
#
# `_cached_read` is the actual lru_cache target. It takes only hashable
# primitives (str path, int mtime_ns, optional tuple of column names). The
# higher-level `read_parquet_cached` wrapper translates a Path + columns list
# into the cache key. We use mtime_ns (not mtime float) so we don't lose
# precision on FAT-style filesystems.

# 128 entries is generous at 18 tickers (~6× headroom even with multiple
# column-subset variants per file). At 2000 tickers we'd want ≥2048; bump
# the maxsize via `set_cache_maxsize()` at startup if needed.
_CACHE_MAXSIZE = 128

# Lightweight stats for /admin diagnostics.
_stats_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0, "errors": 0}


@lru_cache(maxsize=_CACHE_MAXSIZE)
def _cached_read(path_str: str, mtime_ns: int, columns: Optional[tuple]) -> pd.DataFrame:
    """The lru_cache-decorated reader. Args are all hashable."""
    cols = list(columns) if columns else None
    return pd.read_parquet(path_str, columns=cols)


def read_parquet_cached(
    path: Path | str,
    *,
    columns: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Read a parquet with LRU caching keyed by (path, mtime_ns, columns).

    Args:
        path: Path or string. Must be an existing parquet file.
        columns: Optional column subset to load. Different column subsets are
                 cached independently so a request asking for ["date"] doesn't
                 evict the full-frame cache entry.

    Returns:
        DataFrame. The same in-memory frame is returned across calls until the
        underlying file's mtime changes. Callers must not mutate in place.

    Raises:
        FileNotFoundError if path does not exist.
    """
    p = Path(path)
    try:
        st = p.stat()
    except FileNotFoundError:
        with _stats_lock:
            _stats["errors"] += 1
        raise
    cols_tuple = tuple(columns) if columns else None
    cache_key = (str(p), st.st_mtime_ns, cols_tuple)

    # functools.lru_cache exposes hits/misses; we mirror it to our own counter
    # so we can report it via /admin without poking the lru_cache internals.
    info_before = _cached_read.cache_info()
    df = _cached_read(*cache_key)
    info_after = _cached_read.cache_info()
    with _stats_lock:
        if info_after.hits > info_before.hits:
            _stats["hits"] += 1
        else:
            _stats["misses"] += 1
    return df


def cache_info() -> dict:
    """Diagnostics — report cache stats and the underlying lru_cache state.
    Safe to call from any router; useful for an /admin/cache endpoint."""
    info = _cached_read.cache_info()
    with _stats_lock:
        snapshot = dict(_stats)
    return {
        "lru_size": info.currsize,
        "lru_maxsize": info.maxsize,
        "lru_hits": info.hits,
        "lru_misses": info.misses,
        "wrapper_hits": snapshot["hits"],
        "wrapper_misses": snapshot["misses"],
        "wrapper_errors": snapshot["errors"],
    }


def cache_clear() -> None:
    """Drop all cached frames. Used by tests; not normally needed in prod."""
    _cached_read.cache_clear()
    with _stats_lock:
        _stats.update({"hits": 0, "misses": 0, "errors": 0})


def set_cache_maxsize(n: int) -> None:
    """Replace the lru_cache with a new maxsize. Drops the existing cache."""
    global _cached_read, _CACHE_MAXSIZE
    _CACHE_MAXSIZE = n

    @lru_cache(maxsize=n)
    def _new_cached_read(path_str: str, mtime_ns: int, columns: Optional[tuple]) -> pd.DataFrame:
        cols = list(columns) if columns else None
        return pd.read_parquet(path_str, columns=cols)

    _cached_read = _new_cached_read
