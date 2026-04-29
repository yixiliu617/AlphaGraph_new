# AlphaGraph — Architecture & Technical Design (v3)

**Status:** active. Supersedes v2 for Phase 2 architecture, universe schema, cron topology, and Pillar A design. v2 (`architecture_and_design_v2.md`) is preserved for Phase 1 detail (pre-Phase-2 extraction pipeline, audio subsystem, Taiwan revenue subsystem) — those subsystems are unchanged.
**Last updated:** 2026-04-29.

## What v3 covers (and what it doesn't)

| Topic | v3 (this doc) | v2 (legacy) |
|---|---|---|
| Phase 2 schema (auth, credentials, calendar, notes, universe) | ✅ | partial — § 14 only |
| Universe v2 schema (company / listing / thesis groups) | ✅ | not yet |
| Per-market cron topology (US/TW/HK/JP/KR/CN) | ✅ | US+TW only |
| Pre-IPO + dual-listing handling | ✅ | not yet |
| Pillar A architecture (chat with citations, alerts) | ✅ | not yet |
| OAuth integration (Google + Microsoft) | summary + reference | partial |
| Extraction pipeline + ports/adapters | reference v2 § 4 | ✅ canonical |
| Taiwan monthly-revenue subsystem | reference v2 § 12 | ✅ canonical |
| Audio capture / transcription subsystem | reference v2 § 11 | ✅ canonical |

If a subsystem is unchanged from Phase 1, this doc references v2 instead of duplicating. Always read v2 + v3 together for full coverage.

---

## 1. System role & objective (v2 → v3 evolution)

**Phase 1 (v1, v2):** Build a depth-first single-tenant research workbench for AI infra & semis. Hexagonal extraction, cache-first data, source-traced fundamentals.

**Phase 2 (v3, ACTIVE):** Multi-tenant foundation. Postgres + auth + per-user state + universe management. The architectural shift: *the platform is no longer "one analyst's research workbench" — it's a multi-pilot system with per-user universes, alerts, notes, and connected services.*

**Phase 3+ (planned):** Pillar A → Pillar B → Pillar C build-out. See `roadmap_v1.md`.

## 2. Universe schema (v2)

The most important architectural change in this doc. Replaces the legacy `public_universe` + `user_universe` (which lived on the old `Base`) with a **thesis-group-driven, multi-listing schema** on `Phase2Base`.

### 2.1 Why thesis groups, not indices

Pilots don't think in indices. They think in **bottleneck layers**:

```
compute → infra → hosting → energy → materials → software → consumer → industrial
   ↓        ↓        ↓         ↓          ↓           ↓          ↓          ↓
 NVDA    ANET    EQIX,CRWV   CEG,VST    Shin-Etsu   NOW,ADBE   Sony      ETN,VRT
```

We curate **thesis groups** (`ai_compute_design`, `ai_materials_critical_minerals`, `cn_ai_internet`, etc.) and let pilots subscribe to the layers they care about. Indices (SPY, SMH, Nikkei 225, KOSPI 200, TWSE 50) exist as auto-fetched **baseline** groups for benchmarking, not as the primary organizing unit.

Current state: **30 curated thesis groups + 5 auto-fetched indices.** Pilots typically subscribe to 4–8 groups (35–95 names of cognitive load).

### 2.2 Tables (Postgres, Phase2Base)

Migration: `backend/alembic/versions/0005_universe_v2_schema.py`. ORM: `backend/app/models/orm/universe_v2_orm.py`.

| Table | Purpose | Key |
|---|---|---|
| `company` | Analytical entity (TSMC, Alibaba, Tokyo Electron) | `company_id` slug |
| `listing` | Tradeable instrument (BABA, 9988.HK, 2330.TW) | `ticker` (yfinance form) |
| `universe_group` | Thesis group (`ai_compute_design`, `cn_ai_internet`, `index_smh`) | `group_id` |
| `universe_group_member` | many-to-many ticker × group + weight (0.0–1.0) | `(group_id, ticker)` |
| `pre_ipo_watch` | Private companies tracked as metadata only | `id` slug |
| `user_universe_group` | Per-user thesis-group subscriptions | `(user_id, group_id)` |
| `user_universe_ticker` | Per-user manual ticker adds | `(user_id, ticker)` |

### 2.3 Dual-listing handling

A company can have multiple listings (BABA + 9988.HK, TSM + 2330.TW). Both link to the SAME `company_id`. This serves both audiences correctly:

- HK-tape analyst sees `9988.HK` in HKD with HK trading hours
- US-tape analyst sees `BABA` in USD with US trading hours
- Fundamentals attached at company level, fetched once

