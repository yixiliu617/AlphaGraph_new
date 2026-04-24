---
name: x-twitter-ingestion
description: Ingesting X (Twitter) data for account-level + trending tracking via twitterapi.io. Covers endpoint catalog, canonical schema, engagement-drift amendment detection, rate-limit handling, cost model, handle-verification gotchas, and the xAI x_search vs twitterapi.io trade-off. Use when building or troubleshooting social/X ingestion, adding new tracked accounts, or debugging coverage issues.
---

# X (Twitter) Ingestion

## TL;DR — fast facts

- **Vendor**: **twitterapi.io** (Kaito) — third-party reseller of X data
- **Not**: xAI `x_search` — see comparison at bottom; wrong shape for bulk ingestion
- **Cost**: $0.15/1k tweets, $0.18/1k user lookups, $0.00015/request floor
- **Rate limit (free tier)**: **1 req / 5 seconds** — pace accordingly
- **Pagination**: cursor, 20 tweets/page
- **Base URL**: `https://api.twitterapi.io`
- **Auth**: `x-api-key` header, key lives in `TWITTERAPI_IO_KEY` env var (loaded from `.env`)

## The site / API

| Endpoint | Purpose |
|---|---|
| `GET /twitter/user/info?userName=<handle>` | Resolve handle → user_id + profile |
| `GET /twitter/user/last_tweets?userName=<handle>&cursor=<c>&includeReplies=false` | Recent tweets per user; paginated |
| `GET /twitter/tweet/advanced_search?query=...&since_time=&until_time=` | Date-range search; reserved for ad-hoc queries (docs warn against paginating it) |

### Response shape observed 2026-04

`last_tweets` top-level:
```json
{"status":"success","code":0,"msg":"ok",
 "data":{"tweets":[...]},
 "has_next_page": true, "next_cursor": "abc"}
```

One tweet has ~30 fields. Most useful:
```
id, url, text, createdAt (RFC 2822 format),
likeCount, retweetCount, replyCount, quoteCount, viewCount, bookmarkCount,
isReply, inReplyToId, inReplyToUserId, conversationId,
lang, source,
author: {id, userName, name, ...},
extendedEntities.media: [{media_url_https, type, video_info.variants}, ...],
quoted_tweet, retweeted_tweet,
entities: {urls, hashtags, user_mentions, ...}
```

## Canonical schema (one row per post, survives platform-swap)

Defined in `backend/app/services/social/canonical.py`. Every row:
```
platform ('X'), source ('x_twitterapi'),
account_id, account_handle, account_name,
post_id, posted_at (UTC), url,
body, title (None for X), body_en (translation, populated later),
lang, is_reply, is_retweet, in_reply_to_id,
engagement_likes, engagement_shares, engagement_replies,
engagement_quotes, engagement_bookmarks, engagement_views,
media_urls_json,
first_seen_at, last_seen_at, content_hash, edited
```

**Why `shares` not `retweets`:** same column will carry shares for WeChat; platform-neutral lets one UI render any row.

## Storage + amendment

`backend/app/services/social/storage.py` — same pattern as Taiwan's `monthly_revenue`:

- Dedup key: `(platform, post_id)`
- Content hash = SHA-256 over immutable fields + **engagement fields** → same tweet with different `likeCount` is an AMEND, not TOUCH. Prior row → `history.parquet`, primary updated, `edited=True`.
- File layout: `backend/data/social/{platform}/data.parquet` + `history.parquet`

### Why engagement is in the hash

We want to build an engagement time-series (likes at +5min vs +1h vs +24h for trending detection). Making every drift an AMEND is the cheapest way to get that from the existing upsert pattern — no new schema, no separate metrics table.

The trade-off is cost in parquet rows: a viral tweet polled every 30 min for 24 hours generates ~48 history rows. Acceptable for O(10K) tracked accounts; may warrant sampling if we scale to millions.

## Corner cases (in order of frustration)

### C1. Handle validation is mandatory
**Always probe via `/user/info` before backfill.** Out of a 63-account starter list, 5 returned 404 and 2 more resolved to defunct accounts with <10 followers. Budget: ~75 handles × $0.00018 ≈ $0.014 — a rounding error vs wasted backfill time.

`tools/x_validate.py` does a full sweep; `tools/x_validate_delta.py` re-probes only changed handles against an existing `validated_accounts.json`.

### C2. Guessing alt handles is cheap — pay for it
Observed substitutions that worked: `@TimCulpan` → `@tculpan`, `@Dan_Nystedt` → `@dnystedt`, `@elad_gil` → `@eladgil`, `@EquinixDataCtr` → `@Equinix`, `@irisenergy` → `@IREN_Ltd`. Always-lowercase, drop-underscores, prefer corp parent over product sub-brand.

### C3. X handles are CASE-preserved on return, case-insensitive on lookup
We send `@huggingface`, API returns `userName: "huggingface"`. We send `@ClementDelangue`, it returns `"ClementDelangue"`. Store the returned version (preserves display capitalisation) but normalise to lower for dedup lookups.

### C4. `createdAt` is RFC-2822, not ISO-8601
`"Fri Apr 24 02:32:26 +0000 2026"` — parse with `email.utils.parsedate_to_datetime`, not `datetime.fromisoformat`. Always normalise to UTC before storage.

