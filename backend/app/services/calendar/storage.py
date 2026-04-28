"""
Storage layer for the earnings calendar.

Public API:
  read_events(*, data_dir=None, market=None, status=None, ticker=None,
              from_date=None, to_date=None) -> DataFrame
  upsert_events(rows, *, data_dir=None) -> UpsertStats
  get_event(ticker, fiscal_period, *, data_dir=None) -> dict | None

Schema (one row per (ticker, fiscal_period)):
  ticker                str   "NVDA", "2330" (Taiwan)
  market                str   "US" | "TW" | "JP" | "KR"
  fiscal_period         str   "FY2026-Q3", "FY2025"  -- normalized format
  release_datetime_utc  ts    when the earnings call/release happens (UTC)
  release_local_tz      str   IANA tz, e.g. "America/New_York", "Asia/Taipei"
  status                str   "upcoming" | "confirmed" | "done"

  -- Hard data (from regulator):
  press_release_url     str   8-K exhibit URL (US) / MOPS announcement URL (TW)
  filing_url            str   the originating filing URL

  -- Soft data (LLM-enriched, optional):
  webcast_url           str
  transcript_url        str
  dial_in_phone         str
  dial_in_pin           str

  -- Provenance:
  source                str   "edgar_8k" | "mops_material_info" | "nasdaq_calendar"
                              | "yahoo_calendar" | "llm_grounded" | "manual"
  source_id             str   accession_no, MOPS notification ID, etc.

  -- Cross-reference (used for upcoming events):
  verification          str   "nasdaq+yahoo_match" (both agree on date)
                            | "nasdaq_only"        (NASDAQ has, Yahoo missing)
                            | "yahoo_only"         (Yahoo has, NASDAQ missing)
                            | "date_disagreement"  (both have, dates differ > 1 day)
                            | ""                   (single-source / past event)

  -- NASDAQ-rich fields (when source includes NASDAQ calendar data; else NaN):
  time_of_day_code      str   "BMO" (before-market-open, ~08:00 ET)
                            | "AMC" (after-market-close, ~16:30 ET)
                            | "TBD" (unspecified)
                            | ""   (non-NASDAQ source)
  eps_forecast          float consensus EPS forecast (USD/share); None if unavailable
  eps_estimates_count   Int64 number of analyst estimates contributing to consensus
  market_cap            float market cap in USD at time of NASDAQ snapshot
  last_year_eps         float reported EPS for the prior-year same quarter
  last_year_report_date date  date of the prior-year same quarter's earnings release

  -- Audit:
  first_seen_at         ts
  last_updated_at       ts

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Any

import pandas as pd

logger = logging.getLogger(__name__)

# Resolve to backend/data/earnings_calendar regardless of CWD.
# parents[3] from backend/app/services/calendar/storage.py == backend/
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "earnings_calendar"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

KEY_COLS = ["ticker", "fiscal_period"]

ALL_COLS = [
    "ticker", "market", "fiscal_period",
    "release_datetime_utc", "release_local_tz", "status",
    "press_release_url", "filing_url",
    "webcast_url", "transcript_url", "dial_in_phone", "dial_in_pin",
    "source", "source_id",
    "verification",
    "time_of_day_code",
    "eps_forecast", "eps_estimates_count", "market_cap",
    "last_year_eps", "last_year_report_date",
    # Per-source soft-field provenance columns (Method A / B / C):
    "webcast_url_a",        "webcast_url_b",        "webcast_url_c",
    "dial_in_phone_a",      "dial_in_phone_b",      "dial_in_phone_c",
    "dial_in_pin_a",        "dial_in_pin_b",        "dial_in_pin_c",
    "press_release_url_a",  "press_release_url_b",  "press_release_url_c",
    "transcript_url_b",
    # Per-source enrichment metadata:
    "enrichment_a_attempted_at",
    "enrichment_b_attempted_at",
    "enrichment_c_attempted_at",
    "enrichment_b_cost_usd",
    "enrichment_c_vendor",
    "first_seen_at", "last_updated_at",
]

# Fields that may be null for an upcoming event but should never be empty for done.
CORE_FIELDS = ["ticker", "market", "fiscal_period"]


@dataclass
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    touched: int = 0   # row already existed with same data, only last_updated_at bumped


def _is_empty(v) -> bool:
    """True for None, empty string, NaN/NA/NaT. Robust against pandas'
    ambiguous-truth error on `value in (..., pd.NA)`."""
    if v is None:
        return True
    if isinstance(v, str):
        return v == ""
    if isinstance(v, (list, tuple, dict, set)):
        return False
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


# The 4 soft fields and their per-source column names, in run-order priority
# (A first, then B, then C as last resort). C runs only on rows where A+B
# left at least one field empty, so the resolver's first-non-null logic
# matches the runtime contract.
_SOFT_FIELD_SOURCES: dict[str, tuple[str, str, str]] = {
    "webcast_url":       ("webcast_url_a",       "webcast_url_b",       "webcast_url_c"),
    "dial_in_phone":     ("dial_in_phone_a",     "dial_in_phone_b",     "dial_in_phone_c"),
    "dial_in_pin":       ("dial_in_pin_a",       "dial_in_pin_b",       "dial_in_pin_c"),
    "press_release_url": ("press_release_url_a", "press_release_url_b", "press_release_url_c"),
}


def _resolve_soft_fields(row: pd.Series) -> dict[str, str | None]:
    """Return a dict mapping the public soft-field name to the first
    non-null value across (a, b, c) sources in run-order priority."""
    out: dict[str, str | None] = {}
    for public, (a, b, c) in _SOFT_FIELD_SOURCES.items():
        for col in (a, b, c):
            v = row.get(col)
            if not _is_empty(v):
                out[public] = v
                break
        else:
            out[public] = None
    # transcript_url has only one source (B)
    tv = row.get("transcript_url_b")
    out["transcript_url"] = None if _is_empty(tv) else tv
    return out


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def _events_path(data_dir: Path) -> Path:
    return data_dir / "events.parquet"


def _empty_frame() -> pd.DataFrame:
    df = pd.DataFrame(columns=ALL_COLS)
    # Coerce timestamp columns
    for c in ("release_datetime_utc", "first_seen_at", "last_updated_at"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    return df


def read_events(
    *,
    data_dir: Path | None = None,
    market: str | None = None,
    status: str | None = None,
    ticker: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> pd.DataFrame:
    """Read the events parquet, optionally filtered.

    All filters are AND-combined. `from_date` / `to_date` filter on
    `release_datetime_utc`. None = no filter."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    p = _events_path(data_dir)
    if not p.exists():
        return _empty_frame()
    df = pd.read_parquet(p)
    # Ensure expected columns exist on legacy parquets
    for c in ALL_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[ALL_COLS]

    if market:
        df = df[df["market"] == market]
    if status:
        df = df[df["status"] == status]
    if ticker:
        df = df[df["ticker"] == ticker]
    if from_date is not None:
        df = df[df["release_datetime_utc"] >= _to_utc_timestamp(from_date)]
    if to_date is not None:
        df = df[df["release_datetime_utc"] <= _to_utc_timestamp(to_date)]
    df = df.reset_index(drop=True)

    if df.empty:
        return df

    # Materialize the public soft-field columns from per-source columns.
    # Frontend continues to read webcast_url / dial_in_phone / etc. without
    # caring about provenance.
    for idx, row in df.iterrows():
        resolved = _resolve_soft_fields(row)
        for public_col, value in resolved.items():
            if value is not None:
                df.at[idx, public_col] = value

    return df


