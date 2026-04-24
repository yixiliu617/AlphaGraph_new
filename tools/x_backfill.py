"""
One-year X backfill for validated accounts.

Reads validated_accounts.json (from tools/x_validate.py), iterates each
account's tweets via twitterapi.io last_tweets with cursor pagination,
stops once tweets cross the 1-year-ago threshold, normalises to
canonical rows, upserts to the social/x/data.parquet.

Cost estimate at twitterapi.io $0.15/1k tweets:
  ~60 accounts × ~500 tweets/yr = ~30k tweets = ~$4.50 one-shot
  + ~$0.22 in per-request floor
  + ~$0.01 validation

Usage:
    python tools/x_backfill.py                      # defaults: 1yr back, all valid accounts
    python tools/x_backfill.py --days 180           # 6-month backfill
    python tools/x_backfill.py --tier key_individuals  # only one tier
    python tools/x_backfill.py --handle sama        # only one handle
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))

from backend.app.services.social.sources.x_twitterapi import (
    TwitterApiIoClient,
    tweet_to_canonical,
)
from backend.app.services.social.storage import (
    DEFAULT_DATA_DIR,
    upsert_social_posts,
)


VALIDATED = ROOT / "backend" / "data" / "market_data" / "x" / "validated_accounts.json"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=365,
                   help="Backfill lookback in days (default 365 = 1 yr)")
    p.add_argument("--tier", default=None,
                   help="Only backfill accounts in this tier name")
    p.add_argument("--handle", default=None,
                   help="Only backfill this handle")
    p.add_argument("--include-replies", action="store_true",
                   help="Include reply tweets (default excludes replies)")
    p.add_argument("--max-per-account", type=int, default=None,
                   help="Cap tweets per account (for low-budget testing)")
    p.add_argument("--data-dir", type=pathlib.Path, default=DEFAULT_DATA_DIR)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not VALIDATED.exists():
        print("[ERROR] no validated_accounts.json — run tools/x_validate.py first",
              file=sys.stderr)
        return 1
    validated = json.loads(VALIDATED.read_text(encoding="utf-8"))
    accounts = validated["valid"]
    if args.tier:
        accounts = [a for a in accounts if a["tier"] == args.tier]
    if args.handle:
        accounts = [a for a in accounts if a["handle"].lower() == args.handle.lower()]
    if not accounts:
        print("[backfill] no matching accounts; aborting", file=sys.stderr)
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    print(f"[backfill] accounts={len(accounts)}  lookback={args.days}d  cutoff={cutoff.isoformat()}")
    print(f"[backfill] output: {args.data_dir}/x/data.parquet")
    print()

    client = TwitterApiIoClient()

    total_stats = {"inserted": 0, "touched": 0, "amended": 0, "tweets_fetched": 0}

    for idx, acct in enumerate(accounts, start=1):
        handle = acct["handle"]
        t0 = datetime.now(timezone.utc)
        canonical_rows: list[dict] = []
        try:
            for tweet in client.iter_user_tweets(
                handle,
                include_replies=args.include_replies,
                stop_before=cutoff,
                max_tweets=args.max_per_account,
            ):
                post = tweet_to_canonical(tweet)
                if post is None:
                    continue
                if post.posted_at < cutoff:
                    continue
                canonical_rows.append(post.to_row())
        except Exception as exc:
            print(f"  [{idx:3d}/{len(accounts)}] @{handle:22s}  FAIL  {type(exc).__name__}: {exc}")
            continue

        if not canonical_rows:
            print(f"  [{idx:3d}/{len(accounts)}] @{handle:22s}  0 tweets in window")
            continue

        stats = upsert_social_posts(canonical_rows, platform="X", data_dir=args.data_dir)
        total_stats["inserted"] += stats.inserted
        total_stats["touched"] += stats.touched
        total_stats["amended"] += stats.amended
        total_stats["tweets_fetched"] += len(canonical_rows)
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        print(
            f"  [{idx:3d}/{len(accounts)}] @{handle:22s}  "
            f"fetched={len(canonical_rows):4d}  "
            f"ins={stats.inserted:4d}  "
            f"touch={stats.touched:4d}  "
            f"amend={stats.amended:4d}  "
            f"({elapsed:.1f}s)"
        )

    print()
    print(f"[backfill] TOTALS  {total_stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