Resolution: `DUAL_LISTING_OVERRIDES` map in `backend/scripts/seed_universe.py` lists 31 known dual pairs. New ones discovered at auto-promotion time.

### 2.4 Pre-IPO tracking

Private companies that don't have a tradeable ticker yet (OpenAI, Anthropic, xAI, Zhipu pre-listing, Cerebras pending S-1) live in `pre_ipo_watch` with metadata only. When they IPO, status flips and a `listing` row is created. Pattern:

```
pre_ipo  →  IPO event  →  recent_ipo (24mo grace)  →  active
                          ↑
                          ticker assigned, listing row created, prices backfill
```

This lets analysts track IPO calendar, valuation marks, S-1 filings, lock-up expirations as research events.

### 2.5 Auto-promotion flow

When a user adds a ticker not in the broader universe:

```
POST /api/v1/me/universe/add  { "ticker": "9988.HK" }
  ↓
1. listing exists? → just INSERT into user_universe_ticker
2. yfinance.Ticker(ticker).info → verify, fetch metadata
3. UPSERT company + listing (status='active'), INSERT user_universe_ticker
4. Background job: backfill 10y daily + 60d intraday + (if US) SEC EDGAR XBRL probe
5. Return { backfill: 'in_progress', est: '~2 min' }
```

API endpoint not yet built (Stream 1 day 6).

### 2.6 Effective-universe query

The hot-path query for any feature needing "this user's tickers":

```sql
SELECT DISTINCT l.ticker, l.company_id, c.display_name, c.hq_country
FROM listing l
JOIN company c USING (company_id)
LEFT JOIN universe_group_member ugm USING (ticker)
LEFT JOIN user_universe_group uug ON uug.group_id = ugm.group_id AND uug.user_id = $1
LEFT JOIN user_universe_ticker uut ON uut.ticker  = l.ticker AND uut.user_id = $1
WHERE l.status IN ('active', 'recent_ipo')
  AND (uug.user_id IS NOT NULL OR uut.user_id IS NOT NULL);
```

Will be wrapped in a Postgres view `user_effective_universe(user_id)` once the API endpoints are built.

## 3. Data depth tiers

Critical concept for cost-of-build analysis. Different data has wildly different per-ticker effort:

| Tier | Content | Per-ticker effort | Source |
|---|---|---|---|
| **T1** Deep extraction | IR PDFs (transcripts, presentations, press releases) + forward guidance + reconciliation | **Days** (bespoke) | Company IR sites |
| **T2** Standardized fundamentals | Quarterly IS/BS/CF (XBRL primitives) | **Minutes** (automated) | SEC EDGAR (US), TWSE/MOPS (TW), TDnet (JP), DART (KR) |
| **T3** Prices only | Daily OHLCV + 60d intraday 15m + adj close | **Seconds** | yfinance + exchange feeds |

Current state:
- T1: 8 companies (TSMC, UMC, MediaTek deep; AMD partial; foundation for others)
- T2: SEC EDGAR plumbing built (`edgar-topline-extraction` skill); not yet run universe-wide
- T3: **387 unique tickers** backfilled (10y daily + 60d intraday) as of 2026-04-29

T1 and T2 grow demand-driven (a pilot asks → next deep extractor). T3 grows automatically with the universe (a new listing row → cron fetches prices).

## 4. Cron topology (per-market scheduler)

`backend/app/services/prices/scheduler.py` registers 10 jobs:

| Job ID | Schedule (TPE) | What runs | Source for tickers |
|---|---|---|---|
| `prices.us_daily` | 07:00 daily | yfinance daily OHLCV for US tickers | listing WHERE exchange IN (NYSE, NASDAQ) |
| `prices.taiwan_daily` | 14:30 daily | yfinance .TW daily | listing WHERE ticker LIKE %.TW or %.TWO |
| `prices.japan_daily` | 15:30 daily | yfinance .T daily | listing WHERE ticker LIKE %.T |
| `prices.korea_daily` | 16:00 daily | yfinance .KS/.KQ daily | listing WHERE ticker LIKE %.KS or %.KQ |
| `prices.china_daily` | 16:30 daily | yfinance .SS/.SZ daily | listing WHERE ticker LIKE %.SS or %.SZ |
| `prices.hk_daily` | 17:00 daily | yfinance .HK daily | listing WHERE ticker LIKE %.HK |
| `prices.us_intraday_15m` | every :00/:15/:30/:45 during 21:00–04:59 TPE | 15m bars rolling 60d | same as us_daily |
| `prices.taiwan_intraday_15m` | every :00/:15/:30/:45 during 09:00–13:59 TPE | 15m bars rolling 60d | same as taiwan_daily |
| `prices.taiwan_twse_patch` | 15:00 daily | TWSE-direct overwrite of last 30 days; gap fill | same as taiwan_daily |
| `prices.health_check` | hourly @ :43 | Read heartbeats; WARN on stale | — |

