# Financial Research Platform: Architecture & Technical Design

## System Role & Objective
This document outlines the architecture for an institutional-grade, AI-driven Financial Research Platform designed for long/short portfolio managers. 
The system must be highly scalable, handle 500+ concurrent users efficiently, and feature strict decoupled separation between the frontend and backend to ensure maximum modularity and zero vendor lock-in.

---

## 1. Core Tech Stack & Polyglot Persistence
The platform utilizes a "right tool for the job" database strategy, prioritizing performance and specific data shapes.

*   **Backend Framework:** Python with FastAPI.
*   **Structured Quant Data (Layer 1):** DuckDB querying local `.parquet` files. Optimized for high-concurrency math and historical financial metrics without locking.
*   **Unstructured Qual Data (Layer 1):** Pinecone (VectorDB) for semantic search of broker reports, notes, and transcripts.
*   **Relational & State Data:** PostgreSQL. Manages user metadata, active ledgers, extraction recipes, and enforces strict public/private `tenant_id` separation.
*   **Topology Mapping:** Neo4j (GraphDB) maps relationships between companies, sectors, topics, and data lineage.
*   **LLM Orchestration:** LangChain / LangGraph for agentic routing.
*   **Frontend:** Next.js (React), Tailwind CSS, and Zustand for state management.

---

## 2. Strict Modularity & Decoupling Constraints

### Backend: Ports and Adapters (Hexagonal Architecture)
*   **The Rule:** Business logic (`services/` and `agents/`) must NEVER import concrete implementations like `duckdb`, `openai`, or `pinecone` directly.
*   **The Implementation:** We utilize an `interfaces/` directory containing Abstract Base Classes (Ports). Concrete implementations reside in `adapters/`. Switching the LLM from OpenAI to Gemini requires only a new adapter file and a configuration change. Dependency Injection wires the correct adapter at runtime.

### Frontend: Strict Container / Presenter Pattern
*   **The Rule:** UI components (e.g., charts, blocks) must be 100% "dumb" presentational modules. They cannot import API clients or global state stores.
*   **The Implementation:** Components accept generic React props (e.g., `data: { x: string, y: number }[]`). A `mappers/` layer transforms complex backend responses into these generic props. Next.js Pages act as "Smart Containers" that fetch data, run mappers, and pass props down.
*   **Independence:** State and hooks are organized by domain features. Modifying one tab (e.g., the Topology Tab) cannot break another (e.g., the Data Engine Tab) due to shared state.

---

## 3. The Data Model: The Hybrid "Firewall" Strategy
The platform utilizes a hybrid approach to balance strict type safety with dynamic user flexibility.

### 3.1 Pydantic V2 (The Core Firewall)
*   **The Rule:** All core domain models (`Data_Fragment`, `Tenant`, `Position`) must be implemented using **Pydantic V2**.
*   **Performance:** Pydantic V2 is mandatory. Its core validation engine is written in **Rust**, providing the high throughput necessary for processing massive amounts of financial data during high-volatility periods like earnings season.
*   **The Contract:** The `Data_Fragment` acts as the ironclad contract. Every piece of data entering the system *must* pass through this Rust-powered validation layer.

### 3.2 JSON Schema (The Dynamic Logic)
*   **The Rule:** User-defined extraction logic within `Extraction_Recipes` is stored as **JSON Schema**.
*   **Why:** This allows users to define custom output shapes (e.g., "Extract `CEO_Sentiment` as a boolean") without writing or executing Python code.
*   **LLM Integration:** JSON Schemas are passed directly to the LLM (OpenAI/Gemini) to enforce "Structured Output" formats.
*   **Validation Flow:** LLM output -> JSON Schema Validation -> Pydantic V2 `Data_Fragment` injection.

---

## 4. The Extraction Pipeline: "Extraction Recipes"
To allow users to create, share, compare, and modify extraction logic without breaking the system, we implement a Declarative "Extraction Recipe" Engine (Pipeline-as-Data).

*   **Concept:** Extraction logic is stored as structured JSON objects (`Extraction_Recipes`) in PostgreSQL.
*   **Components of a Recipe:**
    *   Ingestor Type (e.g., SEC_XBRL_Parser, PDF_OCR_Loader).
    *   Chunking Strategy (e.g., Semantic_Sentences, Table_Row_By_Row).
    *   LLM Prompt Template ("Extract forward-looking revenue guidance...").
    *   **Output Schema (JSON Schema):** Defines the exact structure of metrics the user wants the LLM to return.
*   **Sharing & Comparing:** Users can clone recipes. The frontend compares logic by performing a JSON Diff on the recipe schemas.
*   **The Firewall:** An `ExtractionRunner` service executes the recipe. Regardless of the custom logic, the output *must* successfully instantiate into the strict `Data_Fragment` Pydantic V2 model. Validation errors are caught gracefully.
*   **Immutability:** Recipes are versioned (Event Sourcing). Old data retains the lineage of the specific recipe version used to extract it.

---

## 5. Agentic Routing & The Ledger

