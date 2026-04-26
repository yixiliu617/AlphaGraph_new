# AlphaGraph — Architecture & Technical Design (v2)

> **Revision history**
> - v1 — Initial design
> - v2 — Refactor pass: extraction pipeline steps, executor registry, Container/View split, domain-split API clients, runtime contract guards, API version header.
> - 2026-04-26 — Section 13 added: implementation status, performance baseline (Phase 1 perf shipped), and concrete Phase 2-4 scaling roadmap toward 2000-ticker / multi-user-agentic operation.

> **For agents reading this cold:** §1-12 describe the *aspirational* full architecture. §13 describes the *current actual state* of the deployed system as of 2026-04-26, what's live, what's stubbed, and the planned path from here. When the two diverge, §13 is authoritative for "what works today"; §1-12 is authoritative for "where we're heading."

---

## System Role & Objective

AlphaGraph is an institutional-grade, AI-driven Financial Research Platform for long/short portfolio managers.
Core requirements: 500+ concurrent users, strict frontend/backend independence, zero vendor lock-in, and every module changeable in isolation without cascading breakage.

---

## 1. Core Tech Stack & Polyglot Persistence

| Concern | Technology |
|---|---|
| Backend framework | Python · FastAPI · Uvicorn |
| Structured quant data (Layer 1) | DuckDB querying local `.parquet` files |
| Semantic / qual data (Layer 1) | Pinecone (vector DB) |
| Relational & state data | PostgreSQL (SQLite in dev) via SQLAlchemy |
| Graph / topology | Neo4j |
| LLM | Google Gemini (via swappable adapter) |
| Validation | Pydantic V2 (Rust core) |
| Frontend framework | Next.js 15 · React 19 |
| Styling | Tailwind CSS |
| State management | Zustand (per-feature stores) |
| Charts | Recharts |
| Graph visualization | React Flow |

---

## 2. Modularity & Decoupling Principles

### 2.1 Backend — Hexagonal Architecture (Ports & Adapters)

**Rule:** Business logic (`services/`, `agents/`) must **never** import concrete implementations directly.

**Ports** (`interfaces/`) are Abstract Base Classes that define the contract.
**Adapters** (`adapters/`) are concrete implementations that fulfil the contract.
**Dependency Injection** (`api/dependencies.py`) wires the correct adapter at runtime via FastAPI `Depends`.

Swapping any vendor (Gemini → Anthropic, Pinecone → Weaviate, Neo4j → ArangoDB) requires:
1. A new adapter file implementing the relevant port.
2. One line change in `dependencies.py`.
Zero changes to services, agents, or routers.

| Port (interface) | Adapters | What it abstracts |
|---|---|---|
| `LLMProvider` | `GeminiAdapter` (extraction + embeddings), `AnthropicAdapter` (Engine default), `OpenAIAdapter` | Text generation, structured output, embeddings, tool-use |
| `DBRepository` | `PostgresAdapter` | Relational state (fragments, recipes, ledger) |
| `QuantRepository` | `DuckDBAdapter` | OLAP queries over Parquet |
| `VectorRepository` | `PineconeAdapter` | Semantic search |
| `GraphRepository` | `Neo4jAdapter` | Topology relationships & paths |

### 2.2 Frontend — Container / View Pattern

**Rule:** UI components are 100% dumb/presentational — they cannot import API clients or Zustand stores.

**Layers:**

| Layer | Responsibility | May import |
|---|---|---|
| `page.tsx` (route entry) | Renders the container, nothing else | Container only |
| `*Container.tsx` (smart) | API calls, store reads/writes, error handling | API clients, stores |
| `*View.tsx` (dumb) | Layout & rendering only | Components, local types |
| `components/domain/` | Reusable dumb components | React props only |
| `lib/mappers/` | Transform backend JSON → component props | Types only |

Changing how data is fetched never touches the View. Changing the UI never touches the Container.

### 2.3 API Contract Stability

The frontend and backend share a versioned contract boundary:

- **Backend** injects `X-API-Version: <version>` on every HTTP response (configured in `main.py`).
- **Frontend** `lib/api/base.ts` reads this header and logs a `console.warn` if it doesn't match `EXPECTED_API_VERSION`, surfacing drift during development.
- **OpenAPI codegen** (`npm run generate:api-types`) generates `src/types/api.generated.ts` from the live backend spec. Any backend schema change that breaks the TypeScript contract is caught at compile time, not at runtime.

---

## 3. Data Model — The Hybrid Firewall Strategy

### 3.1 Pydantic V2 (The Firewall)

Every piece of data entering the system must pass through a Pydantic V2 model.
Its Rust-powered validation engine provides the throughput needed during high-volatility periods.

Core domain models:

| Model | Purpose |
|---|---|
| `DataFragment` | The ironclad output contract for all extracted data |
| `ExtractionRecipe` | Declarative extraction logic (versioned, immutable) |
| `ThesisLedger` | Active long/short positions and their catalysts |
| `Catalyst` | An event that confirms, triggers, or breaks a thesis position |

### 3.2 JSON Schema (Dynamic Logic)

User-defined `ExtractionRecipe` schemas are stored as JSON Schema objects.
They are passed to the LLM to enforce structured output, then the result is validated by Pydantic — creating a two-stage validation firewall:

```
LLM output → JSON Schema validation → Pydantic V2 DataFragment
```

---

## 4. The Extraction Pipeline

The extraction system has **two layers** that work together:

- **Infrastructure layer** (`app/services/extraction_engine/`) — shared `ExtractionContext`, `Pipeline` runner, and reusable step functions. Never changes when modules are added.
- **Module layer** (`scripts/extractors/`) — one file per extraction module, each declaring its own pipeline as a list of step functions. Modules are fully isolated from each other.

Extraction logic is declared in `ExtractionRecipe` objects stored in PostgreSQL. Changing what a module extracts requires editing a recipe record; changing how steps execute requires editing only that module's step functions.

### 4.1 Two-Layer Architecture

```
scripts/extractors/                         app/services/extraction_engine/
─────────────────────────────────           ────────────────────────────────────
causal_extractor.py                         pipeline.py
  CAUSAL_PIPELINE = Pipeline([               ExtractionContext  ← shared state
    step_load_document,      ◄──────────      Pipeline          ← step runner
    _step_chunk_pages,
    _step_call_text_llm,                    steps/shared_steps.py
    _step_build_fragments,                    step_load_document    ◄── used by all
    step_store_fragments,    ◄──────────      step_store_fragments  ◄── used by all
    _step_fanout_to_graph,
  ])

chart_extractor.py
  CHART_PIPELINE = Pipeline([
    step_load_document,      ◄──────────
    _step_detect_charts,
    _step_render_images,
    _step_call_vision_llm,
    _step_save_images,
    _step_build_fragments,
    step_store_fragments,    ◄──────────
  ])

company_intel_extractor.py
  COMPANY_INTEL_PIPELINE = Pipeline([
    step_load_document,      ◄──────────
    _step_identify_companies,    # first 3 pages → primary + peer list
    _step_extract_per_company,   # full-doc LLM per company
    _step_build_company_fragments,
    step_store_fragments,    ◄──────────
    _step_fanout_to_graph,       # HAS_SEGMENT / HAS_PRODUCT / COMPARED_TO
  ])

relationship_extractor.py
  RELATIONSHIP_PIPELINE = Pipeline([
    step_load_document,      ◄──────────
    _step_chunk_pages,
    _step_call_relationship_llm,
    _step_build_rel_fragments,
    step_store_fragments,    ◄──────────
    _step_fanout_to_graph,       # SUPPLIES_TO / CUSTOMER_OF / COMPETES_WITH / PARTNERS_WITH / MENTIONED_WITH
  ])
```

