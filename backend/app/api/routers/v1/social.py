"""
Social Media API router -- serves Reddit + Google News data.

GET /social/reddit/posts          -- all scraped posts with filters
GET /social/reddit/stats          -- summary stats
GET /social/reddit/trending       -- top posts by score
GET /social/news/articles         -- Google News articles (cluster-grouped by default)
GET /social/news/cluster/{id}     -- all articles in one cluster
GET /social/news/stats            -- news feed stats (counts, date range)
GET /social/news/feeds            -- live view of news_config.json — labels + order
                                     The frontend uses this so renaming a feed label
                                     in news_config.json propagates on next page load.
"""

import json
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.app.services.data_cache import read_parquet_cached

router = APIRouter()

REDDIT_DIR = Path("backend/data/market_data/reddit")
NEWS_DIR = Path("backend/data/market_data/news")


def _load_reddit(source: str | None = None):
    dfs = []
    files = {
        "subreddit": REDDIT_DIR / "subreddit_posts.parquet",
        "keyword": REDDIT_DIR / "keyword_search.parquet",
        "trending": REDDIT_DIR / "trending.parquet",
    }
    targets = {source: files[source]} if source and source in files else files

    for name, path in targets.items():
        if path.exists():
            df = read_parquet_cached(path)
            dfs.append(df)

    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df = df.drop_duplicates(subset=["id"], keep="last")
    return df


@router.get("/reddit/stats")
def reddit_stats():
    df = _load_reddit()
    if df.empty:
        return {"total_posts": 0, "subreddits": [], "keywords": [], "date_range": None}

    sub_counts = df["subreddit"].value_counts().head(15).to_dict()
    kw_counts = {}
    if "query" in df.columns:
        kw_df = df[df["query"].notna() & (df["query"] != "")]
        kw_counts = kw_df["query"].value_counts().head(20).to_dict()

    dates = df["created_date"].dropna()
    return {
        "total_posts": len(df),
        "subreddits": [{"name": k, "count": int(v)} for k, v in sub_counts.items()],
        "keywords": [{"keyword": k, "count": int(v)} for k, v in kw_counts.items()],
        "date_range": {
            "min": str(dates.min()) if len(dates) else None,
            "max": str(dates.max()) if len(dates) else None,
        },
    }


@router.get("/reddit/posts")
def reddit_posts(
    subreddit: str | None = Query(None),
    keyword: str | None = Query(None),
    sort: str = Query("score", description="score or date"),
    limit: int = Query(100, le=500),
):
    df = _load_reddit()
    if df.empty:
        return {"posts": []}

    if subreddit:
        df = df[df["subreddit"].str.lower() == subreddit.lower()]
    if keyword:
        mask = (
            df["title"].str.contains(keyword, case=False, na=False)
            | df["selftext"].str.contains(keyword, case=False, na=False)
            | (df["query"].str.contains(keyword, case=False, na=False) if "query" in df.columns else False)
        )
        df = df[mask]

    if sort == "score":
        df = df.sort_values("score", ascending=False)
    else:
        df = df.sort_values("created_utc", ascending=False)

    df = df.head(limit)

    posts = []
    for _, r in df.iterrows():
        posts.append({
            "id": r["id"],
            "subreddit": r["subreddit"],
            "title": r["title"],
            "selftext": r.get("selftext", ""),
            "author": r.get("author", ""),
            "score": int(r.get("score", 0)),
            "upvote_ratio": float(r.get("upvote_ratio", 0)),
            "num_comments": int(r.get("num_comments", 0)),
            "permalink": r.get("permalink", ""),
            "created_date": r.get("created_date", ""),
            "flair": r.get("flair", ""),
            "query": r.get("query", ""),
        })
    return {"posts": posts, "total": len(posts)}


