# SKILL: Edgar Tools — XBRL Financial Statement Extraction

Extract clean, stitched financial statements (income statement, balance sheet,
cash flow) from SEC EDGAR filings using the `edgartools` package. Understands
how to handle the YTD cumulative vs standalone quarterly problem, derive Q4,
and produce a clean wide-format DataFrame ready for storage.

---

## When to use this skill

- Building or rebuilding the `topline/` clean data layer for any ticker
- Debugging why a metric looks wrong in the DataAgent (check what edgartools gives before assuming the backbone parquet is wrong)
- Adding a new ticker to the universe and needing its historical financial statements
- Comparing edgartools output against backbone parquet to validate data quality

---

## Prerequisites

1. `edgartools` installed: `pip install edgartools` (PyPI package name is `edgartools`, imports as `edgar`)
2. SEC identity set — required before **any** API call or you get `IdentityNotSetException`
3. Internet access (edgartools fetches from SEC EDGAR on demand, with local caching)

---

## Core imports (exact — do not guess)

```python
from edgar import Company, set_identity
from edgar.xbrl import XBRLS          # CRITICAL: NOT from edgar import XBRLS
```

**Common wrong imports that fail silently or raise ImportError:**
```python
# WRONG — XBRLS is not in the edgar root namespace
from edgar import XBRLS

# WRONG — MultiFinancials exists but its .xbs attribute is EntityFilings,
#          NOT an XBRL object, so .statements doesn't work on it
from edgar import MultiFinancials
mf = MultiFinancials(filings)
mf.income_statement()  # AttributeError: EntityFilings has no attribute 'statements'
```

---

## Step-by-step instructions

### Step 1 — Set identity (MUST be first)

```python
set_identity('Your Name your@email.com')
```

Called once per session. If forgotten: `IdentityNotSetException: User-Agent identity is not set`.

---

### Step 2 — Fetch filings

```python
company = Company('NVDA')
filings = company.get_filings(form=['10-K', '10-Q']).head(30)
```

- `.head(30)` gives ~7 years of history (4 quarters × 7 years + 7 annual = 35, so 30 is safe)
- Always include **both** `10-K` and `10-Q` in the `form` list — 10-K provides annual totals needed for Q4 derivation
- `head()` orders most-recent first; edgartools stitches them into a time series

---

### Step 3 — Build the stitched XBRL set

```python
xbrls = XBRLS.from_filings(filings)
```

`XBRLS` (not `XBRL`) is the multi-filing stitcher. It reads each filing's XBRL,
aligns concepts across periods, and exposes `.statements` for combined access.

Useful attributes:
```python
xbrls.statements          # statement accessor
xbrls.xbrl_list           # list of individual XBRL objects, one per filing
xbrls.get_periods()       # list of all period dicts with full metadata (see Step 4)
```

---

### Step 4 — Get period metadata (essential for period identification)

```python
all_periods = xbrls.get_periods()
```

Returns a list of dicts. Each dict for a duration period looks like:
```python
{
    'type': 'duration',
    'start_date': '2024-07-29',
    'end_date':   '2024-10-27',
    'start_obj':  datetime.date(2024, 7, 29),
    'end_obj':    datetime.date(2024, 10, 27),
    'days':       90,
    'fiscal_period': 'Q3',      # KEY FIELD — see values below
    'fiscal_year':   2025,
    'key':  'duration_2024-07-29_2024-10-27',
    ...
}
```

**`fiscal_period` values and what they mean — THIS IS THE PRIMARY FIELD TO USE:**

| fiscal_period | days (approx) | What it is | YTD? |
|---|---|---|---|
| `'Q1'` | ~90 | Q1 standalone | No |
| `'Q2'` | ~90 | Q2 standalone (exists alongside YTD) | No |
| `'Q3'` | ~90 | Q3 standalone (exists alongside YTD) | No |
| `'Q4'` | ~90 | Q4 standalone (same end_date as Annual) | No |
| `'FY'` | ~365 | Full fiscal year Annual | No |
| `None` / `'N/A'` | ~180 or ~270 | YTD cumulative (H1 or 9M) | **Yes** |