Heartbeat table: `taiwan_scraper_heartbeat` SQLite (cross-domain — name is historical). All `prices.*` jobs write rows.

Intraday for HK/JP/KR/CN: not in v3. Doubles cron load and pilots can wait until daily-only proves insufficient. Add when asked.

## 5. Phase 2 service-integration architecture

OAuth-backed third-party service integrations (Calendar, OneNote, Outlook Mail, OneDrive, Google Docs/Drive, Gmail). Pattern documented in:

- Skill: `.claude/skills/oauth-service-integration/SKILL.md`
- Scope registry: `backend/app/services/auth/oauth_scopes.py`
- Adapter pattern: `backend/app/services/integrations/<provider>/<service>.py`
- Sync runner: `backend/app/services/integrations/sync_runner.py`

Adding a service is one entry in `SERVICES` + one adapter file. Routers and sync runner pick services up data-driven. 15 captured edge cases (refresh-token loss, OIDC iss mismatch on Microsoft `common`, OneNote personal-account quirks, etc.) — see the skill for the full catalogue.

Currently working: `google.calendar`, `microsoft.calendar`, `microsoft.onenote`. Built but unfilled: `microsoft.outlook_mail`, `microsoft.onedrive`, `google.gmail`, `google.docs`.

## 6. Pillar A architecture (chat with citations)

**Status: not yet built.** Design captured here so the build can start clean.

### 6.1 Chat surface design

```
User question  →  intent classifier  →  tool-use loop with mandatory citation

  Tool 1: query_fundamentals(ticker, period)        → returns rows + source_url(s)
  Tool 2: query_guidance(ticker, period)            → returns guidance + source_url(s)
  Tool 3: query_prices(ticker, range)               → returns OHLCV + source='yfinance'
  Tool 4: search_filings(ticker, query)             → returns relevant chunks + source_url + page
  Tool 5: list_user_universe(user_id)               → returns user's tickers
  Tool 6: cross_company_compare(metric, tickers)    → returns aligned values + source_url(s)
```

Every tool returns a **`sources` array** alongside its data. The chat assembly stage REQUIRES at least one source per numeric claim or named fact. If the LLM produces an answer without a source it's allowed to attach, the answer is rewritten to either:
- include a `[Source: ...]` pill, OR
- say "I don't have a sourced answer for X."

This is the architectural commitment that operationalizes user request #8 (zero hallucination).

### 6.2 RAG store

For PDF-derived content (transcripts, press releases, presentations), Postgres `pgvector` extension. One vector per document chunk (~800 tokens, 200-token overlap), with metadata `{ticker, period, doc_type, source_url, page}`. Retrieval is hybrid: BM25 (Postgres FTS) + cosine (pgvector), weighted blend. ~50ms p95 budget.

For numeric data (parquet-resident), no vector — direct SQL over the silver/gold layer.

### 6.3 Citation rendering

UI: inline pill `[$48.2B Q2 rev — TSMC 2Q26 PR, p.3]` clickable to open the PDF at the cited page. On hover: full source URL + extracted text snippet.

Scale of trust signal: green pill = file we have on disk, hash-verified. Yellow pill = remote URL (web fetch in flight or cached). Red pill = "unsourced" — signals to user that the claim has no verification.

### 6.4 LLM choice

Default Anthropic Claude (Opus for chat / Sonnet for tools / Haiku for classification). Abstracted via `LLMProvider` interface so we can swap to OpenAI / Gemini for cost or capability without app-side changes. Anthropic-first because of:
- Strong tool-use ergonomics
- Prompt caching support (planned for week 4 cost discipline)
- Per-message thinking budget for hard reasoning tasks

### 6.5 Alerts (request #4)

Postgres-backed rule definitions. Natural-language input → tool-translated to a structured rule (price >X, GPM guidance cut, news mention). Cron evaluator runs every 5 min during market hours, sends email + in-app notification. Email via Postmark or Resend (decision PD pending).

Schema:
```sql
CREATE TABLE user_alert (
  id            UUID PRIMARY KEY,
  user_id       UUID REFERENCES app_user(id),
  rule_natural  TEXT,            -- as the user typed it
  rule_structured JSONB,         -- {kind: 'price_above', ticker: 'NVDA', threshold: 200}
  enabled       BOOLEAN DEFAULT true,
  last_fired_at TIMESTAMPTZ
);
```

Already in place from migration `0001_phase2_user_alert_heartbeat.py`.

