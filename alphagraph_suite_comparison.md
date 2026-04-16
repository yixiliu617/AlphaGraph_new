# AlphaGraph Suite — Comparative Analysis
*Generated: 2026-04-10*

A deep review of the 4 AlphaGraph project iterations in `C:\Users\Sharo\AI_projects`.  
**Purpose:** Understand how the product and architecture evolved, and use it as a reference for continuing development on `AlphaGraph_new`.

---

## Quick Summary

| | AlphaGraph | AlphaGraph_updated | AlphaGraph123 | AlphaGraph_new |
|---|---|---|---|---|
| **Status** | Proof of concept | Firebase polish | Empty placeholder | Production-ready |
| **Backend** | Express (Node.js) | Express (Node.js) | — | FastAPI (Python) |
| **Frontend** | React + Vite | React + Vite | — | Next.js 15 + React 19 |
| **Architecture** | Monolithic | Monolithic | — | Hexagonal (Ports & Adapters) |
| **Databases** | DuckDB only | DuckDB + Firebase | — | Postgres + DuckDB + Pinecone + Neo4j |
| **Extraction modules** | 1 (SEC ETL) | 1 (SEC ETL) | — | 4 parallel modules |
| **Agents** | Prototype | Prototype | — | Router + Executors + Ambient |
| **Production readiness** | ~40% | ~50% | 0% | ~80% |

---

## 1. AlphaGraph (Original) — Proof of Concept

### Product Design
Vision: Pull + Push + Generate. Bloomberg-style data explorer (SEC EDGAR Parquet → DuckDB queries → Recharts), a drag-and-drop research composer, and AI synthesis via Gemini.

Core features:
- SEC financial data pipeline (10-K/10-Q via EDGAR)
- Interactive data explorer with DuckDB/Parquet analytics
- Drag-and-drop research composition
- AI-assisted report generation (Gemini API)
- Meeting notes and transcript capture (YouTube only)
- Agentic research builder (prototype)

### Tech Stack
| Component | Technology |
|---|---|
| Frontend | React 18 + TypeScript + Vite |
| Backend | Express (Node.js) — same server as frontend |
| Styling | TailwindCSS, Framer Motion |
| Charts | Recharts |
| Graph Visualization | ReactFlow |
| Analytical DB | DuckDB (in-memory, reads local Parquet) |
| SEC ETL | Python (edgar-py library) |
| LLM | Google Gemini 2.5 Flash |
| Drag-and-Drop | @hello-pangea/dnd |
| Cloud Storage | AWS S3 (optional fallback) |

### Frontend
- 18 pages, 6 React Contexts (global state), heavy mock data
- Monolith: Express serves both API and the Vite-built frontend
- Notable pages: DataExplorerPage, ResearchPage, ProjectMapPage, AgenticResearchBuilder

### Data Storage
- `data/backbone/ticker={TICKER}.parquet` — raw XBRL facts (concept, value, dates, dimensions)
- `data/filings/analysis/year={YYYY}.parquet` — YTD-to-quarterly discretized ("Prioritize then Calculate" logic: prefer 3-month facts, calculate Q4 from YTD math)
- `data/filings/segments_all.parquet` — geographic/product segments
- `SEC_Company_Registry.parquet` — which tickers/periods are loaded
- Immutable read-only Parquet; DuckDB is ephemeral

### Metadata & Indexing
- Concept mapping hard-coded in frontend (~40 XBRL tags → UI labels like "Net Revenue", "Op Income")
- Period filtering via `period_end` column at query time
- No formal lineage, no deduplication

### Agents
- None beyond a basic Gemini wrapper (`generateContentWithTracking`)
- No ambient monitoring or alerts

---

## 2. AlphaGraph_updated — Firebase Polish

Same codebase as AlphaGraph with incremental additions:
- **Firebase Auth** replaces local profiles
- **Firestore** replaces local JSON for persistence
- `Dockerfile` + GitHub Actions CI/CD added
- `firestore.rules` for access control

Still monolithic. No architectural improvements. This was a "make it deployable" pass.

---

