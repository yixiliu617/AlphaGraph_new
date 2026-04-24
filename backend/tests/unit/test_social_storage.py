"""
Storage tests for social posts — insert → touch → amend on engagement drift.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.services.social.canonical import SocialPost
from backend.app.services.social.storage import (
    read_social_posts,
    upsert_social_posts,
)


def _tweet(
    post_id="123",
    likes=100,
    handle="elonmusk",
    body="hello world",
):
    return SocialPost(
        platform="X",
        source="x_twitterapi",
        account_id="44196397",
        account_handle=handle,
        account_name="Elon Musk",
        post_id=post_id,
        posted_at=datetime(2026, 4, 24, 2, 32, 26, tzinfo=timezone.utc),
        url=f"https://x.com/{handle}/status/{post_id}",
        body=body,
        engagement_likes=likes,
        engagement_shares=10,
        engagement_replies=5,
        engagement_views=1000,
    ).to_row()


def test_insert_creates_new_row(tmp_path):
    stats = upsert_social_posts([_tweet()], platform="X", data_dir=tmp_path)
    assert stats.inserted == 1
    assert stats.touched == 0

    df = read_social_posts(platform="X", data_dir=tmp_path)
    assert len(df) == 1
    assert df.iloc[0]["post_id"] == "123"
    assert df.iloc[0]["edited"] is False or df.iloc[0]["edited"] == False  # noqa: E712


def test_same_row_twice_is_touch_not_insert(tmp_path):
    upsert_social_posts([_tweet()], platform="X", data_dir=tmp_path)
    stats = upsert_social_posts([_tweet()], platform="X", data_dir=tmp_path)
    assert stats.inserted == 0
    assert stats.touched == 1
    df = read_social_posts(platform="X", data_dir=tmp_path)
    assert len(df) == 1


def test_engagement_drift_becomes_amendment(tmp_path):
    """Same tweet polled twice with different like counts → AMEND, prior
    row written to history.parquet, primary row updated with `edited=True`.
    """
    upsert_social_posts([_tweet(likes=100)], platform="X", data_dir=tmp_path)
    stats = upsert_social_posts(
        [_tweet(likes=500)], platform="X", data_dir=tmp_path,
    )
    assert stats.amended == 1
    df = read_social_posts(platform="X", data_dir=tmp_path)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["engagement_likes"] == 500
    assert bool(row["edited"]) is True

    # History parquet contains the prior (likes=100) row
    import pandas as pd
    history = pd.read_parquet(tmp_path / "x" / "history.parquet")
    assert len(history) == 1
    assert history.iloc[0]["engagement_likes"] == 100


def test_body_edit_also_becomes_amendment(tmp_path):
    """X supports 30-min post-publish edits for Blue users.
    A text change should flip edited=True."""
    upsert_social_posts([_tweet(body="hello world")], platform="X", data_dir=tmp_path)
    stats = upsert_social_posts(
        [_tweet(body="hello earth")], platform="X", data_dir=tmp_path,
    )
    assert stats.amended == 1
    df = read_social_posts(platform="X", data_dir=tmp_path)
    assert df.iloc[0]["body"] == "hello earth"


def test_multiple_distinct_posts_insert_cleanly(tmp_path):
    rows = [_tweet(post_id=str(i)) for i in range(5)]
    stats = upsert_social_posts(rows, platform="X", data_dir=tmp_path)
    assert stats.inserted == 5
    df = read_social_posts(platform="X", data_dir=tmp_path)
    assert len(df) == 5


def test_read_empty_returns_empty_dataframe_with_canonical_columns(tmp_path):
    df = read_social_posts(platform="X", data_dir=tmp_path)
    assert len(df) == 0
    assert "post_id" in df.columns
    assert "content_hash" in df.columns
    assert "edited" in df.columns
