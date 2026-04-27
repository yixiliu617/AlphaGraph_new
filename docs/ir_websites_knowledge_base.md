# Company IR Website Knowledge Base

> **Purpose.** A living, append-only catalog of every company's investor-relations site we have verified, the file types each one publishes, where to find them, what's inside, the corner cases we tripped over, and a per-site change log.
>
> **Audience.** Future agents (and humans) building a new extractor for an existing or new company; debugging extraction drift after a site redesign; planning the rollout of a new region.
>
> **How to use.** Each company has a self-contained section. When you build / update an extractor, append to the relevant section's "Change log" subsection with date + summary. When a site is redesigned and breaks our extractor, the "Recovery playbook" subsection tells you where to look first.
>
> **Status taxonomy.** Each company is tagged:
> - 🟢 **Live** — extraction running, parquet written, dashboard panel present
> - 🟡 **Recon done** — IR site mapped, file URLs verified, extractor not yet built
> - ⚪ **Planned** — on the coverage list, no recon yet
>
> *Last updated: 2026-04-27*

---

## Index

| # | Ticker | Company | Region | Status | IR landing |
|---|---|---|---|---|---|
| 1 | 2330.TW | TSMC | Taiwan | 🟢 Live | https://investor.tsmc.com/english |
| 2 | 2303.TW | UMC | Taiwan | 🟢 Live | https://www.umc.com/en/Investors |
| 3 | 2454.TW | MediaTek | Taiwan | 🟢 Live | https://www.mediatek.com/investor-relations/financial-information |
| 4 | 3711.TW | ASE Technology Holding | Taiwan | ⚪ Planned | (TBD) |
| 5 | 6488.TW | GlobalWafers | Taiwan | ⚪ Planned | (TBD) |
| 6 | NVDA | NVIDIA | US | 🟢 Live (EDGAR) | https://investor.nvidia.com/ |
| — | (other US semis) | AMD, INTC, AAPL, AMAT, AVGO, CDNS, DELL, KLAC, LITE, LRCX, MRVL, MU | US | 🟢 Live (EDGAR) | per-company IR sites; we use SEC EDGAR XBRL not the IR sites |

US-region companies are sourced via SEC EDGAR (XBRL + 8-K Item 2.02 exhibits). Their IR sites are not the primary data path; the EDGAR-extraction skill (`.claude/skills/edgar-topline-extraction/`) is authoritative for those.

---

## 1. TSMC (2330.TW) — 🟢 Live

**Status:** Fully integrated. Quarterly management report, earnings release, presentation, transcript (LSEG), full financial statements all extracted into silver. Dashboard panel: TSMC sub-tabs in DataExplorer.

### IR landing
- Primary: https://investor.tsmc.com/english
- Quarterly results: https://investor.tsmc.com/english/quarterly-results — protected by Cloudflare Turnstile.
- Deep-link template (works without the SPA index, AFTER Cloudflare clearance is established):
  `https://investor.tsmc.com/english/quarterly-results/{YYYY}/q{N}` — e.g., `/2025/q3`. This loads a per-quarter page with 5 PDF anchors.

### Files published per quarter

| Type | Filename pattern | Format | What's inside |
|---|---|---|---|
| Management Report | `4Q23ManagementReport.pdf` (varies by year) | Workiva PDF (2023+) or older Word | Headline P&L (P&L summary table on p. 3-4), revenue by technology / platform / geography, capex breakdown, balance sheet highlights, full-year summary |
| Earnings Release | `4Q23PressRelease.pdf` | Workiva | Same headline numbers in prose form, plus full-year summary in Q4 reports |
| Presentation | `4Q23Presentation.pdf` | PowerPoint→PDF | Slide deck — wafer revenue chart, segment mix, capex chart, guidance slide (公司展望) |
| Transcript | `4Q23Transcript.pdf` | LSEG StreetEvents (since ~2024); pre-2024 = Refinitiv | Speaker turns: `{Name} - {Company} - {Role}` headers, then PRESENTATION + Q&A sections |
| Full Financial Statements | `4Q23FullFinancialStatement.pdf` | Workiva | Audited TIFRS statements: balance sheet, P&L, cash flow, equity |

### Extraction strategy

