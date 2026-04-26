---
name: taiwan-sector-heatmap
description: Build a dense heatmap table + drill-down combo chart for Taiwan-listed companies' monthly revenue / YoY% / MoM%. Heatmap rows grouped by subsector, columns = trailing months (most-recent on left). Click a row to load that ticker's 10-year history into a ComposedChart below (bars=revenue NT$B, lines=YoY%/MoM% on right axis). Use when the user asks for a Taiwan sector heatmap (semi, financials, TMT, anything) in DataExplorer or as a standalone panel — or for a subset of tickers (single subsector, custom ticker list, different market like TPEx-only). Covers the Taiwan data layout, the storage-path gotcha, the reusable panel + chart template, and how to swap in a different universe.
---

# Taiwan Sector Heatmap — Build Skill

## What this Skill does

Produces two linked visualizations for Taiwan-listed companies' MOPS monthly revenue:

1. **Heatmap table** — one ticker per row (grouped by subsector band), one month per column (most recent on the left), metric toggle between Revenue / YoY% / MoM%. Clicking any ticker row selects it.
2. **Drill-down combo chart** — below the heatmap, renders the selected ticker's 10-year monthly revenue as bars (NT$B, left axis) with YoY% and MoM% overlays as lines (right axis). Defaults to TSMC (2330) on first load.

Canonical reference: `frontend/src/app/(dashboard)/data-explorer/TaiwanSemiHeatmapPanel.tsx` (one file, both components).

This skill is for **building UIs** on top of Taiwan data that already exists in parquet. It is NOT about scraping — see the separate `taiwan-monthly-data-extraction` skill for that.

## When to use this Skill

- "Add a heatmap for Taiwan [sector] companies"
- "Show Taiwan financials / TMT / consumer companies' monthly revenue as a heatmap"
- "Filter the Taiwan Semi heatmap to just Foundries / IC Design / OSAT"
- "Build the same heatmap but for a custom ticker list"
- "Add the Taiwan heatmap to [some other page]"

Do NOT use for:
- Scraping Taiwan data (use `taiwan-monthly-data-extraction`)
- Generic financial data tables (use `readable-data-table`)
- US/EDGAR heatmaps (use the existing `SectorHeatmapPanel` in `DataExplorerView.tsx`)

---

## The Data We Have

### Parquet: monthly revenue

| Field | Path |
|---|---|
| File | `backend/data/taiwan/monthly_revenue/data.parquet` |
| History file | `backend/data/taiwan/monthly_revenue/history.parquet` (amended rows only) |
| Dedup key | `(ticker, fiscal_ym)` |
| Fiscal period format | `YYYY-MM` string (e.g. `"2026-03"`) — already western calendar, ROC conversion done upstream |
| Rows as of Apr 2026 | ~12,200 rows, 50 tickers, coverage back to 1999 |

Columns:

```
ticker                 str        # "2330", "2454", ... (NOT "TSMC")
market                 str        # "TWSE" or "TPEx"
fiscal_ym              str        # "2026-03"
revenue_twd            int64      # Raw TWD (NOT thousands) — TSMC Mar-2026 ≈ 4.15e11
yoy_pct                float64    # Fraction, NOT percent — 0.45 = +45% YoY
mom_pct                float64    # Fraction — 0.31 = +31% MoM
ytd_pct                float64    # Fraction — cumulative YTD vs prior-year YTD
cumulative_ytd_twd     int64      # YTD through this month, TWD
prior_year_month_twd   int64      # Same month, prior year, TWD
first_seen_at          datetime   # When we first captured this row
last_seen_at           datetime   # When we last confirmed/overwrote
content_hash           str        # For amendment detection
amended                bool       # True if this row supersedes a prior value
```

### Watchlist: which tickers to show

| Path | `backend/data/taiwan/watchlist_semi.csv` |

```csv
ticker,name,market,sector,subsector,notes
2330,TSMC,TWSE,Semiconductors,Foundry,
2454,MediaTek,TWSE,Semiconductors,IC Design,
...
```

The watchlist is the authoritative list of "companies we care about" + their subsector bucketing. For a new sector heatmap, create a new CSV with the same schema.

### Existing 50-ticker semi universe

Subsectors and ordering (value-chain order, upstream → downstream):

