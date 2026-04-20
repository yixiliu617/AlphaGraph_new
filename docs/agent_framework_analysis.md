# Agent Framework Comparative Analysis

**Date:** 2026-04-20
**Projects Analyzed:** Hermes-Agent, Nanobot, OpenClaw
**Purpose:** Evaluate agent architectures, memory systems, tool patterns, and web scraping capabilities for potential integration with AlphaGraph

---

## 1. Project Overview

| | **Hermes-Agent** | **Nanobot** | **OpenClaw** |
|---|---|---|---|
| **Language** | Python 3.11+ | Python 3.11+ | TypeScript (Node.js 24) |
| **Codebase** | 85K LOC, 884 files | ~15K LOC, moderate | 50+ directories, large |
| **GitHub Stars** | Smaller community | Smaller community | ~247K stars |
| **License** | Open source | Open source | Open source |
| **Philosophy** | Feature-rich, all-in-one | Minimal, file-based, "memory should feel alive" | Configuration-over-code, local-first |
| **Origin** | Nous Research | Community | Peter Steinberger (Nov 2025, originally "Clawdbot") |

---

## 2. Agent Architecture

### 2.1 Agent Definition

| | **Hermes** | **Nanobot** | **OpenClaw** |
|---|---|---|---|
| **Agent definition** | Code (AIAgent class in `run_agent.py`) | Code (AgentLoop class in `agent/loop.py`) | Configuration (Markdown: SOUL.md, AGENTS.md) |
| **Core loop** | Synchronous, blocking LLM calls | Async (asyncio), streaming-native | Async, WebSocket gateway |
| **Max iterations** | 90 default | 200 default | Configurable per agent |
| **Entry points** | CLI, Gateway (14 platforms) | CLI, Gateway, API server | CLI, Gateway, WebSocket API |
| **Streaming** | Supported | First-class (every layer) | First-class (typed protocol) |

**Key difference:** Hermes and Nanobot define agents in code. OpenClaw defines agents purely through Markdown personality files + JSON config — no code needed to create a new agent.

### 2.2 Hermes Agent Architecture Detail

**AIAgent Class** (`run_agent.py`, Line 535+):
- Synchronous conversation loop with tool-calling iteration
- `chat(message)` for simple interface, `run_conversation()` for full control
- IterationBudget class (Line 181) tracks total iterations, prevents infinite loops
- Async bridging via persistent event loops per thread (avoids "Event loop is closed" errors)

**Message Format:** OpenAI-compatible: `{"role": "system/user/assistant/tool", "content": "..."}`

### 2.3 Nanobot Agent Architecture Detail

**AgentLoop** (`agent/loop.py`, Lines 125-200):
1. Consumes InboundMessage from bus
2. Resolves session (per channel or unified)
3. Builds context (identity, bootstrap, memory, skills)
4. LLM invocation with tool schema registration
5. Iterator-based retry loop (up to 200 iterations)
6. Response routing via OutboundMessage

**AgentRunner** (`agent/runner.py`, Lines 93-300+): Shared execution engine with context recovery on length overflow, tool result backfill for interrupted calls.

**Lifecycle Hooks** (`agent/hook.py`): CompositeHook with error isolation — `before_iteration()`, `on_stream()`, `before_execute_tools()`, `after_iteration()`, `finalize_content()`.

### 2.4 OpenClaw Agent Architecture Detail

**Configuration-driven agents** defined via Markdown files:
- `SOUL.md` — persona, tone, values, behavioral boundaries
- `AGENTS.md` — operating instructions, memory guidelines, tool conventions
- `TOOLS.md` — documents tool usage (does not grant/revoke permissions)
- `IDENTITY.md` — agent name and emoji

**Agent Harness** (`src/agents/harness/`): Low-level execution loop implementing `supports(ctx)` and `runAttempt(params)`.

**Execution flow:** Inbound message → Channel Bridge → Session Resolution → Command Queue → Agent Runtime (harness)

---

## 3. Agent Communication & Multi-Agent

| | **Hermes** | **Nanobot** | **OpenClaw** |
|---|---|---|---|
| **Pattern** | Parent-child delegation | Subagent spawning | Flat, isolated agents with explicit messaging |
| **Delegation** | `delegate_task` tool spawns child with fresh context | `/spawn` creates background tasks | `agent-to-agent` tool (opt-in) |
| **Depth limit** | Max 2 (no grandchildren) | No explicit limit | No hierarchy — flat by design |
| **State sharing** | None — child gets clean context | Subagent has own registry + budget | Cross-agent memory via `extraCollections` |
| **Blocked tools** | Child can't delegate, clarify, write memory | Configurable | Per-agent allow/deny lists |
| **Orchestration** | Built-in delegation tool | Skill-layer orchestration | Deterministic routing (first-match binding) |

