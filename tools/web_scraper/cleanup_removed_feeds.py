"""
One-off: purge google_news.parquet rows for feeds that have been removed
from news_config.json, and re-tag rows belonging to feeds that were merged.

Idempotent — running twice produces the same result.

Removed feeds (historical rows dropped):
  - Regulation: Healthcare & Biotech  (feed_key regulation_healthcare)
  - Macro: Geopolitics & Trade        (feed_key macro_geopolitics)
  - Macro: Economy & Markets          (feed_key macro_economy)
  - Trump Policy Announcements        (feed_key trump_policy_announcements)

Merged feeds (re-tagged to the survivor):
  - "AI Funding / VC / IPO / M&A"  -> "AI Business Dynamics (Funding + Revenue + M&A)"
    feed_key: ai_startup_funding    -> ai_business_dynamics
  - "AI Startup Revenue & ARR"     -> same target

Usage:
    python tools/web_scraper/cleanup_removed_feeds.py
    python tools/web_scraper/cleanup_removed_feeds.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PARQUET = Path("backend/data/market_data/news/google_news.parquet")

REMOVED_LABELS = {
    "Regulation: Healthcare & Biotech",
    "Macro: Geopolitics & Trade",
    "Macro: Economy & Markets",
    "Trump Policy Announcements",
}

REMOVED_KEYS = {
    "regulation_healthcare",
    "macro_geopolitics",
    "macro_economy",
    "trump_policy_announcements",
}

MERGED_LABEL_TO_NEW = {
    "AI Funding / VC / IPO / M&A": "AI Business Dynamics (Funding + Revenue + M&A)",
    "AI Startup Revenue & ARR":    "AI Business Dynamics (Funding + Revenue + M&A)",
}

MERGED_KEY_TO_NEW = {
    "ai_startup_funding": "ai_business_dynamics",
    "ai_startup_revenue": "ai_business_dynamics",
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not PARQUET.exists():
        print(f"[ERROR] {PARQUET} not found")
        return 1

    df = pd.read_parquet(PARQUET)
    n_before = len(df)
    print(f"[cleanup] loaded {n_before:,} rows")

    # Count rows that will be affected
    to_drop = df["feed_label"].isin(REMOVED_LABELS) | df.get("feed_key", pd.Series(dtype=str)).isin(REMOVED_KEYS)
    to_retag_label = df["feed_label"].isin(MERGED_LABEL_TO_NEW)
    to_retag_key = df.get("feed_key", pd.Series(dtype=str)).isin(MERGED_KEY_TO_NEW)

    print(f"[cleanup] will drop:       {int(to_drop.sum()):,} rows (removed feeds)")
    print(f"[cleanup] will re-tag:     {int((to_retag_label | to_retag_key).sum()):,} rows (merged feeds)")
    for label in REMOVED_LABELS:
        cnt = int((df["feed_label"] == label).sum())
        if cnt:
            print(f"  drop   {label!r}: {cnt}")
    for old, new in MERGED_LABEL_TO_NEW.items():
        cnt = int((df["feed_label"] == old).sum())
        if cnt:
            print(f"  retag  {old!r} -> {new!r}: {cnt}")

    # Apply drops
    df = df[~to_drop].copy()

    # Apply re-tag
    df.loc[df["feed_label"].isin(MERGED_LABEL_TO_NEW), "feed_label"] = (
        df.loc[df["feed_label"].isin(MERGED_LABEL_TO_NEW), "feed_label"]
        .map(MERGED_LABEL_TO_NEW)
    )
    if "feed_key" in df.columns:
        df.loc[df["feed_key"].isin(MERGED_KEY_TO_NEW), "feed_key"] = (
            df.loc[df["feed_key"].isin(MERGED_KEY_TO_NEW), "feed_key"]
            .map(MERGED_KEY_TO_NEW)
        )

    print(f"[cleanup] result: {len(df):,} rows ({n_before - len(df):+,} change)")
    print()
    print("[cleanup] top 10 feed_label by count after cleanup:")
    print(df["feed_label"].value_counts().head(10).to_string())

    if args.dry_run:
        print("\n[cleanup] --dry-run: not writing")
        return 0

    df.to_parquet(PARQUET, index=False, compression="zstd")
    print(f"\n[cleanup] wrote {len(df):,} rows back to {PARQUET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
