"""
Reddit Tracker — monitors subreddits and searches via Arctic Shift API (no auth needed).

Usage:
    python tools/web_scraper/reddit_tracker.py scrape          # scrape all configured subs
    python tools/web_scraper/reddit_tracker.py scrape --sub hardware
    python tools/web_scraper/reddit_tracker.py search          # search all configured keywords
    python tools/web_scraper/reddit_tracker.py search "DDR5 price"
    python tools/web_scraper/reddit_tracker.py config          # show config

Arctic Shift API: free, no API key, archives all Reddit posts.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path("backend/data/market_data/reddit")
CONFIG_PATH = DATA_DIR / "reddit_config.json"
API_BASE = "https://arctic-shift.photon-reddit.com/api"

DEFAULT_CONFIG = {
    "subreddits": [
        "hardware",
        "buildapc",
        "nvidia",
        "amd",
        "intel",
        "sysadmin",
        "homelab",
        "pcmasterrace",
        "stocks",
        "wallstreetbets",
    ],
    "keywords": [
        "DDR5 price",
        "DDR4 price",
        "DRAM price",
        "GPU shortage",
        "GPU price",
        "NAND price",
        "SSD price",
        "supply chain semiconductor",
        "tariff semiconductor",
        "out of stock GPU",
        "price increase memory",
        "NVDA",
        "AMD earnings",
        "Micron",
        "SK Hynix",
        "Samsung semiconductor",
    ],
    "posts_per_sub": 100,
    "search_results_per_keyword": 50,
    "request_delay_seconds": 2,
}


def load_config():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    with open(CONFIG_PATH, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    return DEFAULT_CONFIG


def api_get(endpoint, params, retries=3):
    """GET an Arctic Shift API endpoint with retry."""
    url = f"{API_BASE}/{endpoint}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 30
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("data") or []
        except requests.RequestException as e:
            if attempt < retries - 1:
                print(f"    Retry {attempt+1}: {e}")
                time.sleep(5)
            else:
                print(f"    FAILED: {e}")
                return []
    return []


def parse_post(p):
    """Extract relevant fields from an Arctic Shift post object."""
    created = p.get("created_utc", 0)
    return {
        "id": p.get("id", ""),
        "subreddit": p.get("subreddit", ""),
        "title": p.get("title", ""),
        "selftext": (p.get("selftext") or "")[:500],
        "author": p.get("author", ""),
        "score": p.get("score", 0),
        "upvote_ratio": p.get("upvote_ratio", 0),
        "num_comments": p.get("num_comments", 0),
        "url": p.get("url", ""),
        "permalink": f"https://reddit.com{p.get('permalink', '')}",
        "created_utc": created,
        "created_date": datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if created else "",
        "flair": p.get("link_flair_text") or "",
        "is_self": p.get("is_self", False),
        "domain": p.get("domain", ""),
    }


def save_posts(df, source_name):
    """Save posts to parquet, merging with existing data."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{source_name}.parquet"

    if out_path.exists():
        existing = pd.read_parquet(out_path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["id"], keep="last")
        combined.to_parquet(out_path, index=False, compression="zstd")
        new_count = len(combined) - len(existing)
        print(f"    Merged: {new_count} new, {len(combined)} total -> {out_path}")
    else:
        df.to_parquet(out_path, index=False, compression="zstd")
        print(f"    Saved: {len(df)} posts -> {out_path}")


