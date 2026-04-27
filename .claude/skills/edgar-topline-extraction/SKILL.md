---
name: edgar-topline-extraction
description: HIGH PRIORITY. Build and maintain the AlphaGraph topline + calculated layers for SEC EDGAR-sourced quarterly financial data. Use whenever the user asks to (re)build, refresh, or fix any ticker's quarterly income statement / cash flow / balance sheet data; whenever they report missing values, wrong fiscal labels, missing YoY%, missing QoQ%, NaN cells in the quarterly data table, or any data quality issue with a sector heatmap or financial chart. Captures every fiscal-period quirk and concept-mapping fallback we discovered for DELL, AAPL, LITE, AVGO, AMZN, ORCL, MU, NVDA. Includes the mandatory post-build coverage checks that surface missing data instead of letting it ship silently.
version: 1.0
last_validated_at: 2026-04-28
conditions:
  - requires_dir: [backend/data/filing_data]
prerequisites: [edgar-period-analysis, data-quality-invariants]
tags: [edgar, extraction, financials, topline, high-priority]
---

# EDGAR Topline Extraction — The Complete Skill

## 0. The single most important rule

**Never guess fiscal periods. Never trust per-row fiscal_year from edgartools for historical rows.** Always anchor on the latest row's edgartools label and step backward by one fiscal quarter per position. When historical rows disagree with the stepped-back label, keep the stepped-back label and record the mismatch.

This is enforced by `_reanchor_period_labels()` in `topline_builder.py`. The companion read-side skill is at `.claude/skills/edgar-period-analysis/SKILL.md`.

## 0a. Cache-first rule (project-wide)

Every external data source must be persisted on first fetch; downstream pipelines read from cache. The EDGAR fetch layer lives in `backend/app/services/data_agent/xbrl_cache.py` (per-filing parquets + per-ticker stitched outputs, accession-keyed). The build pipeline reads from this cache and never calls `XBRL.from_filing()` or `XBRLS.from_filings()` directly. Full rule: `CLAUDE.md` § "External-Data Cache-First Rule".

## 1. What this Skill does

Owns the end-to-end pipeline that produces the quarterly financial data table and every downstream consumer (sector heatmap, charts, data exports):

```
EDGAR (via edgartools)
        │
        ▼
1. ToplineBuilder.build(tickers)
        │   Per-ticker: fetch filings → consolidate → gap-fill → process
        │   → derive Q4 → YTD-to-standalone → reanchor labels → write parquet
        ▼
backend/data/filing_data/topline/{income_statement,cash_flow,balance_sheet}/ticker=*.parquet
        │
        ▼
2. CalculatedLayerBuilder.build(tickers)
        │   Read topline → join statements → compute derived metrics
        │   → compute YoY/QoQ via merge_asof → run validation rules
        ▼
backend/data/filing_data/calculated/ticker=*.parquet
        │
        ▼
3. Consumers
        ├── DataAgent.fetch() → quarterly financial data table
        ├── /data/sector-heatmap → sector heatmap
        └── /data/* and /insights/* → charts and analytics
```

The Skill also owns the post-build coverage checks that fail loudly when revenue exists but YoY% / QoQ% / net_income / EPS is missing.

## 2. When to use this Skill

Use whenever any of the following:

- User reports missing data in the quarterly financial table (revenue, net_income, EPS, gross_margin, etc. = "—" or NaN).
- User reports wrong fiscal period labels (e.g. "this row says FY2026-Q4 but it should be FY2025-Q4").
- User reports missing YoY% or QoQ% values for quarters that are not the oldest in the series.
- User reports gaps in a ticker's history (e.g. "DELL is missing Feb 2024 Q1").
- User adds a new ticker to the universe.
- User asks to refresh data after a new earnings filing.
- A sector heatmap or chart looks visibly broken or incomplete.
- A new edgartools quirk is discovered for a specific filer.

## 3. The 7-step build pipeline

The build runs once per ticker in `topline_builder.build()`. Every step has a reason; do not skip or reorder.

### Step 1 — Fetch filings

```python
filings = company.get_filings(form=["10-K", "10-Q"]).head(30)
```

30 filings covers ~7 years of quarterly history. Increasing this doesn't help much because the bottleneck is XBRLS consolidation, not filing count.

### Step 2 — Build period_map from XBRLS consolidation + per-filing augmentation

```python
xbrls = XBRLS.from_filings(filings)
period_map = self._build_period_map(xbrls)
period_map = self._augment_period_map_from_filings(period_map, filings)
```

`_build_period_map` walks `xbrls.get_periods()` and assigns `canonical_fp` (Q1/Q2/Q3/Q4/Annual/Instant) and `canonical_fy` to each end_date. The augmentation pass calls `XBRL.from_filing(f)` on each individual filing to fill in any period_ends that the consolidated view dropped.

**Critical rules baked into `_build_period_map`:**

- **Handle AMZN-like rolling TTM `fp=FY` periods**: when a standalone quarter (Q1-Q3, ~90 days) and a rolling TTM (fp=FY, ~365 days) coexist at the same mid-year end_date, prefer the standalone. The column value from `to_dataframe()` is the standalone/YTD value, not the TTM. Do NOT use a generic FY-rejection filter — it breaks filers whose fiscal year crosses calendar year boundaries (CDNS, etc.).
- **Identify real fiscal year starts** by grouping FY candidates by calendar year of end_date and keeping only the entry with the latest end_date per calendar year.
- **80-100 day `fp=None` entries are Q1 standalone, not YTD**. The first quarter of a fiscal year IS its own YTD baseline. AVGO's `2024-02-04` had only a 97-day `fp=None` entry; the old code mis-labeled it as "Q2 with is_ytd=True" and the calculator dropped it. The fix splits by duration: 80-100 → Q1, 160-200 → Q2 YTD, 250-290 → Q3 YTD.
- **YTD detection by duration, not label**. `is_ytd = ytd_entry exists AND ytd_entry.days > value_entry.days`. We do NOT gate on `fp ∈ (Q2, Q3)` because edgartools mis-labels DELL's Q3 as "Q4" and the gating broke.
- **fiscal_year from the SHORTEST entry**. When multiple entries exist for one end_date, the shortest (Q4 standalone, ~90 days) carries the correct fiscal_year. The longest (Annual, ~365 days) is often a comparative column tagged with the filing year, not the period's own year.

### Step 3 — Extract each statement with gap-fill

```python
is_raw = xbrls.statements.income_statement(max_periods=40).to_dataframe()
is_raw = self._gap_fill_raw_dataframe(is_raw, filings, "income_statement")
```

**`XBRLS.from_filings().to_dataframe()` silently drops period columns for some filers** (DELL loses ~3 periods, AVGO loses 1 critical Q1, others vary). The gap-fill pass:

