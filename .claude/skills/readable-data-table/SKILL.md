---
name: readable-data-table
description: Build a dense, filings-style data table (financial statements, metric grids, time-series tabulars) that a domain expert — PM, analyst, researcher — can scan effortlessly. Use when the user asks for a "clean table", "financial data table", "readable data display", or shares a reference screenshot of a filings-style table and wants the UI to match. Covers row structure, column ordering, alignment, color, sticky columns, hover states, and derived rows.
version: 1.0
last_validated_at: 2026-04-28
conditions: []
prerequisites: [time-axis-sort-convention]
tags: [ui, table, design, dense-data, frontend]
---

# Readable Data Table — Build & Refinement Skill

## What this Skill does

Produces a dense tabular view of numeric data — one metric per row, one time period per column — that matches how experts read a 10-Q/10-K income statement or a Bloomberg blotter. The output is a React/TSX component styled with Tailwind, but the principles are framework-agnostic: applying them to an HTML table, a Streamlit grid, or an ag-Grid instance follows the same rules.

The goal is **effortless scanning**, not "pretty". Dense, restrained, typography- and alignment-driven. Color is used surgically to signal sign (positive/negative), never as decoration.

## When to use this Skill

Trigger this Skill when any of the following apply:

- The user wants a table of financial metrics across time periods (most common case).
- The user shares a reference screenshot of a filings-style table ("make it look like this").
- The user complains an existing table is "hard to read", "cluttered", or "doesn't feel like a filing".
- The user asks for common-size statements, walk-down views, or side-by-side comparisons.
- Any domain-expert UI where numbers dominate and the user's job is to spot anomalies fast.

Do NOT use this Skill for:

- Interactive data grids with inline editing (use ag-Grid / TanStack Table).
- Small summary tables (< 5 rows, < 3 columns) — the principles are overkill.
- Tables where text dominates over numbers.

## Core principles (do not compromise these)