### 4.2 ExtractionContext — Shared State Object

Every pipeline step receives a single `ExtractionContext` dataclass. Steps read from it and write to it — no data is passed as arguments between steps.

| Field | Type | Set by |
|---|---|---|
| `pdf_path`, `doc_meta`, `recipe` | inputs | caller before pipeline starts |
| `db`, `llm`, `vector_db`, `graph_db` | adapters | caller (each thread owns its own set) |
| `gemini_api_key`, `output_dir` | module config | caller (optional, module-specific) |
| `pages`, `content_page_nums` | state | `step_load_document` |
| `chunks` | state | `_step_chunk_pages` (causal, relationship) |
| `chart_page_nums`, `chart_images`, `chart_files` | state | chart steps |
| `llm_outputs` | state | LLM call steps (tagged with `_location` / `_page_num` / `_ticker`) |
| `identified_entities` | state | `_step_identify_companies` — list of `{ticker, name, is_primary}` dicts |
| `fragments` | state | build steps → consumed by `step_store_fragments` |

### 4.3 Pipeline Definitions

**Module 1 — Causal Relationship Extractor (6 steps)**
```
step_load_document            # shared — PDF -> content pages, disclosure filter applied
_step_chunk_pages             # content pages -> 3-page text chunks
_step_call_text_llm           # each chunk -> causal chains via LLM; skips empty chunks
_step_build_causal_fragments  # LLM output + doc_meta -> DataFragments with full provenance
step_store_fragments          # shared — save each fragment to DB + embed to Pinecone
_step_fanout_to_graph         # (cause_entity)-[:CAUSES]->(effect_entity) edges to Neo4j
```

**Module 2 — Chart / Exhibit Extractor (7 steps)**
```
step_load_document            # shared — PDF -> content pages, disclosure filter applied
_step_detect_charts           # heuristic scan (exhibit labels, axis terms, embedded images)
_step_render_images           # each chart page -> PNG bytes at 200 DPI
_step_call_vision_llm         # Gemini Vision: image + page text -> structured chart data
_step_save_images             # write PNGs as {chart_title}_{broker}_{date}.png
_step_build_chart_fragments   # LLM output + doc_meta -> DataFragments with full provenance
step_store_fragments          # shared — save each fragment to DB + embed to Pinecone
```

**Module 3 — Company Business Intelligence Extractor (6 steps)**
```
step_load_document            # shared — PDF -> content pages, disclosure filter applied
_step_identify_companies      # LLM on first 3 pages -> primary + peer list -> ctx.identified_entities
_step_extract_per_company     # full-document LLM call per company; peer skipped if no segment/metric
_step_build_company_fragments # one DataFragment per company; peer records compared_to_primary
step_store_fragments          # shared — save each fragment to DB + embed to Pinecone
_step_fanout_to_graph         # (Company)-[:HAS_SEGMENT]->(Segment)
                              # (Segment)-[:HAS_PRODUCT]->(Product)
                              # (Peer)-[:COMPARED_TO]->(Primary)
```

**Module 4 — Business Relationship Extractor (6 steps)**
```
step_load_document            # shared — PDF -> content pages, disclosure filter applied
_step_chunk_pages             # content pages -> 3-page text chunks
_step_call_relationship_llm   # each chunk -> relationship list via LLM; skips empty chunks
_step_build_rel_fragments     # LLM output + doc_meta -> DataFragments with full provenance
step_store_fragments          # shared — save each fragment to DB + embed to Pinecone
_step_fanout_to_graph         # typed directed edges to Neo4j:
                              #   SUPPLIES_TO / CUSTOMER_OF / COMPETES_WITH / PARTNERS_WITH / MENTIONED_WITH
```

**Isolation guarantee:** Every step function has one job and one job only. Editing one step (e.g. adding retry logic to `_step_call_vision_llm`) does not touch any other step or any other module. Adding a new module requires only a new extractor file + one entry in `run_parallel_extraction.py`.

### 4.4 Parallel Execution

All four pipelines run concurrently via `ThreadPoolExecutor(max_workers=4)` in `scripts/run_parallel_extraction.py`. Each thread gets its own `ExtractionContext` with its own DB session and adapters — no shared mutable state between threads.

Document metadata (`title`, `author`, `date`, `source_document_id`) is extracted once in the main thread by `doc_metadata.py` before the pool starts, then passed read-only to all threads as `doc_meta: dict`.

### 4.5 Deduplication

Every `DataFragment` is assigned a `content_fingerprint` before insert:

```
SHA-256("{tenant_id}:{source_document_id}:{exact_location}")
```

- `source_document_id` is a UUID5 seeded from the PDF filename — stable across runs and filename renames.
- `exact_location` is the page range ("pp. 1-3") or chart page ("p. 3").
- `postgres_adapter.save_fragment()` checks for an existing fingerprint before every insert and skips silently if one is found.
- `scripts/deduplicate_fragments.py` is a standalone tool to backfill fingerprints on existing rows and remove historical duplicates.

### 4.6 Recipe Components

| Field | Purpose |
|---|---|
| `ingestor_type` | Module type identifier (e.g. `CAUSAL_RELATIONSHIP`, `CHART_VISION`) |
| `llm_prompt_template` | Extraction instructions injected into the LLM prompt |
| `expected_schema` | JSON Schema passed to LLM for constrained structured output |
| `version` | Immutable — old fragments retain their recipe lineage |

### 4.7 Document Provenance

Every `DataFragment`, regardless of module, carries the same document-level provenance fields in `extracted_metrics`:

| Field | Source |
|---|---|
| `source_article_title` | `doc_metadata.py` LLM call |
| `source_article_main_point` | `doc_metadata.py` LLM call |
| `source_article_author` | `doc_metadata.py` LLM call |
| `source_article_date` | `doc_metadata.py` LLM call |
| `source_document_id` | UUID5 from PDF filename (stable, reproducible) |
| `source_pdf_filename` | Original PDF filename |

The `raw_text` field (embedded to Pinecone) always leads with these provenance fields so that semantic search on the article topic surfaces every fragment from that document.

---

## 5. Agentic Query Routing — Engine (Tool-Use Architecture)

### 5.1 Architecture: Tool-Use Loop → Execute via Registry

The `EngineAgent` uses Claude (Anthropic) with native tool-use. The LLM receives the user query and a fixed set of typed tool definitions. It decides which tools to call and with what parameters — it never generates SQL or knows the storage schema.

Execution is delegated to the `ExecutorRegistry`, which dispatches each tool call to the matching `QueryExecutor`.