1. For each filing, calls `XBRL.from_filing(f).statements.{income_statement,cash_flow,balance_sheet}().to_dataframe()`.
2. Compares its period columns against the consolidated set.
3. For any period column NOT in the consolidated set, builds a `concept → value` map from that filing's rows.
4. Appends the missing column to the consolidated dataframe, aligning rows by `standard_concept` → `concept` → `label` (in priority order).
5. First filing to surface a missing period wins (filings are iterated newest-first, so the freshest filing is the source of truth).

Without this, DELL is missing FY2024 Q1/Q2/Q3, AVGO is missing FY2024 Q1, and the reanchor stepping cascades wrong labels through everything.

### Step 4 — Process statement (concept matching)

```python
is_wide = self._process_statement(
    is_raw, period_map, _INCOME_MAP, ticker,
    scale=True,
    eps_label_map=_EPS_LABEL_MAP,
    sum_concept_map=_INCOME_SUM_MAP,
    concept_fallback_map=_INCOME_CONCEPT_FALLBACK,
)
```

`_process_statement` walks every (line_item, period_end) pair and tries to match the line to a metric using **5 tiers in priority order**:

1. **`standard_concept` → `concept_map`**: edgartools' normalized concept (`"Revenue"` → `revenue`, `"NetIncome"` → `net_income`).
2. **`eps_label_map`**: label-based fallback for EPS rows where `standard_concept` is NaN. Matches `"basic (in usd per share)"` → `eps_basic`.
3. **`cf_label_fallback`** (cash flow only): catches NVDA-style `nvda_PurchasesRelatedTo...` capex rows. Requires label start `"purchases"` AND contains `"property"`.
4. **`concept_fallback_map`**: raw `concept` column → metric. Catches AVGO's EPS rows (label is just `"Basic"`/`"Diluted"`, std_concept is NaN, but raw concept is `us-gaap_EarningsPerShareBasic`/`Diluted`).
5. **`sum_concept_map`**: accumulates multiple matching rows into one metric. Catches ORCL's three-way COGS split (`orcl_CloudAndSoftwareExpenses` + `orcl_HardwareExpenses` + `orcl_ServicesExpense`).

**Default disambiguation when multiple rows match the same metric**: keep the FIRST occurrence (`elif metric not in row`). For `_OVERWRITE_ON_MATCH` metrics (CF totals like `operating_cf`/`investing_cf`/`financing_cf`), use last-match-wins because the true total appears after its sub-components in the filing.

### Step 5 — Derive Q4 and convert YTD to standalone

```python
is_wide = self._derive_q4(is_wide, "income_statement")
is_wide = self._ytd_to_standalone(is_wide, "income_statement")
```

**Order matters**. `_derive_q4` MUST run first because it computes `Q4 = Annual - 9M_YTD` from the original YTD values. If YTD-to-standalone runs first, the 9M YTD column has been overwritten with the 9M standalone (Q3 alone), and the Q4 derivation gives wrong values.

**`_ytd_to_standalone` rules:**

- Group rows by `(ticker, period_start)`. Q1, H1 YTD (Q2), 9M YTD (Q3), and Annual all share the same fiscal year start.
- Q1 (is_ytd=False) is the baseline. Q2_standalone = Q2_YTD - Q1. Q3_standalone = Q3_YTD - Q2_YTD.
- **EPS and share counts are NOT additive** — they're `_NON_ADDITIVE`. We skip them during subtraction and recompute EPS afterwards from `net_income / shares`.
- **Rows that can't be converted (missing baseline) stay `is_ytd=True`** and are filtered out by the calculator. Do not mark them is_ytd=False — that would leak a YTD value into the standalone column.
- After conversion, **relabel by position within the group** (sorted by period_end ascending). This corrects edgartools' calendar-quarter-shifted labels (NVDA's fiscal Q1 ending May → edgartools labels it Q2).

### Step 6 — Anchor-and-step-back period labels

```python
is_wide, is_mismatches = self._reanchor_period_labels(is_wide)
```

This is the heart of the period-label correctness story. After all other processing, walk the dataframe sorted by `period_end` and:

- **Standalone quarters (Q1-Q4, is_ytd=False)**: anchor on the latest row's edgartools `(fiscal_year, fiscal_quarter)`. Step backward one fiscal quarter per position. Q4 → Q3 → Q2 → Q1 → prior year Q4.
- **Annual rows**: anchor on the latest annual's fiscal_year. Step backward one fiscal year per position.
- **is_ytd=True rows are left alone** — they'll be filtered downstream and their labels are meaningless.
- For every row whose stepped-back label differs from edgartools' raw label, append `{period_end, old, new}` to `mismatches` and apply the new label.

This step is the single fix that resolves DELL's "2025-01-31 labeled FY2026-Q4" off-by-one and similar issues across the universe.

### Step 7 — Calculator: derive metrics and compute growth

```python
calc_df = ...  # join IS + CF + BS by period_end
df = self._add_growth(df, GROWTH_BASE_METRICS, shift=4, expected_days=365, suffix="_yoy_pct")
df = self._add_growth(df, GROWTH_BASE_METRICS, shift=1, expected_days=91, suffix="_qoq_pct")
```

**Use `merge_asof` end-date matching, NOT row-shift.** The `_find_prior_rows` helper does:

```python
matched = pd.merge_asof(
    left.sort_values("_target_date"),       # current rows + (current - 365 days)
    right.sort_values("prior_end_date"),    # prior rows
    left_on="_target_date",
    right_on="prior_end_date",
    direction="nearest",
    tolerance=pd.Timedelta(days=tolerance_days),  # ±45 days
)
```

This is robust to **gaps in the time series**. If a single row is missing at position N-4, the old `shift(4)` approach broke YoY for rows at N, N-1, N-2 (cascade); `merge_asof` finds each row's own ~365-day-prior match independently.

## 4. Concept maps catalog

These live at the top of `topline_builder.py`. Add new entries here when a new filer's concepts don't match.

### `_INCOME_MAP` — standard_concept → metric

```python
"Revenue":                         "revenue",
"CostOfGoodsAndServicesSold":      "cost_of_revenue",
"GrossProfit":                     "gross_profit",
"OperatingIncomeLoss":             "operating_income",
"NetIncome":                       "net_income",
"ProfitLoss":                      "net_income",   # AVGO
"ResearchAndDevelopementExpenses": "rd_expense",   # edgartools typo
"ResearchAndDevelopmentExpenses":  "rd_expense",
...
```

### `_EPS_LABEL_MAP` — lowercased label → metric

```python
"diluted (in usd per share)": "eps_diluted",
"basic (in usd per share)":   "eps_basic",
```

### `_INCOME_CONCEPT_FALLBACK` — raw concept → metric

```python
"us-gaap_EarningsPerShareBasic":   "eps_basic",
"us-gaap_EarningsPerShareDiluted": "eps_diluted",
"us-gaap_WeightedAverageNumberOfSharesOutstandingBasic":   "shares_basic",
"us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding": "shares_diluted",
```

