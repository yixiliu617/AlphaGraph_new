# AlphaGraph — Product Design v1

*Last updated: 2026-04-26 (sections 14-17 appended; revision history at section 17.5)*

> **For agents reading this cold:** §1-13 describe the *vision and design*. §14-16 describe the *current actual state of implementation* and the multi-region scaling roadmap toward 2000 covered companies + multi-user agentic operation.

---

## Executive Summary

AlphaGraph is a 6-layer AI financial research platform that accumulates knowledge over time — turning every document ingested, every insight approved, and every output generated into a compounding personal knowledge base for investment professionals.

**Core moat:** Unlike Bloomberg (pull only), AlphaSense (search only), or ChatGPT (stateless), AlphaGraph learns from you. After 2 years of use, it thinks like you.

---

## 1. The 6-Layer Architecture

```
Layer 1: Data Ingestion
  1.1 Market data (OHLCV, options)
  1.2 Structured numbers (SEC/EDGAR, macro data)
  1.3 Text & documents (broker reports, transcripts, press releases)
  1.4 Proprietary intelligence (PM's own notes, channel checks, theses)  ← ELEVATED
  1.5 Alternative / scraped (web scraping, social, satellite)

Layer 2: Data Fragments
  2.1 Time-stamped (event time + record time + knowledge time)
  2.2 Source-tiered and confidence-scored
  2.3 Customizable extraction pipelines (recipe engine)
  2.4 Optimized storage (Postgres + Pinecone + DuckDB)

Layer 3: Insights
  3.1 Customizable combination pipelines (related fragments → insight)
  3.2 "Related" defined by: entity match + temporal proximity + semantic similarity
  3.3 Feedback loop: approve / improve / reject → preference store → few-shot injection

Layer 4: Wiki (Knowledge Base)
  4.1 Topic-based (company, sector, theme, factor, person)
  4.2 Temporal structure (periodized business history)
  4.3 Auto-update proposals when linked insights change
  4.4 Cross-linked graph of wiki pages (topology view)
  4.5 Feedback loop: same as Layer 3

Layer 5: Output Generation
  5.1 Grand agent: clarify intent → identify sources → confirm → generate
  5.2 Formats: PDF, PPT, HTML, structured memo
  5.3 Sources: fragments + insights + wikis + prior approved outputs
  5.4 Feedback loop: approve / edit → adds to approved output corpus

Layer 6: Intelligence Delivery  ← THE ORIGINALLY MISSING LAYER
  6.1 Daily briefing: overnight events ranked by thesis impact
  6.2 Catalyst monitoring: ambient agents watching active thesis positions
  6.3 Anomaly detection: metric deviations, thesis challenges
  6.4 Topology alerts: new causal relationships detected
  6.5 Weekly synthesis: "what changed" memo across coverage universe
```

**Cross-cutting concerns (not a layer, but essential):**
- Agent DAG / event bus: fragment creation → triggers downstream agents
- Search & browse workspace: daily query interface across all layers
- Compliance & audit: source attribution, MNPI guardrails, data lineage
- Collaboration: workspace/team model, roles, shared vs private

---

## 2. Competitor Landscape

### Tier 1 — Institutional Terminals

| Tool | Annual Cost | Core Weakness | How AlphaGraph Wins |
|---|---|---|---|
| Bloomberg Terminal | $24K/seat | 1980s UX, zero personalization, no learning | Bloomberg knows nothing about you after 10 years. AlphaGraph knows everything. |
| FactSet | $15K/seat | Rigid taxonomy (their way or no way), AI bolted on | AlphaGraph lets you define your own extraction schema |
| AlphaSense | $50K+ | Search tool only. Nothing accumulates. | AlphaSense is a library card. AlphaGraph is the library itself. |
| Tegus / Caplight | $20K+ | Content platform, not a research platform | No knowledge builder, no output generation |
| Sentieo / Visible Alpha | $10K+ | No feedback loop, no wiki, no output | Same — consumption only |

### Tier 2 — AI-Native Research Tools

| Tool | Core Weakness |
|---|---|
| Perplexity Finance / ChatGPT | Stateless, generic, hallucination risk on numbers |
| Minerva / Kensho | Quant-only, no qualitative layer |
| Notion AI + templates | No financial data, fully manual, no agents |

### Tier 3 — PKM Tools (indirect competition)

| Tool | Core Weakness |
|---|---|
| Obsidian | Fully manual, no financial data, no agents (see Section 3) |
| Roam Research | Developer-skewed, niche |
| Logseq | Open-source, no finance integration |

---

## 3. Comparison: Obsidian

### What Obsidian Does Brilliantly
- **Bidirectional linking**: Every note links to every other. Graph view shows connections you didn't know existed.
- **Longevity**: Plain Markdown on disk. You own your data forever.
- **Plugin ecosystem**: 1,000+ community plugins.
- **Zero latency**: Everything local. Search is instant.
- **Serendipitous discovery**: Graph view surfaces non-obvious connections.