```
User query
    │
    ▼
EngineAgent.process_query()
    │
    ├── Claude (claude-sonnet-4-6) receives query + TOOLS
    │   └── Returns: (text, [tool_calls])
    │
    ▼
ExecutorRegistry.run_all(tool_calls)   ← parallel execution
    ├── DataAgentExecutor  (get_financial_data)  → financial_table block + summary
    └── PineconeExecutor   (search_documents)    → text block + summary
    │
    ▼
Claude synthesis pass (summaries only — NOT full data, for token efficiency)
    │
    ▼
ChatResponse (synthesis text + [AgentBlock, ...])
```

### 5.2 Tool Definitions

Tools are defined in `backend/app/agents/tools.py` as typed schemas. Claude picks tools by reading their descriptions — descriptions are load-bearing and must be kept accurate.

| Tool | Executor | Data source | Block type | When used |
|---|---|---|---|---|
| `get_financial_data` | `DataAgentExecutor` | ToplineBuilder / CalculatedLayerBuilder | `financial_table` | Revenue, margins, EPS, growth rates, balance sheet |
| `search_documents` | `PineconeExecutor` | Pinecone (all qualitative sources) | `text` | Strategy, commentary, filings, transcripts, notes |

### 5.3 Adding Data Sources

**Qualitative sources (documents, text):**
All qualitative sources funnel into a single `search_documents` tool via the `doc_types` filter parameter. Adding a new source requires only a new extractor that ingests to Pinecone with a new `doc_type` tag. Zero changes to the tool definition or router.

```
SEC 10-K/10-Q    → extractor → Pinecone (doc_type="10-K")       ─┐
Earnings calls   → extractor → Pinecone (doc_type="transcript")  ─┤
Company news     → extractor → Pinecone (doc_type="news")        ─┤→ search_documents(doc_types=[...])
User notes       → Notes tab → Pinecone (doc_type="note")        ─┤
Broker reports   → extractor → Pinecone (doc_type="broker")      ─┘
```

**Quantitative sources (new structured data):**
1. Create `executors/<name>_executor.py` implementing `QueryExecutor`.
2. Add a new tool definition to `agents/tools.py`.
3. Register in `dependencies.py`.
4. Zero changes to `EngineAgent`, existing executors, or existing tools.

**Adding a new executor** (e.g. Neo4j graph insights, OHLCV data):
Same as above — new executor file + tool definition + one line in `dependencies.py`.

### 5.4 Token Efficiency — Result Bifurcation

Tool results are split into two streams to keep Claude's context lean:
- **Full data → frontend directly** as `AgentBlock` (never passed back through Claude)
- **Brief summary → Claude's context** for synthesis (~30 tokens vs ~1000+ for raw JSON table)

Additional efficiency measures:
- Valid metric list injected into system prompt (Claude picks exact names, never hallucinates)
- Parallel tool execution via `ExecutorRegistry.run_all()` (concurrent, not sequential)
- Per-session result cache (same ticker+metrics returns cached DataAgent result)
- `search_documents` returns top-k snippets with char limits, not full documents
- Session sliding window: after N turns, summarize earlier messages

### 5.5 Multi-Provider LLM — Engine Agent

The Engine agent's LLM is selected at runtime via the `ENGINE_LLM` environment variable. All three providers are fully implemented and can be switched without code changes.

| `ENGINE_LLM` | Adapter | Default model | Required key |
|---|---|---|---|
| `anthropic` (default) | `adapters/llm/anthropic_adapter.py` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| `gemini` | `adapters/llm/gemini_adapter.py` | `gemini-2.5-flash` | `GEMINI_API_KEY` |
| `openai` | `adapters/llm/openai_adapter.py` | `gpt-4o` | `OPENAI_API_KEY` |

`ENGINE_MODEL` overrides the default model for any provider (e.g. `ENGINE_MODEL=gpt-4o-mini`).

**Tool schema design:** `TOOL_SPECS` in `agents/tools.py` uses a provider-neutral JSON Schema format (lowercase types). Each adapter calls its own converter (`to_anthropic_tools`, `to_openai_tools`, `to_gemini_tools`) before the API call. Adding a new provider = new adapter + one converter function in `tools.py`.

**Embeddings for Pinecone:** Always uses `GeminiAdapter` regardless of `ENGINE_LLM`, because the Pinecone index was populated with Gemini vectors. `AnthropicAdapter` does not support embeddings; `OpenAIAdapter` supports a different embedding model.

### 5.6 Thesis Ledger & Ambient Agents

The system maintains active Long/Short positions in `ThesisLedger`. Ambient background agents continuously evaluate new `DataFragments` against this ledger. When a catalyst is triggered or a thesis is challenged, a WebSocket alert is pushed to the Monitors tab.

---

## 6. Frontend Architecture — The 5 Tabs

| Tab | Route | Status |
|---|---|---|
| Topology | `/topology` | Placeholder |
| Unified Data Engine | `/engine` | Implemented |
| Library | `/library` | Placeholder |
| Synthesis | `/synthesis` | Placeholder |
| Monitors | `/monitors` | Placeholder |

### 6.1 Engine Tab (Implemented)

The most complete tab demonstrates the full Container/View pattern:

```
engine/
├── page.tsx           # Route entry — renders EngineContainer only
├── EngineContainer.tsx # Smart: chatClient calls, store wiring, error handling
├── EngineView.tsx      # Dumb: pure JSX, mock-prop-testable, no imports from lib/store
└── store.ts            # Co-located Zustand store (engine-tab state only)
```

### 6.2 State Management Rules

- Stores are **co-located with their feature** when the state is tab-specific.
- Shared/cross-tab state lives in `src/store/`.
- No store may be imported by a dumb component or a `*View.tsx` file.

| Store file | Scope |
|---|---|
| `app/(dashboard)/engine/store.ts` | Engine tab only (messages, session) |
| `src/store/useLedgerStore.ts` | Shared: Synthesis + Monitors tabs |
| `src/store/useAuthStore.ts` | Global: authentication state |
| `src/store/useEngineStore.ts` | Re-export shim only (backward compat) |

---

## 7. Directory Structure

### Backend