### C5. Free tier is 1 req / 5 sec — hard
The API returns HTTP 429 with `"For free-tier users, the QPS limit is one request every 5 seconds."` Our client's `min_interval_seconds=5.2` default respects this without triggering the limit. Paid tier lowers this; pass `min_interval_seconds=0.1` or lower.

### C6. RT auto-truncation
Retweet text often starts `"RT @name: ..."` and gets truncated to 140 chars. If we need full text, follow `retweeted_tweet.id` → fetch that tweet separately (doubles our tweet-count cost). We ingest truncated text for now; good enough for signal; `is_retweet=True` lets UI link through.

### C7. Engagement "views" (impressions) is sparse before 2023
Tweets older than mid-2023 have `viewCount: null`. Code already handles None. Historical backfills beyond 2 years will have many None views — don't use view count as a signal for old content.

### C8. Edit window
X Blue users can edit a tweet within 30 minutes of posting. After an edit, the same `id` returns different `text`. Our content-hash design already catches this — an edited tweet becomes an AMEND + `edited=True`. Confirmed with test_body_edit_also_becomes_amendment.

### C9. Deleted tweets silently disappear
A tweet that was ingested and later deleted will not come back from subsequent polls, but our stored row stays. We never see the deletion. **Don't claim "this tweet was posted and deleted" as signal** — it might just be a coverage gap.

### C10. Protected / suspended accounts
If a tracked handle goes protected or gets suspended, `/user/info` returns status=error with msg mentioning the state. The scheduler should mark the heartbeat DEGRADED but continue polling other accounts.

### C11. Handle rename
Users can rename their handle. The numeric `user_id` is stable; the handle isn't. If we see `/user/info` for a remembered handle return 404, try looking up by the stored `user_id` before marking dead. (TODO in code.)

## Budget model

Starter list (60 active accounts, ~300-500 tweets each in past year):
- **1-year backfill: ~$4-6 one-time** (~30k tweets at $0.15/1k + request floor)
- **Ongoing daily poll**: depends on volume.
  - Low-volume accounts (corp, researchers): ~3-5 tweets/day × 40 accts × 30 days = ~5k tweets/mo = ~$0.75/mo
  - High-volume accounts (@elonmusk, @realDonaldTrump, @zerohedge): 50-200 tweets/day × 3 accts × 30 = ~15k-20k = ~$2-3/mo
  - Total steady-state: **~$3-5/mo** at 60 accounts with 1-hour poll cadence

Scaling to 150 accounts + 5-min polling during market hours: ~$15-25/mo. Still well below X API Basic's $100/mo.

## xAI `x_search` comparison (why we didn't use it)

Considered for the same job. Hard blockers:

| Metric | twitterapi.io | xAI `x_search` |
|---|---|---|
| Max handles per call | unlimited (1 per call) | **10** (`allowed_x_handles` capped) |
| Returns | raw tweet objects (30+ fields) | LLM-summarised text + citation URLs |
| Historical backfill | full per-account via cursor pagination | per-call `from_date`/`to_date` but still summaries |
| Cost for 1-yr backfill | ~$5 one-time | ~$20-50 (tool + token fees) AND no raw data |
| Cost per daily poll | ~$0.10-$0.15 | ~$0.20-$1 (varies with tokens) |
| Fits parquet + amendment design | ✅ directly | ❌ summaries aren't rows |

**Where xAI is still useful:** as a query layer on TOP of the parquet for ad-hoc analyst questions ("summarize the last week from tracked AI labs"). Build the ingestion layer (twitterapi.io) first, add the NL wrapper later only if desired.

## Build order (fresh implementation)

1. Copy `sources/x_twitterapi.py` verbatim — rate-limited client + canonical normaliser.
2. Copy `canonical.py` + `storage.py` — these are platform-neutral; WeChat and future sources drop into the same parquet shape.
3. Write `x_config.json` — tier-organised account list with human-readable notes.
4. Run `tools/x_validate.py` — filter to active handles (~5 min at 1 req/5s for 60 handles).
5. Eyeball the valid/invalid split; fix obvious renames; re-run `tools/x_validate_delta.py` for corrections (skips already-valid).
6. Run `tools/x_backfill.py --days 365` — one-shot historical backfill. Parallelism doesn't help (rate-limited).
7. Schedule: `monthly_revenue_window`-style cron in APScheduler, reusing the Taiwan scheduler shape. Polling cadence: 1-5 min during waking hours, 30 min overnight.
8. Frontend `social-media/X` sub-tab — per-account columns + trending strip computed from the engagement-drift history.

## Quick operational cheatsheet

- **Add a handle mid-flight:** edit `x_config.json`, run `python tools/x_validate_delta.py`, run `python tools/x_backfill.py --handle <name>`.
- **Catch up after scheduler downtime:** `python tools/x_backfill.py --days 7` fills the gap; content hashes dedupe everything already present.
- **Inspect one account's latest:** `python tools/x_smoke.py --handle sama` — top 5 by likes.
- **See amendments:** `SELECT * FROM history.parquet WHERE post_id = '<id>'` — each row is a prior version.
- **API quota check:** twitterapi.io dashboard; no programmatic quota endpoint at time of writing.