```
Foundry          → TSMC, UMC, VIS, PSMC
IC Design        → MediaTek, Realtek, Novatek, Phison, Silergy, GUC, Faraday, Elan, Ali Corp, Himax
Memory           → Nanya, Macronix, Winbond, Walton Advanced, Powertech
DRAM Module      → Adata, Transcend, Team Group
OSAT             → ASE, ChipMos, KYEC, Ardentec, Greatek, Chipbond
Wafer            → GlobalWafers, SAS (Sino-American Silicon), WIN Semi (GaAs)
Equipment        → Gudeng, M31, Topoint, Taiwan Mask
PCB/Substrate    → Unimicron, Nan Ya PCB, Kinsus, Gold Circuit, Tripod, WUS
Materials        → Eternal Materials, Chang Wah Electromaterials
Optical          → Largan, GSEO
Server EMS       → Hon Hai (Foxconn), Pegatron, Quanta, Wistron, Inventec, ASUS
```

---

## The Backend

### Router: `backend/app/api/routers/v1/taiwan.py`

Relevant endpoints:

| Endpoint | Returns |
|---|---|
| `GET /taiwan/watchlist` | Full watchlist CSV as JSON rows |
| `GET /taiwan/monthly-revenue?tickers=A,B,C&months=N` | Trailing N months (max 120) per ticker, sorted ascending within ticker |
| `GET /taiwan/ticker/{ticker}` | Ticker meta + latest revenue row |
| `GET /taiwan/health` | Scraper heartbeat |

All responses use the `APIResponse` envelope: `{success, data, error, metadata}`.

### Storage module: `backend/app/services/taiwan/storage.py`

**CRITICAL gotcha — do not repeat:** `DEFAULT_DATA_DIR` uses `Path(__file__).resolve().parents[N]`. The correct value is **`parents[3]`** (resolves to `backend/`), NOT `parents[4]`. If you see `/taiwan/monthly-revenue` returning `{"data": []}` even though the parquet has rows, this is the bug. A previous version had `parents[4]` which silently resolved to a non-existent directory and returned empty DataFrames. Fix is one character; verify with:

```python
from backend.app.services.taiwan import storage
assert storage.DEFAULT_DATA_DIR.exists(), storage.DEFAULT_DATA_DIR
```

The `registry.py` module uses `parents[3]` correctly — that's why `/watchlist` worked while `/monthly-revenue` returned empty before the fix. If you see only one of the two endpoints working, suspect this again.

---

## The Frontend Client

### `frontend/src/lib/api/taiwanClient.ts`

Already wired with `watchlist()`, `monthlyRevenue(tickers, months)`, `ticker(ticker)`, `health()`. All return `{success, data}`. Types `WatchlistEntry` and `MonthlyRevenueRow` mirror the backend.

Use as-is. Do not add new methods unless the backend grows new endpoints.

---

## The Heatmap Panel — Reusable Template

Canonical file: `frontend/src/app/(dashboard)/data-explorer/TaiwanSemiHeatmapPanel.tsx`.

### Structure (top to bottom)

1. `"use client"` directive
2. Imports — `taiwanClient`, `Loader2`, `TrendingUp`
3. `Metric` type — `"revenue" | "yoy_pct" | "mom_pct"`
4. `METRIC_OPTIONS` constant (user-facing labels)
5. `SUBSECTOR_ORDER` — explicit array defining row-group order (value chain for semi; adapt for other sectors)
6. `PCT_CAP` — `60`. The color scale clips at ±60%. Values beyond still display but the background hits max saturation.
7. Formatting helpers — `fmtPct`, `fmtRevenueB`, `fmtFiscalYm`, `yearBoundary`
8. Color helpers — `pctColor`, `revenueColor`, `textColorForBg`
9. Main component

### Key implementation decisions for the CHART (don't re-litigate)