```text
backend/
├── main.py                         # FastAPI app + CORS + X-API-Version middleware
├── app/
│   ├── api/
│   │   ├── dependencies.py         # DI: wires adapters into services/agents
│   │   └── routers/v1/             # HTTP endpoints (chat, ingest, ledger, topology)
│   ├── core/
│   │   └── config.py               # Pydantic Settings — single source of truth for env vars
│   ├── interfaces/                 # PORTS (Abstract Base Classes — no implementations)
│   │   ├── llm_provider.py
│   │   ├── db_repository.py
│   │   ├── quant_repository.py
│   │   └── graph_repository.py     # GraphRepository + VectorRepository
│   ├── adapters/                   # ADAPTERS (Concrete implementations)
│   │   ├── llm/gemini_adapter.py
│   │   ├── db/postgres_adapter.py
│   │   ├── db/duckdb_adapter.py
│   │   ├── vector/pinecone_adapter.py
│   │   └── graph/neo4j_adapter.py
│   ├── models/
│   │   ├── domain/                 # Pydantic V2 domain models (DataFragment, Recipe, Ledger…)
│   │   ├── orm/                    # SQLAlchemy ORM models
│   │   └── api_contracts.py        # Request/response Pydantic schemas + APIResponse wrapper
│   ├── services/
│   │   ├── extraction_engine/
│   │   │   ├── pipeline.py         # ExtractionContext dataclass + Pipeline runner
│   │   │   ├── runner.py           # Legacy orchestrator (single-text entry point)
│   │   │   ├── validators.py       # ExtractionValidator (Pydantic firewall logic)
│   │   │   └── steps/              # Single-responsibility step functions
│   │   │       ├── shared_steps.py # step_load_document, step_store_fragments (used by all modules)
│   │   │       ├── fetch_recipe.py
│   │   │       ├── call_llm.py
│   │   │       ├── validate.py
│   │   │       ├── store_fragment.py
│   │   │       └── fanout.py
│   │   └── alert_service.py
│   ├── agents/
│   │   ├── router_agent.py         # PLAN GENERATION ONLY (RoutingPlan via LLM)
│   │   └── executors/              # One executor per data source
│   │       ├── base.py             # QueryExecutor abstract base
│   │       ├── duckdb_executor.py
│   │       ├── pinecone_executor.py
│   │       └── executor_registry.py # Dispatches RoutingPlan to all matching executors
│   └── db/
│       └── session.py              # SQLAlchemy session factory
├── scripts/
│   ├── run_parallel_extraction.py  # Entry point — runs all modules in parallel (ThreadPoolExecutor)
│   ├── export_fragments_to_json.py # Debug tool — dumps DB fragments to JSON files
│   ├── deduplicate_fragments.py    # Dedup tool — backfills fingerprints, removes duplicates
│   └── extractors/
│       ├── pdf_utils.py            # Shared PDF utilities (text extract, render, chart detect, disclosure filter)
│       ├── doc_metadata.py         # Extracts doc-level metadata once per PDF (title, author, date, UUID5 ID)
│       ├── causal_extractor.py          # Module 1: 6-step causal relationship pipeline
│       ├── chart_extractor.py           # Module 2: 7-step chart/exhibit vision pipeline
│       ├── company_intel_extractor.py   # Module 3: 6-step company business intelligence pipeline
│       └── relationship_extractor.py    # Module 4: 6-step business relationship pipeline
├── data/
│   ├── Broker_report/              # Source PDFs (gitignored)
│   ├── extracted_charts/           # Saved chart PNGs (gitignored)
│   └── fragment_debug/             # JSON debug exports of all DB fragments (gitignored)
└── requirements.txt
```

### Frontend

```text
frontend/src/
├── app/
│   ├── (dashboard)/
│   │   ├── engine/
│   │   │   ├── page.tsx            # Route entry — renders EngineContainer only
│   │   │   ├── EngineContainer.tsx # SMART: API calls + store wiring
│   │   │   ├── EngineView.tsx      # DUMB: pure JSX, no lib/store imports
│   │   │   └── store.ts            # Co-located Zustand store (engine-tab only)
│   │   ├── topology/page.tsx
│   │   ├── library/page.tsx
│   │   ├── synthesis/page.tsx
│   │   └── monitors/page.tsx
│   └── layout.tsx
├── components/
│   └── domain/
│       ├── charts/MetricChart.tsx  # Dumb: accepts { name, value }[] only
│       └── blocks/
│           ├── AgentBlockRenderer.tsx
│           └── TextBlock.tsx
├── lib/
│   ├── api/                        # Domain-split API clients
│   │   ├── base.ts                 # apiRequest<T> + X-API-Version assertion
│   │   ├── chatClient.ts           # /chat endpoint
│   │   ├── ingestClient.ts         # /ingest endpoint
│   │   ├── ledgerClient.ts         # /ledger endpoints
│   │   ├── topologyClient.ts       # /topology endpoints
│   │   └── index.ts                # Central re-export
│   ├── api.ts                      # Backward-compat shim (AlphaGraphAPI object)
│   └── mappers/
│       └── mapAgentBlock.ts        # Runtime type guard + backend → component prop transform
├── store/                          # Shared / global state only
│   ├── useAuthStore.ts
│   ├── useLedgerStore.ts
│   └── useEngineStore.ts           # Re-export shim → app/(dashboard)/engine/store.ts
└── types/
    └── api.generated.ts            # Auto-generated from backend OpenAPI spec (npm run generate:api-types)
```

---

## 8. API Contract Management

### Version Header Flow

```
Backend (main.py)                       Frontend (lib/api/base.ts)
────────────────────────────────        ──────────────────────────────────
API_VERSION = "1.0.0"           ──────► EXPECTED_API_VERSION = "1.0.0"
X-API-Version header on every           console.warn on mismatch
response
```

Increment `API_VERSION` in `main.py` and `EXPECTED_API_VERSION` in `base.ts` whenever a **breaking** change is made to any API contract shape.

### OpenAPI Codegen

With the backend running:

```bash
cd frontend
npm run generate:api-types
# Writes: src/types/api.generated.ts
```

TypeScript then enforces the contract at build time. Any backend model rename or field removal becomes a compile error in the frontend, not a silent runtime bug.

---

## 9. Security & Multi-Tenancy

- All `DataFragment`, `ExtractionRecipe`, and `ThesisLedger` records carry a `tenant_id` and `tenant_tier` (PUBLIC / PRIVATE).
- Public-tier fragments are readable cross-tenant; private-tier fragments are isolated.
- Auth: JWT tokens via `python-jose` + `passlib` (8-day expiry, configurable).
- All secrets managed via environment variables — never hardcoded. Loaded by `pydantic-settings` in `core/config.py`.

---

## 10. Extension Checklist

When adding a new feature, follow these rules to preserve modularity:

| What you're adding | Where to add it | What NOT to touch |
|---|---|---|
| New extraction module | New `scripts/extractors/<name>_extractor.py` with step functions + `Pipeline([...])` + `run_X_extraction()`. Add a `_run_X` wrapper + one `futures` entry in `run_parallel_extraction.py` | All other extractor files, `pipeline.py`, `shared_steps.py` |
| New shared pipeline step | `steps/shared_steps.py` — only if the step is genuinely reused by 2+ modules | Module-specific step functions |
| New LLM provider for Engine | New `adapters/llm/` file + new converter in `agents/tools.py` + one entry in `get_engine_llm()` | Everything else |
| Switch Engine LLM | Set `ENGINE_LLM=anthropic\|gemini\|openai` in `.env` | Nothing — zero code changes |
| Override Engine model | Set `ENGINE_MODEL=<model-id>` in `.env` | Nothing |
| New qualitative data source (docs) | New extractor → Pinecone with new `doc_type` tag → update `doc_types` description in `agents/tools.py` | Tool definition, router, executors |
| New quantitative data source | New `agents/executors/` file + new tool in `agents/tools.py` + register in `get_engine_agent()` | `router_agent.py`, other executors |
| New extraction fanout target | Add a `_step_fanout_X` function to the relevant extractor's pipeline list | Other steps, other modules |
| New API endpoint | New router file in `api/routers/v1/` + register in `main.py` | Existing routers |
| New frontend tab | New `app/(dashboard)/<tab>/` directory with Container + View + store | Other tabs, shared components |
| New shared component | `components/domain/` — must accept only plain props, no store/api imports | Containers, mappers |

---

## 11. Audio Capture & Transcription Subsystem

Meeting recordings and transcription for the **Notes** tab (future frontend integration).

