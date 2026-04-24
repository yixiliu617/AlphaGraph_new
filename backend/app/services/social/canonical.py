"""
Canonical social-post schema shared across platforms.

Every source (X, WeChat, Reddit, ...) normalises into this shape so the
storage + API + frontend layers work on one schema.

Schema fields:
  platform          'X' | 'WeChat' | ...
  source            'x_twitterapi' | 'wechat_rss' | ...
  account_id        platform-specific stable id (numeric for X user_id)
  account_handle    user-facing handle (case-preserved) e.g. 'elonmusk'
  account_name      display name, e.g. 'Elon Musk'
  post_id           stable id (tweet id for X, article_url_hash for WeChat)
  posted_at         datetime (UTC)
  url               permalink
  title             str | None  (WeChat has; X tweets don't)
  body              str         (tweet text or article body)
  body_en           str | None  (Gemini translation for non-English; added later)
  lang              str | None  ('en', 'zh', 'ja', ...)
  is_reply          bool
  is_retweet        bool        (True if the tweet is a retweet of someone else)
  in_reply_to_id    str | None
  engagement_likes  int | None
  engagement_shares int | None  (retweets for X, shares for WeChat)
  engagement_replies  int | None
  engagement_quotes   int | None
  engagement_bookmarks int | None
  engagement_views    int | None
  media_urls_json   str         (serialised list of image/video URLs)
  first_seen_at     datetime (UTC)
  last_seen_at      datetime (UTC)
  content_hash      str         (sha256 over immutable + engagement fields)
  edited            bool        (True on amendment — engagement drift OR text edit)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


CANONICAL_COLUMNS: list[str] = [
    "platform", "source",
    "account_id", "account_handle", "account_name",
    "post_id", "posted_at", "url",
    "title", "body", "body_en", "lang",
    "is_reply", "is_retweet", "in_reply_to_id",
    "engagement_likes", "engagement_shares", "engagement_replies",
    "engagement_quotes", "engagement_bookmarks", "engagement_views",
    "media_urls_json",
    "first_seen_at", "last_seen_at", "content_hash", "edited",
]


@dataclass
class SocialPost:
    platform: str
    source: str
    account_id: str
    account_handle: str
    account_name: str
    post_id: str
    posted_at: datetime
    url: str
    body: str
    is_reply: bool = False
    is_retweet: bool = False
    title: str | None = None
    body_en: str | None = None
    lang: str | None = None
    in_reply_to_id: str | None = None
    engagement_likes: int | None = None
    engagement_shares: int | None = None
    engagement_replies: int | None = None
    engagement_quotes: int | None = None
    engagement_bookmarks: int | None = None
    engagement_views: int | None = None
    media_urls_json: str = "[]"

    def to_row(self) -> dict[str, Any]:
        """Emit a storage-ready row (without first_seen_at / last_seen_at /
        content_hash / edited — those are set by the storage layer)."""
        d = asdict(self)
        # Ensure consistent types for parquet
        if isinstance(d["posted_at"], datetime):
            if d["posted_at"].tzinfo is None:
                d["posted_at"] = d["posted_at"].replace(tzinfo=timezone.utc)
        return d


# Fields excluded from content-hash — they mutate during every poll
# without a semantic change we want to flag.
_HASH_EXCLUDE = {
    "first_seen_at", "last_seen_at", "content_hash", "edited",
}


def compute_post_content_hash(row: dict) -> str:
    """SHA-256 over the immutable + engagement fields.

    Engagement counts (likes, retweets, etc.) ARE included so a tweet
    whose likeCount goes from 100 → 500 becomes an amendment. That's by
    design — we track the engagement curve, not just final values.
    """
    import hashlib
    payload = {k: v for k, v in row.items() if k not in _HASH_EXCLUDE}
    # Normalise datetimes to ISO for stable serialisation
    for k, v in list(payload.items()):
        if isinstance(v, datetime):
            payload[k] = v.astimezone(timezone.utc).isoformat()
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def media_urls_to_json(urls: list[str] | None) -> str:
    return json.dumps(urls or [], ensure_ascii=False)
