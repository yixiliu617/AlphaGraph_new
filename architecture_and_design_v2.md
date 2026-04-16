# AlphaGraph — Architecture & Technical Design (v2)

> **Revision history**
> - v1 — Initial design
> - v2 — Refactor pass: extraction pipeline steps, executor registry, Container/View split, domain-split API clients, runtime contract guards, API version header.

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
