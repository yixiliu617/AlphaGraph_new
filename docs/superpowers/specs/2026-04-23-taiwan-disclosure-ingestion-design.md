# Taiwan Disclosure Ingestion — Design Spec

**Date:** 2026-04-23
**Status:** Approved design — ready for implementation planning

---

## Goals

Ingest, normalise, translate, and expose Taiwan-listed-company regulatory disclosures as a first-class data surface on AlphaGraph. The platform treats these as a production feed — analysts read them in the UI, AI agents (both the internal chat agent and external "agent-as-a-service" clients) query them via API. Data must be authoritative (sourced directly from MOPS), durable (raw captures preserved for re-parsing), auditable (amendment history), and near-real-time where it matters (material information).

This is the first non-US market on the platform. It is the prototype for the pattern that will extend to Korea (KRX / DART), Hong Kong (HKEX), mainland China (SSE/SZSE), Japan (EDINET), etc.

## Non-goals (this spec)

- Adding non-semi Taiwan tickers. Watchlist expansion is future work.
- Non-MOPS data sources (TEJ, FinMind, brokerage feeds, Bloomberg). MOPS is authoritative; third parties are future enrichment.
- Other filing types beyond monthly revenue + material information. All explicitly deferred to `docs/superpowers/backlog.md` with the specific MOPS endpoint each would use.
- Market-data feeds (daily OHLCV, institutional flow, margin/short data). Future.
- Multi-tenant access control / billing for the agent-as-a-service consumers. Future.

## Scope

### Covered filing types (MVP)

**Monthly revenue (月營收 / M-revenue)** — every Taiwan-listed company is legally required to publish monthly revenue by the 10th of the following month. This is a **Taiwan-unique data point** (no US equivalent); global data providers do not cover it cleanly. Pre-earnings leading indicator that reliably moves stocks.

**Material information (重大訊息)** — real-time issuer disclosures of events the company considers material (M&A, large orders, customer losses, personnel changes, legal, capacity guidance). Published continuously, often same-hour as the triggering event. Moves stocks same-day.

### Watchlist

Fifty-one Taiwan-listed semiconductor-ecosystem tickers, curated and saved to `backend/data/taiwan/watchlist_semi.csv` with columns `ticker, name, market, sector, subsector, notes`. Grouped into subsectors: Foundry, IC Design, Memory, DRAM Module, OSAT, Wafer, Equipment, PCB/Substrate, Materials, Optical, Server EMS.

The watchlist is the source of truth for "which tickers do we care about". Validation of ticker existence + MOPS-specific company-ID (`co_id`) mapping happens on first run against MOPS's company master.

### Deferred filings (documented in `backlog.md`)

Quarterly financials (XBRL), annual reports, shareholders' meetings, board resolutions, insider trades, financial forecasts, M&A and private placement filings, dividend distributions, ESG reports, corporate governance rankings, related-party transactions.

---

## High-level architecture

```
   MOPS                                                          Fly.io
(mops.twse.com.tw)                                   ┌──────────────────────────────┐
       │                                             │  taiwan-scheduler process    │
       │ HTTP (requests first, Playwright fallback)  │                              │
       │                                             │  APScheduler jobs:           │
       │   ┌─────────────────────────────────────────┤   • monthly_revenue  (daily) │
       │   │                                         │   • material_info     (5min) │
       │   │                                         │   • company_master  (monthly)│
       │   │                                         │   • health_check    (hourly) │
       │   ▼                                         │                              │
       │  mops_client.py                             │                              │
       │     - request session + rate limit          │                              │
       │     - Big5/UTF-8 handling                   │                              │
       │     - Playwright upgrade on 403/CAPTCHA     │                              │
       │   │                                         │                              │
       │   ▼                                         │                              │
       │  Raw capture layer (_raw/)                  │                              │
       │     - content-hashed bytes on disk          │                              │
       │     - async S3 mirror                       │                              │
       │   │                                         │                              │
       │   ▼                                         │                              │
       │  Parsers + Gemini Flash translation         │                              │
       │     - revenue parser (HTML table → rows)    │                              │
       │     - RSS parser (XML → entries)            │                              │
       │     - material info parser (HTML → body)    │                              │
       │     - content_hash, amendment detection     │                              │
       │   │                                         │                              │
       │   ▼                                         │                              │
       │  Storage (backend/data/taiwan/)             │                              │
       │    monthly_revenue/data.parquet (latest)    │                              │
       │    monthly_revenue/history.parquet (amends) │                              │
       │    material_info/index.parquet              │                              │
       │    material_info/bodies.parquet             │                              │
       │    _raw/* (immutable)                       │                              │
       │   │                                         │  heartbeats → SQLite         │
       │   ▼                                         │                              │
       └─ Heartbeat table (SQLite)                   └──────────────────────────────┘
              ▲
              │
              │ reads
              │
       ┌──────┴────────┐
       │ FastAPI app   │
       │ taiwan router │──► JSON APIs (humans + agents)
       │ health router │
       └───────────────┘
              ▲
              │
       ┌──────┴────────┐
       │  Next.js UI   │    Top-level [Taiwan] nav
       │  • watchlist  │       - watchlist revenue grid
       │  • feed       │       - material info feed (zh + en side-by-side)
       │  • drill-down │       - per-ticker history + chart (Recharts)
       └───────────────┘
```