## 3. AlphaGraph123 — Empty

No code. Just a placeholder directory.

---

## 4. AlphaGraph_new — Production Architecture

### Product Design: 6-Layer Architecture

```
Layer 1: Data Ingestion
  - Market data (OHLCV, options)
  - Structured numbers (SEC/EDGAR, macro)
  - Text & documents (broker reports, transcripts)
  - Proprietary intelligence (PM notes, theses)
  - Alternative/scraped data

Layer 2: Data Fragments
  - Customizable extraction, time-stamped, source-tiered

Layer 3: Insights
  - Related fragments → insights via feedback loops

Layer 4: Wiki
  - Knowledge base, temporal structure, cross-linked

Layer 5: Output Generation
  - Agents, report generation, PDF/PPT

Layer 6: Intelligence Delivery
  - Alerts, daily briefings, anomaly detection, synthesis
```

5-Tab UI Design:
1. **Mission Control** (Layer 6) — Daily briefing, alerts, push notifications
2. **Topology Graph** — Causal relationships, visual knowledge map
3. **Unified Data Engine** — Chat-based query interface, modular blocks
4. **Notes & Insights** — Proprietary intelligence capture (voice, text, links)
5. **Synthesis** — Bull/bear debates, thesis tracking, document generation

### Infrastructure: Hexagonal Architecture

```
Frontend (Next.js 15)
    ↕  [X-API-Version header + OpenAPI codegen]
Backend (FastAPI)
  ├── interfaces/     ← Abstract ports (LLMProvider, DBRepository, QuantRepository, GraphRepository)
  ├── adapters/       ← GeminiAdapter, PostgresAdapter, DuckDBAdapter, PineconeAdapter, Neo4jAdapter
  ├── services/       ← Business logic (imports ONLY interfaces)
  └── agents/         ← Router agent + executor registry
    ↕
Polyglot persistence:
  ├── PostgreSQL (fragments, recipes, users, metadata)
  ├── DuckDB on Parquet (OLAP/quant queries)
  ├── Pinecone (vector/semantic search)
  └── Neo4j (graph topology, causality, supply chains)
```

**Zero vendor lock-in:** Swap Gemini → Anthropic by writing one new adapter file. Services never import concrete implementations directly.

### Tech Stack
| Component | Technology |
|---|---|
| Backend | Python · FastAPI · Uvicorn |
| Frontend | Next.js 15 · React 19 · TypeScript |
| Styling | TailwindCSS (PostCSS) |
| State Management | Zustand (per-feature stores) |
| Charts | Recharts |
| Graph Visualization | ReactFlow |
| Quant DB | DuckDB querying Parquet files |
| Semantic/Qual DB | Pinecone (vector embeddings) |
| Relational DB | PostgreSQL (SQLAlchemy ORM) |
| Graph DB | Neo4j |
| LLM Orchestration | FastAPI + LangChain/LangGraph + Pydantic V2 |
| LLM Provider | Google Gemini (via swappable adapter) |
| Audio Capture | sounddevice (WASAPI) + ffmpeg + Whisper / Deepgram |
| Validation | Pydantic V2 (Rust-powered) |

### Frontend: Container/View Pattern

Every feature has three strict layers:
- `page.tsx` — route entry only (no logic)
- `FeatureContainer.tsx` — SMART: API calls, store reads/writes, error handling
- `FeatureView.tsx` — DUMB: pure JSX, only accepts props, zero imports from `lib/`

State is domain-segregated via Zustand (e.g., engine tab state lives in `engine/store.ts`, not global). A `mappers/` layer decouples backend JSON from component props. API contract enforced via `X-API-Version` header + OpenAPI codegen → `src/types/api.generated.ts`.

```
app/(dashboard)/
├── engine/
│   ├── page.tsx, EngineContainer.tsx, EngineView.tsx, store.ts
├── topology/
├── library/
├── synthesis/
└── monitors/

lib/
├── api/         ← domain-split API clients (chatClient, ledgerClient…)
└── mappers/     ← backend JSON → component props

components/
└── domain/
    ├── charts/  ← MetricChart (dumb; accepts {name, value}[] only)
    └── blocks/  ← AgentBlockRenderer, TextBlock
```

