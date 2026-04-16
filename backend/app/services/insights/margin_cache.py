"""
margin_cache.py -- disk-backed cache for MarginInsights.

Layout on disk (JSON at backend/data/insights/margin_insights.json):

    {
      "NVDA": {
        "2026-01-26": { <MarginInsights JSON> },
        "2025-10-27": { <MarginInsights JSON> }
      },
      "AMD":  { ... }
    }

Cache key is (ticker, period_end). When a new quarter lands, the new
period_end becomes the lookup key and the old entry is implicitly dead
weight -- not evicted automatically, but never read either.

Thread-safety: file I/O is not thread-safe. Callers should serialize
writes per ticker. In practice MarginInsightsService is invoked per-request
and writes are rare (first load per quarter), so races are unlikely.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .margin_schemas import MarginInsights

log = logging.getLogger(__name__)

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[4]
_CACHE_DIR = _REPO_ROOT / "backend" / "data" / "insights"
_CACHE_FILE = _CACHE_DIR / "margin_insights.json"


class MarginInsightsCache:

    def __init__(self) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = self._load()

    # -- public API -----------------------------------------------------

    def get(self, ticker: str, period_end: str) -> MarginInsights | None:
        entry = self._data.get(ticker.upper(), {}).get(period_end)
        if not entry:
            return None
        try:
            return MarginInsights.model_validate(entry)
        except Exception as exc:
            log.warning("Cached insights for %s %s failed validation: %s", ticker, period_end, exc)
            return None

    def set(self, insights: MarginInsights) -> None:
        ticker = insights.ticker.upper()
        self._data.setdefault(ticker, {})[insights.period_end] = insights.model_dump(mode="json")
        self._save()

    def invalidate(self, ticker: str) -> None:
        self._data.pop(ticker.upper(), None)
        self._save()

    # -- persistence ----------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        if not _CACHE_FILE.exists():
            return {}
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("margin_insights.json unreadable: %s -- starting empty", exc)
            return {}

    def _save(self) -> None:
        try:
            _CACHE_FILE.write_text(
                json.dumps(self._data, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            log.error("Failed to write margin_insights.json: %s", exc)
