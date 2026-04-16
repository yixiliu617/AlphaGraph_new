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