Used when both standard_concept and label fail (AVGO's "Basic"/"Diluted" rows).

### `_INCOME_SUM_MAP` — multi-row sum aggregation

```python
"cost_of_revenue": [
    "orcl_CloudServicesAndLicenseSupportExpenses",  # ORCL FY2019–FY2025
    "orcl_CloudAndSoftwareExpenses",                # ORCL FY2026+
    "orcl_HardwareExpenses",
    "orcl_ServicesExpense",
],
```

ORCL doesn't report a single Cost of Revenue line — they split into business-segment expense rows that we sum.

### `_CASHFLOW_MAP` — standard_concept → metric (cash flow)

```python
"NetCashFromOperatingActivities":      "operating_cf",
"NetCashFromInvestingActivities":      "investing_cf",
"NetCashFromFinancingActivities":      "financing_cf",
"CapitalExpenses":                     "capex",
"CapitalExpenditures":                 "capex",
"Depreciation":                        "depreciation",
"DepreciationExpense":                 "depreciation",
"DepreciationAndAmortization":         "depreciation",
"OtherDepreciationAndAmortization":    "depreciation",  # AMD
"DepreciationDepletionAndAmortization":"depreciation",
```

### `_CF_LABEL_FALLBACK` — capex label fallback

NVDA reports capex via a custom concept `nvda_PurchasesRelatedToPropertyAndEquipmentAndIntangibleAssets` with `standard_concept=NaN` for FY2022-Q4 through FY2024-Q2. We catch it via label: must start with `"purchases"` AND contain `"property"`. The "purchases" prefix is critical — it excludes `"principal payments on property and equipment"` which is a financing-activity (debt repayment) line, not capex.

### `_CF_CONCEPT_FALLBACK` — raw concept → metric (single-row, full-value)

Used when `standard_concept` and label fallbacks miss but the row IS a complete value (not a split component). Single match per period.

```python
"us-gaap_DepreciationAndAmortization":  "depreciation",
"us-gaap_Depreciation":                 "depreciation",
"us-gaap_DepreciationNonproduction":    "depreciation",
```

### `_CF_SUM_MAP` — concepts that are split or use company-specific tags

For filers who report Depreciation and Amortization as **two separate XBRL rows** in their 10-K (or use a company-specific concept that needs to be discovered). The sum logic accumulates all matching rows per period into a single metric value.

```python
_CF_SUM_MAP: dict[str, list[str]] = {
    "depreciation": [
        # AMD-style split: separate D and A lines, mis-tagged std_concepts
        "us-gaap_OtherDepreciationAndAmortization",   # AMD: $671M (D only) FY2024
        "us-gaap_AdjustmentForAmortization",          # AMD: $2,393M (A only) FY2024
        "us-gaap_AmortizationOfIntangibleAssets",
        "us-gaap_DepreciationDepletionAndAmortization",  # DELL combined
        # Company-specific combined concepts (std_concept=NaN)
        "msft_DepreciationAmortizationAndOther",
        "csco_DepreciationAmortizationAndOther",
    ],
}
```

**The AMD FY2024 depreciation case (the canonical example):**

In AMD's FY2025 10-K (filed Feb 2026), the FY2024 column reports D+A as two separate rows:

| concept | std_concept (mis-tagged) | label | FY2024 |
|---|---|---|---:|
| `us-gaap_OtherDepreciationAndAmortization` | `NonoperatingIncomeExpense` | "Depreciation and amortization" | $671M |
| `us-gaap_AdjustmentForAmortization` | `GoodwillWriteoffs` | "Amortization" | $2,393M |

Without the sum map, only the first row matches and the build records FY2024 Annual depreciation = $671M. The 10-Q quarterlies (Q1+Q2+Q3 = 2,309M) far exceed this, so the derived Q4 = 671 − 2,309 = **−$1,638M** — a fictitious negative depreciation that's clearly wrong.

With the sum map, both rows accumulate: $671 + $2,393 = $3,064M ≈ Q1+Q2+Q3+Q4 sum. Q4 = 755M (positive, correct).

**Why the sum is safe even for non-split filers**:

The matching priority in `_process_statement` is:
1. `standard_concept` → `_CASHFLOW_MAP`
2. `eps_label_map` (income only)
3. `cf_label_fallback`
4. `_CF_CONCEPT_FALLBACK` (raw concept, single-value)
5. `_CF_SUM_MAP` (raw concept, sum)

A row only reaches the sum path if **no earlier rule matched**. So filers who report a single combined `us-gaap_DepreciationAndAmortization` line with a normal `standard_concept = "DepreciationAndAmortization"` are matched at step 1 — their row[depreciation] is set once via the standard map and the sum path is never entered. No double-counting.

For MSFT and CSCO, their `msft_*` and `csco_*` rows have `standard_concept = NaN` and concept names that are not in `_CF_CONCEPT_FALLBACK`, so they fall through to step 5 — the sum sets row[depreciation] to their single value. This unifies the extraction path: every depreciation source (combined-std, combined-fallback, split-D+A, company-specific) lands the right metric value.

**Detection rule** in `audit_topline.py`: `annual_vs_quarters` on `depreciation` (CRITICAL at >25% discrepancy). This was added specifically to catch AMD's FY2024 cascade and any future filer that hits the same trap.

### `_OVERWRITE_ON_MATCH` — last-match-wins metrics

```python
{"operating_cf", "investing_cf", "financing_cf"}
```

These CF totals can appear multiple times in a filing because edgartools surfaces every row tagged with the standard_concept, including upstream sub-components. **But "last wins" alone is insufficient** — see `_CANONICAL_TOTAL_CONCEPTS` below for the AMD-class fix.

### `_CANONICAL_TOTAL_CONCEPTS` — concept-name preference for CF totals

When multiple rows share `standard_concept = "NetCashFromOperatingActivities"` (or the equivalent investing/financing standard_concept), the simple "last wins" rule fails for filers that have **supplemental disclosures appearing AFTER the headline total**. The fix:

```python
_CANONICAL_TOTAL_CONCEPTS: dict[str, set[str]] = {
    "operating_cf": {
        "us-gaap_NetCashProvidedByUsedInOperatingActivities",
        "us-gaap_NetCashProvidedByOperatingActivities",  # legacy spelling
    },
    "investing_cf": {
        "us-gaap_NetCashProvidedByUsedInInvestingActivities",
        "us-gaap_NetCashProvidedByInvestingActivities",
    },
    "financing_cf": {
        "us-gaap_NetCashProvidedByUsedInFinancingActivities",
        "us-gaap_NetCashProvidedByFinancingActivities",
    },
}
```

**Selection rule** (in `_process_statement`): for `_OVERWRITE_ON_MATCH` metrics, when a row's raw `concept` is in the canonical set, that value WINS and is locked — no later row in the same period can overwrite it. Only when no canonical row has been seen does the legacy "last wins" fallback apply (this preserves behavior for company-specific concepts like `orcl_*`).

**Why "last wins" alone failed (the AMD bug)**: AMD's FY2025 10-K cash flow has FOUR rows that edgartools tags with `standard_concept = NetCashFromOperatingActivities`:

| concept | label | FY2025 |
|---|---|---:|
| `us-gaap_NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` | continuing ops | $6,493M |
| `us-gaap_CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations` | discontinued ops | $1,216M |
| **`us-gaap_NetCashProvidedByUsedInOperatingActivities`** | **TOTAL ✓** | **$7,709M** |
| `us-gaap_RightOfUseAssetObtainedInExchangeForOperatingLeaseLiability` | ROU lease (supplemental, non-cash) | $285M |

Last-wins picked the supplemental ROU disclosure ($285M) — that's the wrong row. The `_derive_q4 = Annual − 9M_YTD` step then produced a Q4 of −$4,824M (since 285 − 5,109 = −4,824) — a fictitious Q4 cash outflow that never happened.

Same pattern appears whenever a filer has discontinued operations, ROU lease disclosures, or any other "non-cash investing/financing" supplemental row that shares the parent total's standard_concept. Affects AMD, AAPL FY2020, ADI FY2019-2024, ORCL FY2020, NVDA FY2024 (one quarter), and likely most large-cap filers in some year.

### Why we can't use `parent_abstract_concept`

The XBRLS stitched API (`xbrls.statements.cash_flow_statement(max_periods=40).to_dataframe()`) returns columns `[label, concept, standard_concept, *dates, preferred_sign]` — it drops the `parent_abstract_concept` column that single-XBRL parsing exposes. So we can't filter rows by their position in the abstract hierarchy (e.g. "skip rows under `NoncashInvestingAndFinancingItemsAbstract`"). The canonical-concept-name preference is the workable substitute.

## 5. Per-ticker edge case catalog

Real edge cases observed in this universe. When a new ticker behaves badly, check this list first.

### NVDA (works correctly via standard path, with one fix)

- **Capex**: NVDA's filings switched concept names mid-history. FY2018-2020 used `us-gaap_PaymentsToAcquirePropertyPlantAndEquipment` (`standard_concept=CapitalExpenses`, in `_CASHFLOW_MAP`). FY2022-Q4 onwards used `nvda_PurchasesRelatedToPropertyAndEquipmentAndIntangibleAssets` (`standard_concept=NaN`). Caught via `_CF_LABEL_FALLBACK`.
- **EBITDA**: not in EDGAR; computed via `COMPUTED_METRICS` as `operating_income + abs(depreciation)`.
- Otherwise NVDA is the canonical "well-behaved" filer — fiscal calendar Feb→Jan, all quarter labels correct.

### DELL (off-by-one fiscal_year + missing comparatives + VMware spinoff)

- **Fiscal calendar**: Feb→Jan (FY2026 = Feb 2025 → Jan 2026).
- **Off-by-one fiscal_year**: edgartools labels `2025-01-31` as `FY2026-Q4` (filing year), but it's actually the end of FY2025. Fixed by `_reanchor_period_labels`.
- **Off-by-one fiscal_quarter**: edgartools labels DELL's Q3 periods (~Nov end) as `Q4`. Cascades through all standalone quarters. Fixed by reanchor.
- **Missing FY2024 Q1/Q2/Q3 comparative columns**: `XBRLS.from_filings().to_dataframe()` drops `2023-05-05`, `2023-08-04`, `2023-11-03` from the consolidated output. They exist in individual 10-Q comparative columns. Fixed by `_gap_fill_raw_dataframe`.
- **Q3 reported as 9M YTD**: when a 10-Q's Q3 comparative is the only entry edgartools provides, it's `fp=None days=272`. The `_ytd_to_standalone` step subtracts the H1 YTD baseline; if the baseline is also missing, the row stays `is_ytd=True` and is filtered.
- **VMware spinoff artifact (FY2022-Q4 = -8.2% gross margin)**: VMware was spun off Nov 1 2021 (mid-Q3 FY2022). DELL's FY2022 10-K was **restated** on a "continuing operations" basis, but the 9M cumulative from Q1+Q2+Q3 10-Qs includes pre-spinoff VMware figures. The `_derive_q4 = Annual - 9M` subtraction produces a Q4 where cost_of_revenue ($24B) exceeds revenue ($22B) = -8.2% gross margin. **This is a derivation artifact, not real negative margins.** DELL's actual Q4 FY2022 had ~30% gross margins. UNFIXABLE from EDGAR data — DELL 10-Ks have only annual columns, no standalone Q4. Caught by sanity check #4 (soft: gross_profit < 0). Document and live with it. Any mid-year M&A or spinoff for any filer will produce the same class of artifact in the derived Q4.

### AAPL (off-by-one + Apple's Sept fiscal year)

- **Fiscal calendar**: Sept → Sept (FY2026 starts Oct 2025, ends Sept 2026). FY Q1 = Oct-Dec, Q2 = Jan-Mar, Q3 = Apr-Jun, Q4 = Jul-Sep.
- **Off-by-one fiscal_year on comparative columns**: Apple's 2024-09-28 row was tagged `fy=2025` by edgartools (filing year). Fixed by `_reanchor_period_labels`.
- **Some historical rows mis-labeled by one quarter**: similar to DELL, fixed by reanchor.
- All EPS / net_income concepts use standard NetIncomeLoss / EarningsPerShare* — no special handling needed.

### LITE (NeoPhotonics acquisition + missing quarters + is_ytd misclassification)

- **NeoPhotonics acquisition** (closed June 2022): post-acquisition quarters include NeoPhotonics revenue. YoY comparisons across the acquisition boundary are non-comparable.
- **Missing quarters**: 3 quarters are affected. `2021-10-02` and `2022-04-02` are in the topline but marked `is_ytd=True` — the calculator filters them. `2023-01` is not in any filing at all. These produce cascading YoY NaN for the quarters a year later.
- **is_ytd misclassification**: `_build_period_map` marks some LITE rows as YTD when their values are actually standalone, because a longer-duration YTD entry exists at the same end_date. The `_ytd_to_standalone` step can't convert them (missing baseline) so they stay is_ytd=True and get dropped.
- Recent quarters (FY2025+) are clean; older ones around 2021-2023 have legitimate gaps.
- Capex line uses `"Purchases of test, manufacturing and other equipment"` (label fallback path).

### CDNS (Unknown fiscal_quarter for Q4 FY2021)

- **Fiscal calendar**: Calendar year (Jan → Dec).
- **Missing Q4 FY2021 (2022-01-01)**: the period exists in the topline but was labeled `fiscal_quarter = "Unknown"` because `_build_period_map` had no entry for this end_date. **Fix**: `_reanchor_period_labels` now includes "Unknown" rows in the step-back sequence, so they get proper Q1-Q4 labels from their position in the end_date ordering.
- Without this fix, the row was dropped by the calculator's `fiscal_quarter.isin(["Q1","Q2","Q3","Q4"])` filter, and the quarter a year later (FY2022-Q4) lost its YoY match.

### TER (genuine Q2 2020 data gap)

- **Fiscal calendar**: Calendar year (Jan → Dec).
- **Missing Q2 2020**: this quarter is NOT in any EDGAR filing (10-K or 10-Q) for Teradyne. Not recoverable. Produces 1 YoY NaN at FY2021-Q2. Legitimate blank — document and accept.

### AVGO (custom Q1, ProfitLoss net income, generic EPS labels)

This is the most-affected ticker in the universe. Three independent issues:

1. **Q1 represented only as 97-day `fp=None`**: AVGO's Q1 of each fiscal year has only one edgartools entry — a 97-day period with `fp=None`. Old code routed it to YTD-fallback and labeled "Q2 is_ytd=True", and the calculator dropped it. **Fix**: in the YTD-only fallback in `_build_period_map`, periods with 80-100 days are treated as Q1 standalone (`is_ytd=False`), not YTD.
2. **`net_income` uses `us-gaap_ProfitLoss`**, standard_concept=`ProfitLoss`. Old `_INCOME_MAP` only matched `NetIncome`. **Fix**: added `"ProfitLoss": "net_income"` to `_INCOME_MAP`.
3. **EPS labels are just `"Basic"` / `"Diluted"`**, not `"basic (in usd per share)"`. `standard_concept=NaN`. **Fix**: added `_INCOME_CONCEPT_FALLBACK` matching raw concept `us-gaap_EarningsPerShareBasic`/`Diluted`.
- **VMware acquisition (Q3 FY2024)**: caused a -$1.88B net loss that quarter. Real, not a bug.

### AMZN (rolling TTM `fp=FY` entries at every mid-year quarter end)

- Calendar fiscal year (Jan → Dec).
- Edgartools provides `fp=FY days=364` entries at **every** quarter end_date (Mar 31, Jun 30, Sep 30, Dec 31), not just the fiscal year end. These are trailing-twelve-month comparative disclosures filed alongside the actual quarterly data.
- Each mid-year end_date has BOTH a standalone quarter entry (Q1 ~90 days) AND a rolling TTM entry (FY ~364 days) in `all_standalone`.
- **Key insight**: edgartools' `to_dataframe()` puts the STANDALONE/YTD value in the column, NOT the TTM. So the column value at 2025-03-31 is Q1 standalone ($155B), not the TTM ($620B). The TTM entries are phantom metadata that don't affect column values.
- **Fix**: NO FY-TTM rejection filter. Instead, when both a standalone quarter (~90 days) and an Annual/TTM (~365 days) exist at the same end_date, the period_map prefers the standalone quarter (since that matches the column value). The Annual entry is only used at the fiscal year end, where the column value IS the annual total and `_derive_q4` subtracts 9M to get Q4.
- **Previous broken approach**: a generic FY-TTM rejection filter that checked `start_date ∈ real_fy_starts`. This accidentally broke CDNS (whose FY2021 annual crosses the calendar year boundary) and any other filer with a non-December year-end. **NEVER use a generic FY-rejection filter** — handle AMZN's TTMs by preferring standalone entries at the value-selection level, not by rejecting period_map entries.

### MU (Memory cycle volatility, large negative YoY)

- Fiscal calendar: late August year-end (FY2026 = Sept 2025 → Aug 2026).
- Has legitimate ~-50% YoY swings during memory downturns and +200% during recoveries — these are real, not bugs.
- Capex via standard path, no special handling needed.

### SMCI (raw-facts fallback substring trap + per-row first-wins missing)

SMCI's 10-Q has a `us-gaap:ContractWithCustomerLiabilityRevenueRecognized` row at $63M (deferred-revenue disclosure) at the same end_date with 91-day duration. Pre-fix, `_resolve_ytd_with_raw_facts` did fuzzy substring matching:

```python
for std_key, metric_name in concept_map.items():
    if std_key.lower() in concept.lower():    # "revenue" IN "...revenuerecognized"
        metric = metric_name
        break
```

The substring `"revenue"` matches both:
- `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax` ($12.7B, real revenue)
- `us-gaap:CostOfRevenue` ($11.9B, cost — should be cost_of_revenue, not revenue)
- `us-gaap:ContractWithCustomerLiabilityRevenueRecognized` ($63M, deferred-revenue sub-line)

The function iterated all matching facts and overwrote the same metric repeatedly — last match won. Result: SMCI's Q2 FY26 standalone revenue was set to $63M, then `is_ytd=False` was flagged so `_ytd_to_standalone` skipped the row. Same trap on cost_of_revenue.

The same trap also broke AAPL FY2019-2025 (revenue showing $1,900-$3,400 in Q2/Q3 standalone rows — mid-year quarters where AAPL's 10-Q has a sub-disclosure line whose concept name contains "revenue").

**Fix**: replace fuzzy substring match with an explicit raw-concept whitelist `_RAW_CONCEPT_TO_METRIC` (defined inside `ToplineBuilder`), and stop overwriting once a metric is set per (row, metric):

```python
metrics_set: set[str] = set()  # only first match wins per metric
...
metric = self._RAW_CONCEPT_TO_METRIC.get(concept)
if metric is None or metric in metrics_set:
    continue
df.at[idx, metric] = val
metrics_set.add(metric)
```

The whitelist is intentionally narrow — exact us-gaap concept names only. Adding a new ticker means evaluating whether to add its concepts (rare), NOT loosening the matching rule.

**Detection rule** in `audit_topline.py`: `gross_profit_identity` (revenue − COGS ≠ GP by >1% of revenue) catches this kind of mis-mapping reliably. SMCI's pre-fix audit had 12 such WARNs across FY2019-2026 because revenue and cost were swapped.

### AMD (CF supplemental-disclosure trap + mis-tagged depreciation + same-day 10-K/A)

This is the canonical example of the canonical-total-concept disambiguation failure.

1. **CF totals were silently wrong before the canonical-concept fix.** `operating_cf` Annual landed on the supplemental Right-of-Use lease row ($285M) instead of the real total ($7,709M). Same shape on `investing_cf`. Cascaded into a derived Q4 of −$4,824M.

   **Fix**: `_CANONICAL_TOTAL_CONCEPTS` (see Section 4). Locks the bare `us-gaap_NetCashProvidedByUsedIn*Activities` concept against later overwrites in the same period.

2. **Depreciation reported as separate D and A rows in the 10-K**. AMD's FY2024+ 10-Ks split "Depreciation and amortization" into TWO XBRL rows:
   - `us-gaap_OtherDepreciationAndAmortization` ($671M, std_concept mis-tagged as `NonoperatingIncomeExpense`)
   - `us-gaap_AdjustmentForAmortization` ($2,393M, std_concept mis-tagged as `GoodwillWriteoffs`)

   The 10-Q reports them combined under `us-gaap_DepreciationAndAmortization` (~$750M per quarter). Picking up only the D row gave Annual = $671M but quarterly sum = $3,064M — derived Q4 = $-1,638M. **Fix**: `_CF_SUM_MAP` accumulates both D and A rows into the `depreciation` metric. See section 4 for the full priority logic. Same fix simultaneously catches MSFT and CSCO who use company-specific concepts (`msft_DepreciationAmortizationAndOther`, `csco_*`).

3. **AMD files 10-K/A on the same day as the original 10-K**. Both `0000002488-26-000018` (10-K) and `0000002488-26-000021` (10-K/A) are dated 2026-02-04 and cover period 2025-12-27. The amendment's XBRL is a stub — it has no income/balance/cashflow statements — and edgartools logs `"Failed to resolve … No statements available"` warnings. The build still completed but the warnings hint at a flaky stitch. **Fix**: filter `amendments=False` when building the filings list passed to `XBRLS.from_filings(...)`. Amendment filings remain visible to `_get_filing_info` (the refresh-detection path) so a real restatement still triggers a rebuild.

4. **Discontinued operations introduced in FY2025**. AMD divested ZT Systems (or similar) in FY2025, so the FY2025 10-K splits OCF, investing, and financing into three rows each (continuing / discontinued / total) for the first time. Prior-year columns in the same filing show only the total. The canonical-concept lock handles this transparently — picks the one row whose concept is the bare us-gaap total.

5. **Gross-profit identity warnings** (FY2022-Q1, Q2, Q4; FY2023-Q1, Q4; FY2024-Q1, Q4; FY2025-Q1, Q4): `revenue − cost_of_revenue ≠ gross_profit` by 1-7% of revenue. Real, not a parser bug — AMD nets amortization of acquired intangibles into COGS in some quarters, so the COGS line and the GP line don't satisfy strict accounting identity. Treat as informational; do not modify.

### ORCL (no GrossProfit subtotal, three-way COGS split)

- Fiscal calendar: late May year-end.
- Oracle doesn't report a single "Cost of Revenue" line. They split it into:
  - `orcl_CloudServicesAndLicenseSupportExpenses` (FY2019–FY2025, before reorg)
  - `orcl_CloudAndSoftwareExpenses` (FY2026+, after reorg — mutually exclusive in time)
  - `orcl_HardwareExpenses`
  - `orcl_ServicesExpense`
- These are summed via `_INCOME_SUM_MAP` to produce `cost_of_revenue`.
- `gross_profit` is then derived as `revenue - cost_of_revenue` via `_fill_derived_gross_profit` since Oracle also doesn't report a GrossProfit subtotal.

### INTC (transitions, restructuring charges)

- Fiscal calendar: late Dec.
- Recent quarters have legitimate negative net income / negative YoY due to foundry restructuring. Real, not a bug.
- `2025-12-27` Q1 isn't reported yet (filing happens late Jan 2026) — shows as last row in oldest direction.

## 6. Mandatory post-build coverage checks

Run after every `CalculatedLayerBuilder.build()`. Lives in `calculator.py :: _validate()`.

### Check 1 — Core metrics must be populated for every quarter

For every standalone quarterly row (`fiscal_quarter ∈ {Q1,Q2,Q3,Q4}` AND `is_ytd=False`), these columns must NOT be NaN:

```
revenue
net_income
gross_profit          # may be missing for filers without a Cost of Revenue line — note in build report
operating_income
eps_basic
eps_diluted
```

If any row is missing one of these, emit a warning:

```
{ticker}: revenue present but {column} is NaN for N row(s):
{end_dates list, max 5} — investigate and add to skill catalog.
```

### Check 2 — YoY% and QoQ% coverage

For every row beyond the **first 4 rows** of the ticker's series (oldest-first), `revenue_yoy_pct` must NOT be NaN. For every row beyond the **first row**, `revenue_qoq_pct` must NOT be NaN.

This is implemented in `_validate()`:

```python
std = df[
    df["fiscal_quarter"].isin(["Q1","Q2","Q3","Q4"])
    & (~df["is_ytd"].astype(bool))
    & df["revenue"].notna()
].sort_values("end_date").reset_index(drop=True)

for col, expected_skip in [
    ("revenue_yoy_pct", 4),
    ("revenue_qoq_pct", 1),
]:
    gap_rows = std.iloc[expected_skip:][std.iloc[expected_skip:][col].isna()]
    if not gap_rows.empty:
        warnings_.append(f"{ticker}: revenue present but {col} is NaN for ...")
```

### Check 3 — No duplicate fiscal labels per ticker

After `_reanchor_period_labels` runs, no two standalone quarterly rows should share the same `(fiscal_year, fiscal_quarter)`. If they do, the reanchor's anchor-and-step logic was confused by an extra row in the middle.

### Check 4 — Reanchor mismatches recorded

Every reanchor mismatch is recorded in the build report under `ticker_report["is_reanchor_mismatches"]` and `ticker_report["cf_reanchor_mismatches"]`. Review these after every build. A growing list per ticker is normal (these are honest disagreements with edgartools' historical labels). A SUDDEN spike is a regression signal.

### Check 5 — Cross-statement audit (the AMD regression test)

Run `python -m backend.scripts.audit_topline` after every full build. The script loads each ticker's `income_statement`, `balance_sheet`, and `cash_flow` parquets and applies the rules below; outputs go to `backend/data/filing_data/audit_topline_report.{json,md}`.

**Rules** (`backend/scripts/audit_topline.py`):

| rule | severity threshold | catches |
|---|---|---|
| `annual_vs_quarters` | >5% WARN, >25% CRITICAL | AMD-class CF disambiguation, ORCL gross-profit split, AAPL revenue mis-mapping |
| `q4_sign_anomaly` | sign opposite to Q1-Q3 mean AND \|Q4\| > 2× mean AND both > $100M | Q4 derivation cascade from a wrong Annual |
| `nan_coverage` | >50% NaN WARN, >95% CRITICAL | missing concept aliases (e.g. depreciation pre-fix on AMD); MSFT/CSCO depreciation column missing |
| `gross_profit_identity` | \|revenue − COGS − GP\| / revenue > 1% WARN | YTD-conversion bugs in COGS, intangibles netting |
| `sign_violation` | capex must be negative | misclassified financing rows ending up in capex |
| `ocf_to_revenue_outlier` | OCF/revenue outside [−30%, +150%] for ≥2 quarters | one-off cash flow values |
| `non_positive_assets` | total_assets ≤ 0 | catastrophic balance sheet parse |
| `missing_column` | column absent | new statement structure for a ticker |

**Sparsity filter**: `annual_vs_quarters` skips fiscal years with zero quarterly rows present (history-edge data is sparse, not buggy). Years with 1-3 of 4 quarters present are downgraded to INFO to avoid drowning the real CRITICAL signal.

**How to read the report**:

1. Open `audit_topline_report.md` first — the per-ticker summary table sorts highest-CRIT to lowest. Anything with CRIT > 0 needs a look.
2. CRIT `annual_vs_quarters` on a CF metric usually means a new filer hit the supplemental-disclosure trap (Section 4 fix). Add the company-specific concept(s) to `_CANONICAL_TOTAL_CONCEPTS` if they use a non-us-gaap canonical name.
3. CRIT `q4_sign_anomaly` is almost always a downstream symptom of the Annual being wrong; fix the Annual and the Q4 derivation falls out automatically.
4. WARN `gross_profit_identity` is usually informational (intangibles netting in COGS), but a CONSISTENT 30-50% gap suggests the COGS column is YTD when it should be standalone (Class C3, see Section 5: AAPL).

**Run the audit on a single ticker** when investigating a fix:

```bash
python -m backend.scripts.audit_topline --tickers AMD NVDA MSFT
```

The audit is also the regression test before merging any change to `topline_builder.py`. A change that fixes one filer must not introduce a new CRITICAL on another.

## 7. Investigation playbook — when a check fails

### Step 0 — Heatmap scan (run FIRST, before any ticker-level investigation)

When the user reports missing data or wrong values in the heatmap or quarterly table, **scan the heatmap endpoint for ALL tickers in one shot** to identify every blank and anomaly. Do not investigate one ticker at a time.

```python
import sys; sys.path.insert(0, '..')
from backend.app.api.routers.v1 import data as dr
import importlib; importlib.reload(dr)

for metric in ['revenue_yoy_pct', 'gross_margin_pct', 'gross_margin_pct_diff_yoy']:
    result = dr.sector_heatmap(group_definition='GICS_industry', quarters=20, metric=metric)
    print(f'=== {metric} ===')
    for g in result['groups']:
        for r in g['rows']:
            pts = r['points']
            blanks = [(i, pts[i]['label']) for i in range(len(pts)) if pts[i]['value'] is None]
            anomalies = [(i, pts[i]['label'], pts[i]['value']) for i in range(len(pts))
                         if pts[i]['value'] is not None and (
                             (metric.endswith('_pct') and abs(pts[i]['value']) > 500) or
                             (pts[i]['value'] < 0 and 'margin_pct' in metric and 'diff' not in metric)
                         )]
            if blanks or anomalies:
                print(f'  {r["ticker"]:5} blanks={blanks[:5]} anomalies={anomalies[:5]}')
```

This gives a universe-wide view of what's broken. THEN investigate each finding per the steps below.

**Known categories of blanks that are NOT bugs** (document but don't chase):
- Boundary blanks: the oldest 1-4 rows of a ticker's series have no prior year for YoY%. Normal.
- M&A period blanks: quarters around major acquisitions/spinoffs where the entity changed (LITE NeoPhotonics FY2023, DELL VMware FY2022). YoY is meaningless across entity changes.
- Derived Q4 anomalies: negative gross_profit in a Q4 row where Q4 was derived as `Annual - 9M` and a mid-year corporate action made the subtraction inaccurate. Flag with soft warning, don't try to fix.

When a coverage check fires for a ticker, follow this sequence. **Do not skip to "rebuild and pray"**.

### Step 1 — Identify the missing field

```bash
python -c "
import pandas as pd
df = pd.read_parquet('data/filing_data/calculated/ticker={TICKER}.parquet')
q = df[df['fiscal_quarter'].isin(['Q1','Q2','Q3','Q4']) & (~df['is_ytd'].astype(bool))]
print(q.sort_values('end_date').tail(15)[['end_date','fiscal_quarter','revenue','net_income','eps_diluted','revenue_yoy_pct']])
"
```

Identify exactly which `(end_date, column)` pairs are NaN.

### Step 2 — Check the topline parquet

```bash
python -c "
import pandas as pd
df = pd.read_parquet('data/filing_data/topline/income_statement/ticker={TICKER}.parquet')
print(df.sort_values('period_end').tail(15)[['period_end','fiscal_quarter','fiscal_year','is_ytd','revenue','net_income']])
"
```

If the topline already has the correct value, the bug is in the calculator or `_compute_derived`. If the topline is missing the value or is wrong, continue.

### Step 3 — Inspect raw edgartools output

```python
from edgar import Company, set_identity
from edgar.xbrl import XBRLS
set_identity('AlphaGraph Research alphagraph@research.com')
c = Company('{TICKER}')
filings = c.get_filings(form=['10-K','10-Q']).head(30)
xbrls = XBRLS.from_filings(filings)
is_raw = xbrls.statements.income_statement(max_periods=40).to_dataframe()

# Find the line item we're missing — search by likely label/concept keywords
mask = is_raw['concept'].str.contains('Profit|NetIncome|Earnings', case=False, na=False)
print(is_raw[mask][['label','concept','standard_concept']].to_string())
```

This reveals whether:
- The concept exists but uses an unexpected `standard_concept` value → add to `_INCOME_MAP`.
- The concept exists with `standard_concept=NaN` → add to `_INCOME_CONCEPT_FALLBACK`.
- The line is split across multiple rows → add to `_INCOME_SUM_MAP`.
- The line doesn't exist at all in this filer's filings → real data gap, document and live with it.

### Step 4 — Inspect period_map for missing period_ends

```python
from app.services.data_agent.topline_builder import ToplineBuilder
tb = ToplineBuilder()
pm = tb._build_period_map(xbrls)
pm = tb._augment_period_map_from_filings(pm, filings)

# Check if a specific date is present
target = '2024-02-04'  # the date you suspect is missing or mislabeled
if target in pm:
    print(pm[target])
else:
    print(f'{target} NOT in period_map')
```

If the period_end is missing → either `_gap_fill_raw_dataframe` failed, or the period genuinely isn't in any filing. Also check by inspecting raw edgartools periods:

```python
for p in xbrls.get_periods():
    if p.get('end_date') == target:
        print(p)
```

If the period IS in the period_map but with wrong `canonical_fp` or `is_ytd=True` → there's a labeling bug to fix in `_build_period_map`.

### Step 5 — Check for `fp=None` standalone quarters

A common pattern: edgartools provides ONLY a `fp=None` entry for a Q1 (because Q1 IS its own YTD baseline). This was the AVGO Q1 issue. The fix is already in `_build_period_map` (80-100 day fp=None → Q1 standalone), but if a new variant appears (say, 75 days or 105 days due to fiscal calendar oddity), the rule may need extending.

### Step 6 — Document the new edge case

If you discover a new corner case, **update this skill file**. Add a per-ticker entry to section 5 with:

- The specific filer behavior observed
- The fix applied (which map / which method changed)
- The reasoning ("why this filer's data looks like this")
- A code snippet showing the line that catches it

The skill file is the institutional memory. Future-you (or another agent) will not remember why `_INCOME_CONCEPT_FALLBACK` exists unless this file says so.

## 8. Output format — what the parquets MUST look like

### `topline/income_statement/ticker={T}.parquet`

Columns:
```
ticker          str          # constant per file
period_end      timestamp    # end of fiscal period
period_start    timestamp    # start of fiscal period (= fiscal year start for YTD/Annual)
fiscal_quarter  str          # "Q1" | "Q2" | "Q3" | "Q4" | "Annual" (NEVER "Unknown" for IS)
fiscal_year     int          # post-reanchor — derived by stepping back from latest, NOT raw edgartools value
is_ytd          bool         # True only when value is cumulative YTD that couldn't be converted
revenue         float        # in USD millions
gross_profit    float
cost_of_revenue float
operating_income float
net_income      float
eps_basic       float        # per-share, NOT scaled by 1e6
eps_diluted     float
shares_basic    float        # raw count, NOT scaled
shares_diluted  float
... other IS metrics
```

Row count should be approximately `(years × 5)` — 4 quarters + 1 annual per fiscal year.

### `topline/cash_flow/ticker={T}.parquet`

Columns:
```
ticker, period_end, period_start, fiscal_quarter, fiscal_year, is_ytd
operating_cf, investing_cf, financing_cf, capex, depreciation
```

### `topline/balance_sheet/ticker={T}.parquet`

Same shape but `fiscal_quarter` may include `"Instant"` for point-in-time snapshots.

### `calculated/ticker={T}.parquet`

Columns:
```
ticker, end_date, fiscal_quarter, fiscal_year, is_ytd
# Income statement metrics (renamed period_end → end_date)
revenue, gross_profit, ..., net_income, eps_basic, eps_diluted
# Cash flow metrics (left-joined on period_end)
operating_cf, investing_cf, financing_cf, capex, depreciation, free_cash_flow
# Computed metrics
gross_margin_pct, operating_margin_pct, net_margin_pct, ebitda, opex
# Growth metrics (via merge_asof end-date matching)
revenue_yoy_pct, revenue_qoq_pct, gross_profit_yoy_pct, ...
operating_income_yoy_pct, net_income_yoy_pct, free_cash_flow_yoy_pct, ...
# Margin deltas (percentage-point YoY)
gross_margin_pct_diff_yoy, operating_margin_pct_diff_yoy, net_margin_pct_diff_yoy
```

For every standalone quarterly row beyond the oldest 4, `revenue_yoy_pct` should be a number, not NaN. For every standalone row beyond the oldest 1, `revenue_qoq_pct` should be a number.

## 9. Recipe — how to repeat this from scratch

For a fresh build (or after pulling a major change):

```bash
cd /c/Users/Sharo/AI_projects/AlphaGraph_new/backend
python -c "
import sys; sys.path.insert(0, '.')
import logging; logging.basicConfig(level=logging.INFO, format='%(message)s')
from app.services.data_agent.topline_builder import ToplineBuilder
from app.services.data_agent.calculator import CalculatedLayerBuilder
ToplineBuilder().build()
CalculatedLayerBuilder().build()
print('DONE')
"
```

For a single ticker (when investigating):

```bash
python -c "
import sys; sys.path.insert(0, '.')
import logging; logging.basicConfig(level=logging.INFO, format='%(message)s')
from app.services.data_agent.topline_builder import ToplineBuilder
from app.services.data_agent.calculator import CalculatedLayerBuilder
ToplineBuilder().build(['AVGO'])
CalculatedLayerBuilder().build(['AVGO'])
"
```

After every build, **immediately run the coverage check** by inspecting `data/filing_data/calculated/_build_report.json` for warnings. If any ticker has a "revenue present but ... is NaN" warning, follow the investigation playbook in section 7.

## 10. The forever-rules

These are non-negotiable. Violating them will produce wrong data that ships silently.

1. **Never trust per-row `fiscal_year` from edgartools for historical rows.** Always use the reanchor result.
2. **Never use `shift(N)` for YoY/QoQ.** Always use `merge_asof` with end-date matching.
3. **Never mark a row `is_ytd=False` if the YTD subtraction baseline was missing.** Leave it as `is_ytd=True` and let the calculator filter it.
4. **Never silently swallow exceptions in the build path.** If a filing fails, log the ticker + accession_no + error and continue. Do not bury the error.
5. **Never add a topline-layer "fix" without also adding the corresponding coverage check** to detect future regressions.
6. **Never ship a build without running the coverage checks**. The "revenue present but YoY is NaN" warning is a hard failure signal.
7. **Always update this skill file when a new edge case is discovered**. The institutional memory lives here, not in your head.

## 11. Heatmap calendar-view edge cases

### Nearest-quarter-end bucketing (the INTC blank fix)

When displaying the calendar-view heatmap, each ticker's `end_date` must be bucketed into a calendar quarter. **Never use strict month-based bucketing** (`Math.floor(month / 3) + 1`). Some filers end quarters on Saturdays that land on April 1-2, July 1-2, or October 1 — strict bucketing puts these in the NEXT calendar quarter, creating blanks in the intended column.

**Fix**: use "nearest calendar quarter end" — for each `end_date`, find the closest of Mar 31, Jun 30, Sep 30, Dec 31 (across current + adjacent years). April 2 is 1 day from Mar 31 → Q1. July 2 is 2 days from Jun 30 → Q2. Implemented in `_nearestCalendarQ()` in `DataExplorerView.tsx`.

**Tickers affected**: INTC (ends on Saturdays, regularly lands on April 1-2), AAPL (ends on last Saturday of quarter, occasionally hits the boundary), any filer with a Saturday fiscal period convention.

### M&A / spinoff annotations

Quarters affected by M&A events (DELL VMware spinoff, LITE NeoPhotonics, AVGO VMware, MSFT Activision) produce anomalies in the heatmap (negative margins, extreme YoY jumps, or blanks). These are flagged via:

1. **`backend/data/config/corporate_events.json`** — structured catalog of M&A events per ticker with deal dates, types, impacted quarters, and related companies.
2. **Heatmap tooltip** — tickers with known events show "..." below the name. Hover reveals a popup with event details so the user understands why a quarter looks unusual.
3. **Sanity checks** — soft warnings for negative gross_profit (check #4) and QoQ revenue cliffs (check #15) catch M&A artifacts even when the event isn't in the catalog.

## 12. Files that implement this skill

| File | Owns |
|---|---|
| `backend/app/services/data_agent/topline_builder.py` | Steps 1-6: fetch, period_map, gap-fill, process, derive_q4, ytd_to_standalone, reanchor |
| `backend/app/services/data_agent/calculator.py` | Step 7: derive computed metrics, end-date-based YoY/QoQ, validate |
| `backend/app/services/data_agent/concept_map.py` | `BASE_METRIC_CONCEPTS`, `COMPUTED_METRICS`, `GROWTH_BASE_METRICS`, `MARGIN_DELTA_BASE_METRICS` — what to compute, what to compare |
| `backend/app/services/data_agent/data_agent.py` | DataAgent.fetch() — the read path used by the quarterly financial data table |
| `backend/app/api/routers/v1/data.py` | `/data/sector-heatmap` and `/data/fetch` endpoints |
| `backend/data/filing_data/calculated/_build_report.json` | Build outcomes + warnings — read this after every build |
| `backend/scripts/audit_topline.py` | Cross-statement audit (Check 5) — the AMD-class regression test |
| `backend/data/filing_data/audit_topline_report.{json,md}` | Latest audit results; re-generated by `python -m backend.scripts.audit_topline` |
| `.claude/skills/edgar-period-analysis/SKILL.md` | The complementary read-side skill (anchor + step back when consuming the parquets) |