1. **Right-align numbers. Always.** Digit columns must stack — ones under ones, thousands under thousands. Center-alignment forces the eye to re-anchor on every row and destroys scannability. Apply `text-right pr-4` to every data cell and to column headers.
2. **No heatmap backgrounds.** They add visual noise without information. Use color only on the text, and only to signal sign (positive = green, negative = red).
3. **Parentheses for negatives on $ / M values.** `(1,234)` not `-1,234`. Real filing convention, instantly recognizable. Percent cells keep the minus sign (italic + red already signals direction — don't double-encode).
4. **Italic for percentages.** Distinguishes margin/growth rows from absolute-value rows at a glance, even before the color registers.
5. **Real numbers, not k/M/B abbreviations.** `57,000` not `57k`. The user wants to compare exact values. Round to whole units with `toLocaleString("en-US")` for thousands separators.
6. **Filing order: most recent period on the LEFT.** PMs always look at "this quarter" first. Reversing the default ascending order is non-negotiable.
7. **Sticky metric column must be FULLY opaque.** Semi-transparent Tailwind utilities (`bg-slate-50/40`, `bg-white/80`) cause data cells to bleed through during horizontal scroll. Always use solid colors on the sticky column + its per-row stripe match + a subtle right-edge shadow.
8. **Bold the P&L spine.** Revenue, Gross Profit, Op Income, Net Income, and their corresponding margins. Everything else is subordinate.
9. **Indent and shrink derived rows.** YoY %, QoQ %, margin deltas live immediately below their parent metric, visually tucked underneath (indent, smaller font, lighter label color). The primary spine becomes the visual hero.

## Step-by-step instructions

Follow these in order. Each step builds on the previous — don't skip ahead even if the user is impatient.

### Step 1 — Understand the data shape

Before writing any code, verify:

- What does each row in the API response represent? (Usually one period per row.)
- Is the data sorted? (Usually ascending by `end_date` — you will need to reverse for display.)
- What fields are available? (Run a test fetch against the real endpoint, don't trust the type definition alone.)
- Are any metrics computed client-side vs. fetched? (Growth rates are usually backend; margin deltas are cheap enough to compute client-side.)

**Edge case seen**: backend returned `end_date` as `"2024-07-28T00:00:00"` (ISO with time). Strip to `YYYY-MM-DD` with `.slice(0, 10)` — don't try to `new Date(...).toLocaleDateString()` because that introduces timezone shifts.

### Step 2 — Define the row model

Use a typed structure. Keep the per-row metadata tight:

```tsx
type CellFmt = "M" | "%" | "$" | "pp";  // pp = percentage-point delta

interface RowDef {
  label: string;
  metric: string;       // key into the DataRow
  fmt: CellFmt;
  bold?: boolean;       // P&L spine rows
  derived?: boolean;    // YoY, QoQ, margin deltas — indented, shrunken
}

interface RowGroup {
  heading: string;      // used logically, not necessarily rendered
  rows: RowDef[];
}
```

Then list every row in the order an expert reads the filing. For an income statement that means:

1. Revenue → YoY → QoQ
2. Cost of Sales → Gross Profit → Gross Margin % → GM% Δ YoY
3. R&D → SG&A → OpEx → Op Income → YoY → QoQ → Op Margin % → OPM% Δ YoY
4. Net Income → YoY → QoQ → Net Margin % → NPM% Δ YoY
5. EPS Basic → EPS Diluted

Group logically (`ROW_GROUPS: RowGroup[]`), but **do not render loud group header bands** unless the user asks for them. Most users find them distracting. A thin top border on the first row of each group is enough structure.

**Edge case seen**: First draft had labels like `"Net Revenue YoY %"`. Once grouped, this becomes redundant — within the Revenue block, just say `"YoY %"`. Shorter labels make the table breathe.

### Step 3 — Label conventions

Shorten labels the way a Bloomberg screen does, not the way a textbook does:

| Textbook | Blotter |
|---|---|
| Operating Income | Op Income |
| Operating Margin % | Op Margin % |
| Net Income YoY % | YoY % (in context) or NI YoY % (flat) |
| EPS (Diluted) | EPS Diluted |
| Cost of Goods Sold | Cost of Sales |
| Research & Development | R&D |
| Selling, General & Admin | SG&A |
| Gross Margin percentage-point change vs prior year | GM% Δ YoY |

Use `Δ` for deltas (copy-pastable from the Greek letter) and `pp` for percentage-point units (`+2.5 pp`, never `+2.5%` — that implies a percentage of a percentage).

### Step 4 — Formatting helpers

Two pure functions. Never inline formatting logic into JSX.

```tsx
function fmtCell(value: number | null, format: CellFmt): string {
  if (value === null) return "—";
  const neg = value < 0;
  const abs = Math.abs(value);

  if (format === "%") {
    // Keep minus sign — italic + red already carries direction.
    return `${value.toFixed(1)}%`;
  }
  if (format === "pp") {
    // Explicit +/- sign to emphasize direction of change.
    const sign = value > 0 ? "+" : value < 0 ? "−" : "";
    return `${sign}${abs.toFixed(1)} pp`;
  }
  if (format === "$") {
    const body = `$${abs.toFixed(2)}`;
    return neg ? `(${body})` : body;
  }
  // "M" — whole millions, thousands-separated, parens for negatives
  const body = Math.round(abs).toLocaleString("en-US");
  return neg ? `(${body})` : body;
}

function cellStyle(value: number | null, format: CellFmt): React.CSSProperties {
  const italicFmts = format === "%" || format === "pp";
  if (value === null) return { color: "#cbd5e1", fontStyle: italicFmts ? "italic" : undefined };
  if (italicFmts) {
    const base: React.CSSProperties = { fontStyle: "italic" };
    if (value > 0) return { ...base, color: "#059669" };  // emerald-600
    if (value < 0) return { ...base, color: "#dc2626" };  // red-600
    return { ...base, color: "#64748b" };                  // slate-500 (zero)
  }
  // $ / M: black positive, red negative
  return value < 0 ? { color: "#dc2626" } : { color: "#0f172a" };
}
```

**Edge case seen**: First attempt used a heatmap (`heatStyle(value, allRowValues, goodHigh)`). User explicitly rejected it — "numbers in black, % numbers in green/red". Delete the heatmap; don't half-keep it.

### Step 5 — Two-row column header

Row 1: `end_date` in `YYYY-MM-DD`.
Row 2: compact fiscal period label (e.g. `2025Q2`) in monospace, lighter color.

```tsx
const periods      = tableRows.map((r) => (r.end_date ?? "").slice(0, 10));
const periodLabels = tableRows.map((r) =>
  (r.period_label ?? "").replace(/^FY/, "").replace(/-/g, "")
);
```

The "Metric" header cell uses `rowSpan={2}` so it spans both header rows cleanly. This cell must be sticky + `z-30` (higher than body `z-10`) so vertical scroll doesn't leak body content into the header.

### Step 6 — Sticky metric column (the trap)

This is the single biggest source of bugs. The rules:

1. The row background and the sticky `<td>` background must be the **exact same opaque color**. Never use alpha (`/40`, `/60`, `/80`).
2. Apply the same background to both even and odd rows, matched (e.g. `bg-white` on even, `bg-slate-50` on odd). Apply the identical class to the sticky `<td>`.
3. Sticky `<td>` must be `z-10` at minimum — above default data cells.
4. Add a subtle right-side shadow for visual separation: `shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]`.
5. For row-hover state, use Tailwind `group` on the `<tr>` and `group-hover:!bg-indigo-50/60` on the sticky `<td>`. The `!` important is required to override the row stripe.

```tsx
<tr className={`group border-b border-slate-50 ${stripe} hover:!bg-indigo-50/60 transition-colors`}>
  <td
    className={`sticky left-0 z-10 ${stripe} group-hover:!bg-indigo-50/60
                ${derived ? "pl-8 pr-4" : "px-4"} py-1.5
                border-r border-slate-200 whitespace-nowrap
                shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]`}
  >
    ...
  </td>
  ...
</tr>
```

**Edge case seen — critical**: The first attempt used `bg-slate-50/40` on the `<tr>` + `bg-slate-50/60` on the sticky `<td>`. When horizontally scrolled, data cells bled through the sticky column because the alpha let them show. User reported: "the Metric column somehow is transparent and we can see the numbers behind". Fix = fully opaque backgrounds everywhere on the sticky layer. Never use Tailwind alpha suffixes on sticky elements.

### Step 7 — Reverse for display

Table: most recent period on the left. Charts: keep chronological (oldest on left) so lines and bars flow left-to-right.

```tsx
const tableRows = [...rows].reverse();  // table only
const chartData = rows.map(...);         // charts stay ascending
```

Never mutate `rows` — always copy first. Downstream code may depend on the original order.

### Step 8 — Client-side derived columns

When the user asks for a metric that's one small shift away from an existing field (e.g. `GM% Δ YoY` = `gross_margin_pct` − `gross_margin_pct[-4]`), **compute in the frontend**. Faster than a backend round trip and no parquet rebuild.

```tsx
const MARGIN_DIFF_SOURCES: Record<string, string> = {
  gross_margin_pct_diff:     "gross_margin_pct",
  operating_margin_pct_diff: "operating_margin_pct",
  net_margin_pct_diff:       "net_margin_pct",
};
const rowsWithDiffs = rows.map((r, i) => {
  const out: DataRow = { ...r };
  for (const [diffKey, srcKey] of Object.entries(MARGIN_DIFF_SOURCES)) {
    const cur  = typeof r[srcKey] === "number" ? r[srcKey] as number : null;
    const prev = i >= 4 && typeof rows[i - 4][srcKey] === "number"
      ? rows[i - 4][srcKey] as number
      : null;
    out[diffKey] = (cur !== null && prev !== null)
      ? Math.round((cur - prev) * 100) / 100
      : null;
  }
  return out;
});
```

Rules for when to compute client-side vs backend:

- **Client-side**: simple arithmetic on 1–2 fetched fields (deltas, ratios, common-size).
- **Backend**: anything requiring multi-ticker joins, date-tolerance validation (true YoY with missing-quarter detection), or that needs to be queryable by the Engine agent.

**Edge case seen**: Backend YoY already uses a 4-row shift with a 45-day date tolerance check — if there's a missing quarter, it sets YoY to NaN instead of comparing wrong periods. Client-side simple shift doesn't do this; it's acceptable for UI-only derived columns but NOT for data the Engine will reason about.

### Step 9 — The five readability upgrades

Apply these as a batch. They compound; skipping any one makes the others feel off.

1. **Right-align numbers** — `text-right pr-4` on every data cell + header.
2. **Group structure without loud headers** — maintain `ROW_GROUPS` logically, render a thin top border on the first row of each group.
3. **Row hover highlight** — `group` / `group-hover:!bg-indigo-50/60` pattern.
4. **De-emphasize derived rows** — `pl-8` indent, `text-[10px]`, `text-slate-500` label color.
5. **Parentheses for negatives** — only on `$` and `M`, not `%`.

### Step 10 — Verify against the reference

If the user shared a screenshot, compare side-by-side:

- Column order: most recent on left ✓
- Date format: `YYYY-MM-DD` ✓
- Period label sub-header present ✓
- Row order matches filing walk-down ✓
- Bold on spine rows only ✓
- Italic green/red on percent rows ✓
- Parentheses on negative dollar values ✓
- No heatmap backgrounds ✓
- Sticky metric column fully opaque when scrolled ✓

If anything is off, fix before shipping. Do not rationalize deviations — the user compared it to the screenshot for a reason.

## Known edge cases

| Problem | Root cause | Fix |
|---|---|---|
| Loading spinner never resolves | Backend not running, no request timeout on fetch | Verify backend is up; check Network tab for pending request |
| `"2024-07-28T00:00:00"` in headers | Backend sends ISO with time | `.slice(0, 10)` — never `new Date(...).toLocaleDateString()` (timezone shift) |
| Data cells bleed through sticky column | Alpha on sticky bg (`bg-slate-50/40`) | Fully opaque bg; z-10; right-edge shadow |
| Header row disappears on vertical scroll | z-index too low on sticky header | Header `th` uses `z-30`, body sticky `td` uses `z-10` |
| `57k` instead of `57,000` | Premature abbreviation in `fmtCell` | Round to whole units + `toLocaleString("en-US")` |
| Negative values shown as `-1,234` | Default JS string representation | Parens: `(1,234)`; keep red color |
| `(-2.5%)` double-encoded negatives on % | Parens applied to percent format | Percent cells keep `-` sign; italic+red carries direction |
| YoY% row labels redundant inside a group | Flat `"Net Revenue YoY %"` label | When grouped, shorten to `"YoY %"` in context |
| Derived rows drown the spine | All rows same size | `derived: true` flag → indent + smaller + lighter |
| Heatmap backgrounds cluttering | Over-applied color | Delete entirely; color only text, only for sign |
| Date column label `2024-07-28T00:00:00` | Forgot to slice | `.slice(0, 10)` |
| Client-side YoY gives wrong values with missing quarters | Simple 4-shift ignores gap | For data that matters, use backend with date tolerance; client-side OK for display-only deltas |
| Group headers too loud | User found them distracting | Keep `ROW_GROUPS` logically, render only thin top border on first row of each group |

## Expected output format

**File**: A single `.tsx` component in `frontend/src/app/(dashboard)/<feature>/<Feature>View.tsx`, paired with a `<Feature>Container.tsx` that fetches the data and passes it down.

**Structure** (top to bottom of the file):

1. `"use client"` directive
2. Imports (React, icons, chart lib, types from the data client)
3. Props interface
4. `CellFmt` type + `RowDef` / `RowGroup` interfaces
5. `ROW_GROUPS` constant — the full row model
6. Helpers: `getNum`, `cellStyle`, `fmtCell`
7. Small sub-components (`ChartCard`, `LoadingOverlay`, `EmptyState`, etc.)
8. Main view component — header / tabs / error banner / scrollable body with table + charts

**Table render skeleton** (reference shape, adapt to context):

```tsx
<div className="overflow-x-auto">
  <table className="text-xs w-full">
    <thead>
      {/* Row 1: date */}
      <tr className="border-b border-slate-100">
        <th rowSpan={2} className="sticky left-0 z-30 bg-white ...">Metric</th>
        {periods.map((p, i) => (
          <th key={i} className="px-3 pt-2 pb-0.5 text-right text-[10px] font-bold text-slate-600">
            {p}
          </th>
        ))}
      </tr>
      {/* Row 2: fiscal period */}
      <tr className="border-b border-slate-200">
        {periodLabels.map((p, i) => (
          <th key={i} className="px-3 pb-2 pt-0 text-right text-[10px] font-mono text-slate-400">
            {p || "—"}
          </th>
        ))}
      </tr>
    </thead>
    <tbody>
      {ROW_GROUPS.flatMap(g => g.rows).map((rowDef, ri) => {
        const firstOfGroup = ri > 0 && ROW_GROUPS.some(g => g.rows[0]?.metric === rowDef.metric);
        const stripe = ri % 2 === 0 ? "bg-white" : "bg-slate-50";
        const derived = rowDef.derived;
        return (
          <tr key={rowDef.metric}
              className={`group border-b border-slate-50 ${firstOfGroup ? "border-t border-slate-200" : ""} ${stripe} hover:!bg-indigo-50/60 transition-colors`}>
            <td className={`sticky left-0 z-10 ${stripe} group-hover:!bg-indigo-50/60 ${derived ? "pl-8 pr-4" : "px-4"} py-1.5 border-r border-slate-200 whitespace-nowrap shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]`}>
              <span className={`${derived ? "text-[10px] text-slate-500" : "text-[11px] text-slate-700"} ${rowDef.bold ? "font-semibold" : ""}`}>
                {rowDef.label}
              </span>
            </td>
            {tableRows.map((r, qi) => {
              const v = getNum(r, rowDef.metric);
              return (
                <td key={qi}
                    className={`px-3 py-1.5 pr-4 text-right tabular-nums ${derived ? "text-[10px]" : ""} ${rowDef.bold ? "font-semibold" : ""}`}
                    style={cellStyle(v, rowDef.fmt)}>
                  {fmtCell(v, rowDef.fmt)}
                </td>
              );
            })}
          </tr>
        );
      })}
    </tbody>
  </table>
</div>
```

## Iteration discipline

When the user refines the design:

- **Make one change at a time.** Never bundle "while we're here" improvements. The user's reactions tell you whether each change was right.
- **Verify against the reference screenshot after every change.** Drift is real.
- **If the user rejects a change, remove it cleanly.** Do not leave dead code or commented-out blocks. Example: user rejected group header bands — don't leave the `<tr>` commented out, delete it and keep the `ROW_GROUPS` array for logical structure.
- **Trust the user's domain intuition over abstract UX principles.** A PM saying "don't show group headers" outranks "group headers improve scannability" — they read filings every day and know their own cognitive grooves.

## Related files in this repo

- `frontend/src/app/(dashboard)/data-explorer/DataExplorerView.tsx` — canonical reference implementation of this Skill.
- `frontend/src/app/(dashboard)/data-explorer/DataExplorerContainer.tsx` — data fetching pattern.
- `frontend/src/lib/api/dataClient.ts` — `DataRow`, `DataResult` types expected by the view.
- `backend/app/services/data_agent/concept_map.py` — `GROWTH_BASE_METRICS`, `COMPUTED_METRICS` — source of truth for what rows are available.
