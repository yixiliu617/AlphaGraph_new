# AlphaGraph — Active Roadmap (v1)

**Status:** living doc. Updated whenever a stream advances or priorities shift. **This is the file to read first** when picking up the project after a break.
**Last updated:** 2026-04-29.

---

## 0. Where to start as a returning session

1. Read this file (you're here).
2. If anything in **§ 1 Current focus** looks stale, refresh it.
3. For "what to build next?", jump to **§ 4 Backlog**.
4. For product positioning context, read `product_design_v2.md`.
5. For technical detail on a specific subsystem, read `architecture_and_design_v3.md` (Phase 2+) or `architecture_and_design_v2.md` (Phase 1, still load-bearing for extraction pipeline + Taiwan revenue + audio).

---

## 1. Current focus (week of 2026-04-28)

**Active stream: Stream 1 — Universe + prices foundation.** Day-by-day plan from `architecture_and_design_v3.md` § 8.

**Status snapshot:**
- ✅ Universe v2 schema (migration 0005 applied): 363 companies / 394 listings / 31 thesis groups / 28 pre-IPO
- ✅ Seed CSV + addendum + pre-IPO JSON loaded into Postgres
- ✅ T3 prices backfill complete: ~831k daily rows + 60-day intraday for 387 unique tickers across 6 markets
- ✅ Per-market cron jobs registered: US / TW / HK / JP / KR / CN (10 jobs total)
- 🟡 Index baseline fetchers (SMH / SPX / TWSE 50 / Nikkei 225 / KOSPI 200) scaffolded; need real implementations
- ⏳ Universe management API (`/me/universe/{add,remove,list}`) — Stream 1 day 6
- ⏳ Universe management UI — Stream 1 day 7

**Next 3 actions, in order:**

1. **Pick the deployment target.** Render+Neon vs. AWS for the first pilot URL. Decision PD-5 in `product_design_v2.md`. This unblocks "show pilots a real link in week 1."
2. **Build `/me/universe/{list,add,remove,subscribe,unsubscribe}` API + the auto-promotion flow.** Half-day. Required for Pillar A week 1 demos to feel real.
3. **Begin Pillar A week 1: chat-with-citations basic.** Tool-use scaffold + first 3 tools (`query_fundamentals`, `query_prices`, `search_filings`). Mandatory citation gate from day 1.

After those: Pillar A weeks 2–6 per `product_design_v2.md` § 5.

---

## 2. Streams (parallel work tracks)

### Stream 1 — Data + Universe (foundation for Pillar A)

| Day | Task | Status |
|---|---|---|
| 1 | Schema (7 tables) + ORM + migration | ✅ |
| 1 | Seed loader + 363 companies / 394 listings loaded | ✅ |
| 2 | Index baseline fetchers (SMH/SPX/TWSE/Nikkei/KOSPI) | 🟡 scaffolded, NotImplementedError |
| 3 | Dual-listing canonicalization (31 pairs) | ✅ done in loader |
| 4 | T3 daily prices backfill (10y) — 393 tickers | ✅ |
| 5 | T3 intraday 15m backfill (60d) — 393 tickers | ✅ |
| 6 | API: `/me/universe/{list,add,remove,subscribe}` + auto-promotion | ⏳ |
| 7 | Frontend universe management page | ⏳ |
| 8 | Cron rewire to per-market (US/TW/HK/JP/KR/CN) | ✅ |

### Stream 2 — Pillar A (the AI semi analyst)

| Week | Task | Status |
|---|---|---|
| 1 | Chat scaffold + 3 tools (`query_fundamentals`, `query_prices`, `search_filings`) + citation gate | ⏳ |
| 1 | Alerts MVP — natural-language → structured rule, cron evaluator, email | ⏳ |
| 1 | First pilot URL deployed | ⏳ |
| 2 | Walkthrough demo with 3 pilots; capture top-3 friction list | ⏳ |
| 3 | Cross-company forward-guidance dashboard with diff-since-last-quarter | ⏳ |
| 3 | Notes search (Postgres FTS over `meeting_notes` + `user_note`) | ⏳ |
| 4 | "Bring your own filing" extraction endpoint | ⏳ |
| 4 | First daily-active pilot | target |
| 5 | Earnings-season prep — NVDA/TSMC/AMD/MRVL Q2 readiness; "what changed" diffs | ⏳ |
| 6 | First paying pilot signed | target |

### Stream 3 — Pillar B (Research knowledge layer)

Starts week 6 of Pillar A.

| Step | Task | Status |
|---|---|---|
| 1 | Earnings-call audio capture from public webcasts (NVDA/TSMC/AMD/MRVL) | ⏳ |
| 2 | EN↔ZH↔JP translation pipeline + bilingual rendering | ⏳ |
| 3 | Searchable transcript repository + cross-quarter comparison | ⏳ |
| 4 | Meeting bots (Zoom, Teams) — only after #1–3 prove value | ⏳ |
| 5 | Notes team-sharing surface | ⏳ |

### Stream C — Pillar C (Personal pattern agent)

**Deferred to v2.** Pre-defined TA patterns + "save this view as alert" instead.

---

## 3. Decisions log

### Locked — don't revisit without re-discussion

| ID | Decision | Date locked |
|---|---|---|
| L1 | Honcho hosting: self-host (when needed) | 2026-04-27 |
| L2 | Auth: Google + Microsoft direct OAuth (not Auth0) | 2026-04-27 |
| L3 | LLM provider: Anthropic-first, abstracted via `LLMProvider` | 2026-04-27 |
| L4 | First channel: web only; Telegram/Slack/Email deferred until paying users | 2026-04-28 |
| L5 | Universe organising unit: thesis groups (not indices) | 2026-04-28 |
| L6 | Dual-listings: separate listings linked by `company_id` (not canonical-only) | 2026-04-28 |
| L7 | Pillar sequencing: A → B → C deferred | 2026-04-28 |
| L8 | Pricing target for v1 pilot: $300/mo individual, $1,500/mo team-of-5 | 2026-04-28 |
| L9 | OneNote scope for personal MSA accounts: `Notes.Read` not `Notes.Read.All` | 2026-04-28 |
| L10 | IWM Russell 2000 universe: SKIP (out of AI-bottleneck thesis scope) | 2026-04-29 |

### Open — needed before next major milestone

| ID | Decision | Blocks | Owner |
|---|---|---|---|
| O1 | Deployment target — Render+Neon vs. AWS for first pilot URL | Pillar A week 1 | Sharon |
| O2 | Citation render style — pill-with-hover vs. footnote-link | Pillar A week 2 | Sharon |
| O3 | "Refuse to answer without citation" — strict gate vs. soft warning | Pillar A week 2 | Sharon |
| O4 | Auto-promotion threshold — any ticker vs. curated approval | Pillar A week 1 | Sharon |
| O5 | Subagent failure mode (graceful degrade vs. page on-call) | Pillar A week 5+ | Sharon |
| O6 | Honcho privacy policy approval | Pillar B week 4+ | Sharon |
| O7 | Cross-channel verification UX | Multi-channel rollout (deferred) | Sharon |
| O8 | Cost re-evaluation cadence | Monthly | — |

### Cancelled — won't build (record for clarity)

| ID | Cancelled item | Date | Reason |
|---|---|---|---|
| C1 | IWM Russell 2000 universe coverage | 2026-04-29 | Out of AI-bottleneck thesis; 2000 names of small-cap noise |
| C2 | Subagent delegation framework (Phase 3a/b in old plan) | 2026-04-28 | Premature for solo dev with 0 paying users; single-agent + tools is enough for Pillar A |
| C3 | Multi-channel gateway (Phase 4a/4b/4c in old plan) | 2026-04-28 | Defer until web has 50+ paying users |
| C4 | Honcho self-hosted user modeling (Phase 4b) | 2026-04-28 | Postgres `app_user.preferences` JSONB column carries us to 100 users |
| C5 | Phase 2 B3 DuckDB read-side | 2026-04-28 | Defer until an endpoint is actually slow with real load |

---

## 4. Backlog (queued work, next 12 weeks)

Ordered roughly by priority. Pull from top.

### High priority

- [ ] Build `/me/universe/{list,add,remove,subscribe}` API + auto-promotion flow
- [ ] Build universe management frontend page
- [ ] Pick + execute deployment target (Render+Neon expected)
- [ ] Pillar A week 1: chat scaffold + 3 tools + citation gate
- [ ] Pillar A week 1: alerts MVP
- [ ] First pilot demo (week 2)
- [ ] Earnings-season prep — auto "what changed" diffs for NVDA/TSMC/AMD/MRVL
- [ ] Resolve VERIFY flags from universe seed: `3317.HK`, `Powerchip`, `Kazatomprom`, `Arcadium`, `Tianqi-H`, `MetaX` STAR ticker

### Medium priority

- [ ] Implement SMH index fetcher (smallest, AI-core, ~30 names)
- [ ] Implement SPY index fetcher (~500 names)
- [ ] T2 fundamentals: SEC EDGAR XBRL backfill against all SPY tickers
- [ ] Notes search (Postgres FTS)
- [ ] Cross-company guidance dashboard
- [ ] "Bring your own filing" endpoint
- [ ] Pillar B kickoff (week 6+)

### Lower priority — deferred / on-demand

- [ ] Implement TWSE 50 / Nikkei 225 / KOSPI 200 fetchers
- [ ] Outlook Mail adapter
- [ ] OneDrive adapter
- [ ] Google Docs adapter
- [ ] Gmail adapter (CASA audit needed before public launch)
- [ ] Team sharing for notes
- [ ] Add 22 more deep T1 extractors (one per pilot-requested ticker)
- [ ] Asia XBRL parsers (TDnet for JP, DART for KR) — only if pilots ask
- [ ] Index intraday 15m for HK/JP/KR/CN markets — only if daily proves insufficient
- [ ] Postgres `pgvector` enablement + RAG store for filings

---

## 5. Risks (rolling)

| Risk | Severity | Mitigation |
|---|---|---|
| Pilot users delay or churn | high | Real URL by week 1; first paying pilot signal by week 6 |
| Hallucination rate >0.5% on chat | high | Mandatory citation gate (architectural, not policy) |
| yfinance throttle / outage on broader universe | medium | Fallback to TWSE-direct (already built); SEC EDGAR independent of yf for fundamentals |
| Earnings-season Q2 misses (NVDA, TSMC, etc.) | medium | Stream 2 week 5 prep is explicit; calendar set |
| Anthropic API cost exceeds plan | medium | Prompt caching planned; will measure week 1 |
| Postgres + parquet hybrid drift | low | Single-writer pattern; data-quality framework runs on parquets |
| OneNote / Calendar sync rot from provider API change | low | Adapter pattern; oauth-service-integration skill captures 15 edge cases |
| 18-week solo build scope | high | Pillar B/C deferred; Pillar A is 6 weeks not 18 |

---

## 6. Recent changes log

Append-only. Most recent at top.

### 2026-04-29
- Deployment scaffolding ready (Vercel + Render + Neon, AWS-portable):
  - `backend/Dockerfile` (backend-only for Render)
  - `render.yaml` (Blueprint — web + worker + 10 GB disk, Singapore region)
  - `backend/app/core/storage.py` (FS↔S3 abstraction; flip via env var)
  - Env-driven CORS (`CORS_ORIGINS`, `CORS_ORIGIN_REGEX`) + `/healthz` endpoint
  - `.env.production.example` (every prod var documented)
  - `docs/deployment_runbook.md` (3–4 hr step-by-step incl. OAuth callback updates, S3 migration path, AWS year-2 migration plan)
  - Pilot path: filesystem on Render Disk; switch to Backblaze B2 (S3-compatible) when needed; AWS migration is one env-var flip + rsync.
- Stream 1 cron rewire complete: 10 prices jobs registered (US/TW/HK/JP/KR/CN); reads from `listing` table with CSV fallback.
- T3 prices backfill complete: 393 tickers × 10y daily (~831k rows) + 60d intraday across 6 markets.
- Universe seed loaded: 363 companies, 394 listings, 31 thesis groups, 28 pre-IPO.
- Live web check moved 10 entries from pre-IPO to recent_ipo (HK AI IPO wave Jan-Apr 2026: Zhipu, MiniMax, Biren, Iluvatar CoreX, Lightelligence, Axera + Moore Threads on STAR).
- New skill: `.claude/skills/oauth-service-integration/SKILL.md` (15 captured edge cases).

### 2026-04-28
- Pillar A/B/C structure defined; Pillar C deferred.
- Universe v2 schema designed (company / listing / thesis groups / pre-IPO / per-user).
- AI bottleneck thesis adopted as positioning.
- 6 pilot profiles + 8 user requests captured.
- OneNote sync working end-to-end (resolved 3-step Microsoft personal-account quirk: Notes.Read.All → Notes.Read, force-revoke at account.live.com, per-section iteration for many-section accounts).
- Calendar sync working end-to-end (Google + Outlook).

### 2026-04-27 (entry into Phase 2)
- Phase 2 B1 DONE: Postgres + Alembic migration 0001 (app_user, oauth_session, user_alert, scraper_heartbeat).
- Phase 2 B2 DONE: OAuth (Google + Microsoft) + JWT cookie auth + `/auth/{provider}/login,callback,me,logout`.
- W1 DONE: 17/17 skills migrated to Hermes-style frontmatter.
- C DONE: Universe prices backfill for 132 tickers (84 US + 48 TW) from `platform_universe.csv`.

### Pre-2026-04-27
- Phase 1: equity prices layer (137 tickers, daily + intraday, 5 cron jobs).
- Hybrid Taiwan TWSE-direct patcher (gap fill + adjustment-factor segmentation).
- Technical analysis indicators library (SMA, EMA, BB, RSI, MACD, TD Sequential, TD Combo).

---

## 7. References

- Product positioning + ICP: `product_design_v2.md`
- Phase 2+ architecture: `architecture_and_design_v3.md`
- Phase 1 architecture (still load-bearing): `architecture_and_design_v2.md`
- OAuth integration skill: `.claude/skills/oauth-service-integration/SKILL.md`
- Universe seed: `backend/data/universe/broad_universe_seed_v1.csv` + addendum + pre-IPO JSON
- Migration files: `backend/alembic/versions/0001` through `0005`
- Memory pointer: `memory/project_alphagraph_q3_roadmap.md` (auto-loaded into context)
