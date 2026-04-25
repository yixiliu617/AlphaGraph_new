"""
Google News RSS Tracker — flexible, scalable news monitoring.

Usage:
    python tools/web_scraper/news_tracker.py scrape                    # all configured feeds
    python tools/web_scraper/news_tracker.py scrape --feed tariff_policy
    python tools/web_scraper/news_tracker.py search "NVIDIA earnings"  # ad-hoc search
    python tools/web_scraper/news_tracker.py config                    # show config
    python tools/web_scraper/news_tracker.py stats                     # show data stats

No API key needed. Free, real-time, no rate limits.

Search operators:
    "exact phrase"          Exact match
    term1 OR term2          Boolean OR
    -exclude                Exclude term
    site:reuters.com        Source filter
    when:1h / when:1d       Time filter
    intitle:keyword         Title-only search
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _news_cluster import (  # noqa: E402
    ANCHOR_WINDOW_HOURS,
    anchors_match,
    cluster_id as _cluster_id_fn,
    extract_anchors,
    norm_title as _norm_title_fn,
    within_hours,
)

DATA_DIR = Path("backend/data/market_data/news")
CONFIG_PATH = DATA_DIR / "news_config.json"

DEFAULT_CONFIG = {
    "feeds": {
        "semi_earnings": {
            "label": "Semiconductor Earnings",
            "query": "NVIDIA OR AMD OR Micron OR \"SK Hynix\" OR Samsung OR Intel OR TSMC OR Broadcom earnings OR revenue OR guidance",
            "region": "US",
        },
        "dram_nand_pricing": {
            "label": "DRAM & NAND Pricing",
            "query": "\"DRAM price\" OR \"NAND price\" OR \"memory price\" OR \"DDR5 price\" OR \"SSD price\" OR \"HDD price\"",
            "region": "US",
        },
        "gpu_market": {
            "label": "GPU Market",
            "query": "\"GPU price\" OR \"GPU shortage\" OR \"RTX 5090\" OR \"RTX 5080\" OR \"Radeon RX 9070\" OR \"GPU supply\"",
            "region": "US",
        },
        "tariff_policy": {
            "label": "Tariff & Trade Policy",
            "query": "Trump tariff semiconductor OR chip OR \"trade war\" OR \"export controls\" OR CHIPS",
            "region": "US",
        },
        "supply_chain": {
            "label": "Supply Chain",
            "query": "semiconductor \"supply chain\" OR shortage OR \"fab capacity\" OR TSMC OR \"chip shortage\"",
            "region": "US",
        },
        "ai_chips": {
            "label": "AI Chips & Data Center",
            "query": "\"AI chip\" OR \"data center\" OR \"GPU demand\" OR \"H100\" OR \"B200\" OR \"AI infrastructure\"",
            "region": "US",
        },
        "trump_truth_social": {
            "label": "Trump Policy Announcements",
            "query": "Trump \"Truth Social\" OR announcement tariff OR trade OR semiconductor OR \"executive order\"",
            "region": "US",
        },
        "korea_semi": {
            "label": "Korea Semiconductor (English)",
            "query": "Samsung OR \"SK Hynix\" semiconductor OR DRAM OR NAND OR HBM",
            "region": "US",
        },
    },
    "scrape_delay_seconds": 2,
    "max_items_per_feed": 100,
}

REGION_MAP = {
    "US": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "UK": {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"},
    "JP": {"hl": "ja", "gl": "JP", "ceid": "JP:ja"},
    "KR": {"hl": "ko", "gl": "KR", "ceid": "KR:ko"},
    "CN": {"hl": "zh-Hans", "gl": "CN", "ceid": "CN:zh-Hans"},
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Tier 1 = most trusted financial/tech sources
# When the same story exists from both a Tier 1 and Tier 2+ source, prefer Tier 1
TIER1_SOURCES = {
    "Reuters", "Bloomberg", "CNBC", "The Wall Street Journal", "Financial Times",
    "The New York Times", "Associated Press", "AP News", "Barron's",
    "Yahoo Finance", "MarketWatch", "Nikkei Asia", "The Economist",
    "TechCrunch", "The Information", "Ars Technica", "Wired",
    "South China Morning Post", "Korea Herald", "The Korea Times",
    "digitimes", "thelec.net", "SemiAnalysis",
}

PREMIUM_SITE_FILTER = (
    "site:reuters.com OR site:bloomberg.com OR site:cnbc.com "
    "OR site:wsj.com OR site:ft.com OR site:nytimes.com "
    "OR site:barrons.com OR site:techcrunch.com OR site:theinformation.com "
    "OR site:semianalysis.com OR site:nikkei.com"
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _batch_translate(titles, batch_size=25):
    """Translate a list of titles to English via Gemini."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  GEMINI_API_KEY not set, skipping translation")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    all_translations = []

    for start in range(0, len(titles), batch_size):
        batch = titles[start:start + batch_size]
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(batch))

        payload = {
            "contents": [{"parts": [{"text": (
                "Translate each headline below to English. Keep it concise (news headline style).\n"
                "Return ONLY a JSON array of strings, one translation per input, same order.\n\n"
                f"{numbered}"
            )}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
        }

        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*", "", text)
            s, e = text.find("["), text.rfind("]")
            translations = json.loads(text[s:e + 1]) if s != -1 and e != -1 else []
            all_translations.extend(translations)
        except Exception as ex:
            all_translations.extend([""] * len(batch))
            print(f"  Translation batch error: {ex}")

        time.sleep(2)

    return all_translations if len(all_translations) == len(titles) else None


def load_config():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    return DEFAULT_CONFIG


def parse_rss(xml_text):
    """Parse Google News RSS XML into a list of article dicts."""
    items = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)
    articles = []
    for item_xml in items:
        title_m = re.search(r"<title>(.*?)</title>", item_xml)
        link_m = re.search(r"<link>(.*?)</link>", item_xml)
        pub_m = re.search(r"<pubDate>(.*?)</pubDate>", item_xml)
        source_m = re.search(r'<source url="(.*?)">(.*?)</source>', item_xml)
        guid_m = re.search(r"<guid[^>]*>(.*?)</guid>", item_xml)

        title = title_m.group(1) if title_m else ""
        # Strip source name from title (Google appends " - Source Name")
        title_clean = re.sub(r"\s*-\s*[^-]+$", "", title)

        pub_date_str = pub_m.group(1) if pub_m else ""
        try:
            pub_dt = parsedate_to_datetime(pub_date_str)
            pub_iso = pub_dt.isoformat()
            pub_date = pub_dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pub_iso = pub_date_str
            pub_date = pub_date_str

        source_name = source_m.group(2) if source_m else ""
        articles.append({
            "title": title_clean,
            "title_full": title,
            "link": link_m.group(1) if link_m else "",
            "pub_date": pub_date,
            "pub_iso": pub_iso,
            "source_name": source_name,
            "source_url": source_m.group(1) if source_m else "",
            "source_tier": 1 if source_name in TIER1_SOURCES else 2,
            "guid": guid_m.group(1) if guid_m else "",
        })
    return articles


def fetch_feed(query, region="US"):
    """Fetch a Google News RSS feed."""
    params = {"q": query}
    region_params = REGION_MAP.get(region, REGION_MAP["US"])
    params.update(region_params)

    resp = requests.get(
        "https://news.google.com/rss/search",
        params=params,
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return parse_rss(resp.text)


def save_articles(df, source_name):
    """Save articles to parquet, merging with existing data."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{source_name}.parquet"

    if out_path.exists():
        existing = pd.read_parquet(out_path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["guid"], keep="last")
        combined = combined.sort_values("pub_iso", ascending=False)
        combined.to_parquet(out_path, index=False, compression="zstd")
        new_count = len(combined) - len(existing)
        print(f"    Merged: {new_count} new, {len(combined)} total -> {out_path}")
    else:
        df.to_parquet(out_path, index=False, compression="zstd")
        print(f"    Saved: {len(df)} articles -> {out_path}")


def cmd_scrape(args):
    """Scrape all configured feeds."""
    config = load_config()
    feeds = config["feeds"]
    delay = config.get("scrape_delay_seconds", 2)

    if args.feed:
        if args.feed not in feeds:
            print(f"ERROR: Unknown feed '{args.feed}'. Available: {list(feeds.keys())}")
            return
        feeds = {args.feed: feeds[args.feed]}

    print(f"Scraping {len(feeds)} news feeds...")
    all_articles = []

    for i, (feed_key, feed_cfg) in enumerate(feeds.items()):
        if i > 0:
            time.sleep(delay)

        label = feed_cfg.get("label", feed_key)
        query = feed_cfg["query"]
        region = feed_cfg.get("region", "US")

        print(f"  [{i+1}/{len(feeds)}] {label}...", end=" ", flush=True)

        try:
            articles = fetch_feed(query, region)
            for a in articles:
                a["feed_key"] = feed_key
                a["feed_label"] = label
            all_articles.extend(articles)
            print(f"{len(articles)} articles")
        except Exception as e:
            print(f"ERROR: {e}")

        # Whitelisted-source overlay: some niche/analysis sites (e.g.
        # artificialanalysis.ai, theinformation.com) don't rank high enough
        # in Google News to surface through the main keyword query. Fetch
        # them with a site: query so every article they publish is eligible
        # — intentional curator override of Google's relevance ranking.
        #
        # Optional `include_sites_filter_terms`: if set, only overlay
        # articles whose title contains at least one of these terms (case-
        # insensitive substring match) are kept. Use this when the
        # whitelisted source publishes broadly — e.g. theinformation.com
        # covers crypto/macro/earnings beyond AI startups, and we only want
        # the AI-deal slice in the ai_business_dynamics feed.
        filter_terms = feed_cfg.get("include_sites_filter_terms") or []
        filter_terms_lc = [t.lower() for t in filter_terms]
        for site in feed_cfg.get("include_sites", []) or []:
            time.sleep(delay)
            try:
                site_articles = fetch_feed(f"site:{site}", region)
                kept = site_articles
                if filter_terms_lc:
                    kept = [
                        a for a in site_articles
                        if any(t in str(a.get("title", "")).lower() for t in filter_terms_lc)
                    ]
                for a in kept:
                    a["feed_key"] = feed_key
                    a["feed_label"] = label
                all_articles.extend(kept)
                if filter_terms_lc:
                    print(f"    + include_sites site:{site} -> {len(kept)}/{len(site_articles)} articles (filtered by {len(filter_terms_lc)} terms)")
                else:
                    print(f"    + include_sites site:{site} -> {len(kept)} articles")
            except Exception as e:
                print(f"    + include_sites site:{site} FAILED: {e}")

    # Premium source overlay: re-fetch key feeds restricted to Tier 1 sources
    premium_feeds = {k: v for k, v in feeds.items()
                     if v.get("region", "US") == "US"
                     and not k.startswith("korea_semi_kr")
                     and not k.startswith("japan_semi_jp")
                     and not k.startswith("taiwan_semi_tw")}

    if premium_feeds and not args.feed:
        print(f"\nFetching premium source overlay ({len(premium_feeds)} feeds)...")
        for i, (feed_key, feed_cfg) in enumerate(premium_feeds.items()):
            time.sleep(delay)
            query = feed_cfg["query"]
            premium_query = f"({query}) ({PREMIUM_SITE_FILTER})"
            try:
                articles = fetch_feed(premium_query, feed_cfg.get("region", "US"))
                for a in articles:
                    a["feed_key"] = feed_key
                    a["feed_label"] = feed_cfg.get("label", feed_key)
                all_articles.extend(articles)
            except Exception:
                pass
        print(f"  Premium overlay added {len(all_articles)} total articles")

    # Cluster similar articles. Each article gets a cluster_id + is_primary
    # flag; the UI collapses clusters into one card with a source-count badge.
    # Primary = lowest source_tier within the cluster (1 = premium); ties
    # broken by earliest pub_iso.
    #
    # Matching is layered — see tools/web_scraper/_news_cluster.py for the
    # full strategy. Fuzzy SequenceMatcher (>=0.7) for near-duplicates, then
    # an anchor-token fallback (shared digit anchor like "gpt55", or >=2
    # shared alpha anchors like "openai"+"chatgpt") within a 48 h window.
    # The anchor path is what makes multi-outlet framings of the same story
    # cluster even when the raw headlines share little character overlap.
    #
    # Cross-scrape matching: we seed the cluster map with the last 7 days
    # of existing cluster primaries so today's follow-up piece joins the
    # cluster started two days ago. Existing primaries never get demoted
    # by a new scrape (keeps parquet row stability).
    from difflib import SequenceMatcher
    from datetime import timedelta

    # Seed the cluster map with recent existing clusters for cross-scrape
    # matching. We DO NOT demote existing primaries.
    existing_clusters: dict[str, dict] = {}
    digit_index: dict[str, list[str]] = {}
    alpha_index: dict[str, list[str]] = {}
    existing_path = DATA_DIR / "google_news.parquet"
    if existing_path.exists():
        try:
            _existing_df = pd.read_parquet(
                existing_path,
                columns=["title", "pub_iso", "cluster_id", "source_tier", "is_primary"],
            )
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            _recent = _existing_df[
                (_existing_df["pub_iso"] >= cutoff)
                & (_existing_df["is_primary"].fillna(False))
            ]
            for _, row in _recent.iterrows():
                norm = _norm_title_fn(str(row.get("title", "")))
                if not norm or norm in existing_clusters:
                    continue
                digit_a, alpha_a = extract_anchors(norm)
                existing_clusters[norm] = {
                    "cluster_id": str(row["cluster_id"]),
                    "primary_tier": int(row.get("source_tier", 2) or 2),
                    "pub_iso": row.get("pub_iso"),
                    "digit_anchors": digit_a,
                    "alpha_anchors": alpha_a,
                    "from_existing": True,
                }
                for d in digit_a:
                    digit_index.setdefault(d, []).append(norm)
                for a in alpha_a:
                    alpha_index.setdefault(a, []).append(norm)
        except (KeyError, ValueError):
            # Old parquet missing cluster_id/is_primary — recluster_news.py
            # handles migration separately. Fall through with empty seed.
            pass

    seen_guids = set()
    cluster_by_norm: dict[str, dict] = dict(existing_clusters)  # includes recent existing
    clustered: list[dict] = []  # every retained article, with cluster fields

    sm = SequenceMatcher(autojunk=False)
    for a in all_articles:
        if a["guid"] in seen_guids:
            continue
        seen_guids.add(a["guid"])

        norm = _norm_title_fn(a.get("title", ""))
        if not norm:
            a["cluster_id"] = _cluster_id_fn(a["guid"])
            a["is_primary"] = True
            clustered.append(a)
            continue

        pub_iso = a.get("pub_iso")
        digit_a, alpha_a = extract_anchors(norm)

        # Stage 1: fuzzy match. Fast-path: length gate + real_quick_ratio +
        # quick_ratio before the full O(n*m) ratio(), ~10x speedup at 9K rows.
        matched_norm = None
        sm.set_seq2(norm)
        for existing_norm in cluster_by_norm:
            l1, l2 = len(existing_norm), len(norm)
            if abs(l1 - l2) > max(l1, l2) * 0.5:
                continue
            sm.set_seq1(existing_norm)
            if sm.real_quick_ratio() < 0.7:
                continue
            if sm.quick_ratio() < 0.7:
                continue
            if sm.ratio() > 0.7:
                matched_norm = existing_norm
                break

        # Stage 2: anchor fallback via reverse index + 48h window.
        if matched_norm is None and (digit_a or alpha_a):
            candidates: set[str] = set()
            for d in digit_a:
                candidates.update(digit_index.get(d, ()))
            for tok in alpha_a:
                candidates.update(alpha_index.get(tok, ()))
            for cand_norm in candidates:
                cand = cluster_by_norm[cand_norm]
                if not within_hours(pub_iso, cand["pub_iso"]):
                    continue
                if anchors_match(digit_a, alpha_a, cand["digit_anchors"], cand["alpha_anchors"]):
                    matched_norm = cand_norm
                    break

        if matched_norm is None:
            cid = _cluster_id_fn(norm)
            cluster_by_norm[norm] = {
                "cluster_id": cid,
                "primary_tier": int(a.get("source_tier", 2) or 2),
                "primary_idx": len(clustered),
                "pub_iso": pub_iso,
                "digit_anchors": digit_a,
                "alpha_anchors": alpha_a,
                "from_existing": False,
            }
            for d in digit_a:
                digit_index.setdefault(d, []).append(norm)
            for tok in alpha_a:
                alpha_index.setdefault(tok, []).append(norm)
            a["cluster_id"] = cid
            a["is_primary"] = True
            clustered.append(a)
            continue

        cluster = cluster_by_norm[matched_norm]
        a["cluster_id"] = cluster["cluster_id"]
        this_tier = int(a.get("source_tier", 2) or 2)

        if cluster.get("from_existing"):
            a["is_primary"] = False
        elif this_tier < cluster["primary_tier"]:
            clustered[cluster["primary_idx"]]["is_primary"] = False
            cluster["primary_idx"] = len(clustered)
            cluster["primary_tier"] = this_tier
            a["is_primary"] = True
        else:
            a["is_primary"] = False

        clustered.append(a)

    deduped = clustered

    if deduped:
        df = pd.DataFrame(deduped)
        df["scraped_at"] = datetime.now(timezone.utc).isoformat()

        # Auto-translate non-English titles
        non_en_mask = df["feed_label"].str.contains("Korean|Japanese|Chinese", na=False)
        non_en = df[non_en_mask]
        if len(non_en) > 0:
            print(f"\nTranslating {len(non_en)} non-English titles...")
            translations = _batch_translate(non_en["title"].tolist())
            if translations:
                df.loc[non_en_mask, "title_en"] = translations
                print(f"  Translated {len(translations)} titles")
        if "title_en" not in df.columns:
            df["title_en"] = ""

        save_articles(df, "google_news")

        print(f"\nTotal: {len(df)} unique articles across {len(feeds)} feeds")

        # Top articles by recency
        df_sorted = df.sort_values("pub_iso", ascending=False)
        print("\nLatest articles:")
        for _, row in df_sorted.head(10).iterrows():
            src = row["source_name"].encode("ascii", "replace").decode()[:18]
            title = row["title"].encode("ascii", "replace").decode()[:60]
            date = row["pub_date"][:10]
            feed = row["feed_label"].encode("ascii", "replace").decode()[:20]
            print(f"  [{date}] [{src:18s}] [{feed:20s}] {title}")
    else:
        print("\nNo articles found")


def cmd_search(args):
    """Ad-hoc search query."""
    query = args.query
    region = args.region or "US"
    print(f"Searching: \"{query}\" (region: {region})...")

    articles = fetch_feed(query, region)
    print(f"Found {len(articles)} articles\n")

    for a in articles[:20]:
        src = a["source_name"][:18]
        title = a["title"].encode("ascii", "replace").decode()[:65]
        date = a["pub_date"][:16]
        print(f"  [{date}] [{src:18s}] {title}")

    if articles:
        df = pd.DataFrame(articles)
        df["feed_key"] = "adhoc_search"
        df["feed_label"] = f"Search: {query}"
        df["scraped_at"] = datetime.now(timezone.utc).isoformat()
        save_articles(df, "google_news")


def cmd_config(args):
    """Show current config."""
    config = load_config()
    print(json.dumps(config, indent=2))


def cmd_stats(args):
    """Show data stats."""
    path = DATA_DIR / "google_news.parquet"
    if not path.exists():
        print("No data yet. Run 'scrape' first.")
        return
    df = pd.read_parquet(path)
    print(f"Total articles: {len(df)}")
    print(f"Date range: {df['pub_date'].min()} to {df['pub_date'].max()}")
    print(f"\nBy feed:")
    for feed, count in df["feed_label"].value_counts().items():
        print(f"  {feed}: {count}")
    print(f"\nTop sources:")
    for src, count in df["source_name"].value_counts().head(15).items():
        print(f"  {src}: {count}")


def main():
    parser = argparse.ArgumentParser(description="Google News RSS Tracker")
    sub = parser.add_subparsers(dest="command")

    p_scrape = sub.add_parser("scrape", help="Scrape configured feeds")
    p_scrape.add_argument("--feed", help="Single feed key to scrape")

    p_search = sub.add_parser("search", help="Ad-hoc search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--region", default="US", help="Region (US, UK, JP, KR, CN)")

    sub.add_parser("config", help="Show config")
    sub.add_parser("stats", help="Show data stats")

    args = parser.parse_args()
    cmds = {"scrape": cmd_scrape, "search": cmd_search, "config": cmd_config, "stats": cmd_stats}

    if args.command in cmds:
        cmds[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
