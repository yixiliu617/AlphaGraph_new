"""
Social Media API router -- serves Reddit + Google News data.

GET /social/reddit/posts          -- all scraped posts with filters
GET /social/reddit/stats          -- summary stats
GET /social/reddit/trending       -- top posts by score
GET /social/news/articles         -- Google News articles with filters
GET /social/news/stats            -- news feed stats
"""

from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Query

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
            df = pd.read_parquet(path)
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
    return pd.read_parquet(path)


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


@router.get("/news/articles")
def news_articles(
    feed: str | None = Query(None, description="Filter by feed label"),
    keyword: str | None = Query(None, description="Search in title"),
    source: str | None = Query(None, description="Filter by source name"),
    limit: int = Query(100, le=500),
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

    if not feed and not keyword and not source:
        # No filter: return top N per feed to ensure all sections represented
        per_feed = max(limit // max(df["feed_label"].nunique(), 1), 20)
        parts = []
        for fl in df["feed_label"].unique():
            sub = df[df["feed_label"] == fl].sort_values("pub_iso", ascending=False).head(per_feed)
            parts.append(sub)
        df = pd.concat(parts).sort_values("pub_iso", ascending=False)
    else:
        df = df.sort_values("pub_iso", ascending=False).head(limit)

    articles = []
    has_title_en = "title_en" in df.columns
    for _, r in df.iterrows():
        article = {
            "title": r.get("title", ""),
            "link": r.get("link", ""),
            "pub_date": r.get("pub_date", ""),
            "source_name": r.get("source_name", ""),
            "feed_label": r.get("feed_label", ""),
            "guid": r.get("guid", ""),
        }
        if has_title_en and pd.notna(r.get("title_en")) and r["title_en"]:
            article["title_en"] = r["title_en"]
        if "source_tier" in r.index and pd.notna(r.get("source_tier")):
            article["source_tier"] = int(r["source_tier"])
        articles.append(article)
    return {"articles": articles, "total": len(articles)}