def cmd_scrape(args):
    """Scrape subreddits for recent posts."""
    config = load_config()
    subs = [args.sub] if args.sub else config["subreddits"]
    limit = config.get("posts_per_sub", 100)
    delay = config.get("request_delay_seconds", 2)

    print(f"Scraping {len(subs)} subreddits ({limit} posts each)...")
    all_posts = []

    for i, sub_name in enumerate(subs):
        if i > 0:
            time.sleep(delay)

        print(f"  [{i+1}/{len(subs)}] r/{sub_name}...", end=" ", flush=True)

        data = api_get("posts/search", {
            "subreddit": sub_name,
            "limit": limit,
            "sort": "desc",
            "sort_type": "created_utc",
        })

        posts = [parse_post(p) for p in data]
        for p in posts:
            p["source_type"] = "subreddit"
            p["query"] = ""
        all_posts.extend(posts)
        print(f"{len(posts)} posts")

    seen = set()
    deduped = [p for p in all_posts if p["id"] not in seen and not seen.add(p["id"])]

    if deduped:
        df = pd.DataFrame(deduped)
        df["scraped_at"] = datetime.now(timezone.utc).isoformat()
        save_posts(df, "subreddit_posts")
        print(f"\nTotal: {len(df)} unique posts across {len(subs)} subreddits")

        # Print top posts by score
        df_top = df.sort_values("score", ascending=False)
        print("\nTop posts by score:")
        for _, row in df_top.head(15).iterrows():
            print(f"  [{row['score']:>6}] r/{row['subreddit']:15s} {row['created_date'][:10]} | {row['title'][:55]}")
    else:
        print("\nNo posts scraped")


def cmd_search(args):
    """Search for keywords across specific subreddits."""
    config = load_config()
    keywords = [args.query] if args.query else config["keywords"]
    subs = config["subreddits"]
    limit = config.get("search_results_per_keyword", 50)
    delay = config.get("request_delay_seconds", 2)

    # Search each keyword across all tracked subreddits
    search_subs = ["hardware", "buildapc", "nvidia", "amd", "stocks", "wallstreetbets"]

    print(f"Searching {len(keywords)} keywords across {len(search_subs)} subreddits...")
    all_posts = []

    for i, kw in enumerate(keywords):
        print(f"  [{i+1}/{len(keywords)}] \"{kw}\"...", end=" ", flush=True)
        total = 0

        for sub in search_subs:
            if i > 0 or sub != search_subs[0]:
                time.sleep(delay)

            data = api_get("posts/search", {
                "query": kw,
                "subreddit": sub,
                "limit": limit,
                "sort": "desc",
                "sort_type": "created_utc",
            })

            posts = [parse_post(p) for p in data]
            for p in posts:
                p["source_type"] = "keyword_search"
                p["query"] = kw
            all_posts.extend(posts)
            total += len(posts)

        print(f"{total} results")

    seen = set()
    deduped = [p for p in all_posts if p["id"] not in seen and not seen.add(p["id"])]

    if deduped:
        df = pd.DataFrame(deduped)
        df["scraped_at"] = datetime.now(timezone.utc).isoformat()
        save_posts(df, "keyword_search")
        print(f"\nTotal: {len(df)} unique posts for {len(keywords)} keywords")

        # Summary by keyword
        print("\nResults by keyword:")
        for kw in keywords:
            kw_posts = df[df["query"] == kw]
            if len(kw_posts) > 0:
                top = kw_posts.sort_values("score", ascending=False).iloc[0]
                title_clean = top['title'][:50].encode('ascii', 'replace').decode()
                print(f"  \"{kw}\": {len(kw_posts)} posts | top: [{top['score']}] {title_clean}")
    else:
        print("\nNo results found")


def cmd_config(args):
    """Show current config."""
    config = load_config()
    print(json.dumps(config, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Reddit Tracker (Arctic Shift)")
    sub = parser.add_subparsers(dest="command")

    p_scrape = sub.add_parser("scrape", help="Scrape subreddit posts")
    p_scrape.add_argument("--sub", help="Single subreddit")

    p_search = sub.add_parser("search", help="Search keywords")
    p_search.add_argument("query", nargs="?", help="Single keyword (default: all configured)")

    sub.add_parser("config", help="Show config")

    args = parser.parse_args()
    cmds = {"scrape": cmd_scrape, "search": cmd_search, "config": cmd_config}

    if args.command in cmds:
        cmds[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
