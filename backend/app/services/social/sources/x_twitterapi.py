"""
twitterapi.io client — X (Twitter) ingestion source.

https://docs.twitterapi.io/ — Kaito TwitterAPI third-party reseller.
Base URL: https://api.twitterapi.io
Auth: x-api-key header (key comes from TWITTERAPI_IO_KEY env var)

Pricing (observed 2026-04):
  $0.15 / 1,000 tweets (last_tweets and advanced_search)
  $0.18 / 1,000 user profile lookups
  $0.00015 floor per request
  Free tier: 1 request / 5 seconds hard QPS cap.

Endpoints used here:
  GET /twitter/user/info            userName -> profile + numeric user_id
  GET /twitter/user/last_tweets     cursor-paginated recent tweets (20/page)
  GET /twitter/tweet/advanced_search (reserved for date-range backfills)

The client normalises tweets into canonical.SocialPost rows so the
storage + API layers don't need to know about the upstream shape.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator

import requests

from backend.app.services.social.canonical import SocialPost, media_urls_to_json

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twitterapi.io"
_ENV_KEY = "TWITTERAPI_IO_KEY"
_USER_AGENT = "AlphaGraph/1.0 (Taiwan/social ingestion)"


class TwitterApiIoError(RuntimeError):
    pass


@dataclass
class UserProfile:
    user_id: str
    handle: str          # as returned (case-preserved)
    name: str
    followers: int
    following: int
    tweet_count: int
    created_at: datetime | None
    raw: dict


class TwitterApiIoClient:
    """Thin client with rate limit, retries, and canonical conversion.

    The free tier enforces 1 request / 5 seconds, so the default
    `min_interval_seconds` is 5.2. Paid tiers can lower this via the
    constructor.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        min_interval_seconds: float = 5.2,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        timeout: float = 30.0,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(_ENV_KEY)
        if not key:
            raise TwitterApiIoError(
                f"No API key. Set {_ENV_KEY} in .env or pass api_key="
            )
        self._key = key
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "x-api-key": key,
            "User-Agent": _USER_AGENT,
        })
        self._last_request_at: float = 0.0

    # ------------------------------------------------------------------
    # Low-level
    # ------------------------------------------------------------------

    def _sleep_until_min_interval(self) -> None:
        delta = time.perf_counter() - self._last_request_at
        wait = self.min_interval_seconds - delta
        if wait > 0:
            time.sleep(wait)

    def _get(self, path: str, params: dict) -> dict:
        url = f"{BASE_URL}{path}"
        last_err = None
        for attempt in range(self.max_retries + 1):
            self._sleep_until_min_interval()
            try:
                r = self._session.get(url, params=params, timeout=self.timeout)
                self._last_request_at = time.perf_counter()
            except requests.RequestException as exc:
                last_err = exc
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                raise TwitterApiIoError(f"network error: {exc}") from exc

            if r.status_code == 429:
                # Rate-limited even though we think we respected it.
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                raise TwitterApiIoError(f"rate-limited after retries: {r.text[:200]}")

            if r.status_code in (500, 502, 503, 504) and attempt < self.max_retries:
                self._backoff(attempt)
                continue

            if not r.ok:
                raise TwitterApiIoError(
                    f"HTTP {r.status_code} on {path}: {r.text[:200]}"
                )
            try:
                return r.json()
            except ValueError as exc:
                raise TwitterApiIoError(f"non-JSON body on {path}: {exc}") from exc

        raise TwitterApiIoError(f"exhausted retries: {last_err}")

    def _backoff(self, attempt: int) -> None:
        delay = (self.backoff_base ** attempt) + random.uniform(0, 1)
        time.sleep(delay)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_user_info(self, handle: str) -> UserProfile | None:
        """Return profile for a handle. None if user doesn't exist.

        Raises TwitterApiIoError on infra errors (distinguished from
        `user not found` which returns None).
        """
        body = self._get("/twitter/user/info", {"userName": handle})
        status = body.get("status")
        data = body.get("data") or {}
        if status != "success" or not data:
            msg = body.get("msg") or ""
            if (
                "not" in msg.lower()
                or "no user" in msg.lower()
                or "could not find" in msg.lower()
            ):
                return None
            # Fall through: unexpected — treat as missing but log.
            logger.warning(
                "user/info status=%r msg=%r handle=%r", status, msg, handle,
            )
            return None

        return UserProfile(
            user_id=str(data.get("id") or data.get("userId") or ""),
            handle=str(data.get("userName") or data.get("screen_name") or handle),
            name=str(data.get("name") or ""),
            followers=int(data.get("followers") or data.get("followers_count") or 0),
            following=int(data.get("following") or data.get("friends_count") or 0),
            tweet_count=int(data.get("statusesCount") or data.get("statuses_count") or 0),
            created_at=_parse_twitter_date(data.get("createdAt") or data.get("created_at")),
            raw=data,
        )

    def iter_user_tweets(
        self,
        handle: str,
        *,
        include_replies: bool = False,
        max_tweets: int | None = None,
        stop_before: datetime | None = None,
    ) -> Iterator[dict]:
        """Yield raw tweet dicts from last_tweets, oldest to newest by page
        (API returns newest-first per page).

        Args:
          max_tweets: stop after yielding this many tweets total.
          stop_before: stop paginating once a tweet's posted_at is
                       older than this datetime. Inclusive of the tweet
                       that crosses the boundary.
        """
        cursor: str | None = None
        yielded = 0
        while True:
            params: dict = {
                "userName": handle,
                "includeReplies": str(include_replies).lower(),
            }
            if cursor:
                params["cursor"] = cursor
            body = self._get("/twitter/user/last_tweets", params)
            data = body.get("data") or {}
            tweets = data.get("tweets") or body.get("tweets") or []
            if not tweets:
                return
            stop_signal = False
            for t in tweets:
                yield t
                yielded += 1
                if max_tweets is not None and yielded >= max_tweets:
                    return
                if stop_before is not None:
                    posted = _parse_twitter_date(t.get("createdAt"))
                    if posted is not None and posted < stop_before:
                        stop_signal = True
                        break
            if stop_signal:
                return
            if not body.get("has_next_page"):
                return
            cursor = body.get("next_cursor")
            if not cursor:
                return


