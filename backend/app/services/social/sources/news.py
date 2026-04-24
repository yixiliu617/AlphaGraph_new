"""
Scheduler shim for tools/web_scraper/news_tracker.

Calls the existing `cmd_scrape(args)` CLI handler with a stub argparse
Namespace, captures the before/after parquet row count to produce
heartbeat-friendly stats.

The CLI file lives in `tools/` and isn't on sys.path by default; we
add it on demand so the scheduler can import it without making the
tools directory a formal package.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# parents[4] = backend/  (this file is backend/app/services/social/sources/news.py)
# parents[5] = repo root
_BACKEND = Path(__file__).resolve().parents[4]
_REPO = _BACKEND.parent
NEWS_PARQUET = _BACKEND / "data" / "market_data" / "news" / "google_news.parquet"
NEWS_TRACKER_PY = _REPO / "tools" / "web_scraper" / "news_tracker.py"


@dataclass
class NewsScrapeStats:
    rows_before: int = 0
    rows_after: int = 0
    new_rows: int = 0          # delta = rows_after - rows_before
    error: str | None = None


def _import_news_tracker():
    """Load tools/web_scraper/news_tracker.py as a module, cached."""
    mod_name = "alphagraph_news_tracker"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, NEWS_TRACKER_PY)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {NEWS_TRACKER_PY}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _row_count() -> int:
    if not NEWS_PARQUET.exists():
        return 0
    try:
        return len(pd.read_parquet(NEWS_PARQUET, columns=["guid"]))
    except Exception:
        return 0


def scrape_all_feeds() -> NewsScrapeStats:
    """Run the full news scrape across all configured feeds.

    Returns stats with before/after row counts so the scheduler can
    emit a meaningful heartbeat.
    """
    stats = NewsScrapeStats(rows_before=_row_count())
    try:
        mod = _import_news_tracker()
        ns = argparse.Namespace(feed=None)
        mod.cmd_scrape(ns)
    except Exception as exc:
        stats.error = f"{type(exc).__name__}: {exc}"
        stats.rows_after = _row_count()
        stats.new_rows = stats.rows_after - stats.rows_before
        return stats
    stats.rows_after = _row_count()
    stats.new_rows = stats.rows_after - stats.rows_before
    return stats
