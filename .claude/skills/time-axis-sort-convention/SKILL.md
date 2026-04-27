---
name: time-axis-sort-convention
description: Project-wide rule for how time periods are sorted in tables, charts, and lists across the AlphaGraph dashboard. Use when building any UI that displays metrics across time (financials, segments, capacity, guidance, KPI tables) or when modifying a backend endpoint that returns a time-indexed `periods` array. Codifies "newest-first for tables, oldest-first for charts" — the analyst-friendly default.
version: 1.0
last_validated_at: 2026-04-27
conditions: []
prerequisites: []
tags: [convention, ui, time-series, table, chart]
---

# Time-Axis Sort Convention

## The rule (non-negotiable defaults)

**Tables** that display metrics across time:
- **Time on COLUMNS** → most recent period on the LEFT, oldest on the right.
- **Time on ROWS** → most recent period on the TOP, oldest at the bottom.

**Charts** that plot metrics across time:
- X-axis time → oldest on the LEFT, newest on the right (universal chart convention; never invert).

The rule is symmetric: in both layouts, the table's "anchor" (top-left cell) is **the most recent observation** — which is what the analyst's eye lands on first and uses to interpret everything else.

## Why

A PM, analyst, or research user opens the dashboard to answer "what's happening NOW?". The most recent data point should require zero scanning to find. Tables that scroll right-to-left for "now" force a scan-and-orient step every time. The convention is universal in filings (10-Qs, earnings releases, Bloomberg, FactSet) — matching it makes the UI feel native.

Charts go the other way because the convention there is also universal — line/bar charts read left-to-right as a temporal flow. Mixing the two (newest-left chart) breaks every viewer's mental model.

## How to apply

### Backend (FastAPI / Python)

When pivoting long-format data into a wide table for a `/financials/wide`, `/cashflow`, `/balance-sheet`, `/segments`, etc. endpoint:

```python
period_order = (
    agg[["period_label", "period_end"]]
    .drop_duplicates()
    .sort_values("period_end", ascending=False)   # newest first
    .head(quarters)
)
chosen = period_order["period_label"].tolist()    # newest-first per project table convention
```

**Do not** reverse this list. The frontend table iterates the order as-returned. If you absolutely need chronological order somewhere internal (e.g. computing a YoY series), keep that local — never expose oldest-first via the API.

When sorting dimension rows by their latest-period value (e.g. ranking node nodes by current revenue share):

```python
piv = piv.sort_values(by=chosen[0], ascending=False)   # chosen[0] is the most recent period
```

(Was previously `chosen[-1]` when the array was oldest-first; the index changes with the convention.)

### Frontend tables (React / TSX)

Iterate `data.periods` as-returned. The leftmost `<th>` after the metric column should be the most recent period:

```tsx
{data.periods.map((p) => (   // periods come newest-first; render as-is
  <th key={p}>{p}</th>
))}
```

If the API gives you data in the wrong order (e.g. legacy endpoint), reverse at the component edge, not deep inside the rendering loop:

```tsx
const tableRows = [...rows].reverse();   // single reversal, comment why
```

### Frontend charts (Recharts / Visx)

Charts want oldest-first. If the API serves newest-first (per the table convention), reverse explicitly before feeding into the chart:

```tsx
const chartData = useMemo(() => {
  if (!data) return [];
  // API returns periods newest-first (table convention); chart x-axis
  // wants oldest-first so the line reads left→right chronologically.
  return [...data.periods].reverse().map((p) => ({ period: p, ... }));
}, [data]);
```

The comment is mandatory — without it future maintainers might "fix" the reverse, breaking the chart.

### Lists with time-on-rows

Examples: guidance vs actual, transcript quarter index, PDF catalog.

Sort key: convert the period label to a `(year, quarter_index)` tuple and sort descending. Pure lexicographic sort over `'4Q25'`-style strings does the wrong thing (it sorts by leading digit first, clustering all 4Qs together regardless of year).

```python
def _period_sort_key(label: str) -> tuple[int, int]:
    fm = re.match(r"FY(\d{2})", label)
    if fm:
        yy = int(fm.group(1))
        return (2000 + yy if yy < 50 else 1900 + yy, 5)   # FY sorts after Q4
    qm = re.match(r"(\d)Q(\d{2})", label)
    if qm:
        q = int(qm.group(1))
        yy = int(qm.group(2))
        return (2000 + yy if yy < 50 else 1900 + yy, q)
    return (0, 0)

rows.sort(key=lambda r: _period_sort_key(r["for_period"]), reverse=True)
```

### Edge cases

- **Mixed FY + quarterly periods** (e.g. UMC Guidance tab where annual capex is FY-keyed but margin guidance is quarterly): sort using the helper above. FY items get `quarter=5` so they slot AFTER Q4 of the same year (FY26 → 1Q26 → FY25 → 4Q25 → ... reads naturally).
- **Period header in chart x-axis but values shown in a side table**: the chart and table can both render from the same `data.periods` array — the chart reverses, the table does not. Both share the same period strings to avoid drift.
- **Period selector / dropdown for "pick a quarter"**: most recent first (matches the table convention; user usually wants to inspect "this quarter" before "last quarter").

## Verification checklist

Before shipping a new tab or panel:

1. **Open the table**. Is "this quarter" in the leftmost data column / topmost row? If no — fix.
2. **Open the chart on the same data**. Does the line read left-to-right with the most recent point on the right? If no — fix.
3. **Inspect the API response** (`curl /api/v1/.../endpoint | jq '.periods'`). Is it newest-first? If no — flip in the backend, not in the frontend.
4. **Re-run the test client / Storybook fixture** with one quarter of data and one with 20 quarters. Both should anchor correctly.

## Related skills

- `readable-data-table` — broader table aesthetics (alignment, color, sticky columns). This skill enforces principle #6 of that one.

## When this rule does NOT apply

- Pure historical timelines where the user is browsing the past (e.g. "all earnings reports since 2010"). Here oldest-first chronological is fine because there's no "current" anchor.
- Cross-entity comparisons where the X-axis is companies, not time.
- Calendar widgets / date pickers — those have their own conventions.

The rule applies when **the user's primary question is "what's happening now?"** and the data is observational time-series. That covers ~95% of the AlphaGraph dashboard.