### Pipeline Overview

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Record    │ -> │  Convert    │ -> │ Transcribe  │ -> │ AI Summary  │
│ (sounddevice)│   │ (ffmpeg)    │    │ (Whisper/   │    │ + Metadata  │
│   -> WAV    │    │ + loudnorm  │    │  Deepgram)  │    │  (future)   │
│             │    │   -> OPUS   │    │             │    │             │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

### Directory Structure

```
tools/audio_recorder/
├── record_meeting.py           # Capture system audio (WASAPI loopback)
├── convert_to_opus.py          # WAV -> OPUS with volume normalization
├── transcribe_recording.py     # Local Whisper (free, no speaker ID)
├── transcribe_with_speakers.py # Deepgram API (speaker diarization)
├── live_transcribe.py          # Real-time streaming transcription
├── recordings/                 # Output: .opus files
├── transcripts/                # Output: .txt files
├── requirements.txt            # sounddevice, faster-whisper, deepgram-sdk
└── .env                        # DEEPGRAM_API_KEY
```

### Two Transcription Paths

| Method | Cost | Speaker ID | Language | Best For |
|--------|------|------------|----------|----------|
| **Local Whisper** | Free | No | Auto-detect | Large files, offline use |
| **Deepgram API** | ~$0.007/min | Yes (Speaker A, B, C...) | Specify `--language zh` | Meetings with multiple speakers |

### Key Technical Decisions

- **OPUS format**: 95% smaller than WAV, supported by all transcription APIs
- **Volume normalization**: `loudnorm` filter ensures speech detection on quiet recordings
- **Dual-path**: Local for cost savings, Deepgram for speaker identification

### Future Integration (Notes Tab)

- Frontend: `app/(dashboard)/notes/` with Container/View pattern
- Backend: `api/routers/v1/notes.py` for transcript storage/retrieval
- AI Summary: LLM extraction of key points, action items, searchable metadata

---

## 12. Taiwan Monthly-Revenue Subsystem

Ingests monthly revenue (月營收) for a curated 51-ticker Taiwan
semiconductor-ecosystem watchlist. End-to-end coverage: 1999-03 → live
current month, both TWSE (上市) and TPEx (上櫃) listings, with amendment
history tracked per ticker-month.

### 12.1 Three-Source Blend

One parquet dataset, three complementary sources:

```
┌──────────────────────┬─────────────────────────┬────────────────────────────┐
│ Source               │ Coverage                │ Role                       │
├──────────────────────┼─────────────────────────┼────────────────────────────┤
│ MOPS t146sb05_detail │ rolling last 12 months  │ LIVE — regulatory source   │
│ (Playwright/CDP)     │ per ticker              │ of truth; all watchlist    │
│                      │                         │ companies, authoritative   │
├──────────────────────┼─────────────────────────┼────────────────────────────┤
│ TWSE C04003 ZIP      │ 1999-03 → prior month   │ BACKFILL — bulk, domestic  │
│ /staticFiles/...zip  │ (325 months)            │ TWSE main board only       │
├──────────────────────┼─────────────────────────┼────────────────────────────┤
│ TPEx O_YYYYMM.xls    │ 2009-12 → prior month   │ BACKFILL — bulk, TPEx      │
│ /storage/...xls      │ (196 months)            │ (上櫃) companies only       │
├──────────────────────┼─────────────────────────┼────────────────────────────┤
│ MOPS t05st02         │ one day at a time       │ SUPPLEMENT — voluntary     │
│ (material info)      │                         │ early-warning signal       │
│                      │                         │ (~5% of tickers, not       │
│                      │                         │  required for filing)      │
├──────────────────────┼─────────────────────────┼────────────────────────────┤
│ TPEx OpenAPI         │ current month, all ~800 │ REDUNDANCY + cross-check   │
│ /openapi/v1/         │ TPEx cos in one bulk    │ for TPEx tickers. INSERT   │
│ mopsfin_t187ap05_O   │ JSON call               │ on missing (MOPS-down      │
│                      │                         │ fallback), flag DIVERGENT  │
│                      │                         │ when it disagrees with     │
│                      │                         │ our stored MOPS value.     │
└──────────────────────┴─────────────────────────┴────────────────────────────┘
```

### 12.2 Storage Layout

```
backend/data/taiwan/
├── watchlist_semi.csv                 # 51-ticker input (committed)
├── monthly_revenue/
│   ├── data.parquet                   # the analytical dataset (gitignored)
│   └── history.parquet                # amendment trail (older row versions)
├── _raw/                              # raw captures — gitignored; rebuildable
│   ├── twse_zip/{YYYYMM}_C04003.zip
│   ├── tpex_xls/O_{YYYYMM}.xls
│   └── monthly_revenue/{ticker}/{YM}_detail.json
└── _registry/
    └── mops_company_master.parquet    # ticker → market + sector cache
```

### 12.3 Parquet Schema (one row = one ticker-month)

| column | type | example |
|---|---|---|
| ticker | str | `2330` |
| market | str | `TWSE` / `TPEx` / `Emerging` |
| fiscal_ym | str | `2026-03` |
| revenue_twd | int64 | `415_191_699_000` (full TWD) |
| yoy_pct | float64 | `0.4519` (1.0 = 100%) |
| mom_pct | float64 | `0.3070` |
| ytd_pct | float64 | `0.3513` |
| cumulative_ytd_twd | int64 | `1_134_103_440_000` |
| prior_year_month_twd | int64 | `285_956_830_000` |
| first_seen_at | datetime UTC | `2026-04-24T02:17:33Z` |
| last_seen_at | datetime UTC | `2026-04-24T02:17:33Z` |
| content_hash | str | `sha256(canonical row JSON)` |
| amended | bool | `false` |

**Upsert semantics** (backend/app/services/taiwan/amendments.py):
- `(ticker, fiscal_ym)` not seen before → INSERT, hash the value
- seen before with same hash → TOUCH_ONLY (bump `last_seen_at`, nothing else)
- seen before with different hash → AMEND (copy prior row to `history.parquet`, overwrite primary, set `amended=True`)

### 12.4 Live-Tracking Architecture

Monthly revenue must be filed to MOPS **by the 10th of the following
month** (Taiwan Securities and Exchange Act). Peak publication spans the
1st-15th. Our scheduler matches that cadence:

```
                           publication window (1st-15th TPE)
                           │ bursty 17:00-23:00 TPE weekdays │
      daily 10:00 TPE      │                                 │
─────●─────────────────────●─────────────────────────────────●────────────────
     │                     │                                 │
     │       window-active poll: every 30 min @ :00,:30       │
     │       via same t146sb05_detail per-ticker call         │
     │                                                        │
     └─ fallback daily tick outside the window (16th-31st)   ┘
```

Cadence details:

- **Daily 10:00 TPE (always):** `monthly_revenue_daily` — poll
  `t146sb05_detail` for all 51 tickers. Each ticker is ~500ms inside a
  warmed CDP browser context; total tick ~25-45s.
- **Every 30 min, 1st-15th of each month:** `monthly_revenue_window` —
  same endpoint, same code, higher frequency because that's when
  companies actually file. A file that lands at 19:47 TPE is visible
  to users within 30 min.
- **Monthly 1st @ 03:00 TPE:** `company_master_refresh` — re-resolve
  watchlist via `KeywordsQuery` (market + sector drift).
