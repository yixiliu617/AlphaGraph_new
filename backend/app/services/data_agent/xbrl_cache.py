"""
Per-filing XBRL artifact cache.

Filings are immutable once accepted by the SEC, so the accession number
is a perfect cache key. We persist the five artifacts that the topline
builder needs from each filing:

    facts.parquet                 (xbrl.facts.to_dataframe())
    income_statement.parquet      (xbrl.statements.income_statement().to_dataframe())
    balance_sheet.parquet         (xbrl.statements.balance_sheet().to_dataframe())
    cash_flow_statement.parquet   (xbrl.statements.cash_flow_statement().to_dataframe())
    periods.json                  (list(xbrl.get_periods()))

A `_built` marker file signals that we've already attempted to materialize
all artifacts for that filing, so empty/missing files (some filings don't
have a balance sheet, etc.) don't cause repeated re-fetches.

Cache layout:
    backend/data/filing_data/_xbrl_cache/<accession_no>/
        _built                            # sentinel
        facts.parquet                     # may be absent if extraction failed
        income_statement.parquet
        balance_sheet.parquet
        cash_flow_statement.parquet
        periods.json

Cache invalidation: never. Filings are immutable. Amendments file under
their own accession number, so they don't collide with the original.
Manual purge: just `rm -rf` the cache dir.

Why this exists:
    The topline build was hitting EDGAR + parsing 30 filings × 3 callers ×
    ~30 tickers = ~2,700 XBRL parses per full rebuild, taking ~50 minutes.
    Filings don't change, so 2,690 of those are wasted work after the
    first run. With this cache the second build is ~1-2 minutes.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[4]
_CACHE_DIR = _REPO_ROOT / "backend" / "data" / "filing_data" / "_xbrl_cache"

_STATEMENT_NAMES: tuple[str, ...] = (
    "income_statement",
    "balance_sheet",
    "cash_flow_statement",
)


class XBRLCache:
    """Per-filing parquet cache for edgartools XBRL extractions.

    Usage:
        cache = XBRLCache()
        df = cache.get_facts(filing)
        df = cache.get_statement(filing, "income_statement")

    All methods build the cache on first miss for a filing (calling
    XBRL.from_filing once and writing all four artifacts), then serve
    subsequent calls from disk. Cache hits/misses are tracked on the
    instance and exposed via `stats()`.
    """

    def __init__(self) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._hits = 0
        self._misses = 0

    # ---- public API ----------------------------------------------------

    def get_facts(self, filing) -> pd.DataFrame:
        """Return the filing's facts DataFrame (cached)."""
        return self._read("facts", filing)

    def get_statement(self, filing, name: str) -> pd.DataFrame:
        """Return the filing's statement DataFrame for `name`. Cached.

        `name` must be one of: 'income_statement', 'balance_sheet',
        'cash_flow_statement'. Returns an empty DataFrame if the filing
        had no such statement (e.g. some 10-Q amendments).
        """
        if name not in _STATEMENT_NAMES:
            raise ValueError(
                f"unknown statement name: {name!r}. expected one of {_STATEMENT_NAMES}"
            )
        return self._read(name, filing)

    def get_periods_list(self, filing) -> list[dict]:
        """Return the cached output of `xbrl.get_periods()` as a list of
        dicts. Empty list if extraction failed for this filing."""
        fdir = self._filing_dir(filing)
        marker = fdir / "_built"
        target = fdir / "periods.json"

        if marker.exists():
            self._hits += 1
            if target.exists():
                try:
                    return json.loads(target.read_text(encoding="utf-8"))
                except Exception:
                    return []
            return []

        self._misses += 1
        self._materialize(filing)
        if target.exists():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses}

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0

    # ---- internal ------------------------------------------------------

    def _filing_dir(self, filing) -> Path:
        accession = str(getattr(filing, "accession_no", "") or "")
        if not accession:
            # Fall back to a synthetic key from filing repr -- shouldn't
            # happen in production but keeps us from crashing.
            accession = "_unknown_" + str(id(filing))
        return _CACHE_DIR / accession

    def _read(self, artifact: str, filing) -> pd.DataFrame:
        fdir = self._filing_dir(filing)
        marker = fdir / "_built"
        target = fdir / f"{artifact}.parquet"

        if marker.exists():
            self._hits += 1
            if target.exists():
                return pd.read_parquet(target)
            # Marker present but artifact missing => extraction was
            # attempted but produced nothing for this filing. Return empty.
            return pd.DataFrame()

        # Cache miss: build all artifacts for this filing in one pass so
        # subsequent calls for other artifacts in the same run are hits.
        self._misses += 1
        self._materialize(filing)

        if target.exists():
            return pd.read_parquet(target)
        return pd.DataFrame()

    def _materialize(self, filing) -> None:
        """Run XBRL.from_filing once for this filing and persist all four
        artifacts (facts + 3 statements). Failures on individual statements
        are tolerated -- the marker still gets written so we don't keep
        retrying."""
        from edgar.xbrl import XBRL

        fdir = self._filing_dir(filing)
        fdir.mkdir(parents=True, exist_ok=True)

        try:
            xbrl = XBRL.from_filing(filing)
        except Exception as exc:
            logger.debug("XBRL.from_filing failed for %s: %s",
                         getattr(filing, "accession_no", "?"), exc)
            (fdir / "_built").touch()
            return

        # facts
        try:
            df = xbrl.facts.to_dataframe()
            if df is not None and len(df) > 0:
                df.to_parquet(fdir / "facts.parquet", index=False)
        except Exception as exc:
            logger.debug("facts to_dataframe failed for %s: %s",
                         getattr(filing, "accession_no", "?"), exc)

        # statements
        for name in _STATEMENT_NAMES:
            try:
                stmt = getattr(xbrl.statements, name)()
                if stmt is None:
                    continue
                df = stmt.to_dataframe()
                if df is not None and len(df) > 0:
                    df.to_parquet(fdir / f"{name}.parquet", index=False)
            except Exception as exc:
                logger.debug("%s to_dataframe failed for %s: %s",
                             name, getattr(filing, "accession_no", "?"), exc)

        # periods list (used by _augment_period_map_from_filings)
        try:
            periods = list(xbrl.get_periods())
            (fdir / "periods.json").write_text(
                json.dumps(periods, default=str), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug("get_periods failed for %s: %s",
                         getattr(filing, "accession_no", "?"), exc)

        (fdir / "_built").touch()


    # ---- stitched (per-ticker) cache --------------------------------
    # XBRLS.from_filings(filings) does a heavy cross-filing reconciliation
    # and is the largest single cost in the build. Its output (the
    # consolidated DataFrames per statement, plus the periods list) is a
    # deterministic function of the filing set, so we cache by a hash of
    # the sorted accession-no list.

    def _stitched_dir(self, ticker: str, filings) -> Path:
        accessions = sorted(str(getattr(f, "accession_no", "") or "") for f in filings)
        if not any(accessions):
            # Pathological case: empty key. Don't pollute cache with garbage.
            return _CACHE_DIR / "_stitched" / ticker / "_empty"
        h = hashlib.sha1("|".join(accessions).encode("utf-8")).hexdigest()[:16]
        return _CACHE_DIR / "_stitched" / ticker / h

    def get_stitched_statement(self, ticker: str, filings, name: str,
                                max_periods: int = 40) -> pd.DataFrame:
        """Return the stitched statement DataFrame for a ticker / filings
        set. Cached. `name` in {'income_statement','balance_sheet','cash_flow_statement'}."""
        if name not in _STATEMENT_NAMES:
            raise ValueError(f"unknown statement name: {name!r}")
        d = self._stitched_dir(ticker, filings)
        target = d / f"{name}.parquet"
        marker = d / "_built"
        if marker.exists():
            self._hits += 1
            if target.exists():
                return pd.read_parquet(target)
            return pd.DataFrame()
        self._misses += 1
        self._materialize_stitched(ticker, filings)
        if target.exists():
            return pd.read_parquet(target)
        return pd.DataFrame()

    def get_stitched_periods(self, ticker: str, filings) -> list[dict]:
        """Return the stitched `xbrls.get_periods()` output as a list of
        dicts. Cached."""
        d = self._stitched_dir(ticker, filings)
        target = d / "periods.json"
        marker = d / "_built"
        if marker.exists():
            self._hits += 1
            if target.exists():
                try:
                    return json.loads(target.read_text(encoding="utf-8"))
                except Exception:
                    return []
            return []
        self._misses += 1
        self._materialize_stitched(ticker, filings)
        if target.exists():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _materialize_stitched(self, ticker: str, filings) -> None:
        """Build the stitched XBRLS object once and persist all four
        artifacts (3 statement DataFrames + periods list)."""
        from edgar.xbrl import XBRLS

        d = self._stitched_dir(ticker, filings)
        d.mkdir(parents=True, exist_ok=True)

        try:
            xbrls = XBRLS.from_filings(filings)
        except Exception as exc:
            logger.warning("XBRLS.from_filings failed for %s: %s", ticker, exc)
            (d / "_built").touch()
            return

        # Persist the accession set for human inspection / debugging.
        accessions = [str(getattr(f, "accession_no", "")) for f in filings]
        (d / "accessions.json").write_text(json.dumps(accessions), encoding="utf-8")

        # 3 statements
        for name in _STATEMENT_NAMES:
            try:
                method = getattr(xbrls.statements, name)
                stmt = method(max_periods=40)
                if stmt is None:
                    continue
                df = stmt.to_dataframe()
                if df is not None and len(df) > 0:
                    df.to_parquet(d / f"{name}.parquet", index=False)
            except Exception as exc:
                logger.debug("stitched %s failed for %s: %s", name, ticker, exc)

        # Periods
        try:
            periods = list(xbrls.get_periods())
            (d / "periods.json").write_text(
                json.dumps(periods, default=str), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug("stitched get_periods failed for %s: %s", ticker, exc)

        (d / "_built").touch()


# Module-level singleton -- safe because the cache is read-only after the
# first miss for any given filing, and writes are idempotent (parquet write
# overwrites cleanly on the rare race).
_default_cache: XBRLCache | None = None


def get_default_cache() -> XBRLCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = XBRLCache()
    return _default_cache