Scheduler runs as a **separate Fly.io machine** from the FastAPI `web` app. Both read/write the same parquet files (via a shared volume or S3) and the same SQLite DB. Physical separation isolates scheduler crashes from user-facing requests.

---

## Components

### `backend/app/services/taiwan/` package

```
taiwan/
  __init__.py
  mops_client.py               HTTP/browser client; rate limit; session; encoding
  translation.py               Gemini Flash wrapper; per-notice translate + cache
  storage.py                   parquet read/write; raw capture; S3 mirror
  amendments.py                content-hash comparison; history-parquet writes
  registry.py                  company master + per-ticker scraper state
  scrapers/
    __init__.py
    company_master.py          full MOPS registry → backend/data/taiwan/_registry/
    monthly_revenue.py         summary-query fetcher + parser
    material_info_rss.py       RSS poller for ongoing
    material_info_history.py   per-ticker archive fetcher (used at backfill)
    material_info_body.py      single-filing body fetcher + translator
  scheduler.py                 entry point; APScheduler; runs scrapers
  health.py                    reads heartbeats; exposes via router
  validation.py                data-quality invariants (e.g. revenue ≥ 0, dates sane)
```

### `backend/app/api/routers/v1/taiwan.py`

All `/api/v1/taiwan/*` endpoints, calling into the storage layer. No scraping here; the router is read-only except for the `/health` endpoint which reads heartbeats.

### `frontend/src/app/(dashboard)/taiwan/`

Next.js route group for the Taiwan dashboard. Contains a container, a view, and a few specialised components. No new top-level routing concepts.

---

## Storage layout

```
backend/data/taiwan/
  watchlist_semi.csv                       ← already exists; source of truth
  _registry/
    mops_company_master.parquet             ← full MOPS company list; refreshed monthly
    scraper_state.parquet                   ← per-ticker: last_seen_seq, last_material_info_at, last_revenue_ym, status
  _raw/                                     ← immutable; content-hashed
    monthly_revenue/
      {ticker}/
        {YYYY-MM}.html                     ← raw HTML per market-month fetch
    material_info/
      {ticker}/
        {YYYY-MM-DD}_{seq_no}.html
        {YYYY-MM-DD}_{seq_no}.rss.xml      ← when from RSS; stored for cross-check
  monthly_revenue/
    config.json                             ← schema version, column docs
    data.parquet                            ← current; key = (ticker, fiscal_ym)
    history.parquet                         ← prior versions on amendments
  material_info/
    config.json
    index.parquet                           ← header: ticker, published_at, category, title_zh, title_en, seq_no, url
    bodies.parquet                          ← full body per filing: body_zh, body_en, content_hash
```

### Parquet schemas

