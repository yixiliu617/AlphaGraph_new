# AlphaGraph — Product Design v2

**Status:** active. Supersedes `product_design_v1.md` for product positioning and pilot ICP. v1 is preserved for the original 6-layer architecture sketch.
**Last updated:** 2026-04-29.

## TL;DR

AlphaGraph is **the AI-bottleneck research platform for buyside analysts and PMs**. Every claim is source-traced. Every Asian earnings call is multilingual-searchable. Every chat answer cites its filing — or refuses to answer. We start where Bloomberg, ChatGPT, and Smartkarma all fail: **zero-hallucination, supply-chain-deep, multilingual** coverage of the AI infra value chain.

---

## 1. Positioning — the AI bottleneck thesis

The product's organising idea: **"the companies that determine how fast AI scales — and where the bottlenecks are."** This is not "tech stocks," not "S&P 500," and not "AI-themed ETF." It's a thesis graph spanning compute → infra → hosting → energy → materials → software → consumer → industrial.

This thesis is what makes the product defensible vs. incumbents:

| Incumbent | What they do well | Where they fail | AlphaGraph differentiator |
|---|---|---|---|
| Bloomberg / FactSet | Breadth, speed, low-level data | Source-trace; auto-extracted forward guidance; Asia coverage depth | Every number clickable to its filing; auto-extracted guidance; multilingual transcripts |
| ChatGPT / Claude (raw) | Synthesis, writing | Hallucinates numbers; no live data layer | Mandatory citation gate; refuses to answer without source |
| Smartkarma | Asia analyst content | No quant layer; subjective | Reconciled fundamentals + structured guidance + numbers |
| Otter / Fireflies | Generic meeting transcripts | No investment-research vocabulary; no Asian-language depth | Earnings + expert-call corpus, EN↔ZH/JP/KR translation, semi/finance terminology |

## 2. Target users (ICP)

Validated through customer discovery with 8 willing pilots whose coverage spans 6 distinct buyside profiles:

1. **HK + US TMT (software/tech)** — software analyst covering MSFT, GOOGL, ADBE, CRM and Tencent, BABA, BIDU, etc.
2. **Taiwan semi** — analyst covering TSMC, UMC, MediaTek, ASE, Foxconn AI-server ODMs
3. **Japan domestic** — Japan-coverage analyst across semi-cap-eq (Tokyo Electron, Disco, Lasertec) + components (Murata, TDK) + consumer (Sony, Recruit)
4. **HK ADRs (tech + consumer)** — China AI internet + EV + consumer plays
5. **Datacenter + neocloud + power** — capacity and energy bottleneck thesis: CoreWeave, Constellation, Vistra, GE Vernova
6. **Industrial (AI-tilted)** — cooling, electrical, aerospace/defense plays adjacent to AI demand

All 6 share the same meta-question: **"What's the next bottleneck, and who owns it?"** Different lenses on the same elephant.

Future expansion (v2 → v3): sell-side equity research, wealth-management RIAs, family-office private wealth.

## 3. The 8 user requests (validated)

From customer discovery, pilots asked for (in their words):

1. **Live transcript + translation + searchable accumulation** (EN↔JP/ZH/KR). Removing language barrier in investment research.
2. **Agent that joins meetings** (earnings, group, expert calls) — capture transcript + summary, query interactively.
3. **Agent that gets unstructured data** — IR sites, filings, expert calls, news.
4. **Market monitoring agents** — alerts on price hits, condition matches.
5. **Technical analysis agents** — teach-by-example chart pattern recognition (most ambitious; deferred to Pillar C).
6. **Good semi AI analyst** — chat surface that knows the data, answers questions, charts in chat.
7. **Easy note search + knowledge accumulation + team sharing.**
8. **★ Zero-hallucination AI** — every number verified, traceable to filings/presentations. *This is the meta-feature.*

Request #8 is the architectural commitment that makes #6 trustworthy and lets us defend against ChatGPT-with-SEC-search.

## 4. Three product pillars

| Pillar | Maps to user requests | Time-to-pilot demo | Status |
|---|---|---|---|
| **A — Trustworthy AI semi analyst** | #6, #8, parts of #3, #4 | 4–6 weeks | active build |
| **B — Research knowledge layer** | #1, #2, #7 | 8–12 weeks (after A) | not started |
| **C — Personal pattern agent** | #5 | 12+ weeks (or v2) | deferred |

**Pillar A** is the assembly point for what's already built (data layer, fundamentals, guidance, news, prices, universe). Adds: chat surface with mandatory citations, alerts, cross-company forward-guidance dashboard, notes search, "bring your own filing" extraction.