### 6.6 Notes search (request #7)

Postgres FTS over the union of:
- `meeting_notes` (legacy AlphaGraph notes)
- `user_note` (synced OneNote, Phase 2)

GIN tsvector index on each. Cross-source ranking with BM25 score blending. Team sharing (extending `user_note`) deferred to Pillar B.

### 6.7 "Bring your own filing" extraction (subset of request #3)

Endpoint: `POST /api/v1/me/extract` with `{ticker, source_url, doc_type}`. The extraction-engine pipeline already supports per-doc-type extractors; this exposes it as a per-user endpoint. Output stored in user-scoped silver tables; the chat tool layer surfaces it the next session.

## 7. Deployment topology (target — not current)

Current: **localhost only** (`localhost:3001` frontend + `localhost:8000` backend + Docker Postgres).

Target for first pilot URL (week 1 of Pillar A):

| Layer | Host | Why |
|---|---|---|
| Frontend (Next.js) | Vercel | Best-in-class Next deploys, free tier covers pilot scale |
| Backend (FastAPI) | Render or Railway | Push-to-deploy, persistent disk for parquets |
| Postgres | Neon | Serverless Postgres with branching; pgvector available |
| Background workers | Render worker dyno | Same image as web, different process type |
| Static parquets | S3 with CloudFront edge cache | Or move on-disk parquets into Postgres if cost-favourable |
| Logs / metrics | Render built-in + Sentry for error tracking | |

Long-term (Pillar B+): AWS (ECS for FastAPI, RDS for Postgres, S3 for parquets, MediaConvert for audio). Defer until web has paying users.

## 8. What's already built (Phase 2 status snapshot)

| Surface | Built | Notes |
|---|---|---|
| Postgres provisioned (Docker dev) | ✅ | 5 migrations applied (0001 → 0005) |
| Phase2Base + sessionmaker | ✅ | Separate from legacy Base; both can coexist |
| Auth (Google + Microsoft OAuth) | ✅ | `/auth/{provider}/login`, `/me`, `/logout`. JWT cookie. |
| Service connections (Calendar, OneNote) | ✅ | Adapter pattern; auto-discovery; refresh-token rotation tested |
| Outlook Mail / Drive / Docs adapters | scaffolded | `oauth_scopes.py` registered; adapter not yet built |
| Universe v2 schema | ✅ | 7 tables; 363 companies / 394 listings / 31 groups / 28 pre-IPO |
| Universe seed CSV + loader | ✅ | Idempotent UPSERT; dual-listing resolution |
| Index baseline fetchers (SMH/SPX/TWSE/Nikkei/KOSPI) | scaffolded | NotImplementedError placeholders; full impl in week 1 day 3 |
| Per-market cron jobs (US/TW/HK/JP/KR/CN daily) | ✅ | 10 jobs registered; reads from `listing` table |
| Universe management API (`/me/universe/*`) | ⏳ | Stream 1 day 6 |
| Universe management UI | ⏳ | Stream 1 day 7 |
| Chat with citations | ⏳ | Pillar A week 2 |
| Alerts | ⏳ | Pillar A week 1 (basic) |
| Notes search | ⏳ | Pillar A week 3 |
| Cross-company guidance dashboard | ⏳ | Pillar A week 3 |

## 9. Carry-overs from v2 still load-bearing

These v2 sections remain fully canonical — **read v2 alongside this doc**:

- v2 § 1 — Core tech stack (Postgres + DuckDB + parquet + FastAPI + Next.js + Anthropic)
- v2 § 2 — Hexagonal architecture rules (no concrete adapter imports in business logic)
- v2 § 4 — Extraction pipeline (single-file modules in `scripts/extractors/`)
- v2 § 8 — API contract management (X-API-Version header, generated TS types)
- v2 § 9 — Security & multi-tenancy (Phase 2 introduced the tenant boundary; this doc § 2 is the implementation)
- v2 § 11 — Audio capture / transcription subsystem (used in Pillar B)
- v2 § 12 — Taiwan monthly-revenue subsystem (unchanged)
- v2 § 13 — Phase 1 implementation status (frozen — done)
- v2 § 14 — Hermes-inspired migration roadmap (most items absorbed into roadmap_v1.md; some cancelled — see roadmap)

## 10. References

- Active rolling plan: `roadmap_v1.md`
- Product positioning + ICP: `product_design_v2.md`
- Universe seed: `backend/data/universe/broad_universe_seed_v1.csv` (+ addendum, + pre-IPO JSON)
- Migration sketch: `backend/data/universe/MIGRATION_SKETCH.md`
- OAuth integration skill: `.claude/skills/oauth-service-integration/SKILL.md`