- **Cloudflare bypass:** Playwright with a persistent profile at `C:/Users/Sharo/.alphagraph_tsmc_profile`. Use `page.evaluate(fetch)` for **same-origin fetch** so the request carries the Cloudflare clearance cookie. Plain `urllib` always 403s.
- **Module map:**
  - `backend/scripts/extractors/tsmc_management_report.py` — primary financials extractor (P&L summary table on p. 3-4)
  - `backend/scripts/extractors/tsmc_transcript.py` — LSEG transcript parser (handles both LSEG and Refinitiv eras)
  - `backend/scripts/extractors/tsmc_guidance.py` — extracts the guidance table (業績展望) from the presentation deck
- **Silver layer:** `backend/data/financials/quarterly_facts/2330.TW.parquet` (8,678 rows, 30+ metrics), plus `transcripts/2330.TW.parquet` and `guidance/2330.TW.parquet`
- **Bronze layer:** `backend/data/financials/raw/2330.TW/{YYYY}/Q{N}/{type}.json` per PDF

### Layout / format quirks

- **Workiva PDF text extraction quirks:**
  - Numbers can appear inline (`1,134.10  1,046.09  839.25`) or one-per-line. The shared `take_n_numbers` walker in `_quarterly_common.py` handles both.
  - "Net Revenue" row label sometimes wraps as "Net Revenue\n(NT$M)" — multi-line label continuations supported up to 2 lines.
- **3 distinct P&L summary table layouts** across history (Word legacy 2014-2018, transitional 2019, Workiva 2020+). Each handled by the same row-spec walker because the row labels are stable.
- **Transcript era split:** Pre-2024 transcripts are Refinitiv-formatted (`EVENT DATE/TIME:` header, slightly different speaker syntax); 2024+ are LSEG. The transcript extractor probes both via `_PERIOD_TITLE_RE` + `_DATE_LSEG_RE` / `_DATE_EVENT_RE`.
- **Wafer revenue % by technology** — TSMC merged "16nm" + "20nm" into "16/20nm" in 1Q25. Old reports keep the split. The API filters out the legacy "16nm" / "20nm" rows when "16/20nm" exists, otherwise the totals sum to 109%.

### Information captured today

- ✅ Quarterly P&L (~30 metrics): revenue, COGS, gross profit/margin, OpEx, OpInc/margin, net income, EPS, capex, FCF, ending cash, wafer shipments
- ✅ Revenue % by technology (every node from 3nm to 0.5um and above)
- ✅ Revenue % by platform (HPC, Smartphone, IoT, Automotive, DCE, Others)
- ✅ Revenue % by geography (NA, China, Asia Pacific, Japan, EMEA)
- ✅ Quarterly guidance vs actual for revenue, gross margin, operating margin, USD/NTD rate
- ✅ Earnings call transcripts (LSEG / Refinitiv): speaker turns, presentation/Q&A split, full-text searchable

### Information NOT captured

- ❌ Capacity utilization rate — TSMC does NOT disclose this (competitive intel)
- ❌ Per-platform revenue dollar amounts — only % shares published
- ❌ ASP per wafer — never disclosed by TSMC
- ❌ Per-customer revenue breakdown — never disclosed

### Recovery playbook (if site redesigned)

1. Open https://investor.tsmc.com/english in a fresh Playwright session, manually clear Cloudflare once.
2. Inspect anchors on `/quarterly-results` — find the new PDF URL pattern.
3. Update the URL template in `backend/app/services/tsmc_crawler.py`.
4. Re-run extraction on a known quarter; cross-check headline numbers (4Q25 revenue should be ≈ NT$868B).
5. If Workiva → new producer (e.g., Adobe), re-test `take_n_numbers` against page 3-4 of the new PDF; the inline-number-on-one-line case may need adjustment.

### Change log

| Date | Change |
|---|---|
| 2026-04-22 | Initial integration: management report + transcript + guidance extractors live; full-history backfill (2013Q1 onwards). |
| 2026-04-23 | Cross-source consistency check shipped: revenue identity, cash-flow identity. TSMC has NEVER missed its own revenue guidance midpoint across 33 quarters of history. |
| 2026-04-26 | Time-axis sort flip: tables now show newest-first (rule documented in `time-axis-sort-convention` skill). |

**Skill:** `.claude/skills/tsmc-quarterly-reports/` — full corner-case playbook + Cloudflare bypass details.
**Memory:** none yet (skill captures everything).

