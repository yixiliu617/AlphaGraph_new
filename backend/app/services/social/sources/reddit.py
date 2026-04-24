"""
Scheduler shim for tools/web_scraper/reddit_tracker.

Exposes two callables:
  scrape_subreddits()       -> RedditStats  (subreddit top posts)
  search_keywords()         -> RedditStats  (keyword × subreddit cross-product)

Both wrap the existing `cmd_scrape` / `cmd_search` CLI handlers.
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
REDDIT_SUBPOSTS_PARQUET = (
    _BACKEND / "data" / "market_data" / "reddit" / "subreddit_posts.parquet"
)
REDDIT_KEYWORD_PARQUET = (
    _BACKEND / "data" / "market_data" / "reddit" / "keyword_search.parquet"
)
REDDIT_TRACKER_PY = _REPO / "tools" / "web_scraper" / "reddit_tracker.py"


@dataclass
class RedditStats:
    rows_before: int = 0
    rows_after: int = 0
    new_rows: int = 0
    error: str | None = None


def _import_reddit_tracker():
    mod_name = "alphagraph_reddit_tracker"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, REDDIT_TRACKER_PY)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {REDDIT_TRACKER_PY}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(pd.read_parquet(path, columns=["id"]))
    except Exception:
        return 0


def scrape_subreddits() -> RedditStats:
    stats = RedditStats(rows_before=_row_count(REDDIT_SUBPOSTS_PARQUET))
    try:
        mod = _import_reddit_tracker()
        mod.cmd_scrape(argparse.Namespace(sub=None))
    except Exception as exc:
        stats.error = f"{type(exc).__name__}: {exc}"
    stats.rows_after = _row_count(REDDIT_SUBPOSTS_PARQUET)
    stats.new_rows = stats.rows_after - stats.rows_before
    return stats


def search_keywords() -> RedditStats:
    stats = RedditStats(rows_before=_row_count(REDDIT_KEYWORD_PARQUET))
    try:
        mod = _import_reddit_tracker()
        mod.cmd_search(argparse.Namespace(query=None))
    except Exception as exc:
        stats.error = f"{type(exc).__name__}: {exc}"
    stats.rows_after = _row_count(REDDIT_KEYWORD_PARQUET)
    stats.new_rows = stats.rows_after - stats.rows_before
    return stats
