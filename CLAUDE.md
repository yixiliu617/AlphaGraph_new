# AlphaGraph — Claude Code Instructions

## Project Overview

AlphaGraph is an institutional AI-driven financial research platform. See `architecture_and_design_v2.md` for the full design reference and `memory/project_alphagraph.md` for current state.

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

## Time-Axis Sort Convention (project-wide)

**Tables** that show metrics across time: most recent period on the **LEFT** (or **TOP** when time is on rows). Applies to financials wide tables, segment shares, capacity grids, balance sheet pivots, guidance vs actual lists, and every coverage / quarters table in the dashboard.

**Charts** that plot metrics across time: oldest on the **LEFT** of the X-axis (universal chart convention; never invert).

Concretely: backend `/financials/wide`, `/segments`, `/cashflow`, `/balance-sheet`, `/annual`, `/capacity`, `/guidance` endpoints return `periods` newest-first. Frontend tables iterate `data.periods` as-is. Frontend charts reverse with `[...data.periods].reverse()` and a comment explaining why. For mixed FY+quarterly period labels (e.g. UMC's annual capex guidance interleaved with quarterly margin guidance), use the `_period_sort_key` helper that converts `'4Q25'` and `'FY26'` into `(year, q_or_5)` tuples — naive lexicographic sort over period strings clusters all `4Q*` together regardless of year, which is wrong.

Full rationale, code patterns, and edge cases: `.claude/skills/time-axis-sort-convention/SKILL.md`.
