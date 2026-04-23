"""
Content-hash based amendment detection for Taiwan parquet datasets.

Rule:
  hash(canonical(row)) = sha256 of json-sorted-keys(row_without_mutable_fields).
  Compare to the prior row's stored hash; classify as INSERT, TOUCH_ONLY, or AMEND.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Iterable

import pandas as pd


# Columns that change every ingest and therefore MUST NOT participate in the hash.
_MUTABLE_FIELDS = {"first_seen_at", "last_seen_at", "content_hash", "amended"}


class AmendmentDecision(str, Enum):
    INSERT = "insert"
    TOUCH_ONLY = "touch_only"
    AMEND = "amend"


def canonicalise_row(row: dict) -> str:
    filtered = {k: v for k, v in row.items() if k not in _MUTABLE_FIELDS}
    # sort_keys + default=str for timestamps / numpy types
    return json.dumps(filtered, sort_keys=True, default=str, ensure_ascii=False)


def compute_content_hash(row: dict) -> str:
    return hashlib.sha256(canonicalise_row(row).encode("utf-8")).hexdigest()


def detect_amendment(
    prior_df: pd.DataFrame, new_row: dict, *, key_cols: Iterable[str]
) -> AmendmentDecision:
    if prior_df.empty:
        return AmendmentDecision.INSERT
    mask = pd.Series([True] * len(prior_df))
    for k in key_cols:
        mask &= (prior_df[k] == new_row[k])
    match = prior_df[mask]
    if match.empty:
        return AmendmentDecision.INSERT
    prior_hash = str(match.iloc[0].get("content_hash") or "")
    new_hash = str(new_row.get("content_hash") or compute_content_hash(new_row))
    return AmendmentDecision.TOUCH_ONLY if prior_hash == new_hash else AmendmentDecision.AMEND