| Decision | Why |
|---|---|
| Bars for revenue, Lines for YoY% / MoM% | Revenue is a **level**; growth rates are **rates of change**. Different primitives signal that. |
| Two Y-axes: `revenue` on left (NT$B), `pct` on right (%) | Revenue scale (0–500 for TSMC) and growth scale (−50 to +80%) would otherwise clobber each other. |
| Chronological order (oldest left, newest right) | Chart convention — time flows left to right. The heatmap above is reversed (newest on left) by filing convention. Two views, two orderings; don't "fix" this to match. |
| Bar color: `fill="#6366f1"` (indigo-500) at 75% opacity, `maxBarSize={8}` | Thin bars keep 120 months legible; opacity lets gridlines show through |
| Line colors: YoY% = emerald-600 (solid), MoM% = amber-500 (dashed, thinner) | YoY is the primary growth metric; MoM is secondary/noisier. Visual hierarchy matches. |
| `<ReferenceLine yAxisId="pct" y={0} strokeDasharray="2 2" stroke="#cbd5e1" />` | Zero is the single most important anchor on a growth chart — is this month growing or shrinking? |
| `connectNulls` on percent lines | First point in the series has no prior-year or prior-month to compare to; MoM NaN pre-2016 is expected. Connecting preserves the visual flow. |
| `tickInterval = Math.max(0, Math.floor(chartData.length / 10) - 1)` | ~10 x-axis labels regardless of window size. Hardcoding every-12-months breaks when user narrows the range. |
| Tooltip formatter signature takes `unknown` and narrows to `number` | Recharts' `Formatter<ValueType, string>` generic resolves `ValueType = string \| number \| (string \| number)[]`. Typing the callback as `(value: number \| null, ...)` fails TS compile — see Edge Cases table. |

### Key implementation decisions for the HEATMAP (don't re-litigate)

