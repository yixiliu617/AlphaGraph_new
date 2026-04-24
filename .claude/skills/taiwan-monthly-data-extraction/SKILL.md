---
name: taiwan-monthly-data-extraction
description: Extracting Taiwan-listed monthly revenue data. Two-source design — MOPS SPA (latest 12m, per-ticker JSON via Playwright CDP) for freshness + TWSE open-data C04003 ZIPs (10+ years, bulk XLS via plain HTTP) for backfill. Covers anti-bot WAF bypass, ROC calendar, thousand-TWD units, Python 3.13 TLS fixes, and endpoint rediscovery when either site is redesigned.
---

# Taiwan Monthly Data Extraction (MOPS / 公開資訊觀測站)

## TL;DR — Don't Repeat Our Mistakes

The 2024 MOPS redesign broke every scraper that relied on the old `ajax_t05st10_ifrs` endpoints. If you're building this fresh:

1. **DO NOT** use `requests` / `httpx` / any direct HTTP client — MOPS WAF blocks them even with perfect browser headers.
2. **DO NOT** try to parse HTML tables — the new site is a SPA; tables are rendered client-side from JSON.
3. **DO NOT** build around "one call per market-month returns all companies" — that endpoint is gone; the new API is **per-ticker**.
4. **DO** use Playwright attached to a persistent CDP Chrome profile, then use that context's `.request.post()` for fast JSON calls.
5. **DO** warm the origin once (`page.goto("https://mops.twse.com.tw/mops/#/")`) before the first JSON call — the context picks up whatever cookies the WAF wants.

## The Site

| Item | Value |
|---|---|
| Name | Market Observation Post System (公開資訊觀測站) |
| Operator | Taiwan Stock Exchange |
| URL | `https://mops.twse.com.tw/mops/#/` |
| Architecture | Vite-built SPA — `<div id="app">` + JS bundle |
| Bundle entrypoint | `/mops/assets/index.js` |
| Deep links | Hash-routed, e.g. `#/web/t146sb05?companyId=2330` |
| Language | Traditional Chinese (no English mirror for data API) |

## The WAF

MOPS aggressively blocks non-browser traffic. **Confirmed blocked:**