---

## 2. UMC (2303.TW) — 🟢 Live

**Status:** Fully integrated. Quarterly management report extracted across 24 quarters (2020Q1-2025Q4) with 4,910 silver rows + 144 guidance records. Dashboard panel: UMC sub-tabs in DataExplorer.

### IR landing
- Primary: https://www.umc.com/en/Investors
- Quarterly results: https://www.umc.com/en/Download/quarterly_results — public, no auth/CF gate
- Per-quarter detail page: `/en/Download/quarterly_results/QuarterlyResultsDetail/{YYYY}/{YYYY}Q{N}`
- Deep-link template (PDFs):
  `https://www.umc.com/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/{year}/Q{q}_{year}/UMC{yy}Q{q}_{type}.pdf`
  - `{type}` ∈ `report`, `financial_presentation-E`, `financial_statements-E`, `conference_call`
  - Verified for 2020Q1 through 2025Q4. Pre-2020 archive lives in a different folder (`Quarterly_2010-2019_English_pdf/`) — not yet exercised.

### Files published per quarter

| Type | Filename pattern | Format | What's inside |
|---|---|---|---|
| Quarterly Report | `UMC25Q4_report.pdf` | Microsoft Word PDF | THE PRIMARY SOURCE — 13 pages: P&L, segment breakdowns, wafer/capacity/utilization, ASP chart, cash flow, balance sheet, full-year summary (Q4), forward guidance |
| Financial Presentation | `UMC25Q4_financial_presentation-E.pdf` | PowerPoint→PDF | Slide deck with the same numbers as the report, presented graphically |
| Financial Statements | `UMC25Q4_financial_statements-E.pdf` | Word | Audited TIFRS full statements |
| Conference Call | `UMC25Q4_conference_call.pdf` | Word | **NOT a transcript — 1-page calendar invitation only.** Date, dial-in numbers, agenda. UMC does not publish call transcripts. |

### Extraction strategy

- **No Cloudflare, no Playwright needed.** Plain `urllib.request` works for both detail pages and PDF asset URLs.
- **Module:** `backend/scripts/extractors/umc_management_report.py` (single file, 800+ lines, all sections)
- **Silver layers:**
  - `backend/data/financials/quarterly_facts/2303.TW.parquet` (4,910 rows, 48 metrics)
  - `backend/data/financials/guidance/2303.TW.parquet` (144 guidance records across 15 issuing reports)

### Sections extracted (page-by-page from `UMC{YY}Q{N}_report.pdf`)