### Where Obsidian Falls Short for Finance
- **Fully manual**: A PM tracking 50 companies can't manually maintain 50 company wikis.
- **No financial data**: No SEC filings, no price data, no earnings alerts.
- **No agents**: Can't monitor the world for you. Can't alert you when a catalyst triggers.
- **No temporal intelligence**: No event time vs. record time.
- **No feedback loop**: No concept of "this note was useful, learn from it."
- **No output generation**: Can't generate a PDF research report from notes.

### The Obsidian Lesson for AlphaGraph
The graph view changes how you think. When researchers see their knowledge as a network rather than folders, they discover connections they would never find with search alone.

**AlphaGraph's topology layer (Neo4j) is exactly this** — but it needs to be surfaced visually and prominently, not buried. The wiki's cross-linking should have a graph visualization mode: click on NVDA's wiki, see it connected to TSM (supplier), CUDA ecosystem companies, AI infrastructure theme, Jensen Huang's management page, and the Semiconductor sector wiki.

---

## 4. Comparison: Karpathy's Personal AI Concept

### Karpathy's Core Insight
The value of an AI assistant is proportional to how much it understands your specific context. A generic LLM knows everything about the world but nothing about you. A personalized one knows everything about how *you* think.

His approach: ingest everything about you (emails, papers, code, talks, notes) → learn your mental model → surface non-obvious connections across your entire knowledge history.

### How AlphaGraph Aligns
- ✅ The feedback loop (layers 3, 4, 5) — learning what *this specific user* values
- ✅ The wiki building is personalized — NVDA's wiki reflects *your* investment thesis
- ✅ Output generation uses *your* approved fragments, not generic web data
- ✅ The more you use it, the better it gets (compounding knowledge effect)

### Where AlphaGraph Falls Short of Karpathy's Vision
- ❌ **The user's own thinking is missing**: Where do the PM's qualitative observations go? Their handwritten thesis? Call notes from a company meeting?
- ❌ **Learning mechanism underspecified**: "Scalable method to keep record of user's feedback" mentioned 3 times but never designed.
- ❌ **Serendipitous discovery**: The system is mostly pull-based. Where is the "you might not have realized NVDA's margin trajectory looks exactly like Cisco in 2000" moment?

---

## 5. Strengths

**S1 — Time-awareness is a genuine differentiator**
Event time vs. record time for qualitative data. Bloomberg has it for price data. Nobody has it for qualitative data. This is novel for auditing investment decisions.

**S2 — The feedback loop philosophy is right**
Generate → learn → improve. Most tools generate and forget. This is the compounding moat.

**S3 — The wiki's temporal structure matches how finance actually works**
The NVDA wiki structure (startup era → CUDA era → Bitcoin era → GenAI era) reflects how fundamental analysts actually think. No existing tool structures company knowledge this way.

**S4 — Pull + Push + Generate covers the full workflow**
Bloomberg is pull. Email newsletters are push. ChatGPT is generate. Combining all three in one platform that accumulates knowledge is complete, not a point solution.

**S5 — Customizable pipelines at every layer**
Not forcing a rigid taxonomy. The recipe-based extraction (Layer 2) already implemented is a strong foundation.

**S6 — The output layer's agent clarification loop is sophisticated**
"Grand agent asks clarifying questions, tells the user what data is available, gets confirmation, then generates" — the right UX for high-stakes outputs. The explicit "here's what I have, shall I proceed?" step builds trust.

---

## 6. Weaknesses

| ID | Weakness | Priority | Status |
|---|---|---|---|
| W1 | Layer 6 unnamed and underdesigned | 🔴 Critical | ✅ Resolved: Tab 1 Mission Control IS Layer 6 |
| W2 | Feedback learning mechanism unspecified | 🔴 Critical | ✅ Resolved: soul.md + tiered memory architecture (see Section 7) |
| W3 | User's own voice missing from data layer | 🔴 High | ✅ Resolved: Tab 5 multi-channel input (notes, voice, channel checks) |
| W4 | Cold start problem unaddressed | 🔴 High | 🟡 Partial: Onboarding soul.md captures coverage universe; auto-seed needs design |
| W5 | "Related fragments" logic underspecified | 🟡 Medium | ❌ Backend design needed (see Section 8) |
| W6 | No collaboration or team layer | 🟡 Medium | ❌ Not yet designed |
| W7 | Data sourcing legal/compliance risks | 🟡 Medium | ❌ Not yet designed |
| W8 | No confidence scoring or source tiers | 🟡 Medium | 🟡 Partial: Could be node colors in Tab 2, source badges in Tab 3 |
| W9 | Agent coordination architecture missing | 🟠 Lower | ✅ Resolved: Tab 4 is control plane, Tab 1 is output surface; executor registry is seed |
| W10 | Search and query interface missing | 🔴 High | ✅ Resolved: Tab 3 (workspace) + Tab 2 (graph browse) + Tab 5 (library); global ⌘K still missing |

---

## 7. Feedback Learning Architecture

### Industry Context (2025 Best Practice)
The industry has moved away from expensive fine-tuning toward **structured persistent memory + RAG from personal corpus**. Faster to update, cheaper to run, no retraining required.

### All Approaches Evaluated