**`monthly_revenue/data.parquet`** — one row per (ticker, fiscal_ym) (ticker's reported month).

| col | type | notes |
|---|---|---|
| `ticker` | string | e.g. `2330` |
| `market` | string | `TWSE` or `TPEx` |
| `fiscal_ym` | string | `YYYY-MM` (the reporting month, not the publish month) |
| `revenue_twd` | int64 | New Taiwan dollars; nullable |
| `yoy_pct` | float64 | year-over-year % change; source-reported when available, else computed |
| `mom_pct` | float64 | month-over-month % |
| `ytd_pct` | float64 | ytd-over-prior-ytd % |
| `cumulative_ytd_twd` | int64 | YTD revenue |
| `prior_year_month_twd` | int64 | same-period prior year (for YoY reference) |
| `first_seen_at` | timestamp | first time we ingested this row |
| `last_seen_at` | timestamp | latest ingest time |
| `content_hash` | string | sha256 of canonical row contents |
| `amended` | bool | true if this value replaced a prior version |
| `parse_error` | string | null when clean; error message when partial parse |

**`monthly_revenue/history.parquet`** — identical schema, one row per historical version. When an amendment replaces a value, the prior row is written here.

**`material_info/index.parquet`** — one row per filing.

| col | type | notes |
|---|---|---|
| `seq_no` | string | unique within ticker+day; MOPS-assigned |
| `ticker` | string | |
| `published_at` | timestamp | filing timestamp (TPE tz) |
| `fact_date` | date | the date the event occurred (distinct from publish date) |
| `category_code` | string | MOPS category enum (e.g. `1`, `23` — see MOPS classification) |
| `category_label_zh` | string | |
| `category_label_en` | string | translated category label |
| `title_zh` | string | filing headline |
| `title_en` | string | translated headline |
| `url` | string | direct link to MOPS HTML |
| `first_seen_at` | timestamp | |
| `source` | string | `rss` or `per_ticker_history` |

**`material_info/bodies.parquet`** — one row per filing (joined to index by seq_no+ticker).

| col | type | notes |
|---|---|---|
| `seq_no` | string | |
| `ticker` | string | |
| `body_zh` | string | full Chinese body text |
| `body_en` | string | Gemini Flash translation |
| `summary_en` | string | optional 1-2 sentence English summary; Gemini produces alongside translation |
| `raw_path` | string | path to `_raw/material_info/{ticker}/{...}.html` |
| `content_hash` | string | sha256 of body_zh |
| `translated_at` | timestamp | |
| `translation_model` | string | e.g. `gemini-2.5-flash` |

### Raw captures + S3 mirror

Every successful HTTP/Playwright fetch writes the response bytes to `_raw/<source>/<ticker>/<key>.<ext>`. Filenames are deterministic (derived from query params). File existence = idempotence marker: rerunning a scrape won't re-fetch unless `--force` is passed.

After a successful parse + parquet write, the raw file is queued for async upload to S3. Bucket: `alphagraph-taiwan-raw-{env}` (dev/prod). Region + credentials via `.env`. Upload failures are logged but don't block ingest; a separate reconcile job picks up laggards.

### History / amendment logic

On every ingest pass for monthly revenue or material info:

1. Parse the row/filing.
2. Compute `content_hash = sha256(canonical_json(row))` where canonical_json drops mutable fields (timestamps) and sorts keys.
3. Lookup by primary key (`(ticker, fiscal_ym)` for revenue, `(ticker, seq_no)` for material info).
4. If no existing row: INSERT with `first_seen_at = last_seen_at = now`, `amended=false`.
5. If existing row, same hash: UPDATE `last_seen_at = now` only.
6. If existing row, different hash: **amendment detected** — write the *existing* row to `history.parquet`, then UPSERT the new row with `first_seen_at` preserved, `last_seen_at = now`, `amended = true`.

Amendments are surfaced in the UI with a small "amended" badge and a "show history" link.

---

## Scraping

### `mops_client.py` — shared client

One class. Responsibilities:

- Session management (cookies persist across requests within a session).
- Encoding: MOPS mixes Big5 and UTF-8; we try `response.encoding = 'utf-8'` first, fall back to `big5` with lenient error handling, log any residual undecodable bytes.
- Rate limit: **1 request/second sustained**, 3/sec burst OK, gentle exponential backoff on 429 / 5xx (2s → 8s → 32s, then fail).
- User-Agent: a realistic Chrome UA (`Mozilla/5.0 ... Chrome/... Safari/...`). Not stealth — we're not hiding from MOPS — but some endpoints reject empty/curl UA.
- Retries: 3 per request by default; configurable.
- Circuit breaker: 10 consecutive failures on the same endpoint → mark endpoint as `degraded`, back off for 30 min before retrying.
- Playwright fallback: decorator / context manager `with mops_client.browser() as browser:` spins up the existing CDP Chrome (from `web-scraping` skill). Automatically invoked when `requests_fetch` returns 403, 503, or an HTML that contains a CAPTCHA marker we detect.

### Monthly revenue scraper

**Endpoint:** `https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs`

**Method:** POST with form data. Two useful call shapes:

- *By market-month* (for backfill + daily catch-up): `step=1&functionName=t05st10_ifrs&TYPEK=sii&year={YYYY}&month={MM}&co_id=` → returns HTML table with one row per company.
- *By ticker* (for targeted catch-up on lagging names): `step=1&TYPEK=sii&year=&month=&co_id={ticker}` → returns the company's full monthly revenue history.

**Backfill plan:** 10 years × 12 months × 2 markets (`sii`, `otc`) = 240 POST calls. At 1 req/sec, ~4 minutes. Each response parsed, filtered to watchlist tickers, written to parquet. Raw HTML saved to `_raw/monthly_revenue/{ticker}/{YYYY-MM}.html`. Trivial.

**Ongoing:** during days 1–15 of each month, fire the by-market-month query daily at 10:00 TPE for the current month. Days 16–31, fire every 3 days as safety net for late filers.

### Material information scraper — three paths

1. **RSS feed** (`https://mops.twse.com.tw/mops/rss/getRss`) — one GET returns recent filings across all listed companies. Used for ongoing real-time polling every 5 min during 08:00–18:00 TPE, hourly overnight. The RSS gives us enough metadata (ticker, date, seq_no, title, URL) to diff and decide which bodies to fetch.

2. **Per-ticker history** (`https://mops.twse.com.tw/mops/web/ajax_t05st01` with `co_id` + date range) — used only at backfill. Paginated; walk backwards until we hit "no more data" or a configurable year floor (default: 10 years back).

3. **Filing body fetcher** — once we have `(co_id, spoke_date, seq_no)` from (1) or (2), POST to the body endpoint to get full HTML. Parse out title, category, body text. Save raw HTML. Call `translation.translate_material_info(body_zh)` to get `body_en` + `summary_en`. Write to `material_info/bodies.parquet`.

### Playwright fallback triggers

Any of these from a `requests` fetch triggers an automatic retry via Playwright:

- HTTP 403 or 503.
- Response HTML contains a known CAPTCHA marker (`<img src=".../kaptcha.jpg">` or similar).
- Response is empty or < 500 bytes when the query is known to return multi-row data (heuristic; tunable per endpoint).

Playwright fallback uses the `web-scraping` skill's CDP Chrome connection — same browser, same profile, session shared.

---

## Translation pipeline

`backend/app/services/taiwan/translation.py` exposes:

```python
def translate_material_info(body_zh: str, title_zh: str, category_label_zh: str) -> dict:
    """Returns {title_en, body_en, summary_en, category_label_en, tokens_in, tokens_out}."""
```

Uses Gemini 2.5 Flash via the existing `LLMProvider` port. Single call per filing. Prompt asks for a structured JSON response:

```json
{
  "title_en": "...",
  "body_en": "...",
  "summary_en": "1-2 sentence analyst summary of what this filing means",
  "category_label_en": "..."
}
```

Reuses the hardened JSON parser (`json_repair` fallback) from `live_transcription.py`. On parse failure, we keep the raw Chinese and log a translation warning — **never drop the filing**.

**Cost model** (2026-Q2 Gemini Flash pricing):
- Avg material info body: ~300 Chinese chars ≈ 600 tokens in + 400 tokens out ≈ $0.00015/filing.
- Full backfill: ~50,000 filings × $0.00015 ≈ **$7.50 one-time**.
- Ongoing: ~300 filings/day across the watchlist × $0.00015 ≈ **$0.05/day**.

Monthly revenue has trivially cheap translation (company names + column headers); done at parse time from a static glossary, no Gemini call.

**Caching:** `translation_cache` table keyed by `content_hash` of the Chinese source. Identical Chinese text → no re-translation. Handy when a ticker's amendment re-files with mostly-unchanged content.

---

## Scheduler

`backend/app/services/taiwan/scheduler.py` is the service entry point. Run via `python -m backend.app.services.taiwan.scheduler`.

Uses `APScheduler`'s `BlockingScheduler` with the following cron jobs:

| Job | Cadence | Target |
|---|---|---|
| `material_info_rss_poll` | every 5 min, 08:00–18:00 TPE; every 60 min, 18:00–08:00 TPE | RSS → diff → fetch new bodies for watchlist matches |
| `monthly_revenue_daily` | 10:00 TPE daily | pull current-month summary for `sii` + `otc`; diff; insert/amend |
| `monthly_revenue_catchup` | 10:00 TPE every 3 days | prior-month summary (catches late filers) |
| `company_master_refresh` | 1st of month, 03:00 TPE | re-pull MOPS full company master |
| `health_check` | hourly | reads heartbeat table; log WARN for stale scrapers |
| `s3_mirror_reconcile` | every 15 min | scans local `_raw/` for files not yet uploaded; uploads lagger |

Every job writes to the `taiwan_scraper_heartbeat` SQLite table on completion (or failure), and logs structured JSON via `structlog` to stdout. Fly.io captures stdout → fly logs.

### Heartbeat table schema (SQLite)

```sql
CREATE TABLE taiwan_scraper_heartbeat (
    scraper_name     TEXT PRIMARY KEY,
    last_run_at      TIMESTAMP,
    last_success_at  TIMESTAMP,
    last_error_at    TIMESTAMP,
    last_error_msg   TEXT,
    rows_inserted    INTEGER,
    rows_updated     INTEGER,
    rows_amended     INTEGER,
    status           TEXT CHECK(status IN ('ok', 'degraded', 'failed'))
);
```

### Retry strategy

- Per-request retries: 3 with backoff 2s / 8s / 32s.
- Per-ticker scrape: if a specific ticker fails 3 consecutive runs, mark `scraper_state[ticker].status = 'needs_investigation'` in the registry parquet. Other tickers continue; the run logs `WARN` but does not fail.
- Per-job retries: if an entire job fails (not a single ticker), APScheduler logs it and re-fires at the next cadence. We do not retry within the same slot.

---

## Backfill (prioritised, per Q6 decision (iii))

### Phase 1 — headers only (~1–2 hours)

1. Run `company_master_scraper` to populate `mops_company_master.parquet`; cross-reference watchlist to validate tickers.
2. Run `monthly_revenue_scraper` in backfill mode: 10 years × 12 months × 2 markets → 240 requests. Populates `monthly_revenue/data.parquet`.
3. Run `material_info_history.py` for all 51 tickers with `metadata_only=True` — this walks each ticker's material-info list page and records `(ticker, seq_no, date, category, title_zh, url)` **without** fetching bodies. ~5,000 requests, ~1.5 hours at 1 req/sec. Populates `material_info/index.parquet`.

After Phase 1: the UI's watchlist grid has full monthly-revenue history, and the material info feed lists every historical filing's header. Users can browse; clicking a filing whose body isn't yet cached triggers Phase 2 logic for that one body (real-time fetch + translate + cache — <5s).

### Phase 2 — bodies, background

4. `material_info_body_backfill` job runs in the background at 1 body/3 sec (gentle). Works through `index.parquet` in reverse-chronological order (newest unresolved first → users see recent history first). Writes to `material_info/bodies.parquet`.
5. Full watchlist body coverage: ~50,000 filings × 3 sec ≈ 42 hours. Spread over 2 days, this runs concurrently with the real-time scraper without interference (separate thread, separate rate limiter).

### Resumability

Phase 1 and Phase 2 both write their progress to `scraper_state.parquet`:

```
ticker   last_month_scraped   last_material_info_backfilled_seq   last_run_status
2330     2026-03              MI2026020300123                     ok
2454     2025-12              MI2026040100042                     needs_investigation
```

Re-running a backfill skips already-done work; it only processes what's missing.

---

## API surface

All under `/api/v1/taiwan/*`. FastAPI auto-generates OpenAPI schema; agents can introspect.

### Endpoints

```
GET  /api/v1/taiwan/watchlist
  → [{ticker, name, market, sector, subsector}]

GET  /api/v1/taiwan/ticker/{ticker}
  → {ticker, name, market, subsector, latest_revenue, latest_material_info_count, last_material_info_at}

GET  /api/v1/taiwan/monthly-revenue?tickers=2330,2454&months=24
  → [{ticker, fiscal_ym, revenue_twd, yoy_pct, mom_pct, ytd_pct, amended, first_seen_at, last_seen_at}]

GET  /api/v1/taiwan/monthly-revenue/{ticker}/history
  → entries from history.parquet: [{fiscal_ym, revenue_twd, content_hash, first_seen_at, superseded_at}]

GET  /api/v1/taiwan/material-info?tickers=...&since=...&until=...&categories=...&limit=50&offset=0
  → [{seq_no, ticker, published_at, category_code, category_label_en, category_label_zh, title_en, title_zh, url}]

GET  /api/v1/taiwan/material-info/{ticker}/{seq_no}
  → {seq_no, ticker, published_at, category_*, title_*, body_zh, body_en, summary_en, url, raw_path}

GET  /api/v1/taiwan/health
  → {scrapers: [{name, status, last_success_at, lag_seconds, last_error_msg}]}
```

### Response conventions

- All timestamps ISO-8601 with TZ.
- All numeric revenue in TWD (int64).
- Percentages as floats (`0.23` = 23%; we also include a `*_display` sibling string with the original MOPS-formatted value for auditability).
- Pagination: `offset` + `limit` + `total`. Max limit 500 per response.
- CORS wide open for GET (these are read-only public-data endpoints), same auth as the rest of AlphaGraph for future write endpoints.

---

## Frontend

### Routing

New top-level tab `[Taiwan]` in the dashboard sidebar. Route: `/taiwan`. Page structure mirrors the existing Notes library pattern:

```
frontend/src/app/(dashboard)/taiwan/
  page.tsx                  entry (server component)
  TaiwanContainer.tsx       smart: data fetching, state, filters
  TaiwanView.tsx            dumb: pure JSX composition
  components/
    WatchlistRevenueGrid.tsx   table; subsector tabs; sortable; YoY heatmap
    MaterialInfoFeed.tsx       scrollable list; filters; side-by-side zh/en
    MaterialInfoDetailDrawer.tsx  full filing view with translation toggle
    TickerDrillDown.tsx        revenue chart (Recharts) + per-ticker material info
```

### Three primary views

1. **Watchlist revenue grid** — subsector tabs (Foundry / IC Design / Memory / etc.) → sortable table: ticker, name, latest month's revenue, YoY%, MoM%, mini-sparkline of last 12 months. Heatmap coloring on YoY column (green/red gradient). Clicking a row opens the drill-down.

2. **Material info feed** — live-polling (client-side poll every 30s during market hours) list of the most recent ~100 filings across the watchlist. Each row shows ticker, timestamp (relative), category badge (coloured per category code), and **both** `title_zh` and `title_en` side-by-side (Chinese ~left 40%, English ~right 60%). Clicking opens the detail drawer.

3. **Material info detail drawer** — slides in from right. Top shows filing metadata. Main body is side-by-side: Chinese body left column, English body right column, same width. Scroll syncs between columns. Copy button per column. Source URL link to MOPS. "Show in raw HTML" button opens the `_raw/...` archive for auditability.

4. **Per-ticker drill-down** — full-page view at `/taiwan/[ticker]`. Top: company metadata + latest revenue summary. Left: monthly revenue chart (last 5 years, Recharts line; toggles for absolute / YoY /indexed). Right: full material info timeline, reverse-chronological. Amendments annotated in both views.

### Components follow existing patterns

- Container / View split (per `CLAUDE.md`).
- Recharts for all charting (already a dep).
- Tailwind classes consistent with Notes library.
- No new lib dependencies.

---

## Data quality / validation

On every parse, `validation.py` runs invariant checks:

- `revenue_twd >= 0` (negative revenue is legal for certain refund scenarios but rare; flag, don't drop).
- `yoy_pct` absolute value < 1000 (>10× YoY is almost always a parse error or a stub year; flag).
- `fiscal_ym` is a valid YYYY-MM string, not in the future.
- `seq_no` non-empty, `published_at` not in the future.
- For amendments: if the value change is >50% of the prior value, flag as `large_amendment` and include in the daily health report.

Flags don't drop the row — they write it with `parse_error` or `quality_flag` fields populated. UI shows a small warning badge on flagged rows.

---

## Observability

- **Structured logs** (stdout JSON) via `structlog` with fields `scraper`, `ticker`, `endpoint`, `duration_ms`, `status`, `rows_inserted`, `error`.
- **Heartbeat table** — as schema above. Read by `/api/v1/taiwan/health`.
- **Frontend health widget** — small coloured dot in the Taiwan tab header: green (all scrapers <2× their cadence), yellow (lag), red (>1 scraper failed in last hour). Tooltip shows per-scraper status. Reads `/health` every 60s.
- **Fly.io built-ins** — stdout logs captured, restarts via `fly restart`, metrics visible in fly dashboard.

## Deployment

Two Fly.io machines in the same app, each running a different process group (Fly's multi-process app pattern):

```toml
# fly.toml (excerpt)
[processes]
  web = "uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"
  taiwan_scheduler = "python -m backend.app.services.taiwan.scheduler"

[[mounts]]
  source = "alphagraph_data"
  destination = "/data"
  processes = ["web", "taiwan_scheduler"]
```

- One shared volume (Fly volumes) mounted at `/data` containing `alphagraph.db` (SQLite) + `backend/data/taiwan/` parquet tree. Both processes read/write the same volume.
- Environment variables via `fly secrets`: `GEMINI_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_RAW=alphagraph-taiwan-raw-prod`, `MOPS_USER_AGENT=<realistic UA string>`.
- Image build: single Dockerfile for both processes (Python + Node + Chromium for Playwright). Multi-stage to keep image <2 GB.
- Scheduler restart policy: `restart = "always"` with 10s min-run; repeated crashes surface in fly logs.

---

## Plan decomposition

This spec will produce **two implementation plans**, each end-to-end shippable. Keep each at ~8–12 tasks so the executing-plans skill can run them inline.

### Plan 1 — Foundation + Monthly Revenue end-to-end

Infrastructure + one feature that exercises every piece of it.

- `mops_client.py` with rate limit + retry + Playwright fallback.
- `translation.py` Gemini wrapper + cache.
- `storage.py` parquet + raw capture + S3 mirror.
- `amendments.py` + `validation.py`.
- `company_master` scraper (+ watchlist validation).
- `monthly_revenue` scraper + parser + backfill.
- Heartbeat table + `health.py` + `/health` endpoint.
- `/api/v1/taiwan/watchlist`, `/monthly-revenue`, `/ticker/{ticker}`.
- Frontend: Taiwan top-level tab + WatchlistRevenueGrid + basic TickerDrillDown.
- Scheduler skeleton with just the monthly-revenue + company-master + health jobs.
- Fly.io deploy config (web + taiwan_scheduler + shared volume).
- Tests: `mops_client` retry/fallback, parser round-trip, amendment detection, validation invariants, heartbeat writer.

Verification: monthly revenue for all 51 tickers visible in the dashboard, sortable, with YoY% coloured. Scheduler running on Fly.io with heartbeat confirming.

### Plan 2 — Material Information end-to-end

Layers on top of Plan 1's foundation.

- `material_info_rss.py` + RSS parser.
- `material_info_history.py` + `material_info_body.py`.
- Translation integration for material info bodies.
- Backfill (prioritised): Phase 1 metadata-only → Phase 2 body fetcher (background).
- `/api/v1/taiwan/material-info*` endpoints.
- Frontend: MaterialInfoFeed + MaterialInfoDetailDrawer + side-by-side bilingual view; integrate into TickerDrillDown.
- Scheduler: add `material_info_rss_poll` + `material_info_body_backfill` jobs.
- Live-polling in the UI (client-side `setInterval`).
- Tests: RSS parser, body fetcher, amendment detection for material info, translation fallback behaviour.

Verification: material info feed updates within 5-10 minutes of MOPS publication; backfill completes over ~2 days with all bodies translated.

---

## Open operational items (not blocking implementation)

These are real decisions but can be made during Plan 1 execution without rework:

- **Fly.io region** — default `nrt` (Tokyo) for low latency to MOPS. ~50ms RTT vs ~150ms from US regions. Easy to change later.
- **S3 region** — match Fly region (`ap-northeast-1`).
- **S3 lifecycle policy** — `_raw/` captures after 2 years → Glacier. After 7 years → delete. Governance-grade retention.
- **MOPS User-Agent** — pick a realistic Chrome UA; register the project contact if MOPS has a registration channel (they don't publicly, but good etiquette).
- **Monitoring threshold** — initial "scraper stale" alert at 2× expected cadence. Tune after first week of production.
- **Amendment notification** — do users want email / Slack notification when a watchlist ticker issues a revenue amendment? Not in MVP; backlog item.

---

## References

- MOPS portal: `https://mops.twse.com.tw/`
- TWSE data: `https://www.twse.com.tw/` (not used in this spec, future market-data source)
- TPEx: `https://www.tpex.org.tw/`
- Existing `web-scraping` skill: `.claude/skills/web-scraping/SKILL.md`
- Existing parquet pattern: `backend/data/market_data/news/`
- Watchlist: `backend/data/taiwan/watchlist_semi.csv`
- Backlog of deferred Taiwan filings: `docs/superpowers/backlog.md`