- **Weekly Sun 03:00 TPE:** `twse_weekly_patch` + `tpex_weekly_patch` —
  download prior-month bulk files; catch amendments the per-ticker
  endpoint missed.
- **Hourly:** `health_check` — reads `taiwan_scraper_heartbeat` SQLite
  table; logs WARN on any scraper stale beyond 2× its cadence.

### 12.5 Why we do NOT use material-info (t05st02) as primary live source

An 11-day scan of April 2026 material-info announcements (our watchlist
filing-window measurement) found:

- 2,104 total announcements from all Taiwan issuers
- 94 revenue-flavored (keywords: 營業額 / 營業收入 / 月份營收 / 自結 / 合併營收) = **4.5%**
- **Only 1 of our 51 watchlist tickers** (2454 MediaTek) posted a
  revenue-flavored material-info announcement in the entire window
- 75 unique tickers market-wide filed voluntary revenue material info

Material information is **elective, not the mandatory filing**. The
authoritative monthly-revenue filing is the structured `t05st10` form,
which surfaces in `t146sb05_detail` within minutes of submission.
Material info is a supplement — useful for tickers who posted
material info BEFORE filing the formal record (rare in our watchlist),
but it misses ~98% of filings.

**Decision:** `t146sb05_detail` is the primary live source.
`t05st02` is kept in the scraper suite as an optional supplement for
future per-ticker early-warning use cases, not as a required channel.

### 12.6 Observability

- **SQLite `taiwan_scraper_heartbeat`** — one row per scraper name, updated on every run with `rows_inserted`, `rows_updated`, `rows_amended`, `last_error_msg`, `status ∈ {ok, degraded, failed}`, `last_run_at`, `last_success_at`.
- **`GET /api/v1/taiwan/health`** — returns the heartbeat rows annotated with `lag_seconds` since last success.
- **Frontend `TaiwanHealthIndicator`** in `/taiwan` tab header — coloured dot + tooltip listing each scraper's status.
- **Amendment signal (TODO):** emit a distinct event when `amended=True` upserts occur so the UI can surface "TSMC restated Feb 2026 at 2026-04-15".

### 12.7 Module Layout

```
backend/app/services/taiwan/
├── mops_client.py               # Playwright/CDP JSON client (persistent ctx)
├── mops_client_browser.py       # CDP Chrome launcher
├── storage.py                   # parquet + raw capture + S3 mirror (opt)
├── amendments.py                # content-hash upsert decisions
├── validation.py                # schema invariants as flags (never drop)
├── registry.py                  # watchlist CSV + ticker→market cache
├── health.py                    # SQLite heartbeat table
├── scheduler.py                 # APScheduler entry (TPE timezone)
└── scrapers/
    ├── company_master.py        # KeywordsQuery-driven market/sector resolver
    ├── monthly_revenue.py       # live: t146sb05_detail per ticker
    ├── twse_historical.py       # backfill: TWSE C04003 ZIPs → XLS
    └── tpex_historical.py       # backfill: TPEx O_YYYYMM.xls
```

Full endpoint catalog, corner-case playbook (28 items across MOPS,
TWSE, TPEx, and Python-3.13 TLS quirks), and a step-by-step
rediscovery guide for when MOPS or TWSE redesigns next are in
`.claude/skills/taiwan-monthly-data-extraction/SKILL.md`.

---

## 13. Implementation Status & Scaling Roadmap

*Last updated: 2026-04-26.*

This section is the **single source of truth for what is currently
running**, what exists in code but is not yet wired in, and the
sequenced plan for scaling from today's footprint (~18 tickers,
single-process backend, dev-laptop scale) to the target footprint
(2000+ tickers across US/TW/JP/CN, multi-user agentic queries, 500+
concurrent active users with sub-second median latency).

The earlier sections describe the design's destination. This section
describes the journey.

### 13.1 What is Actually Live Today (2026-04-26)

**Backend process model.**
- Single uvicorn worker on `localhost:8000` in dev (`uvicorn backend.main:app --reload`).
- Production-shaped launch (`--workers 4`, no reload) is documented in `CLAUDE.md` but not yet running in a managed environment.
- No load balancer, no reverse proxy, no auth layer.

**Storage actually in use.**
- **SQLite** (`alphagraph.db`, ~4.6 MB): `data_fragments` (154 rows), `extraction_recipes` (42 rows), `public_universe` (12 rows), `meeting_notes` (17 rows), `taiwan_scraper_heartbeat` (11 rows). WAL journaling enabled at engine connect since 2026-04-26 (see §13.3).
- **Parquet (silver)** under `backend/data/financials/quarterly_facts/`: 3 Taiwan tickers (`2330.TW`, `2303.TW`, `2454.TW`) with 15K total rows. EDGAR-sourced US tickers (~15 of them) live in `backend/data/filing_data/filings/ticker={TICKER}.parquet`.
- **Parquet (bronze)** under `backend/data/financials/raw/`: 226 JSON files, 82 MB. Raw page text + provenance from each PDF.
- **Parquet (guidance)** under `backend/data/financials/guidance/`: TSMC + UMC structured guidance vs actual records.
- **Parquet (transcripts)** under `backend/data/financials/transcripts/`: TSMC LSEG transcripts as long-format speaker turns.
- **Market-data parquets** under `backend/data/market_data/`: Reddit, Google News, GPU prices, PCPartPicker, CamelCamelCamel, X/Twitter.

**LLMs.**
- Anthropic Claude (Sonnet 4.6) — wired via `backend/app/adapters/llm/anthropic_adapter.py`, used by the EngineAgent for tool-use chat (`POST /api/v1/chat/...`).
- Google Gemini — wired for extraction + embeddings via `gemini_adapter.py`. Used for chart-vision and document-meta extraction.
- OpenAI — adapter exists but unused in active code paths.

**NOT live, despite adapter code existing:**
- **Pinecone** vector DB — adapter exists at `backend/app/adapters/vector/pinecone_adapter.py`. Some scripts call it for embeddings, but no production query path depends on it for serving. Insights / Wiki layers (Layers 3-4 of the product) that would consume vector search are not built.
- **Neo4j** graph DB — adapter exists at `backend/app/adapters/graph/neo4j_adapter.py`. Wired into the relationship-extraction script (`relationship_extractor.py`) but no API endpoint queries the graph. Topology view (Tab 2 in the product spec) is unbuilt.
- **Postgres** — `engine = create_engine(settings.POSTGRES_URI)` resolves to a SQLite URI in dev. The variable name is aspirational; we run on SQLite.
- **DuckDB** — adapter referenced in §1 but not in the active query path. All current API endpoints use pandas-on-parquet.

**Active scrapers (running on a schedule).**
- `backend/app/services/social/scheduler.py` — APScheduler, runs news / Reddit / GPU price / Reddit-keyword-search scrapers on staggered cadences.
- `backend/app/services/taiwan/scheduler.py` — APScheduler, runs MOPS monthly-revenue + material-info scrapers in TPE timezone.
- Scrapers write directly to parquet; no write queue, no validation gate.