| Approach | Verdict |
|---|---|
| Simple rating database | Foundation step only. Necessary but not sufficient. |
| Few-shot preference retrieval (RAG) | Right first layer. Essential. Cold start weakness. |
| soul.md + tiered memory.md | **Selected as spine.** Most powerful and transparent for institutional finance. |
| Preference extraction (constitutional rules) | Phase 2 addition after 50+ ratings. |
| Fine-tuning / LoRA | Wrong tool. $100–1000+ per run. Memory + RAG achieves 80% personalization at 1% cost. |
| DPO (Direct Preference Optimization) | Right for global model improvement, wrong for per-user personalization. |
| MemGPT self-managed memory | Lacks transparency required for institutional trust. |

### Chosen Architecture: 4-Layer Hybrid

```
Layer A: soul.md — Investment Philosophy Profile
         (Onboarding form → user-editable → rarely changes)
         "Who is this person and how do they think?"
                    ↓ read at session start

Layer B: Tiered memory.md — Episodic Memory
         long_term_memory.md (updated monthly by agent)
         weekly_memory.md   (updated every Sunday by agent)
         daily_memory.md    (updated real-time during session)
         "What has this person valued over time?"
                    ↓ injected into every prompt

Layer C: Few-shot Preference Store (RAG via Pinecone)
         Approved outputs → embedded → retrieved by similarity
         "Show me concrete examples of what this person liked"
                    ↓ top-3 similar past outputs as examples

Layer D: Preference Extraction Agent (Phase 2)
         Pattern analysis of 50+ ratings → explicit rules
         → written back into long_term_memory.md
         "What rules can we extract from the pattern?"
```

### soul.md Structure

```markdown
# Investment Philosophy Profile

## Fund Type & Style
[long/short equity, macro, quant-fundamental, etc.]

## Time Horizon
[short-term catalyst, medium-term (1–2yr), long-term (3yr+)]

## Analytical Edge
[what I believe I do better than consensus]

## Coverage Universe
[explicit list of tickers + sectors]

## Source Hierarchy
[SEC filings > institutional research > news > alternative]

## Output Preferences
[concise memos, detailed reports, bullet points, etc.]

## Known Biases to Correct For
[e.g., "I tend to be too early on inflection calls"]
```

### Implementation Sequence

| Phase | What to build | When |
|---|---|---|
| Now | soul.md onboarding form + rating database | Day 1 |
| Month 1 | Few-shot preference store + daily_memory.md | After first users |
| Month 3 | Weekly memory agent + long_term_memory.md | After 30 days usage |
| Month 6 | Preference extraction agent | After 50+ ratings per user |
| Future | Optional LoRA fine-tuning for power users | Only if users demand |

---

## 8. W5: Related Fragments Logic (Unresolved)

The insight layer (Layer 3) must define what makes two fragments "related." Getting this wrong means insights that miss important connections or make spurious ones.

### Three Axes of Relatedness

**Axis 1: Entity Match** (structured, low cost)
- Same ticker symbol, company name, ISIN
- Same sector / subsector classification
- Same supply chain tier (via Neo4j traversal)
- Implementation: filter on `tenant_id` + `entity_tag` in Postgres

**Axis 2: Temporal Proximity** (structured, low cost)
- Fragments from overlapping time windows (event time ± 90 days)
- Fragments citing the same earnings period / fiscal quarter
- Implementation: range query on `event_timestamp` in Postgres/DuckDB

**Axis 3: Semantic Similarity** (vector, medium cost)
- Embedding cosine similarity above threshold (e.g., 0.75)
- Retrieved via Pinecone vector search
- Implementation: `vector_db.query_vectors(embedding, top_k=20, filter={"tenant_id": ...})`

### Recommended Approach: Cascaded Retrieval

```
Step 1: Entity filter    → candidate set (fast, Postgres)
Step 2: Time window      → narrow to relevant period (fast, DuckDB)
Step 3: Semantic search  → final ranked list (Pinecone)
Step 4: Re-rank          → LLM scores final top-N for insight coherence
```

This prevents semantic drift (fragments that sound related but are about different companies) and temporal confusion (fragments from different market regimes being mixed).

---

## 9. W6: Team/Collaboration Design (Future)

Design for teams from the start even if not built first:

**Data model additions:**
- `workspace_id` at the data model level (above `tenant_id`)
- `created_by_user_id` on every fragment, insight, wiki, output

**Roles:**
- `Analyst`: can create fragments, insights, draft wikis
- `PM`: can approve insights, publish wiki, commission outputs
- `Admin`: manages workspace membership and permissions

**Sharing model:**
- Private fragments: each user's channel checks stay private unless promoted
- Shared wikis: visible to the whole team once PM approves
- Attribution: every fragment and insight shows who created it

**Tab 4 enhancement:** Agent sharing — PM can publish a well-designed agent for the rest of the team to clone.

---

## 10. W7: Compliance Framework (Future)

For institutional use, data sourcing needs:

**MNPI guardrails:**
- Explicit user attestation when ingesting potentially material non-public information
- `is_potentially_mnpi: bool` flag on every fragment, visible in UI
- Fragments flagged MNPI excluded from shared workspaces by default

**Data lineage:**
- Every fragment carries unbreakable source attribution
- Chain: raw document → extraction recipe → fragment → insight → output
- Audit trail exportable as CSV for compliance review

