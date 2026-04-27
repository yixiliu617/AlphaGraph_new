# AlphaGraph — Claude Code Instructions

## ⚡ Current focus (2026-04-27)

**We are between Phase 1 (DONE — perf baseline) and Phase 2 (storage refactor + auth foundation).** Two pre-Phase-2 warmups are starting this week:

- **W1**: migrate 17 existing skills to add `version`, `last_validated_at`, `conditions`, `prerequisites`, `tags` frontmatter (Hermes-inspired skill metadata).
- **W2**: lift Taiwan + social schedulers to a declarative `backend/data/cron_jobs.json` (Hermes-inspired cron pattern; APScheduler stays as runner).

**For full status + locked decisions + open decisions + phase plan → `architecture_and_design_v2.md` § 14** (Hermes-Inspired Migration Roadmap, comprehensive).
**For active focus + next 3 actions → memory file `project_alphagraph_q3_roadmap.md`** (auto-loaded).

## Project Overview

AlphaGraph is an institutional AI-driven financial research platform. See `architecture_and_design_v2.md` for the full design reference (§14 = Q3-Q4 roadmap) and `memory/project_alphagraph_q3_roadmap.md` for current focus.

---

## Available Skills (Slash Commands)

### `/new-extractor [plain-English description]`

**Use this whenever a user asks to create a new data fragment extraction module.**

This skill runs the full interactive workflow:
1. Reads the existing codebase and system context automatically
2. Asks clarifying questions about schema, approach, fragment granularity, and graph edges
3. Drafts a complete module plan for user review
4. Waits for explicit user confirmation before writing any code
5. Writes the extractor file, updates the runner, updates docs and memory
6. Verifies all linkages and provides testing guidance

**Example invocations:**
```
/new-extractor extract earnings call key statements, guidance, and analyst Q&A highlights
/new-extractor extract macro risk factors mentioned in each report with impact ratings
/new-extractor extract management commentary on each business segment with sentiment
```

---

## Core Architecture Rules

- **Backend:** Hexagonal architecture — business logic never imports concrete adapters directly. Always use port interfaces (`interfaces/`).
- **Frontend:** Container/View pattern — View components are 100% dumb. Never import API clients or stores in View files.
- **Extraction modules:** Each module is a single file in `scripts/extractors/`. Editing one module cannot affect another. All modules share `ExtractionContext` and `Pipeline` from `app/services/extraction_engine/pipeline.py`.
- **New extraction module checklist:** Write extractor file → update `run_parallel_extraction.py` → update `architecture_and_design_v2.md` → update `memory/project_alphagraph.md`.

## Print Statement Rule

No Unicode characters in print statements anywhere in `scripts/` or `services/`. Use ASCII only (Windows cp950 encoding). Use `->` not `->`, `>>` not `>>`.

## Backend Launch (uvicorn workers + reload)

**Dev (single worker, hot-reload):**
```
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```
Use this when iterating on the API. `--reload` is incompatible with `--workers`.

**Prod / load-test (multi-worker, no reload):**
```
uvicorn backend.main:app --workers 4 --host 0.0.0.0 --port 8000
```
4 workers ≈ 4× throughput on a 4-core box. Each worker has its own parquet
LRU cache (no IPC needed; mtime-keyed reads stay coherent across workers).

**Cache visibility:** `GET /api/v1/admin/cache` returns hit rate + LRU stats per worker. `GET /api/v1/admin/runtime` returns the worker PID so you can confirm load is spreading.

**Storage layer:** SQLite is now in WAL mode (set on every engine connect via the `_set_sqlite_pragmas` listener). Concurrent reads no longer block on writes — one in-flight heartbeat upsert won't stall a dashboard query.

## External-Data Cache-First Rule (project-wide)

**Every external data source must be persisted on first fetch. Downstream pipelines read from cache, never re-fetch unless the source has changed.**

Applies to: SEC EDGAR (filings, XBRL), Taiwan MOPS (monthly revenue, material info), TWSE/TPEx open data, news scrapers (Google News, Reddit), social (X/Twitter), audio transcription, paid LLM responses, anything pulled from a network.

The architecture is bronze → silver → gold:

| Layer | Content | Invalidation |
|---|---|---|
| **Bronze (raw)** | Bytes-identical source artifact (XBRL XML, scraped HTML, raw transcript audio, paid LLM JSON response). Optional but strongly preferred for paid / forensic / disaster-recovery cases. | Never — keyed by source-immutable ID (accession_no, URL+timestamp, content hash). |
| **Silver (parsed)** | Structured form ready for analysis (parquet of facts, normalized JSON). | Only when bronze changes (i.e. new source ID). |
| **Gold (curated)** | Business-friendly artifacts the app/agents read (topline parquets, calculated metrics). | When silver changes OR when our analysis code changes. |

**Required for every new fetcher**:
1. Persist to disk before any downstream processing or DB write that could crash. (See `feedback_save_paid_ai_results_first.md` — this rule was learned the expensive way after losing paid Gemini outputs to a downstream bug.)
2. Cache key must change when source changes, never when our code changes.
3. The fetch module owns NO analysis logic. The analysis module owns NO network calls.

