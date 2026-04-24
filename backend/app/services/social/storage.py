"""
Parquet storage for canonical social posts.

Dedup key: (platform, post_id). Same post arriving twice with the same
content_hash → TOUCH_ONLY. Same key with a different hash (engagement
drift OR body edit) → AMEND: prior row goes to history.parquet, primary
row gets the new values, `edited=True`.

File layout:
  backend/data/social/x/data.parquet
  backend/data/social/x/history.parquet
  backend/data/social/wechat/data.parquet   (future)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from backend.app.services.social.canonical import (
    CANONICAL_COLUMNS,
    compute_post_content_hash,
)

logger = logging.getLogger(__name__)

# parents[3] = backend/, so DEFAULT_DATA_DIR = <repo>/backend/data/social
# Matches the taiwan data-dir convention (backend/data/taiwan/...).
DEFAULT_DATA_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "social"
)


@dataclass
class SocialUpsertStats:
    inserted: int = 0
    touched: int = 0
    amended: int = 0


def _platform_paths(data_dir: Path, platform: str) -> tuple[Path, Path]:
    sub = platform.lower()
    return (
        data_dir / sub / "data.parquet",
        data_dir / sub / "history.parquet",
    )


def read_social_posts(
    *, platform: str, data_dir: Path | None = None,
) -> pd.DataFrame:
    data_dir = data_dir if data_dir is not None else DEFAULT_DATA_DIR
    primary, _ = _platform_paths(data_dir, platform)
    if not primary.exists():
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    return pd.read_parquet(primary)


def upsert_social_posts(
    rows: Iterable[dict],
    *,
    platform: str,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> SocialUpsertStats:
    """Upsert canonical social-post rows for one platform.

    `rows` must contain all CANONICAL_COLUMNS except first_seen_at /
    last_seen_at / content_hash / edited — those are added here.
    """
    sub = platform.lower()
    (data_dir / sub).mkdir(parents=True, exist_ok=True)
    primary, history = _platform_paths(data_dir, platform)

    current = read_social_posts(platform=platform, data_dir=data_dir)
    stats = SocialUpsertStats()
    now = datetime.now(timezone.utc)

    updated = current.copy()
    history_additions: list[dict] = []

    for row in rows:
        canonical = dict(row)
        canonical["content_hash"] = compute_post_content_hash(canonical)

        if updated.empty:
            mask = pd.Series([], dtype=bool)
        else:
            mask = (
                (updated["platform"] == platform)
                & (updated["post_id"] == canonical["post_id"])
            )

        if not mask.any():
            canonical["first_seen_at"] = now
            canonical["last_seen_at"] = now
            canonical["edited"] = False
            updated = pd.concat(
                [updated, pd.DataFrame([canonical])], ignore_index=True,
            )
            stats.inserted += 1
            continue

        # A row with this key exists — compare hashes.
        existing_hash = str(updated.loc[mask, "content_hash"].iloc[0])
        if existing_hash == canonical["content_hash"]:
            updated.loc[mask, "last_seen_at"] = now
            stats.touched += 1
            continue

        # Hash diverged — AMEND. Copy prior into history, overwrite primary.
        prior = updated.loc[mask].iloc[0].to_dict()
        prior["superseded_at"] = now
        history_additions.append(prior)

        canonical["first_seen_at"] = prior.get("first_seen_at", now)
        canonical["last_seen_at"] = now
        canonical["edited"] = True
        for col, val in canonical.items():
            if col in updated.columns:
                updated.loc[mask, col] = val
        stats.amended += 1

    # Keep columns in canonical order for readability + stability.
    for col in CANONICAL_COLUMNS:
        if col not in updated.columns:
            updated[col] = pd.NA
    updated = updated[CANONICAL_COLUMNS]
    updated.to_parquet(primary, index=False)

    if history_additions:
        hdf = pd.DataFrame(history_additions)
        if history.exists():
            hdf = pd.concat(
                [pd.read_parquet(history), hdf], ignore_index=True,
            )
        hdf.to_parquet(history, index=False)

    logger.info(
        "upsert_social_posts platform=%s stats=%s", platform, stats,
    )
    return stats