### 3.1 Hermes Delegation Model

**Delegation Tool** (`tools/delegate_tool.py`):
- Spawns isolated child agents with fresh conversation, restricted toolset, own task_id
- Parent blocks, never sees child's intermediate tool calls — only receives summary
- Blocked tools in children: `delegate_task`, `clarify`, `memory`, `send_message`, `execute_code`
- Configurable max concurrent children (default 3)
- Workspace hint resolution: tries `TERMINAL_CWD`, `_subdirectory_hints`, `terminal_cwd`, `cwd`

### 3.2 Nanobot Subagent Model

**Subagent Manager** (`agent/subagent.py`):
- Spawns background agent tasks for long-running work
- Each subagent has own ToolRegistry and BudgetManager
- Status tracking per task (phase, iteration, tool_events)
- Single-agent by default — multi-agent delegated to skill layer

### 3.3 OpenClaw Flat Agent Model

**Routing:** Deterministic first-match-wins binding hierarchy:
1. Peer match (exact DM/group/channel ID)
2. Parent peer match (thread inheritance)
3. Guild ID + roles (Discord)
4. Account ID match
5. Channel-level match
6. Fallback to default agent

**Cross-agent communication** explicitly opt-in via config:
```json
{
  "tools": {
    "agentToAgent": { "enabled": false, "allow": ["home", "work"] }
  }
}
```

**Cross-agent memory** via `extraCollections`:
```json
{
  "memorySearch": {
    "qmd": {
      "extraCollections": [
        { "path": "~/agents/family/sessions", "name": "family-sessions" }
      ]
    }
  }
}
```

VISION.md explicitly states: "agent hierarchies are NOT the default architecture" — prefers flat, isolated agents with explicit communication channels.

---

## 4. Memory System

| | **Hermes** | **Nanobot** | **OpenClaw** |
|---|---|---|---|
| **Storage** | SQLite (FTS5) + Markdown files | JSONL + Markdown + Git | Markdown files + SQLite/QMD |
| **Long-term files** | MEMORY.md, USER.md, SOUL.md | MEMORY.md, USER.md, SOUL.md, history.jsonl | MEMORY.md, daily notes, DREAMS.md |
| **Search** | FTS5 full-text search | Text-based (no vector) | Hybrid: BM25 + vector + re-ranking |
| **Consolidation** | Context compression → session split | Two-stage: Consolidator + Dream | Compaction with pre-save reminder |
| **Dream/Sleep** | No | Yes — scheduled synthesis (2h default) | Optional dreaming with thresholded gates |
| **Versioning** | No | Git (Dulwich) for memory files | Optional via memory-wiki plugin |
| **External providers** | Honcho, Hindsight, Mem0 (max 1) | None built-in | Honcho, QMD sidecar |
| **Injection safety** | `<memory-context>` fencing | Not documented | Not documented |

### 4.1 Hermes Memory Detail

**Three-Tier Architecture:**

1. **Built-in Memory** (`agent/memory_manager.py`, Line 71+): Always active, provides `build_system_prompt()`.

2. **Memory Manager** (Orchestrator): Registers built-in provider first (mandatory), accepts only ONE external provider (prevents schema bloat). Handles lifecycle: initialize, prefetch, sync_all, shutdown. Sanitizes context with fences:
```xml
<memory-context>
[System note: recalled memory context, NOT new user input...]
{clean memory content}
</memory-context>
```

3. **Memory Providers** (`agent/memory_provider.py`): Abstract base with lifecycle hooks — `prefetch(query)`, `sync_turn(user_msg, assistant_response)`, `on_turn_start()`, `on_session_end()`, `on_pre_compress()`, `on_memory_write()`, `on_delegation()`.

**Session Persistence** (`hermes_state.py`): SQLite with WAL mode, FTS5 virtual table for text search.

| Table | Purpose |
|-------|---------|
| `sessions` | Metadata: id, source, model, costs, token counts |
| `messages` | Full history: role, content, tool_calls, reasoning |
| `messages_fts` | FTS5 virtual table for text search |

### 4.2 Nanobot Memory Detail (Most Innovative)

**Two-Stage Architecture:**