| Page | Section | Periods | Status |
|---|---|---|---|
| 3-4 | Operating Results (P&L summary, 5-column layout) | 3 (cur/prev/YoY) | ✅ |
| 5 | Cash Flow Summary (curQ + prevQ in NT$ million) | 2 | ✅ — capex_ppe + capex_intangibles → derived capex_total + free_cash_flow |
| 6 | Balance Sheet Highlights (curQ/prevQ/4Q-ago in NT$ billion) | 3 | ✅ — cash, AR, AP, inventory, DSO, DOI, ST/LT debt, equipment payables, debt-to-equity |
| 7 | Geography breakdown | 5 rolling | ✅ |
| 8 | ASP chart (USD per wafer, line chart) + Wafer / Utilization tables | 5 rolling | ✅ — wafer/capacity/util tables extracted; **ASP read visually from chart** (no tabular numbers published — ±25-50 USD precision per period) |
| 9 | Total Capacity (12" K equivalents) + per-FAB capacity breakdown | 5 rolling | ✅ for total / ❌ for per-FAB (deferred — complex layout, low analytical priority) |
| 10 | Full-Year Results (Q4 reports only) | 2 (FYxx, FYxx-1) | ✅ |
| 11 | Forward Guidance bullets + Recent Announcements | next-Q + next-FY | ✅ — qualitative ("high-20% range") + structured ranges via heuristic mapping |

### Layout / format quirks (UMC-specific)

1. **Standalone dash placeholder.** When QoQ% can't be computed (prior period was negative), the cell renders as a lone `-`. The shared `_NUM_PLACEHOLDER` set (in `_quarterly_common.py`) accepts `-`, `—`, `N/A`, `n/a` as None placeholders so the row walker doesn't terminate early.

2. **Period-header trap (pre-2023 reports).** Older PDFs typeset the prose intro one word per line. Stray standalone `1Q22` tokens appear inside the commentary, which fullmatch the period regex but are NOT the table header. Fix: require a cluster of 3 fullmatch lines within ≤ 8 lines AND 3 distinct period labels. Stray prose tokens repeat curQ and fail distinctness.

3. **Period-header trap (segment tables — multi-line).** 2022+ reports put 5 periods on one space-separated line ("3Q25 2Q25 1Q25 4Q24 3Q24"); pre-2022 split across 3-5 lines. Walker must collect period tokens across consecutive lines, stopping at the first non-period line.

4. **8" → 12" wafer-equivalent unit shift in 2024.** Pre-2024 reports use 8" K equivalents; 2024+ reports restate in 12" K equivalents (1 12" wafer ≈ 2.25 × 8"). Detect via `'12" K equivalents'` / `'8" K equivalents'` substring in PDF text; tag fact's `unit` field accordingly. API filters by unit so the time-series is continuous from 1Q23 onward (when restated).

5. **Total Capacity table reuses period header.** "Total Capacity" section has no period header in its own slice — it shares with "Quarterly Capacity Utilization Rate" above. Walker falls back to a rolling-5-quarter window computed from cur_period.

6. **Annual P&L (page 10).** Period header sits below ~25 blank lines (PowerPoint chart leaks through fitz as whitespace); scan must walk full slice rather than just prologue. Period labels emit as `FY25` (not `4Q25`) and `period_end = Dec 31`.

7. **Forward guidance is qualitative, not numeric.** UMC issues phrases like "high-20% range" / "mid-70% range" / "Will remain flat", not TSMC-style numeric ranges. We map qualifiers to implied numeric ranges:
   - `low-Xx%` → X to X+3
   - `mid-Xx%` → X+3 to X+7
   - `high-Xx%` → X+6 to X+9
   The verbal text is preserved alongside.

8. **Annual CAPEX guidance.** Bullet of the form "2026 CAPEX: US$1.5 billion" — for_period set to `FY{yy}` (not next-quarter). Realized comparison: sum quarterly `capex_total` across the 4 quarters of for_year, convert NTD → USD using avg `usd_ntd_avg_rate`. **UMC has consistently underspent capex guidance by ~10-15%** across FY24-FY25.

### Information captured today

- ✅ Quarterly P&L (~12 metrics — net_revenue, gross_profit, operating_expenses, operating_income, non_operating_items, net_income, eps, eps_adr, usd_ntd_avg_rate, etc.)
- ✅ Cash flow (16 metrics: ops/investing/financing, capex breakdown, depreciation, dividends, bonds, FCF derived)
- ✅ Balance sheet (14 metrics: cash, AR, AP, inventory, DSO/DOI, ST/LT debt, debt-to-equity)
- ✅ Annual P&L (FY22-FY25 from Q4 reports)
- ✅ Wafer shipments + total capacity + utilization (in 12"-eq, restated history back to 1Q23)
- ✅ Blended ASP (chart-estimated, ±25-50 USD precision, 25 quarters back to 4Q19)
- ✅ Revenue % by geography (4 dims), technology (9 nodes), customer type (Fabless/IDM), application (Computer/Communication/Consumer/Others)
- ✅ Forward guidance + historical guidance vs actual (5 metrics)

### Information NOT captured

- ❌ Earnings call transcript — UMC doesn't publish one (the conference_call.pdf is just an invitation)
- ❌ Per-FAB capacity breakdown (deferred — complex layout)
- ❌ Recent Announcements / Press Releases section (page 11) — captured as raw text in bronze, not structured

### Recovery playbook

1. URL pattern is stable + simple — if a quarter's PDF doesn't load, check the new pattern at `https://www.umc.com/en/Download/quarterly_results/QuarterlyResultsDetail/{YYYY}/{YYYY}Q{N}` — anchors there are authoritative.
2. If layout shifts in a future report (e.g. new section), update the section anchor + end-anchor constants in `umc_management_report.py`.
3. Always re-run the segment-share sum check (`(metric, period)` should sum to 100%); if any sum drifts, the period-header detection needs tightening.

### Change log

| Date | Change |
|---|---|
| 2026-04-26 | Initial extractor + 24-quarter backfill (2020Q1–2025Q4). 4,910 silver rows. Identity check passes (gross + cogs ≈ revenue within 0.0017%). |
| 2026-04-26 | Added Cash Flow / Balance Sheet / Annual / Guidance sections + ASP chart-reading. Updated to 4,910 rows with 48 metrics. |
| 2026-04-26 | Forward guidance card at top of Guidance tab (per `guidance-tab-pattern` skill). |
| 2026-04-27 | Documented 8"→12" unit shift handling (`kpcs_8in_eq` vs `kpcs_12in_eq`). |

**Memory:** `~/.claude/projects/.../memory/project_taiwan_ir_extraction_umc.md` — full quirk catalog.

---

## 3. MediaTek (2454.TW) — 🟢 Live

**Status:** Press-release financials extracted across 36 quarters (2017Q1–2025Q4) with 1,790 silver rows. Materials catalog (all 5 PDF types per quarter) live in dashboard. Transcript text not yet ingested.

### IR landing
- Primary: https://www.mediatek.com/investor-relations
- Financial Information page: https://www.mediatek.com/investor-relations/financial-information
- **Single-page index:** 612 KB of server-side-rendered HTML containing every PDF anchor for the full history. No SPA / dropdowns / pagination — parse anchors once and you're done.
- Two URL families:
  - **Quarterly Earnings Release** (analyst-facing materials):
    `https://www.mediatek.com/hubfs/MediaTek%20Assets/Pdfs/Quarterly%20Earnings%20Release/{YYYY}/Quarterly%20Earnings%20Release-{YYYY}Q{N}/{filename}.pdf`
  - **Financial Reports** (TWSE-mandated full statements):
    `https://www.mediatek.com/hubfs/MediaTek%20Assets/Pdfs/Financial%20Reports/{YYYY}/Q{N}-Consolidated-Report.pdf` (Q4 also has Unconsolidated)

### Files published per quarter (current era, 2022Q1+)

| Type | Filename pattern | Format | What's inside |
|---|---|---|---|
| Earnings Call Invitation | `Earnings call invitation.pdf` | Acrobat PDFMaker (Word) | Date, dial-in, agenda. Posted ~3 weeks BEFORE the call (so the most-recent quarter often has only this until call day, then 4 more PDFs land within hours) |
| Press Release | `Press Release.pdf` | Acrobat PDFMaker | THE PRIMARY SOURCE for our extractor — narrative prose pages 1-3, then a Consolidated Income Statement table on pages 4-5 (clean tabular: Net Sales, OpCosts, GP, expenses by line, OpInc, NetInc, EPS attributable to parent, Non-controlling interests) |
| Presentation | `Presentation.pdf` | PowerPoint→PDF | Slide deck — chart-form revenue / GM / NI bars; segment mix (Smartphone, Computing & Connectivity, Power IC, Others) |
| Transcript | `Transcript.pdf` | Microsoft Word | English transcript (PREPARED REMARKS by IR + CFO + CEO, then Q&A). **Published since 2021Q2.** Pre-2021Q2: no transcript at all; 2021Q1: `Prepared-remark.pdf` only. |
| Financial Statements | `Financial Statements.pdf` | Word | Full TIFRS statements (audited) |
| Consolidated Report | `Q{N}-Consolidated-Report.pdf` | TWSE-mandated XBRL→PDF | Full audited statements; mostly redundant with Financial Statements |
| Unconsolidated Report | `Q4-Unconsolidated-Report.pdf` | Same | Q4 only — parent-company-only view |

### Extraction strategy

- **No Cloudflare, no Playwright needed.** Plain `urllib.request` with a normal browser UA returns HTTP 200.
- **Module:** `backend/scripts/extractors/mediatek_press_release.py` — table-first extraction using shared `_quarterly_common` primitives + prose regex for margin / cash flow / FX (which aren't in the table).
- **PDF index:** `backend/data/financials/raw/2454.TW/_index.json` — built by scraping the financial-information page anchors. Refresh with `tools/mediatek_refresh_pdf_index.py` (or rerun the inline scraper from this session).
- **Silver:** `backend/data/financials/quarterly_facts/2454.TW.parquet` (1,790 rows, 18 metrics).

### Layout / format quirks (MediaTek-specific)

1. **Hybrid prose + table.** Pages 1-3 are narrative ("Operating expenses for the quarter were NT$47,431 million ... up from NT$43,924 million in the previous quarter and NT$45,589 million in the year-ago quarter"). Pages 4-5 contain a clean `Consolidated Income Statement` table — that's our primary source.

2. **Filename separator drift between quarters:**
   - 2025Q2+ : space-separated (`Press Release.pdf`, `Financial Statements.pdf`)
   - 2017-2025Q1 : hyphen-separated (`Press-Release.pdf`, `Financial-Statements.pdf`)
   - Always parse the index HTML for actual URLs rather than templating; the index has both forms.

3. **Period header in segment tables — blank lines between each period token.** PyMuPDF text extraction puts a blank line between each period:
   ```
   '4Q25 '
   ' '
   '3Q25 '
   ' '
   '4Q24 '
   ```
   Cluster collector must skip blank lines (`continue`) instead of terminating (`break`).

4. **`(Note2)` period-suffix trap in 2019 reports.** Pre-2020 reports decorate prev-Q and YoY-Q labels with restatement footnote suffix: `'4Q18(Note2)'` / `'1Q18(Note2)'`. The current-Q label has no suffix. Fix: strip parenthetical suffixes (`re.sub(r"\([^)]*\)", "", tok)`) before fullmatch.

5. **Sign convention divergence.** MediaTek's prose presents costs as positive magnitudes ("R&D expenses of NT$39,248 million"). The press release table shows them in parens (negatives). Our extractor uses `sign=-1` flag in `INCOME_STATEMENT_ROWS` to flip back to positive — so `r_and_d + selling + g_and_a ≈ operating_expenses` reads naturally without bookkeeping. (TSMC and UMC kept costs negative as published.)

6. **Pre-2017 layout entirely different.** Folders are named "Investor Conference Report" / "Material" with prefixed filenames. Out of scope for the current press-release extractor.

7. **2021Q1 and earlier — no transcript.** Transcript ingestion can only cover 2021Q2 onward.

### Information captured today

- ✅ Quarterly P&L (18 metrics: net_revenue, cost_of_revenue, gross_profit, selling/g_and_a/r_and_d expenses, operating_expenses, operating_income, non_operating_items, net_income_before_tax, income_tax_expense, net_income, net_income_attributable, minority_interests, eps, gross_margin, operating_margin, net_profit_margin, operating_cash_flow)
- ✅ 36-quarter backfill (2017Q1–2025Q4)
- ✅ Identity check holds across all 36 quarters: `gross_profit + cost_of_revenue ≈ net_revenue` within 0.0017% rounding
- ✅ Materials catalog (all 5 PDF types per quarter, 93 quarters indexed back to 2003 — earliest era is the older "Investor Conference Report" folder family)
- ✅ **Earnings call transcripts** (since 2026-04-27): 18 quarters (2021Q2–2025Q4), 952 speaker turns, 19 unique speakers (CEO Dr. Rick Tsai, CFO David Ku, IR Jessie Wang + analysts from UBS/MS/BoAML/etc.). Full-text searchable. Module: `mediatek_transcript.py`.
- ✅ **Forward guidance** (since 2026-04-27): structured ranges parsed from the CFO's prepared-remarks guidance section — quarterly revenue range (NT$ B), gross margin point ± spread, FX rate forecast. 18 issuing reports, 203 records. Realized actuals join: revenue → press_release `net_revenue / 1000`; gross margin → derived `gross_profit / net_revenue`.

### Information NOT captured

- ❌ Segment mix percentages — MediaTek doesn't publish these in the press release table; they only appear as charts on the Presentation slide deck (chart-reading deferred — same approach as UMC's blended ASP).
- ❌ Per-customer revenue — never disclosed.
- ❌ Realized USD/NTD rate — guidance card shows the *forecasted* rate but the realized rate is mentioned only in the *next* quarter's transcript prose ("The foreign exchange rate applied to the quarter was 31.1 NT dollar..."). Not yet extracted; could be added with a small prose-regex pass over each transcript.
- ❌ Pre-2021Q2 transcripts — MediaTek didn't publish them; 2021Q1 has a `Prepared-remark.pdf` only (different format, deferred).

### Recovery playbook

1. If the financial-information page redesigns, the URL pattern is the durable thing — `Quarterly Earnings Release/{YYYY}/Quarterly Earnings Release-{YYYY}Q{N}/` has been stable since 2017.
2. To refresh the PDF index after MediaTek posts a new quarter, rerun the inline index-scrape script (the one used to write `_index.json`). The endpoint is anchor-driven, not URL-template-driven, so it auto-picks up new file types if MediaTek adds them.
3. If `Consolidated Income Statement` section header changes wording, the existing extractor falls back to `Income Statement` (looser anchor). Add the new wording to the anchor list in `mediatek_press_release.py`.

### Change log

| Date | Change |
|---|---|
| 2026-04-26 | Initial extractor + full 36-quarter backfill (2017Q1–2025Q4). |
| 2026-04-26 | Found `(Note2)` period-suffix trap in 2019 reports; added strip-parenthetical normalization. |
| 2026-04-27 | Built `_index.json` PDF catalog from financial-information page (93 quarters, 5-7 PDFs each). |
| 2026-04-27 | Added Materials sub-tab in MediaTekPanel showing all PDF types per quarter, with the "upcoming earnings call invitation" pattern surfaced as a yellow `UPCOMING` badge on the most recent quarter when only the invitation is posted. |
| 2026-04-27 | Built `mediatek_transcript.py` extractor — parses MediaTek's Word-format transcript (PREPARED REMARKS section + Q&A section). Speaker headers are `{Name}, {Role}` comma-separated (vs TSMC's LSEG `{Name} - {Company} - {Role}` dash-separated); Q&A turns use `Q – {Name}, {Firm}` and `A – {Speaker} ({Role})` markers. Backfilled 18 quarters (2021Q2-2025Q4) → 952 silver turns, 19 unique speakers. |
| 2026-04-27 | Forward guidance extractor — parses 3 patterns from the CFO's prepared-remarks guidance section: revenue range (`"in the range of NT$X billion to NT$Y billion"` → low/high/mid), gross margin (`"X%, plus or minus Y percentage points"` → point + range), FX rate (`"forecasted exchange rate of X NT dollars to 1 US dollar"` → point). 18 issuing reports → 203 guidance records. Guidance vs actual computed: revenue → press_release `net_revenue / 1000`; gross margin → derived. 4Q25 results: revenue BEAT high (150.19 vs 142.1-150.1), GM in range (46.13% vs 44.5-47.5%). |
| 2026-04-27 | Added Guidance + Transcripts sub-tabs in MediaTekPanel. Guidance tab leads with forward card per `guidance-tab-pattern` skill; Transcripts tab mirrors TSMC's pattern (quarter list, expand to read, full-text search). |
| 2026-04-27 | **3Q23 transcript file mislabeled at source** (MediaTek bug). The URL `.../Quarterly Earnings Release-2023Q3/Transcript.pdf` actually serves the **3Q24** PDF (title page reads `MediaTek 3Q24 Earnings Call · October 30, 2024`). Discovered via the new `period_continuity` data-quality check — silver had 18 issuing reports but the expected continuous range was 19 (2Q21-4Q25). Mitigation: bad PDF quarantined as `transcript.pdf.WRONG_CONTENT_AT_SOURCE_actually_3Q24` so future extracts don't re-ingest. **Recovery options for 3Q23:** (a) periodically re-fetch the URL — MediaTek may correct it; (b) source the 3Q23 transcript from a third-party archive (e.g. SeekingAlpha, LSEG); (c) accept the gap and surface it via the period-continuity check. |
| 2026-04-27 | **3Q21 + 4Q22 + 2Q25 guidance regex bugs fixed.** Three phrasing variants my initial regex missed: (a) prefix variant: `"For the fourth quarter, we expect revenue to be in the range of NT$..."` (vs `"first quarter revenue to be in the range of NT$..."`); (b) typo: `"NT$ 101.7billion"` (no space); (c) GM variant: `"Gross margin for the third quarter is forecasted at..."` (interjects period phrase). Regexes loosened to anchor on `"in the range of NT$"` and allow optional `for the {Nth} quarter` between "Gross margin" and "is forecasted at". |
| 2026-04-27 | Built **modular data-quality framework** (`backend/app/services/data_quality/`) — registry-driven checks per dataset (period continuity, source-period match, identity, range, sign, share-sum, row-count-min, duplicate-key). Mandatory `period_continuity` check on every time-series dataset catches issues like the above 3Q23 + 2Q16 + sundry pre-2018 TSMC gaps before they become user-facing surprises. |

**Memory:** `~/.claude/projects/.../memory/project_taiwan_ir_extraction_mediatek.md` — full quirk catalog including the prose-then-table dual structure.

---

## 4. ASE Technology Holding (3711.TW) — ⚪ Planned

**Status:** Not yet recon'd. Top-2 OSAT (Outsourced Semiconductor Assembly and Test) globally; high coverage priority for Taiwan semis.
**Tentative IR landing:** https://www.aseglobal.com/en/investor-relations/ (verify)
**Recon checklist when started:**
- [ ] Confirm IR site is Cloudflare-free (or document the gate)
- [ ] Find quarterly earnings release / report URL pattern
- [ ] Identify which PDF types are published (mgmt report? presentation? transcript?)
- [ ] Inspect P&L layout: is there a clean Consolidated Income Statement table, or all prose?
- [ ] Note any company-specific quirks (capacity disclosures, segment breakdowns)

---

## 5. GlobalWafers (6488.TW) — ⚪ Planned

**Status:** Not yet recon'd. Top-3 silicon wafer supplier; high relevance for upstream foundry capacity analysis.
**Tentative IR landing:** https://www.gwafers.com.tw/en/investors (verify)
**Recon checklist when started:**
- [ ] Site language (Chinese-only / bilingual / English-only)
- [ ] PDF accessibility
- [ ] What metrics are disclosed (Si wafer ASP? volume by diameter?)

---

## 6-N. US Tickers (NVDA, AMD, AAPL, AMAT, AVGO, CDNS, DELL, INTC, KLAC, LITE, LRCX, MRVL, MU)

**Source:** SEC EDGAR (XBRL via edgartools + 8-K Item 2.02 exhibits for earnings release prose).
**IR sites:** Each company has its own (https://investor.nvidia.com/ etc.), but we do **not** scrape these — the EDGAR-based path is more reliable, has consistent schema across companies, and covers everyone in our coverage at once.
**Skill:** `.claude/skills/edgar-topline-extraction/` — covers every fiscal-period quirk + concept-mapping fallback for these tickers.
**Recovery playbook for IR sites:** N/A — if EDGAR breaks, the entire system has a bigger problem than any one IR site.

---

## How to add a new company section to this file

1. Pick the next available section number (this file is append-only — don't reuse numbers when companies churn).
2. Copy the structure of UMC (most complete reference) or MediaTek (lighter):
   - **Status** badge + 1-line summary
   - **IR landing** — primary URL, deep-link templates if applicable
   - **Files published per quarter** — table of file types with format + content summary
   - **Extraction strategy** — Cloudflare? Module path? Silver path?
   - **Sections extracted** — page-by-page table for complex sites
   - **Layout / format quirks** — numbered list, each with the workaround
   - **Information captured today** ✅ + **Information NOT captured** ❌
   - **Recovery playbook** — what to do when the site redesigns
   - **Change log** — date + summary, append-only
3. Add an entry to the Index at the top.
4. Cross-reference: if there's a per-company memory note, link to it. If there's a skill, link to it.

## Cross-cutting patterns

The patterns we've discovered that recur across multiple companies:

- **Per-line vs inline numbers** — most PDFs alternate; `take_n_numbers` in `_quarterly_common.py` handles both.
- **Multi-line label wrap** — row labels like "Net Income Attributable to Shareholders of the / Parent Company" — handled via `max_label_continuations=2` in the walker.
- **Standalone dash placeholders** — `_NUM_PLACEHOLDER` set accepts `-`, `—`, `N/A`, `n/a` mid-row.
- **Period-header detection traps** — common across UMC + MediaTek: stray prose tokens that fullmatch the regex; multi-line headers with blank-line separators; restatement-footnote period suffixes like `(Note2)`. The unified solution: cluster-window detection requiring N distinct period tokens within a small line window.
- **Forward guidance card** — every guidance tab in the dashboard leads with the latest issuing report's view (per `guidance-tab-pattern` skill).
- **Time-axis sort** — every table newest-first, every chart oldest-first (per `time-axis-sort-convention` skill).

When you encounter a new corner case that's likely to recur (judgment call: have we seen something analogous on another company?), promote the fix into `_quarterly_common.py` and document here. If it's truly company-specific, keep it in the company's extractor module.