| Decision | Why |
|---|---|
| `yoy_pct` / `mom_pct` stored as **fractions** | Backend pre-computes; display function multiplies by 100. Don't double-convert. |
| Revenue stored in **raw TWD** (not thousands) | Display divides by 1e9 → NT$B. TSMC Mar-2026 = 4.15e11 → "415" |
| Diverging scale: **red ↔ white ↔ green**, capped at ±60% | 60% covers the common scan range; beyond that both signs pin to max-saturation so cells stay readable |
| Revenue shading: **per-row relative** (row's own min/max) | TSMC NT$400B and Ali Corp NT$200M cannot share an absolute scale — one side would be invisible |
| Color alpha minimum = 0.05 | Prevents "blank" cells for tiny non-zero values |
| Month columns **descending** (newest on left) | Filing convention — PMs read "this month" first |
| Subsector rows rendered as a **band row** (full-width bg-slate-50 with name + count) | Visible group boundary without screaming; ticker rows below are plain white |
| Year boundaries: **Dec → Jan** gets a `border-l border-slate-300` | Visual year separator when scanning 24+ months |
| Font: **tabular-nums** on all data cells | Digits align so the eye tracks changes without re-anchoring |
| Hover: **group-hover:bg-indigo-50/40** on row + sticky cell | Sticky cell MUST match; otherwise hover bleeds |
| **Click-to-select**: `onClick` on `<tr>`, `cursor-pointer` class | Whole-row click target — easier than only the ticker cell. Selected row uses `bg-indigo-50` + `border-l-2 border-l-indigo-600` on sticky cell. |
| Selected-row hover uses `hover:bg-indigo-100` (not `/40`) | When a row is already tinted, the hover needs a noticeable step up. |
| Sticky ticker column: fully opaque `bg-white`, z-10, right-edge shadow `shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]` | Alpha backgrounds on sticky columns bleed during horizontal scroll — confirmed bug pattern |
| Text color on background: white when |v| > 35% or intensity > 0.55; else dark slate | Keeps contrast legible regardless of cell fill |
| Tooltip on every data cell: `${ticker} ${month} · NT$X.XB · YoY ±X% · MoM ±X%` | Single-hover answers every follow-up question |

### Data flow

```
Heatmap (top component):
  useEffect(months)
    → taiwanClient.watchlist()              # once; 50 rows
    → taiwanClient.monthlyRevenue(allTickers, months)   # trailing N months × 50 tickers
    → local state
  selectedTicker: useState<string>("2330")   # TSMC default; click a row to change

  useMemo:
    monthCols  = sort(unique(fiscal_ym)).reverse()
    byTickerMonth = Map<ticker, Map<ym, row>>
    grouped    = [[subsector, entries[]], ...] in SUBSECTOR_ORDER
    revenueRowRange = Map<ticker, {min, max}>  # for per-row $ shading

Render: subsector band row, then one `<tr key={ticker} onClick={() => setSelectedTicker(ticker)}>`
per ticker. Selected row gets bg-indigo-50 + left border-l-2 border-l-indigo-600;
sticky cell uses matching bg so the highlight doesn't bleed on horizontal scroll.

Chart (TickerRevenueChart sub-component):
  useEffect(ticker)
    → taiwanClient.monthlyRevenue([ticker], 120)   # 10 years for one ticker

  useMemo: chartData sorted ASCENDING by fiscal_ym
    { fiscal_ym, label: "Mar '26",
      revenue_b: twd/1e9,
      yoy_pct: frac*100 (or null),     # CONVERT TO PERCENT for the chart axis
      mom_pct: frac*100 (or null) }

Render: Recharts <ComposedChart>
  - yAxisId="revenue" orientation="left"  → NT$B (Bar)
  - yAxisId="pct"     orientation="right" → % (Lines, both YoY and MoM)
  - <ReferenceLine yAxisId="pct" y={0} />  zero-anchor for growth direction
  - tickInterval = floor(N/10) - 1        so 10y chart shows ~10 x-labels
  - connectNulls on the percent lines      first data point has no YoY/MoM
```

---

## How to Reuse — Common Variants

### Variant A: Different subsector subset

Simplest — add a filter prop:

```tsx
export default function TaiwanSemiHeatmapPanel({
  subsectorFilter,  // optional: only show these subsectors
}: { subsectorFilter?: string[] } = {}) {
  // ... inside grouped useMemo:
  const allowedSet = subsectorFilter ? new Set(subsectorFilter) : null;
  for (const w of watchlist) {
    if (allowedSet && !allowedSet.has(w.subsector)) continue;
    // ... bucket by subsector as before
  }
}
```

Usage: `<TaiwanSemiHeatmapPanel subsectorFilter={["Foundry", "IC Design"]} />`.

### Variant B: Custom ticker list (e.g. AI-server supply chain)

Bypass the watchlist. Pass tickers + display names as a prop:

```tsx
export default function TaiwanCustomHeatmap({
  tickers,  // [{ticker: "2330", name: "TSMC", subsector: "Foundry"}, ...]
}: { tickers: { ticker: string; name: string; subsector: string }[] })
```

Inside, replace `taiwanClient.watchlist()` with the prop and `taiwanClient.monthlyRevenue(tickers.map(t => t.ticker), months)`.

### Variant C: Entirely different sector (e.g. Taiwan Financials)

1. Create a new watchlist CSV: `backend/data/taiwan/watchlist_financials.csv` (same schema).
2. Add a `registry.load_watchlist_financials()` method OR generalize `load_watchlist` to accept a filename.
3. Add an API endpoint `/taiwan/watchlist/financials` OR pass `?universe=financials` to the existing one.
4. Copy `TaiwanSemiHeatmapPanel.tsx` → `TaiwanFinancialsHeatmapPanel.tsx`.
5. Update `SUBSECTOR_ORDER` for the new value chain (e.g. `Banks → Insurance → Securities → Asset Management`).
6. Wire a new tab in `DataExplorerView.tsx` (see Variant E).

The `monthly_revenue` parquet already contains data for all Taiwan tickers the scraper has seen — no pipeline work is needed as long as the new universe's tickers are in the scraper's watchlist.

### Variant D: Different market (TPEx only, or TWSE only)

Filter inside the panel after `watchlist()` returns:

```tsx
const filtered = wl.data.filter(w => w.market === "TPEx");
```

No backend changes. The watchlist CSV already has a `market` column.

### Variant E (chart): Standalone chart without the heatmap

The `TickerRevenueChart` sub-component is reusable — it takes only `ticker` and the optional `entry` (for the header label) as props. Usage:

```tsx
<TickerRevenueChart ticker="2454" entry={{ ticker: "2454", name: "MediaTek", subsector: "IC Design", market: "TWSE" }} />
```

If `entry` is omitted, the header just shows the ticker number.

### Variant F (chart): Different window length

Replace the hard-coded `120` in `taiwanClient.monthlyRevenue([ticker], 120)` with a prop or state value. The endpoint cap is 120 months — for 20-year windows the parquet has data back to 1999 but the API needs the `le` constraint raised in `backend/app/api/routers/v1/taiwan.py:42` first.

### Variant G (chart): Add a third overlay (e.g. YTD% or rolling-3-month)

Add another `<Line yAxisId="pct" dataKey="ytd_pct" ... />`. The `ytd_pct` field is already in `MonthlyRevenueRow`. Multiply by 100 in the `chartData` mapper. Pick a color that differs visually from emerald (YoY) and amber (MoM) — `#8b5cf6` (violet) is a safe choice.

### Variant H: Add a new tab in DataExplorerView

Three edits to `frontend/src/app/(dashboard)/data-explorer/DataExplorerView.tsx`:

```tsx
// 1. Import (near the existing `SemiPricingPanel` import)
import TaiwanFinancialsHeatmapPanel from "./TaiwanFinancialsHeatmapPanel";

// 2. Expand the viewMode union
const [viewMode, setViewMode] = useState<
  "financials" | "semi-pricing" | "taiwan-semi" | "taiwan-financials"
>("financials");

// 3. Add a toggle button + a conditional render block matching the existing
//    "Taiwan Semi" pattern (search for `viewMode === "taiwan-semi"`).
```

Nothing else changes. The Financials block is already gated by `viewMode === "financials"`.

---

## Formatting & Color Helpers — Copy These Verbatim

These four functions are the entire visual spec. Copy-paste for new heatmaps.

```tsx
const PCT_CAP = 60;

function fmtPct(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(1)}%`;
}