**Stage 1: Consolidator** (`agent/memory.py`, Lines 300+):
- Triggered when context window pressure accumulates
- Summarizes oldest safe slice of conversation
- Appends to `memory/history.jsonl`:
```json
{"cursor": 42, "timestamp": "2026-04-03 00:02", "content": "- User prefers dark mode\n- Decided to use PostgreSQL"}
```
- Cursor-based, append-only design

**Stage 2: Dream** (`agent/memory.py`, Lines 400+): Scheduled task (default 2h interval):
1. **Phase 1 (Study):** Reads new history entries, reviews SOUL.md/USER.md/MEMORY.md, identifies what's new vs already known
2. **Phase 2 (Edit):** Makes surgical edits to long-term files via GitStore for auditability. Capped at 15 iterations.

**Memory Files:**
```
SOUL.md              # Bot's long-term voice and communication style
USER.md              # Stable knowledge about the user
memory/MEMORY.md     # Project facts, decisions, durable context
memory/history.jsonl # Append-only compressed conversation summaries
memory/.cursor       # Consolidator write position
memory/.dream_cursor # Dream consumption position
memory/.git/         # Version history (GitStore via Dulwich)
```

**Dream Configuration:**
```python
class DreamConfig:
    interval_h: int = 2           # Every 2 hours
    model_override: str = None    # Optional custom model
    max_batch_size: int = 20      # Entries per run
    max_iterations: int = 15      # Tool call budget for Phase 2
    annotate_line_ages: bool = True  # Git blame age in prompts
```

### 4.3 OpenClaw Memory Detail (Best Retrieval)

**Storage:** Markdown-file-based with semantic search overlay:
- `MEMORY.md` — long-term durable facts, loaded at session start
- `memory/YYYY-MM-DD.md` — daily notes (auto-loads today + yesterday)
- `DREAMS.md` — consolidation summaries for human review

**Key principle:** *"The model only 'remembers' what gets saved to disk — there is no hidden state."*

**Retrieval (two tools):**
- `memory_search` — semantic search using hybrid search (vector + keyword)
- `memory_get` — reads specific files or line ranges

**Three backends:**
1. **Built-in SQLite** — local-first, keyword + vector search
2. **QMD (Query Markdown Documents)** — advanced: BM25 lexical + vector similarity + re-ranking. Runs at least two retrieval channels simultaneously.
3. **Honcho Memory** — AI-native cross-session memory with user modeling

**Lifecycle:**
- Sessions reset daily at 4:00 AM (configurable)
- Pre-compaction reminder to save important context
- Optional dreaming with thresholded gates for promoting short-term to long-term

---

## 5. Tool System

| | **Hermes** | **Nanobot** | **OpenClaw** |
|---|---|---|---|
| **Registration** | Self-registering at import time | Registry with dynamic MCP | Contract-based plugin SDK |
| **Built-in tools** | 59+ across 45 toolsets | ~15 core tools | Session, media, web, agent tools |
| **MCP support** | Yes | Yes (mcp 1.26+) | Yes |
| **Parallel execution** | Yes (safe subset only) | Yes (concurrent tool calls) | Yes |
| **Sandboxing** | 6 terminal backends (Docker, SSH, Modal, Daytona, Singularity, local) | `restrict_to_workspace` config | Docker sandbox for non-main sessions |
| **Skills** | 127 community skills | Workspace skills (Python) | Markdown skills with YAML frontmatter |

### 5.1 Hermes Tool Architecture

