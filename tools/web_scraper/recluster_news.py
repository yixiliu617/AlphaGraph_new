"""
One-off migration: apply the clustering logic to the existing
google_news.parquet rows. Writes back in place with cluster_id +
is_primary populated.

Idempotent — running twice produces the same result (cluster_id is a
deterministic blake2b of the canonical normalised title).

Usage:
    python tools/web_scraper/recluster_news.py
    python tools/web_scraper/recluster_news.py --dry-run       # no write
    python tools/web_scraper/recluster_news.py --threshold 0.7 # default
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Allow running this file directly from repo root: add the tools/web_scraper
# dir to sys.path so the shared helpers module resolves without needing an
# __init__.py or PYTHONPATH gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _news_cluster import (  # noqa: E402
    ANCHOR_WINDOW_HOURS,
    anchors_match,
    cluster_id,
    extract_anchors,
    norm_title,
    within_hours,
)

PARQUET = Path("backend/data/market_data/news/google_news.parquet")


def recluster(
    df: pd.DataFrame,
    *,
    threshold: float = 0.7,
    anchor_window_h: float = ANCHOR_WINDOW_HOURS,
) -> pd.DataFrame:
    """Assign cluster_id + is_primary in place.

    Walks articles by pub_iso ascending (earliest first). For each:
      1. Fuzzy match against existing cluster norms (SequenceMatcher > threshold).
      2. Fallback anchor match: shared digit anchor, or >=2 alpha anchors,
         AND within `anchor_window_h` of the candidate cluster's primary.

    An inverted anchor->cluster index keeps the anchor pass near-O(1) per
    article instead of O(n), which matters at 10 k rows.

    Primary selection within a cluster: lowest source_tier (1 = premium);
    ties broken by earliest pub_iso.
    """
    from difflib import SequenceMatcher

    df = df.sort_values("pub_iso", ascending=True, na_position="last").reset_index(drop=True)

    # cluster_by_norm: norm -> {cluster_id, primary_idx, primary_tier,
    #                           pub_iso, digit_anchors, alpha_anchors}
    cluster_by_norm: dict[str, dict] = {}
    # Reverse index for fast anchor-pass lookup.
    digit_index: dict[str, list[str]] = {}
    alpha_index: dict[str, list[str]] = {}

    rows: list[dict] = df.to_dict(orient="records")
    n = len(rows)
    print(f"[recluster] scanning {n} articles (threshold={threshold}, anchor_window={anchor_window_h}h)")

    sm = SequenceMatcher(autojunk=False)
    anchor_hits = 0

    for i, r in enumerate(rows):
        norm = norm_title(str(r.get("title", "")))
        if not norm:
            r["cluster_id"] = cluster_id(str(r.get("guid", f"ix_{i}")))
            r["is_primary"] = True
            continue

        pub_iso = r.get("pub_iso")
        digit_a, alpha_a = extract_anchors(norm)

        matched_norm = None

        # Stage 1: fuzzy match on norm title.
        sm.set_seq2(norm)
        for existing_norm in cluster_by_norm:
            l1, l2 = len(existing_norm), len(norm)
            if abs(l1 - l2) > max(l1, l2) * 0.5:
                continue
            sm.set_seq1(existing_norm)
            if sm.real_quick_ratio() < threshold:
                continue
            if sm.quick_ratio() < threshold:
                continue
            if sm.ratio() > threshold:
                matched_norm = existing_norm
                break

        # Stage 2: anchor fallback via reverse index + time window.
        if matched_norm is None and (digit_a or alpha_a):
            candidates: set[str] = set()
            for d in digit_a:
                candidates.update(digit_index.get(d, ()))
            for a in alpha_a:
                candidates.update(alpha_index.get(a, ()))
            for cand_norm in candidates:
                cand = cluster_by_norm[cand_norm]
                if not within_hours(pub_iso, cand["pub_iso"], anchor_window_h):
                    continue
                if anchors_match(digit_a, alpha_a, cand["digit_anchors"], cand["alpha_anchors"]):
                    matched_norm = cand_norm
                    anchor_hits += 1
                    break

        tier = int(r["source_tier"]) if not pd.isna(r.get("source_tier")) else 2

        if matched_norm is None:
            cid = cluster_id(norm)
            cluster_by_norm[norm] = {
                "cluster_id": cid,
                "primary_idx": i,
                "primary_tier": tier,
                "pub_iso": pub_iso,
                "digit_anchors": digit_a,
                "alpha_anchors": alpha_a,
            }
            for d in digit_a:
                digit_index.setdefault(d, []).append(norm)
            for a in alpha_a:
                alpha_index.setdefault(a, []).append(norm)
            r["cluster_id"] = cid
            r["is_primary"] = True
        else:
            cluster = cluster_by_norm[matched_norm]
            r["cluster_id"] = cluster["cluster_id"]
            if tier < cluster["primary_tier"]:
                rows[cluster["primary_idx"]]["is_primary"] = False
                cluster["primary_idx"] = i
                cluster["primary_tier"] = tier
                r["is_primary"] = True
            else:
                r["is_primary"] = False

        if i and i % 1000 == 0:
            print(f"  .. {i}/{n}  clusters={len(cluster_by_norm)}  anchor_hits={anchor_hits}")

    out = pd.DataFrame(rows)
    print(f"[recluster] clusters: {len(cluster_by_norm)} (from {n} articles)")
    print(f"[recluster]   anchor-pass matches: {anchor_hits}")
    sing = sum(
        1 for c in cluster_by_norm.values()
        if out[out["cluster_id"] == c["cluster_id"]].shape[0] == 1
    )
    print(f"[recluster]   singleton clusters: {sing}")
    print(f"[recluster]   multi-article clusters: {len(cluster_by_norm) - sing}")

    counts = out.groupby("cluster_id").size().sort_values(ascending=False)
    print("[recluster] top 10 biggest clusters:")
    for cid, cnt in counts.head(10).items():
        primary_rows = out[(out["cluster_id"] == cid) & (out["is_primary"])]
        primary_title = (
            str(primary_rows.iloc[0]["title"])[:80] if len(primary_rows) else "?"
        )
        print(f"  [{cnt:>3} sources] {primary_title}")

    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--threshold", type=float, default=0.7)
    p.add_argument("--anchor-window-hours", type=float, default=ANCHOR_WINDOW_HOURS)
    args = p.parse_args()

    if not PARQUET.exists():
        print(f"[ERROR] {PARQUET} not found")
        return 1

    df = pd.read_parquet(PARQUET)
    print(f"[recluster] loaded {len(df)} rows from {PARQUET}")
    if "cluster_id" in df.columns:
        populated = df["cluster_id"].notna().sum()
        print(f"[recluster] existing cluster_id populated on {populated}/{len(df)} rows")

    out = recluster(df, threshold=args.threshold, anchor_window_h=args.anchor_window_hours)

    if args.dry_run:
        print("[recluster] --dry-run: not writing")
        return 0

    out = out.sort_values("pub_iso", ascending=False).reset_index(drop=True)
    out.to_parquet(PARQUET, index=False, compression="zstd")
    print(f"[recluster] wrote {len(out)} rows back to {PARQUET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