@router.get("/reddit/trending")
def reddit_trending(
    days: int = Query(7, description="Look back N days"),
    limit: int = Query(30, le=100),
):
    df = _load_reddit()
    if df.empty:
        return {"posts": []}

    df["created_utc"] = pd.to_numeric(df["created_utc"], errors="coerce")
    import time
    cutoff = time.time() - (days * 86400)
    df = df[df["created_utc"] >= cutoff]
    df = df.sort_values("score", ascending=False).head(limit)

    posts = []
    for _, r in df.iterrows():
        posts.append({
            "id": r["id"],
            "subreddit": r["subreddit"],
            "title": r["title"],
            "score": int(r.get("score", 0)),
            "num_comments": int(r.get("num_comments", 0)),
            "permalink": r.get("permalink", ""),
            "created_date": r.get("created_date", ""),
            "flair": r.get("flair", ""),
        })
    return {"posts": posts}


# ---------------------------------------------------------------------------
# Google News endpoints
# ---------------------------------------------------------------------------

def _load_news():
    path = NEWS_DIR / "google_news.parquet"
    if not path.exists():
        return pd.DataFrame()
    return read_parquet_cached(path)


@router.get("/news/stats")
def news_stats():
    df = _load_news()
    if df.empty:
        return {"total_articles": 0, "feeds": [], "sources": [], "date_range": None}

    feed_counts = df["feed_label"].value_counts().to_dict()
    source_counts = df["source_name"].value_counts().head(20).to_dict()
    dates = df["pub_date"].dropna()
    return {
        "total_articles": len(df),
        "feeds": [{"name": k, "count": int(v)} for k, v in feed_counts.items()],
        "sources": [{"name": k, "count": int(v)} for k, v in source_counts.items()],
        "date_range": {
            "min": str(dates.min()) if len(dates) else None,
            "max": str(dates.max()) if len(dates) else None,
        },
    }


def _article_dict(r, *, has_title_en: bool) -> dict:
    article = {
        "title": r.get("title", ""),
        "link": r.get("link", ""),
        "pub_date": r.get("pub_date", ""),
        "source_name": r.get("source_name", ""),
        "feed_label": r.get("feed_label", ""),
        "guid": r.get("guid", ""),
    }
    if "feed_key" in r.index and pd.notna(r.get("feed_key")) and r.get("feed_key"):
        article["feed_key"] = str(r["feed_key"])
    if has_title_en and pd.notna(r.get("title_en")) and r.get("title_en"):
        article["title_en"] = r["title_en"]
    if "source_tier" in r.index and pd.notna(r.get("source_tier")):
        article["source_tier"] = int(r["source_tier"])
    if "cluster_id" in r.index and pd.notna(r.get("cluster_id")):
        article["cluster_id"] = str(r["cluster_id"])
    return article


@router.get("/news/feeds")
def news_feeds():
    """Return the current news_config.json as the definitive list of
    feeds the UI should render — in declared order.

    Read fresh on every request: editing news_config.json and hitting
    refresh in the browser is enough to propagate a label change.
    """
    config_path = NEWS_DIR / "news_config.json"
    if not config_path.exists():
        raise HTTPException(status_code=500, detail="news_config.json not found")
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"news_config.json invalid: {exc}")

    feeds = []
    for order, (feed_key, spec) in enumerate(cfg.get("feeds", {}).items()):
        if not isinstance(spec, dict):
            continue
        feeds.append({
            "feed_key": feed_key,
            "label": spec.get("label", feed_key),
            "region": spec.get("region", "US"),
            "order": order,
        })
    return {"feeds": feeds, "total": len(feeds)}