**Self-Registering Pattern** (`tools/registry.py`):
- Each tool file calls `registry.register()` at module level
- `discover_builtin_tools()` imports all tools/*.py
- Auto-discovery via AST parsing for validation
- Discovery order: built-in → MCP → plugins

**Parallel execution safety** (`run_agent.py`, Line 267+):
- Safe only when: single tool OR all in `_PARALLEL_SAFE_TOOLS`
- No overlapping file paths
- No interactive tools (clarify)
- Unsafe patterns trigger sequential fallback

**45+ toolsets** including: terminal, web, browser, file, vision, skills, delegate, messaging, rl, cronjob, hermes-core

### 5.2 Nanobot Tool Architecture

**Tool Base Class** (`agent/tools/base.py`): Each tool implements `name`, `description`, `to_schema()`, `cast_params()`, `validate_params()`, `execute(**params)`.

**Core tools:** filesystem (read/write/edit/list), shell (exec/background), search (glob/grep), web (search/fetch), MCP, messaging, scheduling, spawning.

**Schema validation** (`tools/schema.py`): JSON Schema-based with recursive object/array validation, error messages guide LLM on parameter corrections.

### 5.3 OpenClaw Tool Architecture

**Skill-tool separation:** Skills are Markdown instructions (SKILL.md with YAML frontmatter) that teach the agent *when and how* to use tools. Skills do NOT define tools — they provide context.

```yaml
---
name: image-lab
description: Generate or edit images
command-dispatch: tool
command-tool: image_generate
metadata: {"openclaw":{"requires":{"env":["GEMINI_API_KEY"]}}}
---
```

**Skill gating:** `metadata.openclaw.requires` checks bins on PATH, env vars, config keys, OS restrictions at load time.

**Per-agent tool security:**
```json
{
  "tools": { "allow": ["exec", "read"], "deny": ["write", "browser"] }
}
```

---

## 6. Configuration System

| | **Hermes** | **Nanobot** | **OpenClaw** |
|---|---|---|---|
| **Format** | YAML + .env | JSON + env vars | JSON + Markdown personality files |
| **Scope** | Global (~/.hermes/) | Per-workspace | Global + per-profile + per-agent |
| **Profiles** | Yes (multi-instance) | Single workspace | Yes (openclaw-<profile>.json) |
| **Validation** | Pydantic-ish | Pydantic 2.12+ | TypeBox schemas + 60 test files |
| **LLM Providers** | 40+ | 30+ | Multiple (via config) |

### 6.1 Hermes Configuration

`~/.hermes/config.yaml` + `~/.hermes/.env` (100+ provider configs).

Key sections: `model`, `terminal` (6 backends), `display`, `compression`, `auxiliary`, `agent`, `delegation`, `memory`, `skills`, `tools`, `messaging`, `timezone`.

**Managed Mode:** NixOS/Homebrew support via `HERMES_MANAGED` env var.

### 6.2 Nanobot Configuration

`~/.nanobot/config.json` with Pydantic validation. Environment variable resolution via `${VAR_NAME}` syntax.

Key settings: `workspace`, `model`, `provider`, `contextWindowTokens` (default 65536), `maxToolIterations` (200), `unifiedSession`, `sessionTtlMinutes`, `dream` config.

### 6.3 OpenClaw Configuration

`~/.openclaw/openclaw.json` with TypeBox schema validation.

Hierarchy: Global → per-profile → per-workspace → per-agent. Multi-agent bindings for channel routing. 150+ config files for validation.

---

## 7. Gateway & Messaging

| Platform | **Hermes** | **Nanobot** | **OpenClaw** |
|---|---|---|---|
| Telegram | Yes | Yes | Yes |
| Discord | Yes | Yes | Yes |
| Slack | Yes | Yes | Yes |
| WhatsApp | WIP | Yes | Yes |
| Signal | Yes | No | Yes |
| Email | Yes | Yes | No |
| Matrix | Yes (Linux) | Yes | No |
| WeChat | No | Yes | No |
| DingTalk | Yes | Yes | No |
| Feishu | Yes | Yes | No |
| QQ | Yes | Yes | No |
| SMS | Partial | No | No |
| Mattermost | Yes | No | No |
| Home Assistant | Yes | No | No |
| API Server | Yes | Yes | Yes (WebSocket) |

---

## 8. Web Scraping & Data Fetching

### 8.1 Architecture Comparison

| Capability | **Hermes** | **Nanobot** | **OpenClaw** |
|---|---|---|---|
| **Web search backends** | 4 (Firecrawl, Exa, Parallel, Tavily) + DuckDuckGo | 6 (Brave, DuckDuckGo, Tavily, Jina, Kagi, SearXNG) | Web search tools (config-dependent) |
| **Content extraction** | web_extract (LLM-summarized, PDF support, >5000 chars auto-summarized) | web_fetch (Jina Reader + readability-lxml, 50K char cap) | Web fetch tools |
| **Browser automation** | Yes — 4 providers (Browser Use, Browserbase, Firecrawl, local Chromium) + Camofox stealth | No | Yes — browser tools |
| **Cloudflare bypass** | Yes — Scrapling skill (Stealth/Turnstile bypass) | No | No |
| **Multi-page crawling** | Yes — web_crawl (up to 20 pages, LLM-guided) | No | No |
| **RSS/Atom feeds** | Yes — blogwatcher skill (auto-discovery, OPML import) | No | No |
| **SSRF protection** | Yes | Yes | Yes |
| **Proxy support** | Yes (httpx[socks]) | Yes (HTTP/SOCKS5) | Yes |

### 8.2 Specific Website/Service Support

| Website/Service | **Hermes** | **Nanobot** | **OpenClaw** |
|---|---|---|---|
| **Reddit** | Via web_extract/browser (public pages) | Via web_fetch (public pages) | Via web_fetch/browser |
| **X/Twitter** | Yes — built-in skill (xitter), full API | No | Via MCP or web_fetch |
| **Polymarket** | Yes — built-in skill (3 free APIs: Gamma, CLOB, Data) | No | No |
| **Google Trends** | No (via browser/search only) | No | No |
| **Google News RSS** | Via web_extract | Via web_fetch | Via web_fetch |
| **YouTube** | Yes — transcript extraction skill | No | Via MCP |
| **arXiv** | Yes — built-in skill (free API, 22 categories) | No | No |
| **Semantic Scholar** | Yes — built-in (citations, author profiles) | No | No |
| **GitHub** | Yes — gh CLI skill | Yes — gh CLI skill | Yes — built-in |
| **Google Workspace** | Yes — Gmail, Calendar, Drive, Sheets, Docs (OAuth2) | No | No |
| **Blockchain** | Yes — Base (Ethereum L2) + Solana (wallets, NFTs) | No | No |
| **Notion** | Yes — API skill | No | No |
| **Linear** | Yes — issue tracking | No | No |
| **Weather** | Via web search | Yes — wttr.in + Open-Meteo | No |
| **OpenStreetMap** | Yes — find-nearby skill (geocoding, POIs) | No | No |
| **HuggingFace** | Yes — models/datasets | No | No |
| **Vector DBs** | Pinecone, Qdrant, Chroma, FAISS | No | No |
| **Any public website** | Yes | Yes | Yes |

### 8.3 Hermes Web Scraping Detail

**Core Web Tools:**

1. **web_search** — backends: Firecrawl, Exa, Parallel, Tavily. Auto-fallback priority: Parallel > Firecrawl > Exa > Tavily > DuckDuckGo.

2. **web_extract** — full page to Markdown/HTML. LLM-summarized for >5000 chars. Chunked processing for 500KB+, refuses >2MB. PDF support (arxiv papers). 5 URLs per call max.

3. **web_crawl** — multi-page (up to 20 pages). Firecrawl only. Instructions-based LLM-guided extraction with per-page summarization.

4. **Browser** — 4 cloud providers (Browser Use, Browserbase, Firecrawl, local Chromium). Camofox REST API for stealth mode. Text-based accessibility tree.

**Specialized Skills:**
- **Polymarket:** Gamma API (discovery), CLOB API (prices, orderbooks), Data API (trades). All free, no auth. Rate limits: Gamma 4000/10s, CLOB 9000/10s, Data 1000/10s.
- **X/Twitter (xitter):** Full API — post, search, timelines, likes, retweets, bookmarks. Requires paid API plan.
- **arXiv:** Free API, boolean search, 22 categories, 1 req/3s rate limit.
- **Scrapling:** HTTP, Dynamic JS, Stealth/Cloudflare bypass strategies. Spider framework for multi-page crawls. CSS/XPath selectors.
- **blogwatcher:** RSS/Atom feed tracking with auto-discovery, HTML scraping fallback, OPML import/export.

**Statistics:** 127 total skills, 20+ web-specific, 20+ direct API integrations, 4 search backends, 3 cloud browser providers.

### 8.4 Nanobot Web Scraping Detail

**Minimal but clean:**

1. **web_search** — 6 backends: Brave, DuckDuckGo (default, free), Tavily, Jina (10M free tokens), Kagi, SearXNG.

2. **web_fetch** — Jina Reader API (primary) + readability-lxml (fallback). SSRF protection, redirect validation. 50K char output cap.

**No browser automation, no Cloudflare bypass, no RSS parsing, no specialized site integrations.**

Extensible via MCP servers and custom skills (Python/bash).

### 8.5 OpenClaw Web Scraping Detail

**Middle ground:**
- Browser tools available
- Web fetch capabilities
- Plugin SDK (100+ files) for contract-based extensions
- Less documented specific site integrations
- Focus is on agent orchestration, not scraping

### 8.6 What None of Them Have Built-In

| Service | Status |
|---|---|
| Google Trends | None — needs custom scraper |
| Truth Social | None — Cloudflare too strict |
| LinkedIn | None — login-walled + anti-scraping |
| Instagram | None — login-walled |
| TikTok | None |
| Amazon product data | None (Hermes could via browser) |
| SEC EDGAR | None — we built our own in AlphaGraph |
| PCPartPicker | None — we built our own |
| CamelCamelCamel | None — we built our own |
| Arctic Shift (Reddit archive) | None — we built our own |

---

## 9. Key Design Patterns Comparison

### 9.1 Hermes Patterns
1. **Self-registering tools** — tools declare themselves at import, no central manifest
2. **Message-based skills** — injected as user messages (preserves prompt caching)
3. **Prompt injection scanning** — detects "ignore instructions", unicode tricks, exfil attempts
4. **Compression-triggered session splitting** — new session with parent_session_id link
5. **UTF-16 aware truncation** — platform-specific message limits
6. **Profile/multi-instance support** — all state via `get_hermes_home()`

### 9.2 Nanobot Patterns
1. **Message bus decoupling** — channels and agent communicate only via async queues
2. **Hook-based extensibility** — lifecycle hooks with error isolation via CompositeHook
3. **Two-stage memory** — real-time consolidation + scheduled dream synthesis
4. **File-based state** — no database, Git-compatible, human-readable
5. **Graceful degradation** — MCP down, missing skill, unavailable channel don't crash agent
6. **Config-driven feature flags** — behavioral toggles without code changes

### 9.3 OpenClaw Patterns
1. **Configuration-over-code** — agent behavior in Markdown, not programmatically
2. **Local-first** — everything runs on user's device, no cloud dependency
3. **Lean core, extensible periphery** — new capabilities as separate npm packages
4. **Hub-and-spoke gateway** — single Gateway owns all messaging surfaces
5. **Deterministic routing** — first-match-wins binding, no ML-based routing
6. **Isolation by default** — each agent gets own workspace, state, credentials, sandbox
7. **Typed protocol** — TypeBox schemas → JSON Schema → Swift models
8. **Skill-as-instruction** — skills teach tool usage in natural language, don't define tools

---

## 10. Pros & Cons Summary

### Hermes-Agent
**Pros:**
- Most feature-complete (59 tools, 14 platforms, 40 LLM providers, 127 skills)
- Best web scraping (Cloudflare bypass, multi-page crawl, browser automation)
- Robust session persistence with FTS5 search
- Prompt injection detection and memory fencing
- Production battle-tested

**Cons:**
- Synchronous core loop limits throughput
- Monolithic (85K LOC)
- Complex configuration (100+ env vars)
- Single external memory provider limit

### Nanobot
**Pros:**
- Most innovative memory (Dream two-stage consolidation + Git versioning)
- Clean async architecture
- Lightweight file-based state (no database)
- Graceful degradation design
- Most search backends (6)

**Cons:**
- No vector search for memory
- Single-agent by default
- Smaller tool ecosystem
- No browser automation or Cloudflare bypass

### OpenClaw
**Pros:**
- Best memory retrieval (hybrid BM25 + vector + re-ranking via QMD)
- Cleanest skill/tool separation (skills as instructions, not code)
- Configuration-over-code (agents in Markdown)
- Local-first architecture
- Largest community (247K stars)
- Typed WebSocket protocol

**Cons:**
- TypeScript (vs Python ecosystem for ML/AI)
- More complex multi-agent setup
- No built-in agent hierarchy
- Less documented site-specific integrations

---

## 11. Recommendations for AlphaGraph

If building agent capabilities into AlphaGraph:

1. **Memory pattern:** Adopt Nanobot's Dream model — two-stage (real-time consolidation + periodic synthesis) is ideal for a research platform where insights accumulate over days/weeks.

2. **Agent definition:** Follow OpenClaw's Markdown-based approach — define research agents as personality files (SOUL.md), not code. Easier to iterate and version.

3. **Tool architecture:** Use Hermes' self-registering pattern — scales well, prevents circular imports.

4. **Search:** Use OpenClaw's hybrid retrieval (BM25 + vector) for the news/Reddit corpus — already have Gemini embeddings infrastructure from earnings fragments pipeline.

5. **Web scraping:** Purpose-built scrapers (our current approach) are better than general-purpose agent browsing for systematic, scheduled data collection. The frameworks are good for ad-hoc research, not for structured parquet pipelines.

---

## 12. Source Locations

| Project | Path |
|---|---|
| Hermes-Agent | `C:\Users\Sharo\AI_projects\hermes-agent` |
| Nanobot | `C:\Users\Sharo\AI_projects\nanobot` |
| OpenClaw | `github.com/openclaw/openclaw` |
| AlphaGraph | `C:\Users\Sharo\AI_projects\AlphaGraph_new` |
