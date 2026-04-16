# AlphaGraph — Product Design v1

*Last updated: 2026-04-09*

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

## 13. Phased Build Plan

| Phase | What to Build | Key Deliverable |
|---|---|---|
| Phase 1 | Layer 1 + Layer 2 + Tab 3 search + Cold start seeding | Working data ingestion; analyst can research entities |
| Phase 2 | Layer 3 (Insights) + Feedback preference store + Tab 1 (Layer 6 alerts) | Compounding knowledge loop begins |
| Phase 3 | Layer 4 (Wiki) + Tab 2 graph visualization + Agent event bus | Knowledge base differentiation visible |
| Phase 4 | Layer 5 (Output generation) + Team collaboration + Confidence scoring | Enterprise sales-ready |

**The common mistake:** Building Layer 5 (output) before having enough Layer 2 data quality. The output is only as good as the fragments underneath it.

---

## 14. Key Design Principles

1. **Accumulate, don't just answer**: Every interaction should make the system smarter, not just return a response.
2. **Trust through transparency**: Institutional users need to see their sources. Never hide where data came from.
3. **Time is first-class**: Event time, record time, and knowledge time are all different and all matter.
4. **The user's voice is the primary data source**: Their channel checks, theses, and observations are what makes their knowledge base unique.
5. **Cold start is Day 1 revenue**: If the system isn't useful until Year 2, there is no Year 2.
6. **Obsidian's graph lesson**: When users see their knowledge as a network, they discover connections they would never find with search alone.