- `curl` with no headers → HTML error page "FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED!"
- `curl` with User-Agent + Referer + Origin + XMLHttpRequest + Accept-Language → still blocked
- Python `requests` with a full header dict → blocked
- Static `.js` asset fetch with `curl` → blocked (so you can't even diff the bundle easily)

**What works:** any request that originates from inside a real Chromium context. We use Playwright connected to a CDP-mode Chrome, reusing a persistent user profile — the context it gets from `browser.contexts[0]` carries whatever fingerprint / cookies the WAF considers legitimate.

The WAF does not seem to rate-limit aggressively (we've made dozens of requests per minute from one session without issue), but keep your rate reasonable anyway (1 req/sec steady state is safe).

## Tech Stack

```
backend/app/services/taiwan/
  mops_client.py           # Playwright CDP browser-context JSON client
  mops_client_browser.py   # CDP Chrome launcher / health check
  scrapers/
    monthly_revenue.py     # per-ticker t146sb05_detail → rows
    company_master.py      # KeywordsQuery-driven ticker→sector resolution
  storage.py               # parquet + raw JSON capture + amendment history
  amendments.py            # content-hash upsert decisions (INSERT/TOUCH/AMEND)
  validation.py            # schema invariants as flags (never drop rows)
  registry.py              # watchlist + resolved company master
  health.py                # SQLite scraper_heartbeat observability
  scheduler.py             # APScheduler BlockingScheduler entrypoint
```

## Discovery — Finding the Real API

When MOPS redesigns again (they will), rediscover endpoints this way:

1. Launch CDP Chrome with your scraper profile (`~/.alphagraph_scraper_profile`, port 9222).
2. Connect Playwright, open a page, attach `page.on("request")` and `page.on("response")` hooks that **skip analytics.google.com and googletagmanager** (90% of the noise).
3. Navigate to the relevant section of MOPS manually in the same window, or script the click path.
4. Print every XHR / fetch call + its request body + response body snippet.
5. Look for calls to `mops.twse.com.tw/mops/api/...` — those are the real endpoints.

See `tools/mops_explore.py` in this project for a working template.

## Known Endpoints (as of 2026-04)

### `POST /mops/api/KeywordsQuery`
**Purpose:** ticker autocomplete; returns `[{title: "上市半導體業", data: [{result: "2330 台灣積體電路製造股份有限公司", url: "#/web/t146sb05?companyId=2330"}]}]`.
**Body:** `{"queryFunction": true, "keyword": "2330"}`
**Useful for:** mapping ticker → (market, sector) tag. The `title` prefix `上市` = TWSE, `上櫃` = TPEx, `興櫃` = Emerging, `公開發行` = Public (non-listed).

### `POST /mops/api/t146sb05`
**Purpose:** single-company overview — recent news + basic info + last 4 months of revenue + financial summary + dividend info.
**Body:** `{"companyId": "2330"}`  (camelCase)
**Response:** `result.revenue_information.revenueInformation[]` — YTD summary + 4 months. **Limited window.**

### `POST /mops/api/t146sb05_detail`
**Purpose:** monthly revenue history — last 12 months only.
**Body:** `{"company_id": "2330"}`  (**snake_case — different from t146sb05!**)
**Response shape:**
```json
{
  "code": 200,
  "result": {
    "title": "台積電最近12個月份（累計與當月）營業收入統計表",
    "titles": ["年度","月份","營業收入","累計營業收入"],
    "data": [
      ["115","3","415,191,699","285,956,830","45.19%","1,134,103,440","839,253,664","35.13%"],
      ...  // 12 rows, most recent first
    ],
    "footer": [...]  // IFRS disclosure notes
  }
}
```
Row columns (positional): `[roc_year, month, revenue, prior_yr_month_revenue, yoy_pct, ytd_revenue, prior_yr_ytd, ytd_yoy_pct]`.

### `POST /mops/api/t05st01_detail`
**Purpose:** individual material-information announcement detail (full text of 重大訊息).
**Body:** `{"serialNumber": "N", "enterDate": "1150423", "companyId": "2330", "marketKind": "sii"}` — these tuples come from `t146sb05.result.recent_important_news.data[i][2].parameters`.

### Not yet discovered (TODO as encountered):
- Historical monthly revenue > 12 months (TWSE public data services likely, different host)
- Quarterly financial statements (`t164sb03` or similar — probe via the SPA's 財報資訊 section)
- Company master list by market (TWSE/TPEx full roster — probe via 彙總報表 section)

## Canonical Row Schema (what storage expects)

```python
{
  "ticker": "2330",
  "market": "TWSE",              # resolved via KeywordsQuery title prefix
  "fiscal_ym": "2026-03",         # AD year, zero-padded month
  "revenue_twd": 415_191_699_000, # full TWD (NOT thousand-TWD — multiply by 1000)
  "yoy_pct": 0.4519,              # decimal (1.0 = 100%)
  "mom_pct": ...,                 # COMPUTED locally from consecutive months (not in API)
  "ytd_pct": 0.3513,
  "cumulative_ytd_twd": 1_134_103_440_000,
  "prior_year_month_twd": 285_956_830_000,
  "first_seen_at": <datetime>,
  "last_seen_at": <datetime>,
  "content_hash": <sha256>,
  "amended": False,
  "parse_flags": [...],
}
```

## Corner Cases & Fixes

### 1. ROC calendar (民國年) conversion
MOPS returns years as ROC (民國). Convert: `ad_year = roc_year + 1911`. Always. Everywhere.
- `"115"` → 2026
- `"114"` → 2025
- `"113"` → 2024
Gotcha: some responses use 7-digit ROC dates like `"1150423"` (YYYMMDD) — split as `year=int(s[:3])+1911, month=int(s[3:5]), day=int(s[5:7])`.

### 2. Units: 仟元 (thousand TWD)
Monthly revenue values are in **thousand TWD** per the page's `單位 : 新台幣仟元` note. Our canonical schema stores **full TWD**, so multiply API values by 1000. Don't just take the raw integer.

### 3. Percentages as strings with `%`
API returns `"45.19%"`, not `0.4519`. Strip `%`, parse float, divide by 100. Accept also `"-12.5"` (Western minus), `"−12.5"` (en-dash minus — MOPS has used both historically).

### 4. Sentinel values in percentage fields
MOPS documents a sentinel: `"999999.99"` means "cannot compute" (overflow or divide-by-zero). Treat `abs(value) >= 999_999.99` as `None`, not a real datapoint.

### 5. Empty / dash cells
Some months have `"-"` or `""` in revenue columns (e.g. new listings, IPO month). Coerce to `None`, don't crash. Flag the row via validation but still store it.

### 6. Payload key inconsistency: `companyId` vs `company_id`
The overview endpoint uses `companyId` (camelCase). The detail endpoint uses `company_id` (snake_case). Document it in code. Don't assume one.

### 7. No MoM% in the API
The new API gives you revenue + YoY + YTD + YTD-YoY but **not** month-over-month. Compute it after parsing:
```python
for i, row in enumerate(rows[:-1]):
    if row["revenue_twd"] and rows[i+1]["revenue_twd"]:
        row["mom_pct"] = row["revenue_twd"] / rows[i+1]["revenue_twd"] - 1
```
MoM is useful for our UI heatmap even though MOPS doesn't volunteer it.

### 8. Amendment detection on JSON rows
Same principle as HTML-era: `content_hash = sha256(canonical_json_of_immutable_fields)`. Immutable = everything except `first_seen_at / last_seen_at / content_hash / amended / parse_flags`. New hash with same key → AMEND; same hash → TOUCH_ONLY; no prior key → INSERT.

### 9. Unicode in Windows terminal
Windows `cp950` / `cp1252` consoles crash on `print()` of Chinese titles. Fix once at module top:
```python
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
```
This is required for dev/debug only; prod logs go to structlog → JSON which is already UTF-8-clean.

### 10. Market code resolution
`t146sb05_detail` doesn't tell you if 2330 is TWSE or TPEx. Resolve once via `KeywordsQuery` and cache in a `company_master` parquet. Prefixes:
- `上市…` → `TWSE` (sii)
- `上櫃…` → `TPEx` (otc)
- `興櫃…` → `Emerging`
- `公開發行…` → `Public` (non-listed)

### 11. CDP Chrome cold-start timing
First launch of the scraper profile can take 10+ seconds. The helper in `mops_client_browser.py` polls `http://localhost:9222/json/version` until it answers. Don't use a fixed `time.sleep(N)` — it either wastes time or is too short.

### 12. 12-month history cap on the detail endpoint
`t146sb05_detail` caps at 12 months. For historical backfill (10y+), use
the **TWSE open-data C04003 archive** (see separate section below). Two
sources, same canonical schema:

| Need | Source |
|---|---|
| Latest 12 months, per ticker, refresh daily | MOPS `t146sb05_detail` via Playwright |
| 2016-01 → prior month, full market, one-shot backfill | TWSE open-data C04003 ZIPs |
| Current month before MOPS publishes | MOPS only — TWSE publishes ~1 month lagged |

### 13. Rate limiting & session re-use
The CDP browser context is stateful — cookies built up during `page.goto("…")` travel with `ctx.request.post()`. **Reuse one context across all 51 tickers**. Spinning up a fresh browser per call is 10x slower and some times triggers WAF re-challenge.

### 14. Raw capture for audit
Always persist the raw JSON response (`_raw/monthly_revenue/2330/2026-03_detail.json`) alongside the parsed parquet. If an amendment appears three months later and you need to prove what MOPS said originally, the raw blob is the audit trail. Content-hash the raw file so re-fetches that return identical JSON are no-ops.

### 15. Defensive `_warm_origin` when no page is attached
Production code always has an attached `_page` after `open()`. Tests that stub the Playwright context by directly setting `_ctx = MagicMock()` don't have a page. If the retry path tries to re-warm the origin (after seeing HTML-on-200), it will crash with `AssertionError: open() the client first`. Fix: make `_warm_origin` a no-op when `_page is None` and mark `_warmed = True` so the retry loop doesn't spin:
```python
def _warm_origin(self):
    if self._warmed:
        return
    if self._page is None:
        self._warmed = True  # nothing to warm
        return
    self._page.goto(_WARM_URL, wait_until="domcontentloaded", timeout=...)
    self._warmed = True
```

### 16. Test fixtures must be real, not synthesised
When I tried to reason about the API shape from docs / memory, I got fields wrong (thought MoM was present, thought `companyId` applied to both endpoints). The fix is to **capture live JSON** into `backend/tests/fixtures/taiwan/` and test against the real bytes. Don't hand-write fixtures — MOPS will surprise you. Provide a capture script (`tools/mops_fetch_detail_2330.py`) that re-dumps the fixture whenever needed.

### 17. Field name inconsistency — stash it in one place
`KeywordsQuery` wants `"queryFunction": true, "keyword": "2330"`. `t146sb05` wants `"companyId": "2330"`. `t146sb05_detail` wants `"company_id": "2330"` (snake case). Don't spread these through scraper code — declare them at the top of each scraper file as constants so a future API rename means touching one line, not 20.

### 18. Module-level `Path(__file__).resolve().parents[N]` is fragile
Monkey-patched tests override the resolved path, but only if the monkeypatch happens before the registry module is imported. If you import registry at the top of your test module, the parent path is already baked in. Prefer `registry.REGISTRY_PARQUET` as a module attribute and `monkeypatch.setattr(registry, "REGISTRY_PARQUET", tmp_path / ...)` — works because attribute access is late-bound.

## TWSE Open-Data (historical backfill source)

The MOPS detail endpoint caps at 12 months. For anything older, use the
**TWSE 統計報表 → 上市公司月報** archive at
`https://www.twse.com.tw/zh/trading/statistics/index04.html`. Key facts:

| Item | Value |
|---|---|
| WAF | None — direct HTTP works (but see "Python TLS" note below) |
| Manifest | `GET /rwd/zh/statistics/download?type=04&response=json` |
| Files per month | 4 reports (C04001–C04004) |
| Our report | **C04003 — 國內上市公司營業收入彙總表** (domestic listed revenue summary) |
| Path | `/staticFiles/inspection/inspection/04/003/{YYYYMM}_C04003.zip` |
| Filename date | **AD year** (4-digit), NOT ROC — e.g. `202601_C04003.zip` for Jan 2026 |
| Contains | One legacy `.xls` per ZIP, all listed companies for that month |
| Coverage | 民國 88 (1999) to prior month (current month typically unavailable) |
| Scope | TWSE 上市 only. TPEx 上櫃 is a separate system (TODO) |

### C04003 XLS layout

Exactly 10 columns. Row types discovered empirically:

```
row 0-9:   headers + bilingual titles (SKIP)
row 10:    "01  水泥工業類" — industry section header (1-2 digit code, SKIP)
row 11+:   "2330  台積電"  — company rows (4-6 digit ticker)
...        alternating sections by industry
row ~1052: "總額 Total" / "平均 Average" — aggregates (SKIP)
row ~1059: "備註: …" — footer notes (SKIP)
```

Per-company column positions:

| Col | Content |
|---|---|
| 0 | `{ticker}{whitespace}{name_zh}` |
| 1 | Previous month (M-1) revenue |
| **2** | **Current month revenue** |
| 3 | YTD revenue |
| 4 | Prior year same month (M of Y-1) |
| 5 | Prior year YTD (Jan..M of Y-1) |
| 6 | YTD absolute diff |
| 7 | YTD % diff (ambiguous — we recompute from cols 3,5) |

**Units:** all monetary values are **thousand TWD**. Same convention as MOPS. Multiply by 1000 on ingest.

### Row-type discrimination

```python
# Industry headers: 1-2 digit code
_INDUSTRY_ROW_RE = re.compile(r"^\s*\d{1,2}\s+\S")
# Company rows: 4-6 digit ticker
_COMPANY_ROW_RE = re.compile(r"^\s*(\d{4,6})\s+(\S.*?)\s*$")
```

Check company regex FIRST — some rare 4-digit rows could also match the 2-digit industry prefix. Our implementation keeps the row only if `_COMPANY_ROW_RE.match(col0)` returns a hit.

### Corner cases specific to TWSE open-data

#### CC-T1. Python 3.13 rejects the TWSE cert (Missing Subject Key Identifier)
Same symptom as MOPS, different host. `curl` accepts it, browsers accept it, Python 3.13's stricter TLS rejects it. We pass `verify=False` to `requests.get()` — documented explicitly in `twse_historical.py` as safe because:
- It's public open-data (no secrets on the wire)
- The filename-only URL makes MITM substitution loud (wrong filesize, bad ZIP magic)
- The `raise_for_status()` and `zipfile.ZipFile` parse each act as integrity checks

Alternative if you don't want `verify=False`: `pip install truststore` and call `truststore.inject_into_ssl()` at process start; uses the OS trust store which handles this cert.

#### CC-T2. ZIP contains a `.xls` with a bizarre filename
`20202601.XLS` for the Jan 2026 report — neither the outer ZIP name nor the date. Don't match on filename; just take the first `*.xls` inside.

#### CC-T3. Legacy `.xls` (Excel 97/2003 binary), not `.xlsx`
Requires **`xlrd>=2.0.1`**, not `openpyxl`. Add to `requirements.txt`. `pandas.read_excel(..., engine="xlrd")` is the way.

#### CC-T4. Disk-cache ZIPs by `{YYYYMM}_C04003.zip`
A fresh 10-year backfill is ~120 HTTP calls. Cache each ZIP on disk so rerun-on-failure takes ~30 seconds. Cache key is the filename; content is deterministic (TWSE never rewrites historicals — if they do, raw-capture audit already protects us).

#### CC-T5. Later-IPO tickers have partial history
Our watchlist's `6770` has only 53 months in 10 years — it IPO'd around 2021. Don't treat missing months as a failure; just store what's there.

#### CC-T6. Format is stable 2000 → 2026
Spot-checked column positions at `2000-03`, `2005-07`, `2010-01`, `2015-05`, `2020-06`, `2026-01`. Column layout unchanged across 26 years. The row count grows (470 → 978 as more companies IPO).

#### CC-T7. TPEx (上櫃) tickers are NOT in C04003
Eleven of our 51 watchlist tickers are TPEx-listed and don't appear in the TWSE archive. They currently have only the 12 months MOPS provides. A separate TPEx open-data endpoint exists (TODO — probably at `tpex.org.tw/web/stock/statistics/monthly/`).

#### CC-T8. Current-month file is usually missing
TWSE publishes a month's C04003 roughly mid-following-month. Don't try to fetch the current month — expect `404` or the MOPS daily scraper handles it. Default `--end` in the backfill script is `now - 1 month`.

### Tooling

- `tools/twse_explore.py` — XHR interceptor (same pattern as `mops_explore.py`) for rediscovering the manifest if TWSE changes their site.
- `tools/twse_backfill.py` — one-shot runner: `python tools/twse_backfill.py --start 2016-01 --end 2026-03 --data-dir /tmp/taiwan-backfill-test` (writes to the given parquet dir; disk-caches ZIPs under `<data-dir>/_raw/twse_zip/`).

### Verification

After a 10-year run, confirm:
- TSMC (2330) has ~120 continuous monthly rows
- 2016-01 revenue is in the 70B TWD range (pre-smartphone-boom trough)
- YoY sign changes correctly across historical inflection points
- MoM fills for all rows except the first per-ticker

## TPEx Open-Data (historical backfill for 上櫃 companies)

For the ~15% of Taiwan-listed companies on TPEx (e.g. Phison 8299 for
flash controllers, GlobalWafers 6488 for silicon wafers), the TWSE
C04003 archive does not include them. TPEx publishes its own monthly
revenue archive.

| Item | Value |
|---|---|
| Page | `https://www.tpex.org.tw/zh-tw/mainboard/listed/month/revenue.html` |
| WAF | None — direct `requests.get` works (same Python-3.13 TLS workaround as TWSE) |
| XLS URL | `/storage/statistic/sales_revenue/{prefix}_{YYYYMM}.xls` — NO outer ZIP |
| Prefix | `O` = 上櫃 / TPEx regular, `U` = 興櫃 / Emerging |
| Filename date | AD year (same as TWSE) |
| Coverage | **2009-12 onwards** (earlier months return HTTP 302) |
| JSON alt | `POST /www/zh-tw/statistics/salesRevenue body=date=&id=&response=json` (current month only; no date param) |

### TPEx XLS layout — column offsets differ from TWSE!

TPEx's XLS inserts an **empty spacer column at position 1**. Every data
column is shifted right by one vs. TWSE C04003:

| Col | TWSE C04003 | **TPEx O_YYYYMM** |
|---|---|---|
| 0 | `{ticker}  {name}` | same |
| 1 | Previous month | **SPACER (blank)** |
| 2 | **Current month** | Previous month |
| 3 | YTD | **Current month** |
| 4 | Prior-year same month | YTD |
| 5 | Prior-year YTD | Prior-year same month |
| 6 | YTD diff | Prior-year YTD |
| 7 | YTD % | YTD diff |

Anti-pattern: sharing a parser between TWSE and TPEx via a column-offset
param is fine, but tests MUST lock the offsets per source with a real
fixture — the two files are otherwise visually identical.

### Corner cases specific to TPEx

#### CC-T9. TPEx returns HTTP 302 for not-yet-published months
TWSE returns a 514-byte "invalid zip" HTML page in the same situation; TPEx returns a proper 302 redirect to the homepage. Pass `allow_redirects=False` to `requests.get` so the caller sees the 302 and raises `FileNotFoundError` instead of silently following to the homepage and then choking on an HTML file claiming to be XLS.

#### CC-T10. TPEx XLS is NOT wrapped in a ZIP
Unlike TWSE C04003 (`.zip` containing a `.xls`), TPEx serves the XLS directly. Don't reuse the TWSE `_extract_xls_from_zip` — the content begins with `D0 CF 11 E0` (CFB magic), not `PK`.

#### CC-T11. TPEx archive horizon (2009-12)
The earliest available month is 2009-12. Before that, the files exist for purchase via TPEx's paid historical data service but aren't online. Document `start=(2009, 12)` as the default in the backfill tool.

#### CC-T12. Two markets from one site: `O` vs `U` prefix
- `O_` = 上櫃 (TPEx regular — listed on TPEx main board)
- `U_` = 興櫃 (Emerging — pre-listing companies)
Same URL pattern, different file prefix. We only currently ingest `O_`; `U_` is available via the same scraper with `prefix="U"` if needed.

#### CC-T13. Watchlist CSV's `market` column can be stale
When we ran the first TWSE backfill, tickers 8110 and 8021 — listed as "TPEx" in our watchlist CSV — showed up in the TWSE C04003 file. Cross-check with MOPS KeywordsQuery (returns the authoritative title prefix 上市/上櫃) on a periodic basis and correct the watchlist. The scrapers rely on the watchlist for filtering, not for market assignment; market comes from which file the ticker appears in.

#### CC-T14. Foreign-incorporated listings are excluded from both TWSE and TPEx archives
Foreign companies listed in Taiwan (F-shares, KY, N — e.g. Silergy 6415 incorporated in Cayman Islands) are listed on TWSE/TPEx but their monthly revenue appears in a SEPARATE report, not in C04003 or `O_YYYYMM.xls`. The C04003 title clarifies: "上市公司營業額…(**本國公司**)" — domestic companies only. These tickers get 12-month coverage via MOPS's per-ticker endpoint but no bulk backfill. Finding the foreign-company equivalent report is a TODO. Symptom to watch for: a watchlist ticker missing from both backfills; detect by diffing `list_watchlist_tickers()` against the covered-tickers set after a full run.

### Tooling

- `tools/tpex_probe_download.py` — XHR + download-link discovery, for rediscovering paths when TPEx redesigns.
- `tools/tpex_backfill.py` — one-shot runner with disk cache under `<data-dir>/_raw/tpex_xls/`.

### Verification

After a full TPEx run, confirm:
- Phison (8299) has one row per month from 2009-12 onward
- 2026-01 Phison revenue ~ NT$10.45B, YoY ~ +189% (a real flash-controller blowout)
- Row counts per ticker vary more than TWSE (more late IPOs on TPEx)

## Combined architecture: three-source blend

For complete watchlist coverage, the pipeline uses three sources with
overlapping-but-distinct time windows:

```
Freshness →                               History →
────────────────────────────────────────────────────────────
MOPS t146sb05_detail   | last 12 months, per-ticker, daily
TWSE C04003 archive    |         1999-03 → prior month, 上市 only
TPEx O_YYYYMM archive  |         2009-12 → prior month, 上櫃 only
```

The storage layer's content-hash upsert makes overlap free: when MOPS
and TWSE both report the same (ticker, month), the hash matches and the
row becomes a TOUCH_ONLY. Amendments surface as `amended=True`.

A ticker not yet in the bulk archives (e.g. just IPO'd) still gets
coverage from MOPS's 12-month window, and the bulk archives pick it up
from the next publication.

## Material-Information (t05st02) — SUPPLEMENT, not primary

MOPS's 重大訊息 / material-information announcement stream at
`/mops/api/t05st02` looks like a live signal at first glance (filings
like "公告本公司2026年3月合併營業額" appear there seconds after submission).
**It is elective, not the regulatory filing channel.**

Measured coverage (April 2026 publication window, 1st-11th):

| metric | count | % |
|---|---|---|
| total material-info announcements, all issuers, 11 days | 2,104 | 100% |
| revenue-flavored (營業額 / 營業收入 / 月份營收 / 自結 / 合併營收) | 94 | 4.5% |
| market-wide unique tickers that filed revenue material info | 75 | ~4% of all listings |
| **our 51-ticker watchlist that used this channel** | **1 (MediaTek)** | **2%** |

Peak activity is 1st–10th; 4th–6th (weekend) drops nearly to zero; the
10th explodes (421 announcements, 28 revenue) because it's the filing
deadline. Everyone files, but almost no one files via material info.

### When to still use t05st02

- **Early-warning for specific tickers** — a small subset of tickers
  (MediaTek in our watchlist, others market-wide) reliably post
  material info a few hours to a day BEFORE the formal filing hits
  t146sb05_detail. Running a lightweight t05st02 poll every 15-30 min
  during the window gives a head-start on those specific tickers.
- **Out-of-band amendment announcements** — a company may post
  material info explaining a restated prior-month figure BEFORE the
  amended row surfaces in t146sb05_detail.

### API shape

```
POST /mops/api/t05st02
body:   {"year": "115", "month": "04", "day": "10"}  # ROC year as string; month + day zero-padded
→ result.data: list of [announce_date, time, ticker, name, subject, {parameters, apiName}]
  Subjects for revenue filings look like:
    "公告本公司115年3月合併營業額"
    "公告本公司自行結算115年03月合併營收"
    "公告本公司115年3月份營業收入"
```

### Filter heuristic

```python
_REVENUE_KEYWORDS = ("營業額", "營業收入", "月份營收", "自結", "合併營收")
is_revenue_filing = any(k in subject for k in _REVENUE_KEYWORDS)
```

Catch with fuzzy-OR; don't try to be too strict — filers use
inconsistent phrasing. False positives are harmless because the row
gets cross-checked against `t146sb05_detail` anyway.

### Why the filing SHOWS in MOPS but NOT in material info

The official monthly-revenue filing goes through the structured form
`t05st10` (the one MOPS pushes issuers to use). It does NOT
automatically post to 重大訊息 — that's a separate, company-elected
channel used for items the filer wants to call extra attention to.
Most companies just file via `t05st10` and let the data surface
through `t146sb05_detail` (and the downstream TWSE/TPEx archives).

### Design implication

t05st02 is now documented as a **supplement** in the three-source
blend, not the primary live channel. The primary live source remains
`t146sb05_detail` polled per-ticker on a 30-minute cadence during the
1st-15th publication window. This is cheap (51 calls × ~500ms in a
warmed CDP context ≈ 25s/tick) and captures 100% of filings, not 2%.

## Minimal Fetch Template

```python
from playwright.sync_api import sync_playwright

def fetch_monthly_revenue(ticker: str) -> dict:
    """Returns the raw JSON result dict from t146sb05_detail."""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            # Warm the origin once per session (caller should batch, not re-warm per ticker)
            page.goto("https://mops.twse.com.tw/mops/#/", wait_until="domcontentloaded")
            resp = ctx.request.post(
                "https://mops.twse.com.tw/mops/api/t146sb05_detail",
                data={"company_id": ticker},
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://mops.twse.com.tw",
                    "Referer": "https://mops.twse.com.tw/mops/",
                },
            )
            if resp.status != 200:
                raise RuntimeError(f"MOPS returned {resp.status}")
            return resp.json()
        finally:
            page.close()
```

For production use: keep ONE browser context open for an entire scheduler tick; iterate tickers with 1-second gaps.

## Testing Strategy

- **Unit tests:** vendor real JSON fixtures captured today via `tools/mops_fetch_detail_2330.py`. Don't mock shape — MOPS will break if you guess.
- **Integration test:** full round-trip — scraper → parquet → API endpoint → deserialised dict. Use a tmp data_dir so real data isn't touched.
- **Smoke test (live, manual):** `python tools/mops_smoke_2330.py` prints top 5 rows of TSMC live. Run when you suspect MOPS changed something.

## When MOPS Changes (It Will)

Symptoms → actions:

| Symptom | Likely Cause | Action |
|---|---|---|
| All calls return HTTP 200 with HTML body | WAF returned the "SECURITY" bounce page | Verify browser context is warm; re-warm origin; check profile isn't corrupted |
| `resp.json()` raises JSONDecodeError | Endpoint removed | Re-run `tools/mops_explore.py`, rediscover |
| `code != 200` in response | API changed error codes | Check `message` field; adjust validation |
| Parsed rows all `None` | Column order changed or key renamed | Inspect one raw row vs. the fixture; update parser positional indices |
| YoY looks 100x wrong | They switched `%` → decimal or vice versa | Check one known row against their public page |

## Design Decisions (why we chose what we chose)

- **Per-ticker JSON over bulk HTML** — we have a small (51-ticker) watchlist; per-ticker calls are cleaner, parallelizable, and fail isolated. The old bulk endpoint's appeal was "1 call vs 1000"; for us it's "51 calls vs 24"; the simplicity wins.
- **CDP browser context over pure HTTP** — MOPS WAF. End of discussion; this is not a performance choice, it's the only path.
- **Parquet + raw JSON** — parquet for columnar analytics (frontend queries, dashboards); raw JSON for forensic audit. Duplicated storage is <1GB for 10-year history of 51 tickers.
- **Content-hash amendment detection** — MOPS does correct prior-month filings. Without hash tracking you'd silently overwrite history. Hash + history parquet preserves the amendment trail.
- **Taipei-time scheduler** — MOPS publishes monthly revenue by the 10th of the following month, local time. Scheduler runs in `Asia/Taipei` to match; jobs fire at 10:00 TPE daily.
- **Fly.io nrt region** — Tokyo is the closest region to MOPS (~30ms RTT). US-based scraping added 150ms+ per call and noticeably worsened WAF friction.

## Build Order (for a fresh implementation)

1. `mops_client_browser.py` (CDP launcher + health check) — copy as-is from existing file.
2. `mops_client.py` — Playwright JSON client with one persistent context, rate limit, retries.
3. Minimal discovery run (`tools/mops_explore.py`) to confirm endpoints still live.
4. `scrapers/monthly_revenue.py` — iterate watchlist, call `t146sb05_detail`, normalize, upsert.
5. `scrapers/company_master.py` — build `(ticker → market, sector)` cache from `KeywordsQuery`.
6. `storage.py`, `amendments.py`, `validation.py` — unchanged from HTML-era design; schema is compatible.
7. `health.py`, scheduler, API router, frontend tab — all downstream of parquet, no rewrite needed.
8. Tests: vendor JSON fixtures captured today, not hand-rolled.