*   **Query Router:** A LangChain agent intercepts user queries in the "Unified Data Engine". It routes math/quant questions to DuckDB and text/sentiment questions to Pinecone, synthesizing the results.
*   **Thesis Ledger (`THESIS.md`):** The system maintains active Long/Short positions. Ambient background agents continuously evaluate new `Data_Fragments` against this ledger. If a catalyst breaks or a thesis is challenged, it pushes a WebSocket alert to the UI.

---

## 6. Frontend Architecture (The 5 Tabs)
The Next.js UI is modular, highly fluid, and divided into five distinct workspaces:

1.  **Topology Tab:** A visual node-based graph (React Flow / Vis.js) rendering Neo4j relationships (companies, topics, data lineage).
2.  **The Unified Data Engine Tab:** A central chat interface and modular dashboard. Users ask natural language questions. The backend routes queries and returns modular visual blocks (charts, summaries) that users drag and drop onto the canvas.
3.  **Notes & Insights Tab (The Library):** A structured repository for user notes, raw broker reports, and previously generated AI insights. The inventory manager for unstructured proprietary data.
4.  **Synthesis Tab:** A workspace to combine saved fragments (Tab 2) and notes (Tab 3) into living research documents (Bull/Bear debates, Thesis trackers). Deeply integrated with the `THESIS.md` active ledger.
5.  **Monitors Tab:** A customizable dashboard tracking fundamental metrics, textual alerts from background agents, and real-time WebSocket catalyst pings.

---

## 7. Directory Structure (Strict Decoupling)

### Backend (Python / FastAPI)
```text
backend/
├── app/
│   ├── api/
│   │   ├── dependencies.py        # Dependency Injection (injects Adapters into Services)
│   │   └── routers/               # API Endpoints (v1/chat, v1/ingest, etc.)
│   ├── core/
│   │   ├── config.py              # Single source of truth for active adapters (e.g., ACTIVE_LLM="gemini")
│   │   └── exceptions.py
│   ├── interfaces/                # THE PORTS (Abstract Base Classes - NO implementation details)
│   │   ├── db_repository.py       # abstract methods: get_fragment, save_metrics
│   │   ├── llm_provider.py        # abstract methods: generate_response, embed_text
│   │   └── graph_repository.py    # abstract methods: get_neighbors
│   ├── adapters/                  # THE ADAPTERS (Concrete implementations)
│   │   ├── db/
│   │   │   ├── duckdb_adapter.py  
│   │   │   └── postgres_adapter.py
│   │   ├── llm/
│   │   │   ├── openai_adapter.py  
│   │   │   └── gemini_adapter.py  
│   │   ├── vector/
│   │   │   └── pinecone_adapter.py
│   │   └── graph/
│   │       └── neo4j_adapter.py   # Neo4j GraphDB adapter
│   ├── models/                    # Pydantic Schemas & Domain Models (Pure Python)
│   │   ├── domain/
│   │   │   ├── data_fragment.py       # THE FIREWALL: Strict output contract
│   │   │   └── extraction_recipe.py   # Declarative JSON schema for logic
│   │   └── api_contracts.py
│   ├── services/                  # Business Logic (Imports ONLY from /interfaces and /models)
│   │   ├── extraction_engine/         # Generic recipe runner
│   │   │   ├── runner.py              
│   │   │   ├── loaders/               
│   │   │   └── validators.py          
│   │   ├── ledger_service.py
│   │   └── alert_service.py
│   └── agents/                    # LangChain workflows (Uses generic llm_provider.py)
│       ├── router_agent.py
│       └── workflows/
├── data/                          # LOCAL DATA LAKE (Ignored in git)
│   └── parquet/                   # Layer 1 Structured Quant Data (.parquet files)
├── tests/
├── main.py
└── requirements.txt
```

### Frontend (Next.js / React)
```text
frontend/
├── src/
│   ├── app/                       # SMART CONTAINERS (Next.js App Router)
│   │   ├── (auth)/
│   │   ├── (dashboard)/
│   │   │   ├── engine/page.tsx    # Fetches data -> calls mapper -> passes props to dumb components
│   │   │   ├── topology/page.tsx
│   │   │   ├── library/page.tsx
│   │   │   ├── synthesis/page.tsx
│   │   │   └── monitors/page.tsx
│   ├── components/                # DUMB PRESENTATIONAL COMPONENTS
│   │   ├── ui/                    # Base components (Buttons, Inputs - Tailwind)
│   │   └── domain/                
│   │       ├── charts/            # Generic charts (Accepts {x, y} arrays, unaware of backend)
│   │       ├── blocks/            # Generic visual blocks (Accepts title, content, color)
│   │       └── graph/
│   ├── lib/
│   │   ├── api.ts                 # Axios / Fetch clients
│   │   ├── ws.ts                  # WebSocket manager
│   │   └── mappers/               # CRITICAL: Transforms API JSON into generic component props
│   │       ├── mapFragmentToChart.ts
│   │       └── mapGraphToNodes.ts
│   ├── store/                     # Domain-segregated Zustand stores
│   │   ├── engineStore.ts         # State ONLY for Tab 2
│   │   └── ledgerStore.ts         # State ONLY for Tab 4/5
│   └── types/
│       ├── api.d.ts               # Strict backend JSON contracts
│       ├── models.d.ts
│       └── ui.d.ts                # Generic component prop definitions
├── public/
├── tailwind.config.ts
├── tsconfig.json
└── package.json
```