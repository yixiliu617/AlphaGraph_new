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
import hashlib
import re
import sys
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PARQUET = Path("backend/data/market_data/news/google_news.parquet")


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def _cluster_id(norm: str) -> str:
    return hashlib.blake2b(norm.encode("utf-8"), digest_size=6).hexdigest()


def recluster(df: pd.DataFrame, *, threshold: float = 0.7) -> pd.DataFrame:
    """Assign cluster_id + is_primary in place.

    Greedy: walk articles by pub_iso ascending (earliest first, so the
    first source of a story becomes primary, modulo tier). For each
    article, check existing cluster norms by fuzzy match. If match,
    join it; else create a new cluster.

    Primary selection within a cluster: lowest source_tier; ties
    broken by earliest pub_iso.
    """
    from difflib import SequenceMatcher

    df = df.sort_values("pub_iso", ascending=True, na_position="last").reset_index(drop=True)
    cluster_by_norm: dict[str, dict] = {}
    rows: list[dict] = df.to_dict(orient="records")

    n = len(rows)
    print(f"[recluster] scanning {n} articles")

    sm = SequenceMatcher(autojunk=False)
    for i, r in enumerate(rows):
        norm = _norm_title(str(r.get("title", "")))
        if not norm:
            r["cluster_id"] = _cluster_id(str(r.get("guid", f"ix_{i}")))
            r["is_primary"] = True
            continue

        matched_norm = None
        sm.set_seq2(norm)
        for existing_norm in cluster_by_norm:
            # Length-gate: titles of very different length rarely cluster
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

        if matched_norm is None:
            cid = _cluster_id(norm)
            cluster_by_norm[norm] = {
                "cluster_id": cid,
                "primary_idx": i,
                "primary_tier": (int(r["source_tier"]) if not pd.isna(r.get("source_tier")) else 2),
            }
            r["cluster_id"] = cid
            r["is_primary"] = True
        else:
            cluster = cluster_by_norm[matched_norm]
            r["cluster_id"] = cluster["cluster_id"]
            this_tier = (int(r["source_tier"]) if not pd.isna(r.get("source_tier")) else 2)
            if this_tier < cluster["primary_tier"]:
                rows[cluster["primary_idx"]]["is_primary"] = False
                cluster["primary_idx"] = i
                cluster["primary_tier"] = this_tier
                r["is_primary"] = True
            else:
                r["is_primary"] = False

        if i and i % 1000 == 0:
            print(f"  .. {i}/{n}  clusters_so_far={len(cluster_by_norm)}")

    out = pd.DataFrame(rows)
    print(f"[recluster] clusters: {len(cluster_by_norm)} (from {n} articles)")
    sing = sum(1 for c in cluster_by_norm.values()
               if out[out["cluster_id"] == c["cluster_id"]].shape[0] == 1)
    print(f"[recluster]   singleton clusters: {sing}")
    print(f"[recluster]   multi-article clusters: {len(cluster_by_norm) - sing}")

    # Top 10 biggest clusters for spot-check
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
    args = p.parse_args()

    if not PARQUET.exists():
        print(f"[ERROR] {PARQUET} not found")
        return 1

    df = pd.read_parquet(PARQUET)
    print(f"[recluster] loaded {len(df)} rows from {PARQUET}")
    if "cluster_id" in df.columns:
        populated = df["cluster_id"].notna().sum()
        print(f"[recluster] existing cluster_id populated on {populated}/{len(df)} rows")

    out = recluster(df, threshold=args.threshold)

    if args.dry_run:
        print("[recluster] --dry-run: not writing")
        return 0

    out = out.sort_values("pub_iso", ascending=False).reset_index(drop=True)
    out.to_parquet(PARQUET, index=False, compression="zstd")
    print(f"[recluster] wrote {len(out)} rows back to {PARQUET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
