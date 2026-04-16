"""
splits.py — stock split cache + retroactive share / EPS adjustment.

Why
----
Financial filings are reported in *point-in-time* share denomination. When a
company executes a stock split, older quarters are reported in pre-split
shares but newer filings use post-split shares. A time series built from
raw filings therefore has a discontinuity at every split boundary — EPS
drops, share count jumps, YoY growth rates look absurd.

The fix is **retroactive adjustment**: multiply every pre-split share count
by the cumulative split ratio and divide pre-split per-share values (EPS,
dividends) by the same ratio. The result is a continuous series expressed
in the current denomination, matching how Bloomberg / FactSet display data.

Data source
-----------
`yfinance.Ticker(ticker).splits` — returns a pandas Series indexed by split
date, values are the ratio (e.g. 10.0 = 10-for-1 split where shares increase
10x).  Free, no API key, scraped from Yahoo Finance.

Caching
-------
Splits are immutable history — once fetched, they don't change (unless a
company does another split, in which case the new one is appended). We
cache per-ticker in `backend/data/filing_data/splits/splits.json`.

Refresh strategy:
    - Cache hit + not stale (< 30 days old) → use cache
    - Cache hit + stale                     → refetch, write back
    - Cache miss                            → fetch, write

Graceful degradation: if yfinance is unreachable or errors out, return an
empty list. The topline builder continues without adjustments — the data
will have split discontinuities but the build won't fail.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths / knobs
# ---------------------------------------------------------------------------

_THIS_FILE   = Path(__file__).resolve()
_REPO_ROOT   = _THIS_FILE.parents[4]
_SPLITS_DIR  = _REPO_ROOT / "backend" / "data" / "filing_data" / "splits"
_SPLITS_FILE = _SPLITS_DIR / "splits.json"

_STALE_DAYS = 30   # refetch if cache entry is older than this


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------

@dataclass
class Split:
    """One stock split event."""
    date:  pd.Timestamp   # effective date (ex-split)
    ratio: float          # >1 for forward split, <1 for reverse split


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class SplitsCache:
    """
    Per-ticker splits cache backed by splits.json.

    Thread-safety: file I/O is not thread-safe; callers should serialize
    writes per ticker. The topline builder already processes tickers
    sequentially so this is a non-issue in practice.
    """

    def __init__(self) -> None:
        _SPLITS_DIR.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    # -- Public API ---------------------------------------------------------

    def get_splits(self, ticker: str, force_refresh: bool = False) -> list[Split]:
        """
        Return all known splits for a ticker, sorted by date ascending.

        Fetches from yfinance on cache miss or when the cached entry is stale.
        Returns an empty list if yfinance is unreachable or the ticker has no
        splits on record. Never raises.
        """
        ticker = ticker.upper().strip()
        entry = self._data.get(ticker)

        if not force_refresh and entry and not self._is_stale(entry):
            return self._parse_splits(entry.get("splits", []))

        # Fetch fresh
        try:
            fresh = self._fetch_from_yfinance(ticker)
        except Exception as exc:
            log.warning("Failed to fetch splits for %s: %s", ticker, exc)
            # Return cached data if we have any, else empty
            return self._parse_splits(entry.get("splits", [])) if entry else []

        # Write back
        self._data[ticker] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "splits":     [{"date": s.date.strftime("%Y-%m-%d"), "ratio": s.ratio} for s in fresh],
        }
        self._save()
        return fresh

    # -- Internal: yfinance fetch ------------------------------------------

    @staticmethod
    def _fetch_from_yfinance(ticker: str) -> list[Split]:
        import yfinance as yf
        series = yf.Ticker(ticker).splits
        if series is None or series.empty:
            return []
        splits: list[Split] = []
        for ts, ratio in series.items():
            # yfinance returns tz-aware timestamps; normalize to naive UTC date
            date = pd.Timestamp(ts).tz_localize(None).normalize() if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).normalize()
            splits.append(Split(date=date, ratio=float(ratio)))
        splits.sort(key=lambda s: s.date)
        return splits

    # -- Internal: persistence ---------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not _SPLITS_FILE.exists():
            return {}
        try:
            return json.loads(_SPLITS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("splits.json unreadable: %s — starting empty", exc)
            return {}

    def _save(self) -> None:
        try:
            _SPLITS_FILE.write_text(
                json.dumps(self._data, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            log.error("Failed to write splits.json: %s", exc)

    @staticmethod
    def _is_stale(entry: dict) -> bool:
        fetched = entry.get("fetched_at")
        if not fetched:
            return True
        try:
            dt = datetime.fromisoformat(fetched)
        except Exception:
            return True
        return (datetime.now(timezone.utc) - dt) > timedelta(days=_STALE_DAYS)

    @staticmethod
    def _parse_splits(raw: list[dict]) -> list[Split]:
        out: list[Split] = []
        for item in raw:
            try:
                out.append(Split(
                    date=pd.Timestamp(item["date"]),
                    ratio=float(item["ratio"]),
                ))
            except Exception:
                continue
        out.sort(key=lambda s: s.date)
        return out


# ---------------------------------------------------------------------------
# Adjustment logic
# ---------------------------------------------------------------------------

# Default field classification. Callers can override via function args.
_SHARE_COUNT_FIELDS = ("shares_basic", "shares_diluted")
_PER_SHARE_FIELDS   = ("eps_basic", "eps_diluted")


def apply_split_adjustments(
    df: pd.DataFrame,
    ticker: str,
    cache: SplitsCache | None = None,
    date_col: str = "period_end",
) -> pd.DataFrame:
    """
    Return a copy of ``df`` with share counts and per-share values retroactively
    adjusted to the current (post-all-splits) denomination.

    Rules (for a split with ratio R on date D):
        - rows with date_col <  D:  shares *= R,  EPS /= R
        - rows with date_col >= D:  unchanged

    Multiple splits compound correctly because they're applied in chronological
    order. E.g. NVDA's 4-for-1 (2021) followed by 10-for-1 (2024) produces a
    cumulative 40x adjustment for rows before 2021, 10x for rows between 2021
    and 2024, and 1x (unchanged) for rows after 2024.

    If no splits are cached/available for the ticker, the frame is returned
    unchanged. Never raises — any yfinance failure just means no adjustment.
    """
    if df.empty or date_col not in df.columns:
        return df

    cache = cache or SplitsCache()
    splits = cache.get_splits(ticker)
    if not splits:
        return df

    df = df.copy()

    for split in splits:
        pre_mask = df[date_col] < split.date
        if not pre_mask.any():
            continue

        for col in _SHARE_COUNT_FIELDS:
            if col in df.columns:
                df.loc[pre_mask, col] = df.loc[pre_mask, col] * split.ratio

        for col in _PER_SHARE_FIELDS:
            if col in df.columns:
                df.loc[pre_mask, col] = df.loc[pre_mask, col] / split.ratio

    return df