function fmtRevenueB(twd: number | null): string {
  if (twd == null || Number.isNaN(twd)) return "—";
  const b = twd / 1e9;
  if (b >= 100) return b.toFixed(0);
  if (b >= 10)  return b.toFixed(1);
  return b.toFixed(2);
}

function pctColor(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "#f8fafc";
  const pct = v * 100;
  const t = Math.max(-PCT_CAP, Math.min(PCT_CAP, pct)) / PCT_CAP;
  const alpha = Math.max(0.05, Math.abs(t)).toFixed(3);
  return t >= 0
    ? `rgba(5, 150, 105, ${alpha})`    // emerald-600
    : `rgba(220, 38, 38, ${alpha})`;   // red-600
}

function revenueColor(v: number | null, rowMin: number, rowMax: number): string {
  if (v == null || Number.isNaN(v) || rowMax <= rowMin) return "#f8fafc";
  const t = (v - rowMin) / (rowMax - rowMin);
  const alpha = Math.max(0.05, t).toFixed(3);
  return `rgba(99, 102, 241, ${alpha})`;  // indigo-500
}
```

---

## Known Edge Cases

| Symptom | Root cause | Fix |
|---|---|---|
| `/monthly-revenue` returns `{data: []}` but parquet has rows | `storage.DEFAULT_DATA_DIR = parents[4]` instead of `parents[3]` | See Storage module section above |
| Every YoY cell shows 2234%, 4567% instead of 22%, 45% | Double-multiplying the fraction | `yoy_pct`/`mom_pct` are fractions; `fmtPct` multiplies by 100 once. Don't multiply upstream. |
| TSMC revenue cell shows `0.00` or `415192` | Units confusion | `revenue_twd` is raw TWD. Divide by 1e9 for NT$B. Do NOT divide by 1e6 (that gives NT$M). |
| Sticky ticker column bleeds when scrolling horizontally | Alpha background (`bg-white/80` or `bg-slate-50/40`) on sticky cell | Use fully opaque `bg-white` + right-edge shadow. See `readable-data-table` skill for the shadow class. |
| Data cells show `Invalid Date` in sort | `fiscal_ym` is `"YYYY-MM"` not a Date | Sort as string (lexicographic works); do not convert to `new Date()` |
| Subsector band row duplicates across re-renders | Missing `key` on React Fragment wrapping `(band + ticker rows)` tuple | Wrap in `<Fragment key={subsector}>` — shorthand `<>` cannot take a key |
| First month's MoM is NaN (expected), but YoY is also NaN for every ticker pre-2000 | No prior-year row in parquet → yoy_pct left as NaN at ingest | Expected; display as "—". If user reports "missing YoY for historical period X", verify the prior-year row exists in parquet before blaming the UI. |
| `months` query returns fewer rows than expected for some tickers | Ticker started trading after the window begins | Expected; the endpoint takes the latest N rows per ticker. A ticker with only 14 months of history returns 14 rows even when `months=36`. |
| ROC year appears somewhere in output | You're reading a `_raw` HTML file, not the parquet | Parquet is already western-calendar normalized. Never build UI off the raw captures in `backend/data/taiwan/_raw/`. |
| TS error `Type 'string' is not assignable to type 'number'` on Recharts `Tooltip formatter` | Recharts' formatter callback receives `ValueType = string \| number \| (string \| number)[]`. Typing the param as `(value: number \| null, ...)` fails. | Type the params as `(value, name)` (untyped — let TS infer `unknown`) and narrow inside: `const v = typeof value === "number" ? value : null;` |
| YoY% / MoM% lines show 0.45 instead of 45 on the right axis | Forgot to multiply the fraction by 100 when building chart data | Backend stores fractions; `pctColor` and `fmtPct` multiply by 100. For Recharts, the value goes through the axis as-is — multiply when constructing `chartData` in the `useMemo`. |
| Bar chart shows wrong direction (newest on left) | Used the heatmap's `monthCols` (descending) for the chart | Charts must be ASCENDING by `fiscal_ym`. Always `.slice().sort((a,b) => a.fiscal_ym.localeCompare(b.fiscal_ym))` before mapping. |
| 120 bars are unreadable smudge | `maxBarSize` not set → bars expand to fill width | Set `maxBarSize={8}` on the `<Bar>` — keeps bars thin enough that all 120 months fit |
| First several months have no YoY/MoM line | Expected — those rows have NaN for percent fields (no prior-year comparator) | `connectNulls` on the `<Line>` makes the line skip past nulls instead of breaking. Don't fabricate values. |

---

## Verification Checklist — Do This Before Shipping

1. `GET /api/v1/taiwan/watchlist` returns >0 entries. If empty: check `backend/data/taiwan/watchlist_<universe>.csv` exists.
2. `GET /api/v1/taiwan/monthly-revenue?tickers=2330&months=3` returns non-empty data with `yoy_pct` and `mom_pct` as fractions in roughly −1.0 to +2.0. If empty: `storage.DEFAULT_DATA_DIR` path bug (see above).
3. Load the page → check that:
   - At least one cell per ticker per recent month is colored (not all `#f8fafc`).
   - TSMC's most recent month shows NT$300B–450B range (sanity on units).
   - Subsector bands render in value-chain order, not alphabetical.
   - Sticky ticker column stays opaque when scrolling right.
   - Hover on a cell shows the full `ticker month · NT$X.XB · YoY ±X% · MoM ±X%` tooltip.