# ---------------------------------------------------------------------------
# Canonical normalisation
# ---------------------------------------------------------------------------

def _parse_twitter_date(s: str | None) -> datetime | None:
    """twitterapi.io emits RFC-2822 dates like 'Fri Apr 24 02:32:26 +0000 2026'."""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _extract_media_urls(tweet: dict) -> list[str]:
    out: list[str] = []
    ext = tweet.get("extendedEntities") or {}
    media = ext.get("media") or []
    for m in media:
        if not isinstance(m, dict):
            continue
        # Images: media_url_https. Videos: variants[].url at highest bitrate.
        url = m.get("media_url_https") or m.get("media_url")
        if url:
            out.append(url)
        video = m.get("video_info") or {}
        variants = video.get("variants") or []
        if variants:
            best = max(
                variants,
                key=lambda v: v.get("bitrate", 0) if isinstance(v, dict) else 0,
            )
            if isinstance(best, dict) and best.get("url"):
                out.append(best["url"])
    return out


def tweet_to_canonical(
    tweet: dict, *, source: str = "x_twitterapi",
) -> SocialPost | None:
    """Normalise a twitterapi.io tweet dict into a SocialPost.

    Returns None if the tweet lacks the minimum required fields (id + author).
    """
    author = tweet.get("author") or {}
    author_id = str(author.get("id") or "")
    handle = str(author.get("userName") or "")
    if not tweet.get("id") or not handle:
        return None

    posted = _parse_twitter_date(tweet.get("createdAt"))
    if posted is None:
        return None

    is_retweet = bool(tweet.get("retweeted_tweet"))
    is_reply = bool(tweet.get("isReply"))
    in_reply_to_id = tweet.get("inReplyToId") or None

    return SocialPost(
        platform="X",
        source=source,
        account_id=author_id,
        account_handle=handle,
        account_name=str(author.get("name") or ""),
        post_id=str(tweet["id"]),
        posted_at=posted,
        url=str(tweet.get("url") or tweet.get("twitterUrl") or ""),
        body=str(tweet.get("text") or ""),
        lang=tweet.get("lang"),
        is_reply=is_reply,
        is_retweet=is_retweet,
        in_reply_to_id=in_reply_to_id,
        engagement_likes=_to_int(tweet.get("likeCount")),
        engagement_shares=_to_int(tweet.get("retweetCount")),
        engagement_replies=_to_int(tweet.get("replyCount")),
        engagement_quotes=_to_int(tweet.get("quoteCount")),
        engagement_bookmarks=_to_int(tweet.get("bookmarkCount")),
        engagement_views=_to_int(tweet.get("viewCount")),
        media_urls_json=media_urls_to_json(_extract_media_urls(tweet)),
    )


def _to_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None
