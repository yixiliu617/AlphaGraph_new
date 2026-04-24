"""
Unit tests for the twitterapi.io client + canonical normaliser.

No live network. Uses a locally captured fixture of one real
twitterapi.io /user/last_tweets response so we test against the actual
shape the API returns, not a guessed shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.social.canonical import SocialPost
from backend.app.services.social.sources.x_twitterapi import (
    TwitterApiIoClient,
    TwitterApiIoError,
    _extract_media_urls,
    _parse_twitter_date,
    tweet_to_canonical,
)


# Fixture: 1 response from /user/last_tweets for a real user (@elonmusk).
FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "social" / "twitterapi_io_last_tweets_elonmusk.json"
)


@pytest.fixture
def last_tweets_body() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ------------------- date parsing -------------------

def test_parse_twitter_date_rfc2822():
    dt = _parse_twitter_date("Fri Apr 24 02:32:26 +0000 2026")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 4
    assert dt.day == 24
    assert dt.hour == 2


def test_parse_twitter_date_malformed_returns_none():
    assert _parse_twitter_date("not-a-date") is None
    assert _parse_twitter_date(None) is None
    assert _parse_twitter_date("") is None


# ------------------- media extraction -------------------

def test_extract_media_urls_image():
    tweet = {
        "extendedEntities": {
            "media": [
                {"media_url_https": "https://pbs.twimg.com/1.jpg", "type": "photo"}
            ]
        }
    }
    assert _extract_media_urls(tweet) == ["https://pbs.twimg.com/1.jpg"]


def test_extract_media_urls_video_picks_highest_bitrate():
    tweet = {
        "extendedEntities": {
            "media": [{
                "media_url_https": "https://pbs.twimg.com/thumb.jpg",
                "type": "video",
                "video_info": {
                    "variants": [
                        {"bitrate": 320000, "url": "https://v.lo.mp4"},
                        {"bitrate": 2176000, "url": "https://v.hi.mp4"},
                        {"content_type": "application/x-mpegURL", "url": "https://v.m3u8"},
                    ],
                },
            }]
        }
    }
    urls = _extract_media_urls(tweet)
    assert "https://v.hi.mp4" in urls
    assert "https://pbs.twimg.com/thumb.jpg" in urls


def test_extract_media_urls_no_media():
    assert _extract_media_urls({}) == []
    assert _extract_media_urls({"extendedEntities": {}}) == []


# ------------------- canonical normalisation -------------------

def test_tweet_to_canonical_basic_fields(last_tweets_body):
    tweets = (
        last_tweets_body.get("data", {}).get("tweets")
        or last_tweets_body.get("tweets")
        or []
    )
    assert tweets, "fixture must contain at least one tweet"
    first = tweets[0]
    post = tweet_to_canonical(first)
    assert isinstance(post, SocialPost)
    assert post.platform == "X"
    assert post.source == "x_twitterapi"
    assert post.post_id == str(first["id"])
    assert post.account_handle  # non-empty
    assert post.url.startswith("http")
    assert post.posted_at is not None
    # engagement fields should be integers when present (not strings)
    for field in ("engagement_likes", "engagement_shares", "engagement_views"):
        val = getattr(post, field)
        assert val is None or isinstance(val, int)


def test_tweet_to_canonical_returns_none_on_missing_id():
    post = tweet_to_canonical({"author": {"userName": "x", "id": "1"}})
    assert post is None


def test_tweet_to_canonical_returns_none_on_missing_author():
    post = tweet_to_canonical({"id": "123", "createdAt": "Fri Apr 24 02:32:26 +0000 2026"})
    assert post is None


def test_tweet_to_canonical_retweet_flag():
    tweet = {
        "id": "100",
        "text": "RT @x: hello",
        "createdAt": "Fri Apr 24 02:32:26 +0000 2026",
        "author": {"id": "10", "userName": "elonmusk"},
        "retweeted_tweet": {"id": "50"},
    }
    post = tweet_to_canonical(tweet)
    assert post is not None
    assert post.is_retweet is True


def test_tweet_to_canonical_populates_all_engagement_counts():
    tweet = {
        "id": "200",
        "text": "hello",
        "createdAt": "Fri Apr 24 02:32:26 +0000 2026",
        "author": {"id": "10", "userName": "elonmusk"},
        "likeCount": 12623,
        "retweetCount": 1282,
        "replyCount": 554,
        "quoteCount": 70,
        "viewCount": 664157,
        "bookmarkCount": 35,
    }
    post = tweet_to_canonical(tweet)
    assert post.engagement_likes == 12623
    assert post.engagement_shares == 1282
    assert post.engagement_replies == 554
    assert post.engagement_quotes == 70
    assert post.engagement_views == 664157
    assert post.engagement_bookmarks == 35


# ------------------- client behaviour (no real network) -------------------

def _mk_client() -> TwitterApiIoClient:
    # Disable rate-limit sleep and retries for unit tests.
    return TwitterApiIoClient(
        api_key="test-key",
        min_interval_seconds=0.0,
        max_retries=0,
        backoff_base=0.01,
        timeout=1,
    )


def test_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("TWITTERAPI_IO_KEY", raising=False)
    with pytest.raises(TwitterApiIoError):
        TwitterApiIoClient()


def test_get_user_info_success(last_tweets_body):
    # last_tweets fixture is bigger than needed; fake a user/info response.
    body = {
        "status": "success",
        "msg": "",
        "data": {
            "id": "44196397",
            "userName": "elonmusk",
            "name": "Elon Musk",
            "followers": 250_000_000,
            "following": 800,
            "statusesCount": 60_000,
            "createdAt": "Tue Jun 02 20:12:29 +0000 2009",
        },
    }
    client = _mk_client()
    with patch.object(client, "_get", return_value=body):
        profile = client.get_user_info("elonmusk")
    assert profile is not None
    assert profile.user_id == "44196397"
    assert profile.handle == "elonmusk"
    assert profile.followers == 250_000_000
    assert profile.created_at is not None


def test_get_user_info_returns_none_on_not_found():
    body = {"status": "error", "msg": "User could not be found"}
    client = _mk_client()
    with patch.object(client, "_get", return_value=body):
        assert client.get_user_info("nonexistent_xyz_999") is None


def test_iter_user_tweets_paginates_until_cutoff():
    from datetime import datetime, timedelta, timezone
    # Build two pages: page 1 has 2 recent tweets, page 2 has 1 old tweet.
    now = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S +0000 %Y")
    old_iso = (datetime.now(timezone.utc) - timedelta(days=400)).strftime(
        "%a %b %d %H:%M:%S +0000 %Y"
    )
    pages = [
        {
            "data": {"tweets": [
                {"id": "1", "createdAt": now, "author": {"id": "x", "userName": "y"}},
                {"id": "2", "createdAt": now, "author": {"id": "x", "userName": "y"}},
            ]},
            "has_next_page": True,
            "next_cursor": "cur_1",
        },
        {
            "data": {"tweets": [
                {"id": "3", "createdAt": old_iso, "author": {"id": "x", "userName": "y"}},
            ]},
            "has_next_page": True,
            "next_cursor": "cur_2",
        },
        {"data": {"tweets": []}, "has_next_page": False},
    ]
    client = _mk_client()
    with patch.object(client, "_get", side_effect=pages):
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        out = list(client.iter_user_tweets("y", stop_before=cutoff))
    # We get all 3 tweets — the old one crosses the boundary but is
    # yielded per the "inclusive of boundary-crosser" contract.
    assert [t["id"] for t in out] == ["1", "2", "3"]


def test_iter_user_tweets_respects_max_tweets():
    now = "Fri Apr 24 02:32:26 +0000 2026"
    pages = [
        {
            "data": {"tweets": [
                {"id": str(i), "createdAt": now, "author": {"id": "x", "userName": "y"}}
                for i in range(20)
            ]},
            "has_next_page": True, "next_cursor": "c",
        },
        {
            "data": {"tweets": [
                {"id": str(i), "createdAt": now, "author": {"id": "x", "userName": "y"}}
                for i in range(20, 40)
            ]},
            "has_next_page": False,
        },
    ]
    client = _mk_client()
    with patch.object(client, "_get", side_effect=pages):
        out = list(client.iter_user_tweets("y", max_tweets=25))
    assert len(out) == 25
