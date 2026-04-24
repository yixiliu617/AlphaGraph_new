"""
Storage layer for the Taiwan ingestion package.

Public API:
  upsert_monthly_revenue(rows, *, data_dir)     -> UpsertStats
  read_monthly_revenue(*, data_dir)             -> DataFrame
  upsert_material_info(rows, *, data_dir)       -> UpsertStats
  read_material_info(*, data_dir)               -> DataFrame
  write_raw_capture(source, ticker, key, content, *, data_dir) -> Path
  raw_capture_path(source, ticker, key, *, data_dir) -> Path

Parquet schemas documented in docs/superpowers/specs/2026-04-23-taiwan-disclosure-ingestion-design.md
under §"Parquet schemas".

S3 mirror: on successful parquet write, we enqueue the raw file path for
async upload. The mirror is a best-effort, non-blocking operation; local-only
ingest still works if AWS creds are absent or S3 is unreachable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from backend.app.services.taiwan.amendments import (
    AmendmentDecision,
    compute_content_hash,
    detect_amendment,
)

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "taiwan"


@dataclass
class UpsertStats:
    inserted: int = 0
    touched: int = 0
    amended: int = 0


# ---------------------------------------------------------------------------
# Monthly revenue
# ---------------------------------------------------------------------------

_MR_KEY_COLS = ["ticker", "fiscal_ym"]


def _mr_paths(data_dir: Path) -> tuple[Path, Path]:
    return (
        data_dir / "monthly_revenue" / "data.parquet",
        data_dir / "monthly_revenue" / "history.parquet",
    )


def read_monthly_revenue(*, data_dir: Path | None = None) -> pd.DataFrame:
    # Resolve at call time so tests can monkeypatch DEFAULT_DATA_DIR.
    data_dir = data_dir if data_dir is not None else DEFAULT_DATA_DIR
    path, _ = _mr_paths(data_dir)
    if not path.exists():
        return pd.DataFrame(columns=[
            "ticker", "market", "fiscal_ym",
            "revenue_twd", "yoy_pct", "mom_pct", "ytd_pct",
            "cumulative_ytd_twd", "prior_year_month_twd",
            "first_seen_at", "last_seen_at", "content_hash", "amended",
        ])
    return pd.read_parquet(path)


def upsert_monthly_revenue(
    rows: Iterable[dict], *, data_dir: Path = DEFAULT_DATA_DIR
) -> UpsertStats:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "monthly_revenue").mkdir(parents=True, exist_ok=True)
    data_path, history_path = _mr_paths(data_dir)

    current = read_monthly_revenue(data_dir=data_dir)
    stats = UpsertStats()
    now = datetime.now(timezone.utc)

    updated_rows = current.copy()
    history_additions: list[dict] = []

    for row in rows:
        canonical = dict(row)
        canonical["content_hash"] = compute_content_hash(canonical)
        decision = detect_amendment(updated_rows, canonical, key_cols=_MR_KEY_COLS)

        if decision is AmendmentDecision.INSERT:
            canonical["first_seen_at"] = now
            canonical["last_seen_at"] = now
            canonical["amended"] = False
            updated_rows = pd.concat([updated_rows, pd.DataFrame([canonical])], ignore_index=True)
            stats.inserted += 1

        elif decision is AmendmentDecision.TOUCH_ONLY:
            mask = (updated_rows["ticker"] == canonical["ticker"]) & \
                   (updated_rows["fiscal_ym"] == canonical["fiscal_ym"])
            updated_rows.loc[mask, "last_seen_at"] = now
            stats.touched += 1

        elif decision is AmendmentDecision.AMEND:
            mask = (updated_rows["ticker"] == canonical["ticker"]) & \
                   (updated_rows["fiscal_ym"] == canonical["fiscal_ym"])
            prior_row = updated_rows[mask].iloc[0].to_dict()
            # Copy prior to history, then overwrite primary.
            prior_row["superseded_at"] = now
            history_additions.append(prior_row)
            # Preserve first_seen_at from the prior; bump last_seen_at.
            canonical["first_seen_at"] = prior_row.get("first_seen_at", now)
            canonical["last_seen_at"] = now
            canonical["amended"] = True
            # Update the primary row in-place.
            for col, val in canonical.items():
                updated_rows.loc[mask, col] = val
            stats.amended += 1

    updated_rows.to_parquet(data_path, index=False)
    if history_additions:
        hist_df = pd.DataFrame(history_additions)
        if history_path.exists():
            existing_hist = pd.read_parquet(history_path)
            hist_df = pd.concat([existing_hist, hist_df], ignore_index=True)
        hist_df.to_parquet(history_path, index=False)

    logger.info("upsert_monthly_revenue stats=%s", stats)
    return stats


# ---------------------------------------------------------------------------
# Material information (supplemental early-warning stream)
# ---------------------------------------------------------------------------

_MI_KEY_COLS = ["ticker", "announcement_datetime", "subject"]

_MI_COLS = [
    "ticker", "name_zh",
    "announcement_date", "announcement_time", "announcement_datetime",
    "subject", "filing_type",
    "fiscal_ym_guess",          # parsed from subject when a (YYY年MM月) appears; else ''
    "parameters_json",           # raw parameters dict from the API (serialized)
    "first_seen_at", "last_seen_at", "content_hash",
]


def _mi_path(data_dir: Path) -> Path:
    return data_dir / "material_info" / "data.parquet"


def read_material_info(*, data_dir: Path | None = None) -> pd.DataFrame:
    data_dir = data_dir if data_dir is not None else DEFAULT_DATA_DIR
    path = _mi_path(data_dir)
    if not path.exists():
        return pd.DataFrame(columns=_MI_COLS)
    return pd.read_parquet(path)


def upsert_material_info(
    rows: Iterable[dict], *, data_dir: Path = DEFAULT_DATA_DIR,
) -> UpsertStats:
    """Upsert material-info announcements.

    Dedup key: (ticker, announcement_datetime, subject). Same row arriving
    twice (e.g. two polls in the same window catching the same filing)
    becomes a TOUCH_ONLY. Amendments on material info are rare but
    possible (issuers can correct subjects); we treat them as INSERT of
    a distinct (datetime, subject) rather than overwrite.
    """
    (data_dir / "material_info").mkdir(parents=True, exist_ok=True)
    path = _mi_path(data_dir)

    current = read_material_info(data_dir=data_dir)
    stats = UpsertStats()
    now = datetime.now(timezone.utc)
    updated = current.copy()

    for row in rows:
        canonical = dict(row)
        canonical["content_hash"] = compute_content_hash(canonical)

        if updated.empty:
            mask = pd.Series([], dtype=bool)
        else:
            mask = (
                (updated["ticker"] == canonical["ticker"])
                & (updated["announcement_datetime"] == canonical["announcement_datetime"])
                & (updated["subject"] == canonical["subject"])
            )

        if not mask.any():
            canonical["first_seen_at"] = now
            canonical["last_seen_at"] = now
            updated = pd.concat([updated, pd.DataFrame([canonical])], ignore_index=True)
            stats.inserted += 1
        else:
            updated.loc[mask, "last_seen_at"] = now
            stats.touched += 1

    # Ensure the column order is stable when we save (new parquet OR
    # concat of an empty df + first row would otherwise invent order).
    for col in _MI_COLS:
        if col not in updated.columns:
            updated[col] = pd.NA
    updated = updated[_MI_COLS]
    updated.to_parquet(path, index=False)

    logger.info("upsert_material_info stats=%s", stats)
    return stats


# ---------------------------------------------------------------------------
# Raw captures
# ---------------------------------------------------------------------------

def raw_capture_path(
    *, source: str, ticker: str, key: str, data_dir: Path = DEFAULT_DATA_DIR
) -> Path:
    return data_dir / "_raw" / source / ticker / f"{key}.html"


def write_raw_capture(
    *, source: str, ticker: str, key: str, content: bytes,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> Path:
    p = raw_capture_path(source=source, ticker=ticker, key=key, data_dir=data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.read_bytes() == content:
        # Idempotent: already captured identical content.
        return p
    p.write_bytes(content)
    _enqueue_s3_mirror(p, source=source, ticker=ticker, key=key)
    return p


# ---------------------------------------------------------------------------
# S3 mirror (best-effort, non-blocking)
# ---------------------------------------------------------------------------

_S3_BUCKET = os.environ.get("TAIWAN_S3_BUCKET_RAW")  # e.g. "alphagraph-taiwan-raw-prod"


def _enqueue_s3_mirror(path: Path, *, source: str, ticker: str, key: str) -> None:
    """Best-effort sync upload. Intentionally inline for Plan 1 simplicity.
    If creds missing or S3 down, log warning and continue — local write already
    succeeded. Plan 2 / scale may add an async queue."""
    if not _S3_BUCKET:
        return  # Mirror disabled; local-only mode.
    try:
        import boto3
        client = boto3.client("s3")
        s3_key = f"{source}/{ticker}/{key}{path.suffix}"
        client.upload_file(str(path), _S3_BUCKET, s3_key)
    except Exception as exc:
        logger.warning("S3 mirror upload failed path=%s err=%s", path, exc)