**API endpoint surface.**
- 16 routers under `backend/app/api/routers/v1/` totaling ~80 routes.
- Per-company panels: `tsmc.py`, `umc.py`, `mediatek.py` (one router per ticker — see §13.4 for why this won't scale).
- Cross-cutting: `data.py` (EDGAR financial data), `taiwan.py` (heatmap + monthly revenue), `social.py`, `pricing.py`, `earnings.py`, `chat.py`, `notes.py`, `insights.py`, `topology.py`.
- Diagnostics: `admin.py` — `GET /api/v1/admin/cache` for cache hit rate, `GET /api/v1/admin/runtime` for worker PID. Added 2026-04-26.

**Frontend.**
- Next.js 15 + React 19 dashboard at `frontend/`.
- Single-tenant: no auth, no user model, no per-user state.
- Container/View pattern enforced for new panels (TSMC/UMC/MediaTek panels follow it; some legacy NVDA-style code is in `DataExplorerView.tsx` directly).

### 13.2 Coverage Footprint Today

| Region | Source | Tickers integrated | Per-ticker silver rows | Notes |
|---|---|---|---|---|
| US (EDGAR) | XBRL + 8-K text | ~15 | varies (financials + earnings releases) | NVDA, AAPL, AMD, AMAT, AVGO, CDNS, DELL, INTC, KLAC, LITE, LRCX, MRVL, MU, etc. — semis + AI infra focus |
| Taiwan | UMC + TSMC + MediaTek | 3 | 1.7K-8.7K | All have full quarterly extraction (UMC: 48 metrics, TSMC: 30+, MediaTek: 18). Plus 51-ticker monthly-revenue universe via MOPS. |
| Japan | — | 0 | — | No extraction yet. Target sources: TDnet (immediate disclosure) + EDINET (annual/quarterly filings). |
| China (HK + mainland) | — | 0 | — | No extraction yet. Target sources: HKEX (HKEX news), SSE / SZSE filings; major risk is access (proxy / VPN may be required). |

### 13.3 Phase 1 — Performance Baseline (DONE 2026-04-26)

**Goal:** unblock multi-user concurrency and eliminate redundant
parquet I/O before any storage refactor. Measured ~10× headroom from
~3 hours of work.

**Shipped:**

1. **Mtime-keyed LRU cache for parquet reads** — `backend/app/services/data_cache.py`. Wraps `pd.read_parquet` with `functools.lru_cache` keyed by `(path_str, mtime_ns, columns_tuple)`. Auto-invalidates when an extractor writes a new parquet (mtime changes → cache key changes → fresh read). All 38+ `pd.read_parquet` call sites across 8 routers (`tsmc`, `umc`, `mediatek`, `taiwan`, `pricing`, `earnings`, `data`, `social`) swapped to `read_parquet_cached`. Pandas copy-on-write enabled module-side as a safety net for accidental mutations of cached frames.
2. **Multi-worker uvicorn** — production launch command documented in `CLAUDE.md`. Each worker has its own in-process LRU cache (no IPC needed; mtime-keyed reads stay coherent across workers). 4 workers on a 4-core box ≈ 4× concurrent throughput.
3. **SQLite WAL mode** — `backend/app/db/session.py` wires an SQLAlchemy `event.listens_for(engine, "connect")` hook that runs `PRAGMA journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=5000` on every connection. Eliminates the global write-lock that previously stalled all reads during heartbeat upserts.
4. **Admin diagnostics router** — `backend/app/api/routers/v1/admin.py`. `GET /api/v1/admin/cache` returns `{lru_size, lru_hits, lru_misses, hit_rate, ...}`. `GET /api/v1/admin/runtime` returns the worker PID. `POST /api/v1/admin/cache/clear` for tests.

**Measured impact** (TSMC silver, 8678 rows):

| Metric | Before | After | Speedup |
|---|---|---|---|
| `pd.read_parquet` per call | 6.11 ms | 0.028 ms | **214×** |
| Cross-ticker scan (100 reads) | 498 ms | 19 ms | **26×** |
| End-to-end UMC tab load (3 endpoints) | 124 ms | 118 ms | 1.05× (cache headroom not yet visible at this data scale; payoff is at 100+ ticker workloads) |
| Cache hit rate after warmup | n/a | 97% | — |

**Effective concurrency ceiling after Phase 1:** ~50-100 simultaneous
active users with snappy UI (vs ~5-10 before), assuming a mix of
single-ticker drilldowns and the occasional cross-ticker heatmap.

### 13.4 Phase 2 — Storage & Query Engine Refactor (PLANNED)

**Goal:** make cross-ticker queries first-class. Get the system into
the right shape for 500-2000 tickers. ~2-3 weeks of work.

**Critical task list:**

1. **Re-partition silver to hive-style layout.**
   - From: `backend/data/financials/quarterly_facts/{TICKER}.parquet` (one file per ticker)
   - To: `backend/data/financials/quarterly_facts/region={US,TW,JP,CN}/ticker={TICKER}/data.parquet`
   - All 7 endpoints already use the long-format silver schema `(ticker, period_end, metric, dimension, value, unit, source, extracted_at)` — no schema change needed; just file layout change.
   - Migration script: walk existing parquets, write to new layout, sanity-check row counts and identity invariants.

2. **Wire DuckDB as the query engine.** Endpoints become parameterised SQL templates instead of hand-written pandas pivots. Example:
   ```python
   query = """
     SELECT period_label, metric, AVG(value) AS value, ANY_VALUE(unit) AS unit
     FROM 'backend/data/financials/quarterly_facts/region=*/ticker=*/data.parquet'
     WHERE ticker = ? AND dimension = '' AND metric IN ?
     GROUP BY period_label, metric
   """
   ```
   - DuckDB does columnar reads + predicate pushdown over hive-partitioned parquet, so cross-ticker queries don't load 2000 files into memory.
   - Keeps the existing parquet files as the system of record — no new database to operate.

3. **Replace per-ticker routers with a parametrised router.** Today: `tsmc.py`, `umc.py`, `mediatek.py` are near-duplicates. At 2000 tickers this is unbuildable. Move to `companies.py`:
   ```
   GET  /api/v1/companies/{ticker}/financials/wide
   GET  /api/v1/companies/{ticker}/segments?metric=...
   GET  /api/v1/companies/{ticker}/cashflow
   GET  /api/v1/companies/{ticker}/guidance
   GET  /api/v1/companies/{ticker}/capacity      (returns 404 for non-foundries)
   ```
   - Per-ticker idiosyncrasies (e.g. UMC has capacity tables, MediaTek doesn't) live in a `CompanyProfile` record describing which endpoints make sense.

4. **Migrate user state from SQLite to Postgres** (managed: Neon / Supabase / RDS).
   - User accounts, sessions, watchlists, query history, agent intermediate state — all need concurrent-write capability that SQLite can't deliver above ~100 writes/sec.
   - Keep SQLite for the `data_fragments` knowledge graph if it remains read-mostly; or migrate everything for consistency.
   - Schema migration via Alembic.

5. **Add Redis (or DragonflyDB) for two purposes:**
   - **Result-level cache** keyed by `(endpoint, params, data_mtime)` for the hottest endpoints. Phase-1 cache is per-worker in-process; Phase-2 Redis cache is shared across workers. Eliminates the duplicate computation when the same endpoint is hit by multiple users.
   - **Agent session state** — agents that span multiple LLM round-trips need a place to park intermediate results. Redis is the canonical choice.

6. **Streaming responses via SSE for long agent queries.** Today every endpoint is a request-response. An agentic question that runs 50 sub-queries blocks the UI for seconds. Move to `text/event-stream` for chat + long-running queries; partial results stream as they're computed.

**Capacity ceiling after Phase 2:** ~500-1000 simultaneous active users with the right shape for 2000 tickers. Cost dominated by infrastructure rather than software.

### 13.5 Phase 3 — Multi-Tenant + Agentic Infrastructure (PLANNED)

**Goal:** make the system multi-user safe + agent-first. Months of
work, real product investment.

**Critical task list:**

1. **Auth + tenant isolation.** OAuth (Google / Microsoft / SSO) + per-tenant row-level isolation. Every query carries `tenant_id`. User watchlists, fragments, and outputs are scoped to a tenant. Postgres row-level security (RLS) enforces this at the DB level so application bugs can't leak cross-tenant data.

2. **Per-user query budgets + audit trail.** LLM tokens dominate cost at scale. A single agentic question can chain 5-50 LLM calls (≈ $0.10-2.00). Budgets enforced at the API gateway: monthly token cap per tier, per-question hard cap (e.g. 100 LLM calls before the agent surrenders), full audit trail of every model call with cost attribution.

3. **Vector index for transcripts + management commentary.** Agents need semantic search ("find supply-chain anxiety in 2025 calls") far more than they need exact-match. Two viable choices: pgvector (no new operational surface, great if Postgres is already there) or Qdrant / Pinecone (better at scale, separate ops). At 2000 companies × 5 years × 4 transcripts × 50 turns/transcript = 2M turn-level embeddings — pgvector handles this comfortably.

4. **Agent runtime separated from web tier.** The web tier becomes thin: auth, query queue submission, result streaming. Agents run in a worker pool (Celery + Redis, or RQ, or Temporal) with their own concurrency limits, retry semantics, and timeouts. Web tier never blocks on an agent loop.

5. **Point-in-time queries (`as_of_date`).** For backtesting any agent that does "predict next quarter," every fact must answer "what would I have known on date X?". Schema add: `extracted_at` already there; need `effective_from` / `effective_to` for amendments. Critical for trust — institutional users will ask "show me what your agent said in Q3 2024" and the data must reproduce that point-in-time view exactly.

6. **Caching at the (user, query_signature) level.** Agentic loops re-ask the same question many times during exploration. Per-user query cache (Redis) with TTL ≈ 1h cuts LLM bills meaningfully and improves perceived latency.

**Capacity ceiling after Phase 3:** 10K+ users, but cost (LLM tokens, infra) becomes the binding constraint, not throughput.

### 13.6 Phase 4 — Scrape Farm + Region-Specific Engineering (PLANNED)

**Goal:** reliable scraping of 2000 sources at the cadence each one
publishes. Months of work, plus geopolitical / legal review.

**Critical task list:**

1. **Per-source rate limits + back-off.** Some sources throttle aggressively. A single misbehaving scraper that hammers a Cloudflare-protected site can poison our IP for hours. Each scraper records its rate-limit budget and back-off state.

2. **Browser pool for Playwright-bound scrapers.** TSMC's Cloudflare bypass requires `page.evaluate(fetch)` inside a warm browser context. Today we use one persistent profile. At 200 such sources we need a pool: launch on demand, re-use within a session, recycle after N requests. Budget: ~50 MB RAM per Chrome instance × 20 concurrent = 1 GB.

3. **Per-region scraper farm.**
   - **US**: SEC EDGAR is open-data + bulk APIs; existing path scales without redesign.
   - **Taiwan**: TWSE bulk + MOPS SPA; existing path documented in `.claude/skills/taiwan-monthly-data-extraction/` already handles 51 watchlist tickers; expanding to 500 needs cadence tuning + browser pool.
   - **Japan**: TDnet + EDINET (XBRL). Format: largely XBRL with English filings available for the largest issuers; Japanese-only for mid-caps. Translation step needed.
   - **China**: HKEX (HK), SSE / SZSE (mainland). Mainland sites may require proxy / VPN access depending on hosting region. Significant due-diligence required before scraping at scale (terms of service, jurisdiction).

4. **Per-company idiosyncrasy budget.** TSMC + UMC + MediaTek each took ~1 week of focused engineering for clean extraction. At 2000 × 1 week, that's 40 person-years — unbuildable. Strategy:
   - **~100-150 "elite" companies** get hand-tuned extractors (top market cap per region, most analyst-relevant). These get the per-company `SKILL.md` + memory note pattern that already works for TSMC/UMC/MediaTek.
   - **~1850 "config-driven" companies** use a generic LLM-assisted extractor: ~70-80% accuracy on headline metrics, every fact flagged with a confidence score, data-quality framework (`.claude/skills/data-quality-invariants/`) drops anything that fails identity checks (gross + cogs = revenue, etc.).
   - Manually upgrade companies from "config" to "elite" as user demand surfaces them.

5. **Automatic re-extraction on parser improvement.** When we improve an extractor, re-run on cached bronze (no need to re-fetch PDFs). Bronze + silver split was designed for this; the re-extract path needs to be a routine command + observability for which silver rows changed.

### 13.7 Concrete Capacity Per Phase

| Stage | Workers | State store | Cache | Sustained concurrent users | Heatmap latency (100 tickers) |
|---|---|---|---|---|---|
| Pre-Phase 1 (start of session) | 1 (single uvicorn) | SQLite (no WAL) | none | 5-10 | 5+ s |
| **Phase 1 DONE** | 1-4 (config'd) | SQLite (WAL) | per-worker LRU | 50-100 | 200 ms |
| **Phase 2** | 4-8 (with worker pool) | Postgres + Redis | shared Redis result-cache + DuckDB query-engine | 500-1000 | 100 ms |
| **Phase 3** | autoscaled | Postgres + pgvector + Redis | + per-user query memoization | 10K (cost-bound) | <100 ms |

### 13.8 Where to Find What

| Concern | Code path | Skill / doc |
|---|---|---|
| Phase 1 cache implementation | `backend/app/services/data_cache.py` | — (single 130-line module) |
| Per-company quarterly extractors | `backend/scripts/extractors/{tsmc,umc,mediatek}_*.py` | `.claude/skills/tsmc-quarterly-reports/SKILL.md`; per-company memory in `~/.claude/projects/.../memory/project_taiwan_ir_extraction_*.md` |
| Taiwan monthly revenue (51 tickers) | `backend/app/services/taiwan/` | `.claude/skills/taiwan-monthly-data-extraction/SKILL.md` |
| Social scheduler (news/reddit/gpu) | `backend/app/services/social/scheduler.py` | — |
| Time-axis sort rule (UI tables) | — | `.claude/skills/time-axis-sort-convention/SKILL.md` + `CLAUDE.md` |
| Readable financial table aesthetics | — | `.claude/skills/readable-data-table/SKILL.md` |
| Backend launch (dev vs prod) | — | `CLAUDE.md` § "Backend Launch" |
| Admin / cache stats | `backend/app/api/routers/v1/admin.py` | `/api/v1/admin/cache` and `/api/v1/admin/runtime` |