**Pillar B** is the multilingual transcript + meeting agent layer. Heavier engineering (audio capture from public webcasts, EN↔ZH↔JP translation, queryable transcript repository, eventual Zoom/Teams bot).

**Pillar C** is the teach-by-example TA agent — research-grade ML. Defer to v2 unless a pilot specifically asks. Pre-defined patterns + "save this view as alert" delivers 80% of the value with 20% of the work.

## 5. Pillar A — what we ship in 4–6 weeks

| Week | Build | Ship |
|---|---|---|
| 1 | Universe + prices backfill (Stream 1) | Deploy to a real URL (Vercel + Render + Neon) |
| 2 | Chat with mandatory citations (basic) + alerts MVP | Walkthrough demo with 3 pilot users |
| 3 | Cross-company forward-guidance dashboard | Pilot user feedback → top-3 friction list |
| 4 | Notes search + "bring your own filing" extraction | First daily-active pilot |
| 5 | Earnings season prep — NVDA/TSMC/AMD/MRVL Q2 ready, "what changed" diffs | Demo earnings-season lift to 5 more prospects |
| 6 | Pillar B kickoff — earnings-call audio capture pilot for 4 names | Multilingual transcript demo to Asia-coverage pilots |

Pricing experiment in week 3: **$300/mo individual, $1,500/mo team-of-5** for buyside. Easy to lower; hard to raise.

## 6. Pillar B — research knowledge layer (weeks 6–18)

In sequence:
1. Earnings-call audio capture from public webcasts (NOT phone-bot-joins). Reuse `meeting-transcription` skill scaffolding.
2. EN↔ZH↔JP translation pipeline; bilingual side-by-side rendering.
3. Searchable transcript repository — cross-quarter, cross-company comparison ("show me every time TSMC's CFO said 'gross margin headwind'").
4. Meeting bots (Zoom, Teams) — only after #1–3 prove the value of investment-research-specific transcripts.
5. Notes team-sharing surface (extends Pillar A's notes search).

## 7. Constraints we're choosing

What AlphaGraph is **not** — by deliberate design, not by accident:

- **Not breadth-first.** We will never cover IWM Russell 2000 or commodities-only or fixed income. Pick the AI-bottleneck graph and go deep.
- **Not multi-language UI in v1.** UI is English. Content (transcripts) is multilingual. Ship later.
- **Not a Bloomberg replacement for execution / quote / OMS.** We're research-side only.
- **Not multi-channel in v1.** Web only. Telegram / Slack / Email channels are deferred until web has paying users.
- **Not retail.** Buyside-priced ($300+/seat), buyside-density UI. Retail tier is post-revenue.
- **Not generative-only.** Every AI feature has a verification path back to source. If it can't cite, it refuses.

## 8. Success criteria

| Milestone | Definition | Target date |
|---|---|---|
| First pilot using daily | One named pilot opens AlphaGraph at least 3 days/week for 2+ consecutive weeks | week 4 |
| First paying pilot | $300+/mo committed for 12 months OR $1,500+/mo team commit | week 6 |
| 5 paying users | Each on at least the $300 tier | week 12 |
| Q2 earnings demo | Auto-extracted "what changed" diff vs prior quarter for 8 AI-infra names; sent to 20 prospects | week 5 (matches Q2 earnings season) |
| Hallucination rate | <0.5% of AI-generated claims in chat / summaries fail source-verify | from week 2, measured weekly |

## 9. Open product decisions

| ID | Decision | Blocks | Owner |
|---|---|---|---|
| PD-1 | Pricing tier exact thresholds — confirm $300 / $1,500 with pilots | Week 3 | Sharon |
| PD-2 | Default citation render style — pill-with-hover vs. footnote-link | Week 2 | Sharon |
| PD-3 | "Refuse to answer without citation" — strict gate vs. soft warning | Week 2 | Sharon |
| PD-4 | Auto-promotion threshold — should pilots be able to add ANY ticker, or only those in our broader universe + curated approval? | Week 1–2 | Sharon |
| PD-5 | Deployment target — Render+Neon (fastest) vs. AWS (long-term) for first pilot URL | Week 1 | Sharon |

## 10. References

- 6-layer architecture sketch (legacy): `product_design_v1.md` § 1
- Universe schema + thesis groups: `architecture_and_design_v3.md` § 2
- Active rolling plan: `roadmap_v1.md`
- Meeting-transcription skill scaffolding: `.claude/skills/meeting-transcription/SKILL.md`