**Source compliance tiers:**
- `OFFICIAL`: SEC/regulatory filings — unrestricted
- `INSTITUTIONAL`: Licensed broker research — terms apply
- `NEWS`: Public news — scraping ToS must be reviewed
- `ALTERNATIVE`: Web scraping, social — requires explicit legal review per source

---

## 11. Frontend UI Architecture: 5-Tab Design

### Tab 1: Mission Control (= Layer 6)

**Role:** Daily interaction surface. Morning briefing. Push, not pull.

**Pros:**
- Pin/Dismiss triage feed matches real PM morning workflow — an action surface, not a passive dashboard
- 1/3 + 2/3 split (raw feed → expanded action board) is the right information hierarchy
- [Open in Workspace] bridges passive consumption (Tab 1) → active research (Tab 3)
- Market Monitor filtered to user's Universe solves Bloomberg's "too much noise" problem
- System Health & Divergences (sentiment delta vs. street, information velocity) is genuinely novel

**Cons / Gaps:**
- Earnings season firehose (100+ alerts) — needs aggressive grouping ("12 alerts from Semi Tracker — expand")
- No archive/history — can't go back to yesterday's briefing
- No search within the feed
- No notification badge on Tab 1 in nav (users won't know to check it)

---

### Tab 2: Topology Graph

**Role:** Causal relationship visualization. The Obsidian moment for finance.

**Pros:**
- Live node metrics (Rev YoY%, margin delta, sentiment color) on graph nodes — no competitor has this
- Time Slider to rewind network state — watch semiconductor supply chain reconfigure 2021→2024
- Node Inspector drawer with jump-links to Tab 3/4 — coherent cross-tab navigation
- Related node highlighting on click — serendipitous discovery

**Cons / Gaps:**
- Performance: 50+ live metric nodes simultaneously — needs lazy loading, level-of-detail rendering
- Graph clutter: needs "focus mode" (1–2 hops from selected node) as default
- No graph search box ("show me where TSMC is")
- No manual relationship creation by user
- No export/share for the graph state

---

### Tab 3: Unified Data Engine (The Lens)

**Role:** Primary daily workspace. Entity-level research. The Bloomberg killer.

**Pros:**
- Sub-tab ribbon with last 3 cached entities + auto-pinned adjacent sector/theme mirrors analyst multitasking
- Roll-Up view for sectors (bottom-up drivers heatmap) — track inventory days across all 15 semi companies simultaneously
- Three display levels (Company / Sector / Theme) covers the full granularity hierarchy
- Agent Banner for active monitors — surfaced without cluttering the main view
- Drag-and-drop modules + save as "Lens" template — powerful for repeatable workflows

**Cons / Gaps:**
- Cognitive load risk — this tab does the most work; needs strong visual hierarchy and collapsible sections
- Missing: persistent search/jump bar (how do I jump from NVDA to AAPL?)
- Missing: drill-down path from wiki claim → fragment → source document
- Edit mode vs. view mode distinction needed for drag-and-drop

---

### Tab 4: Automation Engine (Agent Factory)

**Role:** No-code agent creation and management. The control plane for Layer 6.

**Pros:**
- Plain-English agent creation is the right abstraction level for non-technical PMs
- Kanban/list view with health status is clear

**Cons / Gaps:**
- Missing: token/cost usage per agent
- Missing: agent run history and audit log
- Missing: agent testing mode ("run once on sample data before deploying live")
- Missing: agent versioning (rollback if I edit an agent)
- Health status too coarse — needs: Running / Paused / Error (reason) / Last triggered / Findings rate
- Missing: agent sharing for teams

---

### Tab 5: Notes & Insights (The Library)

**Role:** Proprietary intelligence capture. The user's own voice in the system.

**Pros:**
- Multi-channel input (rich text, link parsing, voice memos, API sync) directly addresses W3
- Voice memos are underrated in finance — analysts capture insight in the car after a field visit
- OneNote/Evernote API sync meets users where they already are

**Cons / Gaps:**
- Underspecified — no taxonomy = junk drawer; needs: browse by entity / date / source type / tag
- Missing: explicit link from note → entity wiki in Tab 3
- Missing: search within the library
- Missing: structured channel check forms (vs. free-text note)
- OneNote/Evernote sync is complex; deprioritize vs. native note experience first
- Missing: Tab 5 → Tab 3 bridge ("attach this note to NVDA's wiki as supporting evidence")

---

### Onboarding (soul.md)

**Pros:**
- User-friendly question format (fund type, generalist vs. specialist, sector focus) is exactly right
- Explicit "you can always view, edit, update" builds trust and transparency

**Cons / Gaps:**
- Missing: specific coverage universe (list of tickers) — key input for Market Monitor + cold start seeding
- Missing: immediate "aha moment" post-onboarding — auto-seed first 10 company stubs within 60 seconds
- soul.md captures philosophy but not the other memory tiers — needs viewer/editor in UI (suggest: Tab 5 "System Memory" sub-tab)

---

### Cross-Tab Navigation

**Well-designed bridges:**
- Tab 1 → Tab 3: [Open in Workspace] button ✅
- Tab 3 → Tab 4: Agent Banner → Peek Modal ✅
- Tab 2 → Tab 3: Node Inspector jump-links ✅

**Missing bridges:**
- Tab 3 → Tab 5: "I'm researching NVDA, capture a note" — no direct path
- Tab 5 → Tab 3: "I wrote a note, see it in the wiki" — no direct path
- Tab 5 → Tab 1: "I uploaded a document, trigger an agent" — no direct path

**Missing global UI elements:**
- ⌘K global search across all fragments/insights/wikis/outputs (from any tab)
- Notification badge on Tab 1 nav item
- soul.md / memory.md viewer/editor (Tab 5 "System Memory" sub-tab or Settings)
- Persistent "current entity context" indicator across tabs

---

## 12. Cold Start Solution Design

**Trigger:** User completes soul.md onboarding (coverage universe captured)

**Auto-seed sequence (runs within 60 seconds):**
1. Parse coverage universe (tickers + sectors) from soul.md
2. Fetch last 8 quarters of public earnings transcript fragments (SEC EDGAR)
3. Fetch last 3 years of OHLCV + key financial metrics (public data)
4. Generate basic company wiki stubs (auto-generated from SEC filings)
5. Generate sector wiki stubs (auto-generated from public industry classification)
6. Display: "Your workspace is ready. 247 fragments loaded for 12 companies."

**Result:** User gets immediate value on Day 1. Private content enriches it over time.

---

## 13. Phased Build Plan (vision-level — see §14-16 for current implementation status)

| Phase | What to Build | Key Deliverable |
|---|---|---|
| Phase 1 | Layer 1 + Layer 2 + Tab 3 search + Cold start seeding | Working data ingestion; analyst can research entities |
| Phase 2 | Layer 3 (Insights) + Feedback preference store + Tab 1 (Layer 6 alerts) | Compounding knowledge loop begins |
| Phase 3 | Layer 4 (Wiki) + Tab 2 graph visualization + Agent event bus | Knowledge base differentiation visible |
| Phase 4 | Layer 5 (Output generation) + Team collaboration + Confidence scoring | Enterprise sales-ready |

**The common mistake:** Building Layer 5 (output) before having enough Layer 2 data quality. The output is only as good as the fragments underneath it.

> The phase numbers above refer to product-layer maturity. A separate **infrastructure/scaling** phasing — also numbered 1-4, but tracking a different axis — is documented in `architecture_and_design_v2.md` §13. The two phasings are orthogonal: a Layer 1+2 system can run on Phase-1 infra (today) or Phase-2 infra (DuckDB + Postgres + Redis) regardless of which product layers are built on top.

---

## 14. Key Design Principles

1. **Accumulate, don't just answer**: Every interaction should make the system smarter, not just return a response.
2. **Trust through transparency**: Institutional users need to see their sources. Never hide where data came from.
3. **Time is first-class**: Event time, record time, and knowledge time are all different and all matter.
4. **The user's voice is the primary data source**: Their channel checks, theses, and observations are what makes their knowledge base unique.
5. **Cold start is Day 1 revenue**: If the system isn't useful until Year 2, there is no Year 2.
6. **Obsidian's graph lesson**: When users see their knowledge as a network, they discover connections they would never find with search alone.

---

## 15. Multi-Region Coverage Strategy

*Goal: 2000 covered companies — 500 each across US, Taiwan, Japan, China — within ~12 months. Today: ~18 (15 US-EDGAR + 3 Taiwan).*

### 15.1 Current footprint (2026-04-26)

| Region | Source | Tickers covered | Coverage depth | Status |
|---|---|---|---|---|
| **US** | SEC EDGAR (XBRL + 8-K) | ~15 (NVDA, AAPL, AMD, AMAT, AVGO, CDNS, DELL, INTC, KLAC, LITE, LRCX, MRVL, MU, ORCL, …) | 8-Q income statements, balance sheet, cash flow, earnings releases | Live; runs through `data.py` + `earnings.py` routers. EDGAR backfill skill: `.claude/skills/edgar-topline-extraction/`. |
| **Taiwan** | TSMC IR (Cloudflare-bypassed) + UMC IR + MediaTek IR + MOPS monthly revenue | 3 fully extracted (2330 / 2303 / 2454) plus 51-ticker monthly-revenue universe | Per-company quarterly P&L, segment, capacity, guidance vs actual; transcripts (TSMC); monthly revenue (51 tickers) | Live. Per-company panels live in DataExplorer. |
| **Japan** | TDnet + EDINET (planned) | 0 | — | TODO. Largest issuers publish English XBRL; mid-caps Japanese-only. Translation step required. |
| **China** | HKEX + SSE / SZSE (planned) | 0 | — | TODO. Mainland sites may need proxy / VPN access. Legal due-diligence required before scaling scraping. |

### 15.2 The per-company idiosyncrasy budget problem

Every company we deeply cover has eaten ~5-10 person-days of focused engineering: site recon, PDF layout reverse-engineering, period-detection traps, era-specific layout shifts, post-extraction integrity checks, then frontend wiring. Receipts:

- **TSMC (2330.TW)** — Cloudflare bypass via `page.evaluate(fetch)` inside Playwright; Workiva PDF text quirks; 4-era layout drift; 8678 silver rows. Skill: `.claude/skills/tsmc-quarterly-reports/`.
- **UMC (2303.TW)** — 8" → 12" wafer-equivalent unit shift in 2024; multi-line period-header trap in pre-2022 segment tables; verbal vs structured guidance parsing; per-FAB capacity table (deferred). Memory: `project_taiwan_ir_extraction_umc.md`.
- **MediaTek (2454.TW)** — hybrid prose + table press release; `(Note2)` period-suffix trap in 2019 reports; HubSpot-hosted index. Memory: `project_taiwan_ir_extraction_mediatek.md`.

**At 2000 companies × 1 week each = 40 person-years.** Unbuildable as a hand-rolled effort. Strategy:

1. **~150 elite companies** (top market cap per region, top analyst-relevance) get hand-tuned extractors. These are the names users actually ask about. Each gets the per-company `SKILL.md` + memory note pattern that already works. 150 × 1 week = ~3 person-years — doable across a small team.

2. **~1850 long-tail companies** use a **generic LLM-assisted extractor**. Pipeline:
   - PDF → text (PyMuPDF) → LLM extraction prompt with company-specific JSON schema → structured output
   - Cross-checked by a deterministic data-quality pass (`.claude/skills/data-quality-invariants/`): identity equations (gross + cogs = revenue), magnitude reasonableness, period consistency
   - Anything failing identity checks gets flagged with `confidence < 0.8` and excluded from default panels
   - Headline P&L: ~70-80% accuracy expected. Segment / guidance: lower; opt-in display only.

3. **Manual upgrades** — when usage data shows 5+ users hitting a long-tail company in a week, promote it to the hand-tuned tier.

### 15.3 Regional rollout sequence

The Phase-2 storage refactor (DuckDB + hive-partitioned parquet) is a prerequisite for adding regions cleanly — without it, the per-region partitioning becomes painful retrofit. So:

| Stage | Action | Timeline |
|---|---|---|
| **0 (done)** | TSMC + UMC + MediaTek prove the per-company pattern. Phase-1 perf shipped. | 2026-04 |
| **1** | Phase-2 storage refactor (region-partitioned parquet, DuckDB query engine, parametrised `/companies/{ticker}` router). | 2-3 weeks |
| **2** | Backfill US to 100 elite tickers using EDGAR (mostly already-built path). | 1-2 weeks |
| **3** | Taiwan: extend from 3 elite extractors to ~50 elite (hand-tuned for major IR sites) + 200 generic-LLM extractors, all using existing TWSE / MOPS / per-company IR scrapers. | 4-6 weeks |
| **4** | Japan: TDnet + EDINET integration. Translation pipeline (Japanese → English fact extraction). | 6-8 weeks |
| **5** | China: HKEX + SSE / SZSE. Proxy infrastructure + legal review. | 8-12 weeks |
| **6** | Reach 2000 covered tickers (mix of elite + generic) | ~Q4 2026 |

---

## 16. Multi-User Agentic Workflow Design

*Goal: 500-1000 simultaneous active institutional users, each with their own agentic workflows querying the same back-end knowledge graph.*

### 16.1 What "agentic workflow" means here

An institutional user's research question rarely maps to a single backend query. Concrete examples from our coverage:

- *"Show me Taiwan semis with margin compression in 2025."*
  Agent expands to: query 50 Taiwan tickers' gross margin time series → compute 4Q YoY delta → rank → fetch transcript snippets for the bottom 5 → synthesize.
  Backend cost: ~50 financials/wide queries + ~5 transcript searches + ~10 LLM calls.

- *"What's UMC saying about pricing power vs the rest of the foundry sector?"*
  Agent expands to: pull UMC blended-ASP series → pull TSMC blended-ASP series (where disclosed) → pull peer revenue/wafer ratios → vector-search "pricing power" / "pricing discipline" across 4 quarters of UMC + TSMC + GlobalFoundries transcripts → synthesize.
  Backend cost: ~10 structured queries + ~20 vector searches + ~15 LLM calls.

- *"Has TSMC ever missed its own guidance?"*
  Agent expands to: pull `/tsmc/guidance` → filter by outcome=MISS → fetch the transcript turn for each quarter → cite.
  Backend cost: 1 structured query + 5 transcript queries + ~5 LLM calls.

Average agentic question ≈ **5-50 backend queries + 10-30 LLM calls + a synthesis pass.**

### 16.2 Per-user concurrency budget

A user typically has 1-2 active agentic queries running at a time, with 3-5 second think time between questions during exploration. So:

- **Active concurrent users** ≈ users currently waiting on a query
- **Burst** ≈ peak per-second query rate during active use

If 500 users each issue 1 question per minute on average, and each question expands to 30 backend queries spread over 10 seconds:

- Sustained query rate: 500 users × 30 queries / 60s = **250 queries/sec** at the backend
- Burst: 500 users × 30 queries / 10s = **1500 queries/sec** during the agent expansion window

**Today's capacity (Phase 1 done):** 4 workers × ~50 q/s/worker = 200 q/s. Just barely enough for sustained but not burst.

**Phase 2 capacity:** DuckDB-backed shared cache + 8 workers ≈ 1500-2000 q/s. Enough for the burst pattern.

**Phase 3 capacity:** agent runtime separated from web tier; queue-and-stream model; burst absorbed by the queue.

### 16.3 Agentic UX requirements (institutional-grade)

For institutional users, agent-driven research must satisfy:

1. **Provenance on every claim.** Every fact in the agent's output traces back to a specific PDF page / transcript turn / data fragment. Today's silver layer already carries `source` per fact; the missing piece is end-to-end citation in the agent's response.

2. **Reproducibility.** "What did your agent say last Tuesday about UMC?" must be answerable exactly. Implies: full audit trail of the LLM call chain, inputs, model version, point-in-time data snapshot. Phase 3 task.

3. **Cost transparency.** Per-user query budgets visible in the UI ("This question used 14 tool calls + 8 LLM calls = $0.62"). Token meters on every response. Tier-based budget caps prevent runaway costs from one curious user.

4. **Streaming partial results.** A 30-second agent run that returns nothing for 30 seconds feels broken. Stream the agent's intermediate plan, the tool calls in flight, partial results as they come back. SSE / WebSocket transport.

5. **Editable agent plans.** When the agent picks the wrong companies for "Taiwan semis," the user should see and edit the list before the agent runs the actual queries. "Plan-confirm-execute" pattern, not "fire-and-forget."

6. **Workspace persistence.** Every research session is saved with its full query trail. Users return to a thesis they were exploring 3 weeks ago and find every chart, every snippet, every agent run still there. Maps to Layer 4 (Wiki) + Layer 5 (Output) in the 6-layer architecture.

### 16.4 Cost model at scale

LLM tokens dominate operating cost beyond ~100 users:

| Tier | Users | Avg questions/user/day | LLM calls/question | Tokens/call | Cost/question | $ / day |
|---|---|---|---|---|---|---|
| Free / trial | 100 | 5 | 8 | 5K (in) + 1K (out) | ~$0.05 | $25 |
| Pro | 500 | 30 | 15 | 8K + 2K | ~$0.30 | $4,500 |
| Institutional | 100 | 80 | 30 | 15K + 5K | ~$1.50 | $12,000 |
| **Total at 700-user mix** | 700 | — | — | — | — | **~$16,500/day = ~$6M/year** |

Mitigations needed (all Phase 3):
- Aggressive prompt caching (Anthropic's prompt cache cuts repeat-context cost by ~90%)
- Per-user response cache for repeated questions
- Tier-based hard caps on agent loop depth
- Cheaper models for routing / classification, premium models only for synthesis

### 16.5 Backend changes required for multi-user

| Today | Required for multi-user |
|---|---|
| No auth | OAuth (Google / Microsoft / SAML for institutional) |
| Single-tenant SQLite | Postgres with tenant_id column on every user-state table; row-level security |
| No session model | Sticky sessions or token-based auth on every request |
| No watchlist / preferences | Per-user watchlist, dashboards, query history, agent memory |
| No rate limits | Per-user + per-tier API rate limits + LLM-call budgets |
| No audit trail | Every API call + LLM call logged with `(tenant_id, user_id, request_id, cost_usd)` |
| Endpoints unaware of user | Endpoints honour tenant scoping; `GET /api/v1/companies/{ticker}` returns the current user's enriched view (their notes, their margins of interest, etc.) |

These changes are concentrated in **Phase 3** of the infrastructure roadmap (`architecture_and_design_v2.md` § 13.5).

---

## 17. Status Snapshot — Where We Are Today (2026-04-26)

### 17.1 What works for an end user today

A single user (no auth) can hit the dev server at `localhost:3000` and:

- Browse **18 covered tickers** (15 US-EDGAR + 3 Taiwan) in the DataExplorer.
- For TSMC / UMC / MediaTek: see **financials, segments, capacity, guidance vs actual, transcripts (TSMC only)** as dense filings-style tables.
- For NVDA / AAPL / AMD / etc.: see **8-Q income statement + earnings release text** via the EDGAR-backed `/data/fetch` path.
- Browse **Taiwan monthly-revenue heatmap** for 51 tickers via `/taiwan/heatmap`.
- Browse **news / Reddit / GPU-pricing** social tabs.
- Chat with the EngineAgent (Anthropic Claude tool-use loop) — limited to the tools wired up: structured EDGAR data fetch + Pinecone semantic search.

### 17.2 What is built but not yet user-facing

- **Insight engine** (Layer 3): code stubs in `backend/app/services/insights/`, no live combinations.
- **Wiki layer** (Layer 4): no service yet; `topology.py` router queries Neo4j but the relationship-extractor only runs ad-hoc.
- **Output generation** (Layer 5): not built.
- **Catalyst monitoring / daily briefing** (Layer 6): not built.
- **Vector search index** of transcripts: built for TSMC LSEG transcripts only; Pinecone adapter exists but not deployed against the full transcript corpus.
- **Auth + multi-tenant**: not started.

### 17.3 What blocks the next user-visible milestone

The next visible milestone for users is **multi-region coverage** (more tickers in their watchlist). Blocker order:
1. **Phase 2 storage refactor** (DuckDB + hive-partitioned parquet + parametrised companies router) — required so adding tickers is a config change, not a router-add.
2. **Generic LLM-assisted extractor** — required so the long-tail 1850 tickers don't each need 1 week of engineering.
3. **Data-quality framework** — required so generic-extracted data has automatic confidence scoring + identity-check filtering.

The next visible milestone for **multi-user** is **auth + per-tenant isolation** (Phase 3 § 16.5 of architecture v2).

### 17.4 What we are explicitly NOT doing right now

- Not building the Wiki / Insights / Output layers until coverage breadth is in. The 6-layer vision stays the destination but coverage breadth (Layer 1+2 across 2000 tickers) is the gating concern.
- Not migrating off SQLite until multi-user concurrency is needed. Phase 1 WAL mode buys us months of headroom.
- Not deploying Pinecone production-side until we have a transcript corpus large enough to justify it (today: TSMC only).
- Not building China-region scraping until US + Taiwan + Japan are stable and the legal review is done.

### 17.5 Revision history of this document

| Date | Changes |
|---|---|
| 2026-04-09 | v1 initial — 6-layer vision, competitive landscape, dashboard tabs, cold-start design, phased build plan, design principles. |
| 2026-04-26 | Added §15 (multi-region coverage strategy with per-company idiosyncrasy budget), §16 (multi-user agentic workflow design with cost model), §17 (status snapshot). Refreshed §13 to point at §14-16 for current implementation status. |
| 2026-04-27 | Added §18 (multi-channel + per-user-evolution vision — locked decisions on Telegram + Slack + Email + self-hosted Honcho). Cross-references the architecture roadmap in `architecture_and_design_v2.md` § 14. |

---

## 18. Multi-Channel + Per-User-Evolution Vision

*Added 2026-04-27 after CTO review of the `hermes-agent` codebase, with locked-in product decisions.*

### 18.1 What we're building

Beyond the dashboard, AlphaGraph will reach institutional users **on the channels they already work in** — Telegram, Slack, Email — through a single agent that knows them. The same agent that drives the dashboard runs in each channel; only the IO surface differs.

### 18.2 The user-experience promise

A PM who covers semis can:

1. Ask in Slack: *"Has TSMC ever missed its own guidance? show me the worst-miss quarter"* → bot threads back the answer with a chart and direct provenance.
2. Get a daily 7am Asia-summary Email: revenue surprises, guidance changes, transcript callouts that match their watchlist (computed by their agent overnight).
3. Nudge the agent on Telegram: *"draft a 2-page memo on UMC vs TSMC margin trajectory"* → result generates as a streaming response with charts inline.
4. **The agent learns them over time.** After 50 interactions it knows: this user wants concise hedged answers, prefers tables to prose, always cares about Asia foundry, never wants Smartphone segment data, prefers numbers in NT$ billions.

### 18.3 Locked product decisions (2026-04-27)

| Decision | Choice | Rationale |
|---|---|---|
| First channel | Telegram | Analyst-friendly, rich formatting, no OAuth dance, supports inline buttons for clarify-confirm flow |
| Channels in scope | Telegram, Slack, Email | 95% of institutional use |
| Channels out of scope | Discord, WhatsApp, Signal | Wrong product target |
| User-modeling backend | Self-hosted Honcho | Institutional privacy posture, no vendor lock-in |
| Per-user budget | Tiered: free=20 calls/day, pro=300, institutional=2000 | Prevents runaway cost; tier-based monetization aligns |
| First user-facing surface | Web (existing dashboard) | Lowest-risk rollout; channels follow once agent service is stable |

### 18.4 What "the agent learns them" means concretely

Two distinct memory layers (per `architecture_and_design_v2.md` § 14.7):

- **Builtin facts** (markdown, today's pattern formalized): hard rules, watchlist, account settings.
- **Honcho dialectic model** (self-hosted): everything else — communication style, recurring topics, confidence calibration, time patterns.

When a user asks a question, both layers feed the agent's context. After each turn, Honcho asynchronously updates with what was learned ("user prefers inline percentage YoY" / "user always asks for sequential plus YoY").

### 18.5 What this is NOT

- **Not a chatbot.** The agent is task-driven (clarify → plan → confirm → execute), not chat-driven. Free-form chat is supported but the value is in structured analytical workflows.
- **Not personality cloning.** Honcho captures preferences, not personality. The agent's "voice" is consistent across users; only what it surfaces and how it frames are tuned.
- **Not multi-tenant identity-blending.** A user's data is their data. No cross-user "users like you also asked" — that's a different product.

### 18.6 How this is sequenced in the engineering roadmap

| Engineering phase (per arch v2 §14) | Product surface | Calendar week range from now |
|---|---|---|
| Phase 2 | Storage + auth foundation | Weeks 1-6 |
| Phase 3a + 3b + 3c | Web agent service | Weeks 7-15 |
| Phase 4a | + Telegram | Weeks 16-18 |
| Phase 4b | + Per-user model (Honcho) | Weeks 19-20 |
| Phase 4c | + Slack + Email + cross-channel | Weeks 21-26 |

Total: 4-6 months for full vision; first user-facing agent (web only) at week 15.
