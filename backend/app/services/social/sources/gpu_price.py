"""
Scheduler shim for tools/web_scraper/gpu_price_tracker.

Calls `cmd_snapshot(argparse.Namespace(provider=None))` which runs
Vast.ai + RunPod + Tensordock in one pass and appends to
gpu_price_history.parquet.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

_BACKEND = Path(__file__).resolve().parents[4]
_REPO = _BACKEND.parent
GPU_HISTORY_PARQUET = (
    _BACKEND / "data" / "market_data" / "gpu_prices" / "gpu_price_history.parquet"
)
GPU_TRACKER_PY = _REPO / "tools" / "web_scraper" / "gpu_price_tracker.py"


@dataclass
class GpuPriceStats:
    rows_before: int = 0
    rows_after: int = 0
    new_rows: int = 0
    error: str | None = None


def _import_gpu_tracker():
    mod_name = "alphagraph_gpu_price_tracker"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, GPU_TRACKER_PY)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {GPU_TRACKER_PY}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _row_count() -> int:
    if not GPU_HISTORY_PARQUET.exists():
        return 0
    try:
        # Use only one cheap column for the count
        return len(pd.read_parquet(GPU_HISTORY_PARQUET).index)
    except Exception:
        return 0


def snapshot_all_providers() -> GpuPriceStats:
    stats = GpuPriceStats(rows_before=_row_count())
    try:
        mod = _import_gpu_tracker()
        mod.cmd_snapshot(argparse.Namespace(provider=None))
    except Exception as exc:
        stats.error = f"{type(exc).__name__}: {exc}"
    stats.rows_after = _row_count()
    stats.new_rows = stats.rows_after - stats.rows_before
    return stats