**Reference implementations**:
- `backend/app/services/data_agent/xbrl_cache.py` — silver layer for SEC XBRL (per-filing parquets + per-ticker stitched outputs, accession-keyed).
- `backend/app/services/taiwan/storage.py` + `_raw/` directories — bronze (raw JSON dumps from MOPS API) + silver (parquet) for Taiwan monthly revenue.
- `backend/data/market_data/news/` — silver layer for news feeds.

Full architecture rationale + checklist for new fetchers: `architecture_and_design_v2.md` § "Cache-first data layer".

## Source-Side Data Issues (project-wide)

When the data-quality framework's `period_continuity` check flags a missing period, **always investigate source-side mislabel before assuming an extractor bug**. Procedure:

1. Open the cached PDF, read its title page → does it actually claim the period we think it does?
2. If no, hash-compare with adjacent quarters' same file-type → if byte-identical with another quarter, the company uploaded the same file at two URLs.
3. Quarantine the bad file (`.WRONG_CONTENT_AT_SOURCE_actually_{period}` suffix).
4. Source fallback data from the company's sibling PDFs for that quarter (Presentation slide deck typically carries guidance + headline financials).
5. Append an entry to the company's `_source_issues.json` (e.g. `backend/data/financials/raw/2454.TW/_source_issues.json`) — the `/source-issues` endpoint surfaces these so the frontend renders a banner explaining the gap to users.

Full procedure + example: `.claude/skills/source-mislabel-recovery/SKILL.md`. Reference implementation: MediaTek 3Q23 transcript (mislabeled by MediaTek's CDN; guidance fallback-sourced from the 3Q23 Presentation slide deck).

## Data Quality Framework

`backend/app/services/data_quality/` is a modular check framework that runs against every silver / guidance / transcript dataset we onboard. Run via:
- **CLI:** `PYTHONIOENCODING=utf-8 PYTHONPATH=. python -m backend.app.services.data_quality.runner` (all datasets) or pass dataset keys (e.g. `mediatek.guidance umc.facts`)
- **API:** `GET /api/v1/admin/data-quality` (all) or `?dataset=umc.facts` (one)

When onboarding a new dataset, add a section to `backend/app/services/data_quality/registry.py` declaring which checks should run. Available primitives: `period_continuity`, `period_label_format`, `identity_check`, `range_check`, `sign_consistency`, `source_period_match` (catches mislabeled source PDFs), `share_sum`, `row_count_min`, `duplicate_key`. Add new primitives to `checks.py` when an existing one doesn't fit.

**The period_continuity check is mandatory for every time-series dataset.** It catches: (a) extractor regex bugs that drop occasional periods, (b) source-side mislabeled files (e.g. MediaTek's 3Q23 transcript URL serving the 3Q24 PDF), (c) silent partial backfills.

## IR Website Knowledge Base

`docs/ir_websites_knowledge_base.md` is the append-only catalog of every company's investor-relations site we've verified. Each section covers: IR URLs, file-type inventory, extraction strategy, layout quirks (with workarounds), what's captured vs not, and a recovery playbook for when the site redesigns. **Always update the relevant section's Change log** when adding/modifying an extractor for that company. Add a new section for any newly recon'd company.

## Guidance Tab Pattern (project-wide)

Every company's **Guidance** sub-tab leads with a **Forward Guidance card** — the latest report's view of the next period(s) for every guided metric. Below that, per-metric historical guidance-vs-actual tables (reverse-chronological).

The forward card answers "what does management expect *now*?" without scrolling. Historical tables answer "how good is management at predicting?".

Implementation contract:
- API endpoint sorts rows newest-first → `rows[0].issued_in_period` is the latest issuing report.
- Card filter: `rows.filter(r => r.issued_in_period === latestIssued)`, de-dup to one row per metric.
- Card displays: label, target period chip, numeric value (range OR point) OR em-dash for verbal-only, plus the verbatim verbal quote.
- Skip the card entirely if the latest report has no structured forward guidance (e.g. fabless designers).

Reference: TSMC (canonical), UMC (mixed numeric+verbal). Full spec + code template: `.claude/skills/guidance-tab-pattern/SKILL.md`.

## Time-Axis Sort Convention (project-wide)

**Tables** that show metrics across time: most recent period on the **LEFT** (or **TOP** when time is on rows). Applies to financials wide tables, segment shares, capacity grids, balance sheet pivots, guidance vs actual lists, and every coverage / quarters table in the dashboard.

**Charts** that plot metrics across time: oldest on the **LEFT** of the X-axis (universal chart convention; never invert).

Concretely: backend `/financials/wide`, `/segments`, `/cashflow`, `/balance-sheet`, `/annual`, `/capacity`, `/guidance` endpoints return `periods` newest-first. Frontend tables iterate `data.periods` as-is. Frontend charts reverse with `[...data.periods].reverse()` and a comment explaining why. For mixed FY+quarterly period labels (e.g. UMC's annual capex guidance interleaved with quarterly margin guidance), use the `_period_sort_key` helper that converts `'4Q25'` and `'FY26'` into `(year, q_or_5)` tuples — naive lexicographic sort over period strings clusters all `4Q*` together regardless of year, which is wrong.

Full rationale, code patterns, and edge cases: `.claude/skills/time-axis-sort-convention/SKILL.md`.