### Data Storage

| Data type | Store | Why |
|---|---|---|
| Quantitative (SEC metrics, OHLCV) | DuckDB on Parquet | OLAP speed; columnar; immutable |
| Qualitative (fragments, reports, transcripts) | Pinecone (vector) | Semantic search |
| Metadata, lineage, users, recipes | PostgreSQL (SQLAlchemy) | Relational; audit trails |
| Graph topology, causality, supply chains | Neo4j | Graph traversal; time-sliceable |

### Data Fragment — Core Unit of Knowledge

Every extracted piece of information is a `DataFragment`:
- `fragment_id` (UUID), `tenant_id`, `tenant_tier` (PUBLIC/PRIVATE)
- `source_type` (enum: PDF, TRANSCRIPT, MARKET_DATA, etc.)
- `source` (filename/URI), `exact_location` (page range: "pp. 1-3")
- `reason_for_extraction` — why the agent pulled this
- `content`: {raw_text, extracted_metrics}
- `lineage`: list of extraction recipe IDs (full audit trail)
- `content_fingerprint`: SHA-256 of `(tenant_id, source_doc_id, exact_location)` → deduplication

### Extraction Pipeline: 4 Parallel Modules

All run concurrently via `ThreadPoolExecutor(max_workers=4)`. Each module is a standalone `.py` file — isolated, editing one cannot break another. They share an `ExtractionContext` dataclass (shared state object, thread-isolated):

1. **Causal Relationship** — PDF → chunk (3-page windows) → LLM → (entity_A)-[:CAUSES]->(entity_B) → Neo4j edges
2. **Chart/Exhibit Vision** — PDF → detect exhibit pages (heuristics + labels) → render PNG (200 DPI) → vision LLM → fragments + images saved
3. **Company Business Intelligence** — PDF → identify primary + peer companies (LLM on first 3 pages) → extract per-company metrics → Neo4j edges (HAS_SEGMENT, HAS_PRODUCT, COMPARED_TO)
4. **Business Relationship** — PDF → chunk → LLM → typed edges (SUPPLIES_TO, CUSTOMER_OF, COMPETES_WITH, PARTNERS_WITH, MENTIONED_WITH)

Document metadata (title, author, date, `source_document_id` = UUID5 from filename) extracted once before the thread pool and passed read-only to all 4 threads.

### Agent Architecture

**Query Routing (2-layer):**
1. **UnifiedRouterAgent** — Takes user query → generates a `RoutingPlan` (Pydantic structured JSON). Plan ONLY, no execution.
2. **ExecutorRegistry** — Receives plan → dispatches to registered executors (`DuckDBExecutor`, `PineconeExecutor`, etc.) whose `can_handle()` returns True. Parallel where applicable.

**Ambient Agents:**
- **ThesisLedger** — Active long/short positions with defined catalysts
- Background agents evaluate new fragments against thesis catalysts
- WebSocket alerts to Mission Control when catalyst confirmed or broken

**Audio Transcription Pipeline:**
- Record system audio (sounddevice + WASAPI loopback)
- Convert WAV → OPUS (95% smaller via ffmpeg loudnorm)
- Transcribe: Local Whisper (free, auto-language detect) OR Deepgram (speaker diarization, $0.007/min)

### Metadata & Indexing

**Extraction Recipes (Versioned + Immutable):**
- `ingestor_type` — module identifier (CAUSAL_RELATIONSHIP, CHART_VISION, etc.)
- `llm_prompt_template` — instructions for LLM
- `expected_schema` — JSON Schema for structured output validation
- `version` — immutable; old fragments retain lineage via recipe ID

**Personalization: soul.md + Tiered Memory:**
- `soul.md` — fund type, time horizon, coverage universe, investment philosophy
- `long_term_memory.md` — updated monthly (patterns extracted from 50+ ratings)
- `weekly_memory.md` — updated weekly (what PM valued this week)
- `daily_memory.md` — updated real-time (session context)
- All injected into every LLM prompt → personalized responses