@router.get("/news/articles")
def news_articles(
    feed: str | None = Query(None, description="Filter by feed label"),
    keyword: str | None = Query(None, description="Search in title"),
    source: str | None = Query(None, description="Filter by source name"),
    limit: int = Query(100, le=500),
    group: bool = Query(True, description="Collapse similar stories into clusters (primary + sibling_count)"),
):
    df = _load_news()
    if df.empty:
        return {"articles": []}

    if feed:
        df = df[df["feed_label"].str.lower() == feed.lower()]
    if source:
        df = df[df["source_name"].str.lower().str.contains(source.lower(), na=False)]
    if keyword:
        df = df[df["title"].str.contains(keyword, case=False, na=False)]

    has_cluster = "cluster_id" in df.columns and df["cluster_id"].notna().any()
    has_title_en = "title_en" in df.columns

    if group and has_cluster:
        # Cluster-aware mode: one row per cluster_id = the is_primary row.
        # sibling_count = articles_in_cluster - 1.
        # Rows without a cluster_id (e.g. historical data that predates the
        # clustering migration) pass through as singletons so nothing gets
        # dropped from the UI while the recluster backfill is still running.
        with_cluster = df[df["cluster_id"].notna()].copy()
        without_cluster = df[df["cluster_id"].isna()].copy()

        counts = with_cluster.groupby("cluster_id").size().rename("_sibling_plus_one")
        primary_mask = with_cluster["is_primary"].fillna(False).astype(bool)
        primaries = with_cluster[primary_mask].copy()

        # Edge case: some clusters may have zero is_primary=True rows (e.g.
        # old rows with no primary flag). Fall back to the first row of
        # each such cluster.
        missing_cids = (
            set(with_cluster["cluster_id"].unique())
            - set(primaries["cluster_id"].unique())
        )
        if missing_cids:
            fallback = (
                with_cluster[with_cluster["cluster_id"].isin(missing_cids)]
                .sort_values("pub_iso", ascending=False)
                .drop_duplicates(subset=["cluster_id"], keep="first")
            )
            primaries = pd.concat([primaries, fallback], ignore_index=True)

        primaries = primaries.merge(counts, on="cluster_id", how="left")

        # Append un-clustered singletons with sibling_count = 0 (themselves).
        if len(without_cluster) > 0:
            without_cluster["_sibling_plus_one"] = 1
            primaries = pd.concat([primaries, without_cluster], ignore_index=True)

        # Defensive: any residual NaN in the count → treat as singleton.
        primaries["_sibling_plus_one"] = primaries["_sibling_plus_one"].fillna(1).astype(int)

        if not feed and not keyword and not source:
            per_feed = max(limit // max(primaries["feed_label"].nunique(), 1), 20)
            parts = []
            for fl in primaries["feed_label"].unique():
                sub = primaries[primaries["feed_label"] == fl].sort_values("pub_iso", ascending=False).head(per_feed)
                parts.append(sub)
            primaries = pd.concat(parts).sort_values("pub_iso", ascending=False)
        else:
            primaries = primaries.sort_values("pub_iso", ascending=False).head(limit)

        out = []
        for _, r in primaries.iterrows():
            art = _article_dict(r, has_title_en=has_title_en)
            sib_plus_one = r["_sibling_plus_one"]
            art["sibling_count"] = max(int(sib_plus_one) - 1, 0) if pd.notna(sib_plus_one) else 0
            out.append(art)
        return {"articles": out, "total": len(out), "grouped": True}

    # Flat / legacy mode.
    if not feed and not keyword and not source:
        per_feed = max(limit // max(df["feed_label"].nunique(), 1), 20)
        parts = []
        for fl in df["feed_label"].unique():
            sub = df[df["feed_label"] == fl].sort_values("pub_iso", ascending=False).head(per_feed)
            parts.append(sub)
        df = pd.concat(parts).sort_values("pub_iso", ascending=False)
    else:
        df = df.sort_values("pub_iso", ascending=False).head(limit)

    articles = [_article_dict(r, has_title_en=has_title_en) for _, r in df.iterrows()]
    return {"articles": articles, "total": len(articles), "grouped": False}


@router.get("/news/cluster/{cluster_id}")
def news_cluster(cluster_id: str):
    """Return every article in a cluster, ordered by publish date desc.
    Powers the 'expand siblings' UI on the News tab."""
    df = _load_news()
    if df.empty or "cluster_id" not in df.columns:
        return {"articles": [], "total": 0}
    cdf = df[df["cluster_id"] == cluster_id].sort_values("pub_iso", ascending=False)
    has_title_en = "title_en" in cdf.columns
    articles = [_article_dict(r, has_title_en=has_title_en) for _, r in cdf.iterrows()]
    return {"articles": articles, "total": len(articles)}
