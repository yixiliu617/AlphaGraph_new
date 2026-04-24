"""
Quick smoke test — fetch the last N tweets for each validated account
and print the top 5, sorted by like count (so the most-engaged tweets
bubble up).

Designed for "does the pipeline work end-to-end and does the data look
right" confirmation. For real backfill use tools/x_backfill.py.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

VALIDATED = ROOT / "backend" / "data" / "market_data" / "x" / "validated_accounts.json"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--per-account", type=int, default=20,
                   help="Tweets to fetch per account (default 20 = 1 page)")
    p.add_argument("--top", type=int, default=5,
                   help="Top N to show per account, sorted by likes")
    p.add_argument("--handle", default=None,
                   help="Only show this handle")
    p.add_argument("--tier", default=None)
    args = p.parse_args()

    validated = json.loads(VALIDATED.read_text(encoding="utf-8"))
    accounts = validated["valid"]
    if args.tier:
        accounts = [a for a in accounts if a["tier"] == args.tier]
    if args.handle:
        accounts = [a for a in accounts if a["handle"].lower() == args.handle.lower()]

    client = TwitterApiIoClient()

    for acct in accounts:
        handle = acct["handle"]
        try:
            raw = list(client.iter_user_tweets(handle, max_tweets=args.per_account))
        except Exception as exc:
            print(f"\n@{handle}: ERR {exc}")
            continue
        posts = [p for p in (tweet_to_canonical(t) for t in raw) if p is not None]
        if not posts:
            print(f"\n@{handle}: (no tweets)")
            continue
        posts.sort(key=lambda p: (p.engagement_likes or 0), reverse=True)

        print(f"\n=== @{handle} — top {min(args.top, len(posts))} by likes ===")
        for i, p in enumerate(posts[: args.top], start=1):
            txt = (p.body or "").replace("\n", " ")
            if len(txt) > 110:
                txt = txt[:110] + "..."
            when = p.posted_at.strftime("%Y-%m-%d %H:%M")
            likes = p.engagement_likes or 0
            rts = p.engagement_shares or 0
            views = p.engagement_views or 0
            print(
                f"  {i}. [{when}]  likes={likes:>8,}  rts={rts:>7,}  views={views:>10,}"
            )
            print(f"     {txt}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