4. Toggle 12m / 24m / 36m → count of columns matches the button.
5. Switch metric to "Revenue (NT$B)" → per-row relative shading kicks in; TSMC and a small-cap both show contrast within their own histories.
6. Click TSMC row in heatmap → chart below loads "10-Year Monthly Revenue · 2330 · TSMC" with ~120 bars + YoY/MoM lines. Click a different ticker → chart re-fetches and re-renders within ~300ms. Selected row visibly highlighted (indigo bg + left border).
7. Hover any bar in the chart → tooltip shows `Revenue: NT$X.XXB`, `YoY %: ±X.X%`, `MoM %: ±X.X%` — formatter narrowing works.
8. Zero-line on the right axis is visible (dashed slate-300) — growth direction visually anchored.

If any of these fail, fix before claiming done.

---

## Related Files

- `frontend/src/app/(dashboard)/data-explorer/TaiwanSemiHeatmapPanel.tsx` — canonical implementation
- `frontend/src/app/(dashboard)/data-explorer/DataExplorerView.tsx` — tab wiring (search `viewMode === "taiwan-semi"`)
- `frontend/src/lib/api/taiwanClient.ts` — HTTP client
- `backend/app/api/routers/v1/taiwan.py` — FastAPI router
- `backend/app/services/taiwan/storage.py` — parquet I/O (mind the `parents[3]` gotcha)
- `backend/app/services/taiwan/registry.py` — watchlist CSV loader
- `backend/data/taiwan/monthly_revenue/data.parquet` — the data
- `backend/data/taiwan/watchlist_semi.csv` — the 50-ticker semi universe

## Related Skills

- `taiwan-monthly-data-extraction` — scraping pipeline (how the parquet gets filled)
- `readable-data-table` — general principles for dense financial tables (some apply here: right-align, tabular-nums, most-recent-on-left, opaque sticky columns)
- `edgar-period-analysis` / `edgar-topline-extraction` — the US-side equivalents; different data, same UI philosophy