**Cold-Start Auto-Seeding:**
New user completes `soul.md` → system auto-seeds: 8 quarters of transcripts + 3 years OHLCV + company/sector stubs → "Your workspace is ready. 247 fragments loaded for 12 companies."

---

## 5. Architecture Decision Log

| Decision | Problem Solved |
|---|---|
| FastAPI over Express | Python ecosystem for LangChain, Pydantic V2 (Rust-backed validation), data science tooling |
| Hexagonal pattern | AlphaGraph had 20+ Gemini calls scattered across routes; impossible to swap providers |
| Container/View | View components in AlphaGraph imported API clients directly; untestable, tight coupling |
| ExtractionContext dataclass | Function-argument chains require signature changes across all modules to add a new field |
| Polyglot DB (4 stores) | No single DB handles OLAP + semantic search + relational + graph equally well |
| SHA-256 fingerprinting | Prevent duplicate fragments when the same doc is ingested twice |
| X-API-Version + OpenAPI codegen | Frontend/backend can drift silently — codegen enforces the contract at TypeScript compile time |
| Separate frontend/ and backend/ | Single repo in AlphaGraph made shipping independently impossible; coupled dev environments |

---

## 6. Feature Comparison

| Feature | AlphaGraph | AlphaGraph_updated | AlphaGraph_new |
|---|---|---|---|
| SEC Data Pipeline | Yes (EDGAR) | Yes (EDGAR) | Yes + versioned recipes |
| Interactive Data Explorer | Yes (DuckDB) | Yes (DuckDB) | Yes (chat-based + modular blocks) |
| Research Composition | Yes (drag-and-drop) | Yes (drag-and-drop) | Enhanced (synthesis tab) |
| AI Integration | Gemini (basic) | Gemini (basic) | Gemini (orchestrated, personalized) |
| Knowledge Graph | No | No | Neo4j (topology, causal chains) |
| Meeting Transcription | YouTube only | YouTube only | System audio + Whisper/Deepgram |
| Extraction Modules | 1 | 1 | 4 parallel |
| Feedback Loop / Memory | None | None | soul.md + tiered memory |
| Ambient Agents / Alerts | None | None | Thesis ledger + WebSocket alerts |
| Multi-Tenancy | No | No | Yes (tenant_id + tenant_tier) |
| Audit Trail / Lineage | None | None | Full (lineage field on every fragment) |
| Cold Start | No | No | Yes (auto-seed public data) |
| Deduplication | No | No | SHA-256 fingerprint at DB layer |
| Compliance Framework | No | No | Designed (MNPI flagging, Phase 2) |

---

## 7. Unresolved Design Challenges (AlphaGraph_new)

1. **Related-fragment linking logic** — How to cluster fragments: entity match + temporal proximity + semantic similarity. Cascaded retrieval proposed, not implemented.
2. **Team / RBAC** — Workspace model designed (Analyst/PM/Admin roles), not built.
3. **MNPI compliance** — Flagging + legal guardrails designed, not implemented.
4. **Confidence scoring** — Source tiers (OFFICIAL, INSTITUTIONAL, NEWS, ALTERNATIVE) designed but not rendered in UI.
5. **Notes tab (Tab 4)** — Audio capture pipeline exists, but full proprietary intelligence capture flow incomplete.

---

## 8. Phased Build Plan (AlphaGraph_new)

| Phase | Scope | Goal |
|---|---|---|
| Phase 1 | Layer 1 + 2 + Tab 3 search + Cold start | Working data ingestion; analyst can research |
| Phase 2 | Layer 3 (Insights) + preference store + Tab 1 alerts | Compounding knowledge loop begins |
| Phase 3 | Layer 4 (Wiki) + Tab 2 graph + agent event bus | Knowledge differentiation visible |
| Phase 4 | Layer 5 (Output gen) + teams + confidence scoring | Enterprise sales-ready |

Key principle: "The output is only as good as the fragments underneath it." Build output generation (Layer 5) only after Layer 2 is solid.