**Key insight**: `fiscal_period != 'N/A'` → standalone, fiscal_year is RELIABLE.
`fiscal_period == None/'N/A'` → YTD cumulative, fiscal_year may be unreliable.

**The `start_date` invariant — critical for grouping:**
Q1, Semi-Annual (H1 YTD), Nine-Months (9M YTD), and Annual for the **same fiscal year**
all share the **exact same `start_date`** (= the fiscal year start date).
Q4 standalone, Q2 standalone, Q3 standalone have different start_dates (= their own quarter's start).

This invariant means you can group by `(ticker, period_start)` to find all YTD periods
belonging to a fiscal year, without any date-proximity heuristics.

Balance sheet periods have `'type': 'instant'` with a single `'date'` field (no start/end).

---

### Step 5 — Build a rich period map

```python
from collections import defaultdict

# Collect all standalone entries per end_date
all_standalone = defaultdict(list)
ytd = {}       # end_date → largest-duration YTD info
instant_map = {}

for p in xbrls.get_periods():
    if p['type'] == 'instant':
        d = p['date']
        if d not in instant_map:
            instant_map[d] = {'canonical_fp': 'Instant', 'canonical_fy': p.get('fiscal_year'),
                               'is_ytd': False, 'period_start': None, 'days': 0}
        continue
    if p['type'] != 'duration': continue
    days = p.get('days', 0)
    if days < 80: continue
    fp = p.get('fiscal_period') or 'N/A'
    end = p['end_date']
    fy = p.get('fiscal_year')
    start = p.get('start_date')
    if fp != 'N/A':
        all_standalone[end].append({'fp': 'Annual' if fp == 'FY' else fp,
                                     'fy': fy, 'start': start, 'days': days})
    else:
        if end not in ytd or days > ytd[end]['days']:
            ytd[end] = {'start': start, 'days': days}

period_map = {}
for end, entries in all_standalone.items():
    entries_sorted = sorted(entries, key=lambda x: x['days'])
    value_entry = entries_sorted[-1]   # largest days = what to_dataframe() returns
    fy_entry    = entries_sorted[0]    # smallest days = most reliable fiscal_year
    fp = value_entry['fp']
    ytd_entry = ytd.get(end)
    is_ytd = (fp in ('Q2', 'Q3') and ytd_entry is not None
              and ytd_entry['days'] > value_entry['days'])
    period_map[end] = {
        'canonical_fp': fp,
        'canonical_fy': fy_entry['fy'],    # Q4 entry has correct fy; Annual gets filing year
        'is_ytd':       is_ytd,
        'period_start': ytd_entry['start'] if is_ytd else value_entry['start'],
        'days':         ytd_entry['days']  if is_ytd else value_entry['days'],
    }

# YTD-only end_dates (older filings with no standalone context)
for end, y in ytd.items():
    if end not in period_map:
        fp_guess = 'Q2' if y['days'] < 200 else 'Q3'
        period_map[end] = {'canonical_fp': fp_guess, 'canonical_fy': None,
                           'is_ytd': True, 'period_start': y['start'], 'days': y['days']}

period_map.update(instant_map)
```

**Why the two-entry approach for standalone periods:**
For the Q4/Annual shared end_date, edgartools has both a Q4 standalone entry (~90d,
`fiscal_period='Q4'`, correct `fiscal_year`) and an Annual comparison entry (~365d,
`fiscal_period='FY'`, may have the FILING year not the period year). `to_dataframe()`
returns the Annual value (largest duration). By using `value_entry` for `canonical_fp`
and `fy_entry` for `canonical_fy`, we get:
- `canonical_fp = 'Annual'` (the actual value in the data)
- `canonical_fy = correct year` (from Q4 standalone's entry)

---

### Step 6 — Extract statements

```python
income_stmt   = xbrls.statements.income_statement(max_periods=40).to_dataframe()
balance_sheet = xbrls.statements.balance_sheet(max_periods=40).to_dataframe()
cash_flow     = xbrls.statements.cash_flow_statement(max_periods=40).to_dataframe()
```

**Always pass `max_periods=40`** — the default is too small (~8 periods) and will silently
truncate historical data.

Available statements: `income_statement`, `balance_sheet`, `cash_flow_statement`,
`cashflow_statement` (alias), `comprehensive_income`, `statement_of_equity`.

**Output schema** (rows = line items, columns = metadata + period end dates):

| Column | Type | Description |
|---|---|---|
| `label` | str | Human-readable label: "Revenue", "Gross profit", "Net income" |
| `concept` | str | XBRL concept: "us-gaap_Revenues", "us-gaap_NetIncomeLoss" |
| `standard_concept` | str / NaN | Standardized name: "Revenue", "NetIncome" — NaN for some (EPS, custom) |
| `YYYY-MM-DD` ... | float | Period end date columns, raw USD (not millions) |
| `preferred_sign` | int | 1 = positive is good, -1 = invert sign for display |

**Important**: values are raw USD. Divide by `1e6` for millions, `1e9` for billions.
**EPS rows** have `standard_concept = NaN`. Identify by label: "Diluted (in USD per share)".
**`to_dataframe()` picks the LARGEST-duration value per end_date.** So for Q2/Q3 end_dates
where both a standalone (~90d) and YTD (~180/270d) exist, the YTD value appears.

---

### Step 7 — Understand the YTD problem (critical)

Edgartools returns **as-filed values**. Most companies file their 10-Q income
statement and cash flow on a **cumulative year-to-date** basis for Q2 and Q3:

```
NVDA Revenue example (as returned by edgartools):
  2025-04-27  $44.062B   <- Q1 FY26 standalone  OK (first quarter = same as YTD)
  2025-07-27  $90.805B   <- Q2 FY26 6-month YTD NOT standalone Q2
  2025-10-26 $147.811B   <- Q3 FY26 9-month YTD NOT standalone Q3
  2026-01-25 $215.938B   <- FY26 annual full year OK
```

**fiscal_period from period_map:**
- 2025-04-27 → canonical_fp='Q1', is_ytd=False  → standalone, use as-is
- 2025-07-27 → canonical_fp='Q2', is_ytd=True   → H1 YTD, must convert
- 2025-10-26 → canonical_fp='Q3', is_ytd=True   → 9M YTD, must convert
- 2026-01-25 → canonical_fp='Annual', is_ytd=False → full year, use as-is

**Balance sheet** does NOT have this problem — it is always point-in-time (instant).

---

### Step 8 — Derive Q4 FIRST, then convert YTD to standalone

**CRITICAL ORDER**: Q4 must be derived BEFORE converting YTD values.
- Q4_standalone = Annual - Q3_YTD  (using the ORIGINAL 9M YTD)
- If you convert YTD first, Q3 becomes standalone and the subtraction gives wrong Q4.

```python
def derive_q4(wide_df, period_map, metric_cols):
    """
    Use start_date invariant: Annual and Q3_YTD share the same period_start (= FY start).
    Group by (ticker, period_start) to find each Annual/Q3_YTD pair.
    No date-proximity windows needed.
    """
    new_rows = []
    for ticker_val in wide_df['ticker'].unique():
        t_mask = wide_df['ticker'] == ticker_val
        for ps in wide_df.loc[t_mask, 'period_start'].dropna().unique():
            group = wide_df.loc[t_mask & (wide_df['period_start'] == ps)]
            annual_rows = group[group['fiscal_quarter'] == 'Annual']
            q3_ytd = group[(group['fiscal_quarter'] == 'Q3') & (group['is_ytd'] == True)]
            if annual_rows.empty or q3_ytd.empty:
                continue
            annual = annual_rows.iloc[0]
            q3 = q3_ytd.iloc[0]
            q4_row = {
                'ticker': ticker_val,
                'period_end': annual['period_end'],
                'fiscal_quarter': 'Q4',
                'fiscal_year': annual['fiscal_year'],   # correct via canonical_fy
                'period_start': q3['period_end'] + pd.Timedelta(days=1),
                'is_ytd': False,
            }
            for col in metric_cols:
                a, q = annual.get(col), q3.get(col)
                q4_row[col] = round(float(a) - float(q), 4) if pd.notna(a) and pd.notna(q) else np.nan
            new_rows.append(q4_row)
    return pd.concat([wide_df, pd.DataFrame(new_rows)], ignore_index=True) if new_rows else wide_df
```

---

### Step 9 — Convert YTD to standalone quarterly

```python
def ytd_to_standalone(wide_df, metric_cols):
    """
    Group by (ticker, period_start): Q1, Q2_YTD, Q3_YTD, Annual all share period_start = FY start.
    Q4 has a different period_start (Q3_end + 1 day), so it's unaffected.
    """
    df = wide_df.copy()
    for ticker_val in df['ticker'].unique():
        t_mask = df['ticker'] == ticker_val
        for ps in df.loc[t_mask, 'period_start'].dropna().unique():
            group_mask = t_mask & (df['period_start'] == ps)
            group = df.loc[group_mask].sort_values('period_end')
            if not group['is_ytd'].any():
                continue
            prev_ytd = {}
            for idx, row in group.iterrows():
                if not row['is_ytd']:
                    if row['fiscal_quarter'] == 'Q1':
                        for col in metric_cols:
                            v = row[col]
                            if pd.notna(v): prev_ytd[col] = float(v)
                    continue
                # Q2 or Q3: standalone = YTD - previous YTD
                for col in metric_cols:
                    ytd_val = row[col]
                    if pd.notna(ytd_val) and col in prev_ytd:
                        df.at[idx, col] = round(float(ytd_val) - prev_ytd[col], 4)
                        prev_ytd[col] = float(ytd_val)
                    elif pd.notna(ytd_val):
                        prev_ytd[col] = float(ytd_val)
                df.at[idx, 'is_ytd'] = False
    return df
```

**Validation check after conversion:**
```python
# Q1 + Q2 + Q3 + Q4 should approximately equal Annual for each fiscal year
# Validate using period_start grouping (NOT fiscal_year matching):
for _, ann in annual_rows.iterrows():
    ann_ps = ann['period_start']
    q4_match = q4_rows[q4_rows['period_end'] == ann['period_end']]
    q1_q3 = df[(df['period_start'] == ann_ps) & df['fiscal_quarter'].isin(['Q1','Q2','Q3'])]
    quarterly = q1_q3['revenue'].sum() + q4_match['revenue'].sum()
    pct_diff = abs(quarterly - ann['revenue']) / abs(ann['revenue']) * 100
    # Should be < 2%
```

---

## Edge cases encountered

| Situation | What happens | Fix |
|---|---|---|
| `from edgar import XBRLS` | `ImportError` | Use `from edgar.xbrl import XBRLS` |
| `set_identity()` not called | `IdentityNotSetException` | Call it first, every session |
| `MultiFinancials(filings).income_statement()` | `AttributeError` | Use `XBRLS.from_filings(filings)` instead |
| Some rows have `standard_concept = NaN` | EPS rows, company-specific items | Identify by label string match |
| Q2/Q3 period values larger than expected | YTD cumulative | Check `is_ytd` flag from period_map |
| Same end_date for Annual and Q4 standalone | Both exist in get_periods() | `value_entry` (largest days) gives Annual fp; `fy_entry` (smallest days) gives correct fy |
| `result.update(instant_map)` at end of period_map build | Instant (balance sheet) entries OVERWRITE duration (Q3 YTD) entries for shared dates; income statement rows tagged as 'Instant', YTD conversion silently skipped | Do NOT merge instant_map into duration result; balance sheet shares dates with IS/CF but uses separate processing path |
| Non-December fiscal year companies (NVDA Feb-Jan, MU Sep-Aug) | edgartools assigns CALENDAR quarter labels: NVDA Q1 (Feb-May) gets fp='Q2', NVDA Q2 (May-Aug) gets fp='Q3'. Two periods end up labeled Q3. YTD conversion fails because baseline is set only on fp='Q1' rows | Do not check fiscal_quarter=='Q1' to set baseline. Use first non-Annual non-YTD row in the period_start group as baseline. Relabel Q1/Q2/Q3 by position (sorted by period_end) after conversion |
| Spurious long-duration YTD periods (MU, LRCX, SNPS) | edgartools sometimes creates a YTD period that spans across fiscal year boundaries (e.g. 545-day period starting 2018-08-31), which overwrites the legitimate H1/9M YTD in the ytd dict | Cap YTD dict entries at days <= 320 (Nine Months max = ~270 days, buffer for calendar variation). Reject anything > 320 days as an invalid stitching artifact |
| _derive_q4 picks H1 YTD instead of 9M YTD for Q4 derivation | When two Q3-labeled rows exist in a period_start group (H1 and 9M both get fp='Q3' for NVDA), iloc[0] picks the earliest (H1) giving wrong Q4=Annual-H1 instead of Q4=Annual-9M | Use ytd_rows.sort_values('period_end').iloc[-1] to always pick the LATEST YTD (9M, not H1) for Q4 derivation |
| Annual comparison columns have wrong fiscal_year | edgartools assigns filing year to prior-year Annual in 10-K | Use Q4 standalone's fy (the `fy_entry` approach above) — this is the root cause of negative Q4 values if not handled |
| Q4 derived before YTD conversion gives wrong result | Q4 = Annual - already-converted-Q3-standalone | ALWAYS call `_derive_q4` before `_ytd_to_standalone` |
| Default `max_periods` silently truncates history | Only ~8 periods returned | Pass `max_periods=40` to all three statement calls |
| CDNS fiscal year transition | Two sets of Q1-Q3 both labeled fiscal_year=2021 | `period_start` grouping handles this: different FY starts → different groups; no 365-day filter needed |
| Validation summing wrong quarters | Without lower bound, sums 6 quarters across transition | Use `period_start == Annual.period_start` (not fiscal_year) to find matching Q1-Q3 |
| AAPL labels differ from NVDA | AAPL calls it "Net sales", NVDA calls it "Revenue" | Use `standard_concept` field, not `label`, for programmatic mapping |

---

## Why `fiscal_year` on Annual rows can be WRONG

When edgartools stitches filings, each 10-K contains comparison columns from the
prior fiscal year. These comparison columns get `fiscal_year` set to the **filing year**,
not the period year. Example:

```
NVDA FY2022 10-K contains:
  - FY2022 Annual: fiscal_year=2022  (CORRECT)
  - FY2021 Annual (comparison): fiscal_year=2022  (WRONG — should be 2021)
```

The Q4 standalone entry for the same end_date (in a 10-Q from that year) gets
`fiscal_year=2021` (CORRECT). By using `fy_entry` (smallest days per end_date),
we always get the Q4 standalone's correct fiscal_year.

**Symptom if not fixed**: `_derive_q4` using fiscal_year grouping will try to compute
`Q4 = FY2021_Annual - FY2022_Q3_YTD` → negative revenues.

---

## Standard concept -> AlphaGraph metric name mapping

```python
EDGARTOOLS_TO_METRIC = {
    'Revenue':                        'revenue',
    'CostOfGoodsAndServicesSold':     'cost_of_revenue',
    'GrossProfit':                    'gross_profit',
    'OperatingIncomeLoss':            'operating_income',
    'NetIncome':                      'net_income',
    'ResearchAndDevelopementExpenses':'rd_expense',   # note edgartools typo
    'ResearchAndDevelopmentExpenses': 'rd_expense',   # in case they fix the typo
    'SellingGeneralAndAdminExpenses': 'sga_expense',
    'NetCashFromOperatingActivities': 'operating_cf',
    'NetCashFromInvestingActivities': 'investing_cf',
    'NetCashFromFinancingActivities': 'financing_cf',
    # EPS -- matched by label since standard_concept is NaN
    # 'diluted (in usd per share)'  -> eps_diluted
    # 'basic (in usd per share)'    -> eps_basic
    # Balance sheet
    'CashAndMarketableSecurities':    'cash',
    'ShortTermInvestments':           'short_term_investments',
    'Inventories':                    'inventories',
    'TradeReceivables':               'accounts_receivable',
    'TradePayables':                  'accounts_payable',
    'PlantPropertyEquipmentNet':      'ppe_net',
    'Goodwill':                       'goodwill',
    'TotalAssets':                    'total_assets',
    'TotalLiabilities':               'total_liabilities',
    'TotalEquity':                    'total_equity',
}
```

---

## Output format (what ToplineBuilder stores)

Wide-format parquet, one row per ticker+period, values in millions USD:

```
ticker | period_end | fiscal_quarter | fiscal_year | period_start | is_ytd |
  revenue | cost_of_revenue | gross_profit | operating_income | net_income |
  eps_diluted | eps_basic | rd_expense | sga_expense |
  operating_cf | investing_cf | financing_cf | capex |
  cash | total_assets | total_liabilities | total_equity | ...
```

`fiscal_quarter` values stored: `'Q1'`, `'Q2'`, `'Q3'`, `'Q4'`, `'Annual'`, `'Instant'`
`is_ytd` = False after `_ytd_to_standalone` runs (always False in stored parquets)

Values: raw USD / 1e6 = stored in millions (same scale as backbone parquets).

---

## Where output is stored

```
backend/data/filing_data/topline/
  income_statement/ticker=NVDA.parquet
  balance_sheet/ticker=NVDA.parquet
  cash_flow/ticker=NVDA.parquet
  _build_report.json    <- validation results per ticker
```

Do NOT mix with `backbone/` (raw XBRL facts) or `calculated/` (derived metrics).
The `calculated/` layer is built on `topline/`, never on `backbone/` directly.

---

## Quick reference -- full working example

```python
from edgar import Company, set_identity
from edgar.xbrl import XBRLS
from collections import defaultdict
import warnings
import pandas as pd
import numpy as np
warnings.filterwarnings('ignore')

# 1. Identity
set_identity('AlphaGraph Research alphagraph@research.com')

# 2. Fetch filings (mix 10-K + 10-Q for Q4 derivation)
company = Company('NVDA')
filings = company.get_filings(form=['10-K', '10-Q']).head(30)

# 3. Build XBRLS stitcher
xbrls = XBRLS.from_filings(filings)

# 4. Build rich period map (see Step 5 above for full code)
period_map = build_period_map(xbrls)

# 5. Extract statements — ALWAYS use max_periods=40
income_df   = xbrls.statements.income_statement(max_periods=40).to_dataframe()
balance_df  = xbrls.statements.balance_sheet(max_periods=40).to_dataframe()
cashflow_df = xbrls.statements.cash_flow_statement(max_periods=40).to_dataframe()

# 6. Pivot to wide format using period_map fields:
#    fiscal_quarter = canonical_fp (Q1/Q2/Q3/Annual)
#    fiscal_year    = canonical_fy (correct via fy_entry)
#    is_ytd         = True for Q2/Q3 YTD columns

# 7. Derive Q4 FIRST (Q4 = Annual - Q3_YTD original)
#    Uses period_start grouping: Annual + Q3_YTD share same period_start

# 8. Convert YTD to standalone
#    Uses period_start grouping: Q1 + Q2_YTD + Q3_YTD share same period_start
#    Q4 (different period_start) is unaffected

# 9. Validate: Q1+Q2+Q3+Q4 ~ Annual
#    Use period_start matching (not fiscal_year) to find the correct Q1-Q3 set
#    Works for fiscal year transitions (CDNS etc.) without any special casing
```