def _to_utc_timestamp(value: datetime | str | pd.Timestamp) -> pd.Timestamp:
    """Coerce input to a tz-aware UTC Timestamp regardless of whether it
    arrives as a naive datetime, tz-aware datetime, ISO string, or pandas
    Timestamp. pandas refuses pd.Timestamp(value, tz=...) when value already
    carries tzinfo, so we branch."""
    ts = pd.Timestamp(value)
    if ts.tz is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def get_event(
    ticker: str, fiscal_period: str, *, data_dir: Path | None = None,
) -> dict | None:
    df = read_events(data_dir=data_dir, ticker=ticker)
    row = df[df["fiscal_period"] == fiscal_period]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _write(df: pd.DataFrame, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    # Sort for deterministic diff
    df = df.sort_values(
        ["market", "ticker", "fiscal_period"], kind="stable"
    ).reset_index(drop=True)
    df.to_parquet(_events_path(data_dir), index=False)


def upsert_events(
    rows: Iterable[dict[str, Any]],
    *,
    data_dir: Path | None = None,
) -> UpsertStats:
    """Insert / update calendar events keyed by (ticker, fiscal_period).

    For each input row:
      - If no existing row: INSERT (set first_seen_at and last_updated_at).
      - If existing row + any non-key field differs (and the new value is not
        empty): UPDATE the differing fields, bump last_updated_at. Empty
        new values do NOT clobber existing data -- this lets a soft-data
        enrichment pass add webcast_url to a row whose press_release_url
        was set by the regulator pass.
      - If existing row + everything identical (or new is empty): TOUCH only
        last_updated_at.
    """
    data_dir = data_dir or DEFAULT_DATA_DIR
    rows_list = list(rows)
    if not rows_list:
        return UpsertStats()

    current = read_events(data_dir=data_dir)
    stats = UpsertStats()
    now = pd.Timestamp.now(tz="UTC")

    # Index existing rows by (ticker, fiscal_period) for O(1) lookup.
    if current.empty:
        idx: dict[tuple[str, str], int] = {}
    else:
        idx = {
            (r["ticker"], r["fiscal_period"]): i
            for i, r in current.iterrows()
        }

    updated_rows = current.copy()
    new_rows: list[dict] = []

    for row in rows_list:
        # Validate core fields
        missing = [c for c in CORE_FIELDS if not row.get(c)]
        if missing:
            logger.warning("calendar upsert: skipping row missing %s: %r", missing, row)
            continue

        key = (row["ticker"], row["fiscal_period"])
        if key not in idx:
            # INSERT
            canonical = {c: row.get(c) for c in ALL_COLS}
            canonical["first_seen_at"] = now
            canonical["last_updated_at"] = now
            new_rows.append(canonical)
            stats.inserted += 1
        else:
            # UPDATE / TOUCH
            i = idx[key]
            changed_any = False
            for col in ALL_COLS:
                if col in ("first_seen_at", "last_updated_at"):
                    continue
                new_v = row.get(col)
                if _is_empty(new_v):
                    continue  # don't clobber existing with empty
                old_v = updated_rows.at[i, col]
                # NaN-safe compare
                if _is_empty(old_v) or old_v != new_v:
                    updated_rows.at[i, col] = new_v
                    changed_any = True
            updated_rows.at[i, "last_updated_at"] = now
            if changed_any:
                stats.updated += 1
            else:
                stats.touched += 1

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        # Make sure dtypes line up before concat (especially timestamp cols).
        for c in ("release_datetime_utc", "first_seen_at", "last_updated_at"):
            if c in new_df.columns:
                new_df[c] = pd.to_datetime(new_df[c], utc=True, errors="coerce")
        updated_rows = pd.concat([updated_rows, new_df], ignore_index=True)

    _write(updated_rows, data_dir)
    logger.info("calendar upsert stats=%s", stats)
    return stats


# ---------------------------------------------------------------------------
# Helpers exposed to ingest scripts
# ---------------------------------------------------------------------------

def normalize_fiscal_period(label: str) -> str:
    """Normalize a fiscal-period label.

    Accepts "FY2026-Q3", "Q3 FY2026", "Q3 2026", "2026Q3" -> "FY2026-Q3".
    Accepts "FY2025" -> "FY2025".
    Returns the input unchanged if it can't be parsed (caller should validate).
    """
    if not label:
        return ""
    s = label.strip().upper().replace(" ", "")
    # Already canonical
    import re
    m = re.match(r"^FY(\d{4})-Q([1-4])$", s)
    if m:
        return s
    m = re.match(r"^Q([1-4])FY(\d{4})$", s)
    if m:
        return f"FY{m.group(2)}-Q{m.group(1)}"
    m = re.match(r"^Q([1-4])(\d{4})$", s)
    if m:
        return f"FY{m.group(2)}-Q{m.group(1)}"
    m = re.match(r"^(\d{4})Q([1-4])$", s)
    if m:
        return f"FY{m.group(1)}-Q{m.group(2)}"
    m = re.match(r"^FY(\d{4})$", s)
    if m:
        return s
    return label
