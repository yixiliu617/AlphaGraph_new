---
name: edgar-period-analysis
description: HIGH PRIORITY. Read EDGAR-sourced fiscal data safely when building charts, tables, or analyses across tickers. Never guess the fiscal period; always anchor on the most-recent-row label as reported by edgartools, then step backward by exactly one fiscal quarter per row (sorted by end_date descending). When an older row's edgartools label disagrees with the stepped-back label, prefer the stepped-back label for display and keep a mismatch note below the table. Use whenever the user asks to build a multi-ticker time-series, a sector heatmap, a YoY comparison, a quarterly table, or asks to "see the last N quarters" for any ticker sourced from EDGAR.
version: 1.0
last_validated_at: 2026-04-28
conditions:
  - requires_dir: [backend/data/filing_data]
prerequisites: []
tags: [edgar, fiscal-periods, data-analysis, high-priority]
---

# EDGAR Period Analysis — High Priority Skill

## The rule (in 30 seconds)

When displaying EDGAR quarterly data:

1. **Read the parquet/dataframe as-is.** Do not modify fiscal_year or fiscal_quarter during ingestion. Do not try to "correct" them in topline or calculator layers. Do not invent period labels.

2. **Sort rows by `end_date` descending.** This is the single source of ordering.

3. **Anchor on the first (latest) row.** Read its `fiscal_year` and `fiscal_quarter` from edgartools. That label — and ONLY that label — is trusted as ground truth.

4. **Step backward by fiscal quarter for every subsequent row.** Position 0 → anchor label. Position 1 → anchor label minus 1 fiscal quarter. Position 2 → minus 2. Q1 rolls over to prior-year Q4.

5. **Compare stepped-back label to edgartools' label for each row.** If they disagree, record a `mismatch = {position, end_date, stepped_back_label, edgar_label}`. **Display the stepped-back label; list mismatches below the table.**

This rule exists because edgartools mis-labels historical fiscal periods for several filers (DELL, AAPL, LITE, AVGO, QCOM, MRVL among the ones observed). Row-by-row trust of edgartools' fiscal_period / fiscal_year produces wrong labels like "2025-01-31 → FY2026-Q4" (off by one fiscal year). Anchoring to the latest row and stepping back bypasses this class of bug entirely.

## When to use this Skill

Use whenever any of:

- User asks to build a sector heatmap, comparison table, or cross-ticker time series from EDGAR data.
- User asks to "show the last N quarters" for one or many tickers.
- User reports wrong or inconsistent fiscal labels in a chart.
- User requests a YoY comparison, QoQ delta, or trailing-N quarter calculation using EDGAR-sourced values.
- You are designing a new component that reads from `backend/data/filing_data/calculated/` or `backend/data/filing_data/topline/`.
- You are writing a new API endpoint that returns quarterly metrics grouped across tickers.

## When NOT to use this Skill

- Single-ticker displays that use edgartools' labels only for the ONE most recent row (no stepping needed).
- XBRL ingestion (topline builder). That layer has its own concerns; this skill applies to the consumers of the calculated parquet, not the ingestion.
- Non-EDGAR data (earnings press releases, transcripts, news).

## Core principles

### Principle 1 — Never guess the period

Do not:

```python
# WRONG — trying to infer the fiscal quarter from end_date month
def infer_quarter(end_date):
    return f"Q{(end_date.month - 1) // 3 + 1}"

# WRONG — trusting a historical row's fiscal_year for YoY comparison
prior_year_q = df[(df.fiscal_year == current_fy - 1) & (df.fiscal_quarter == current_q)]
```

Edgartools' `fiscal_period` and `fiscal_year` are reliable only for the most recent filing. Comparative columns in older 10-Qs/10-Ks may carry the filing-year's fiscal_year instead of the period's true fiscal_year. **Do not trust historical fiscal_year labels.**

Do:

```python
# Load, sort by end_date descending, anchor on the latest row
df = df[df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"]) & (~df["is_ytd"].astype(bool))]
df = df.sort_values("end_date", ascending=False).head(n_quarters)

latest = df.iloc[0]
anchor_fy = int(latest["fiscal_year"])
anchor_q  = int(str(latest["fiscal_quarter"]).replace("Q", ""))
```

### Principle 2 — Step backward deterministically

```python
def step_back(fy: int, q_num: int, steps: int) -> tuple[int, int]:
    """Q4 -> Q3 -> Q2 -> Q1 -> prior-year Q4. steps >= 0."""
    total = fy * 4 + (q_num - 1) - steps
    new_fy = total // 4
    new_q  = (total % 4) + 1
    return new_fy, new_q

def fmt_label(fy: int, q_num: int) -> str:
    return f"FY{fy}-Q{q_num}"
```

Each row at position `i` (0-indexed in the descending-by-end_date list) gets label `fmt_label(*step_back(anchor_fy, anchor_q, i))`.

### Principle 3 — Surface mismatches, do not hide them

For each row, compare the stepped-back label against edgartools' raw label:

```python
edgar_label = f"FY{int(row['fiscal_year'])}-{row['fiscal_quarter']}"
stepped_label = fmt_label(*step_back(anchor_fy, anchor_q, pos))
if edgar_label != stepped_label:
    mismatches.append({
        "position": pos,
        "end_date": row["end_date"].strftime("%Y-%m-%d"),
        "expected": stepped_label,
        "edgar":    edgar_label,
    })
```

The display uses `stepped_label`. The UI lists mismatches in a small footnote below the table so the user can audit EDGAR drift without the chart lying to them.

### Principle 4 — Column alignment is RELATIVE, not absolute

When comparing multiple tickers, column headers are `LATEST, −1Q, −2Q, …, −NQ` — **not** calendar quarters, **not** fiscal labels. Different tickers have different fiscal year ends, so any absolute column label is either wrong for someone or aligns reports filed in completely different calendar months into the same column. Relative positions are unambiguous.

Inside each cell, display the per-ticker fiscal label as secondary text (e.g. `+39.5` large / `2026 Q4` small below). The column header gives relative position; the cell gives absolute period.

### Principle 5 — Use is_ytd filter on input

The calculator layer retains `is_ytd=True` rows that couldn't be converted to standalone (missing baseline). The consumer MUST filter those out:

```python
df = df[
    df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
    & (~df["is_ytd"].astype(bool))
    & df["end_date"].notna()
]
```

Failing to filter `is_ytd=True` produces cumulative YTD values masquerading as standalone quarters.

## Recipe: building a multi-ticker stepped-period endpoint

```python
def build_ticker_series(ticker: str, quarters: int) -> dict:
    path = _CALC_DIR / f"ticker={ticker}.parquet"
    if not path.exists():
        return empty_series(ticker)

    df = pd.read_parquet(path, columns=[
        "end_date", "fiscal_year", "fiscal_quarter", "is_ytd",
        "revenue_yoy_pct",  # or whichever metric
    ])

    df = df[
        df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
        & (~df["is_ytd"].astype(bool))
        & df["end_date"].notna()
    ].copy()
    df["end_date"] = pd.to_datetime(df["end_date"])
    df = df.sort_values("end_date", ascending=False).head(quarters)

    if df.empty:
        return empty_series(ticker)

    latest = df.iloc[0]
    try:
        anchor_fy = int(latest["fiscal_year"])
        anchor_q  = int(str(latest["fiscal_quarter"]).replace("Q", ""))
    except Exception:
        return empty_series(ticker)  # anchor unreadable → bail

    points: list[dict] = []
    mismatches: list[dict] = []

    for pos, (_, row) in enumerate(df.iterrows()):
        step_fy, step_q = step_back(anchor_fy, anchor_q, pos)
        stepped_label   = fmt_label(step_fy, step_q)

        try:
            edgar_label = f"FY{int(row['fiscal_year'])}-{row['fiscal_quarter']}"
        except Exception:
            edgar_label = None

        val = row.get("revenue_yoy_pct")
        yoy = float(val) if pd.notna(val) else None

        points.append({
            "label":       stepped_label,          # SHOWN TO USER
            "end_date":    row["end_date"].strftime("%Y-%m-%d"),
            "yoy":         yoy,
            "edgar_label": edgar_label,            # for audit only
            "matches":     edgar_label == stepped_label,
        })

        if edgar_label and edgar_label != stepped_label:
            mismatches.append({
                "position": pos,
                "end_date": row["end_date"].strftime("%Y-%m-%d"),
                "expected": stepped_label,
                "edgar":    edgar_label,
            })

    return {
        "ticker":          ticker,
        "latest_label":    points[0]["label"],
        "latest_end_date": points[0]["end_date"],
        "points":          points,
        "mismatches":      mismatches,
    }
```

## Common failure modes to avoid

1. **Calendar-quarter bucketing across tickers.** Looks tempting ("align all quarters by the calendar date of the end_date"). Breaks because companies with Jan fiscal year-ends bucket their Q4 into the same calendar-Q1 column as companies reporting their Q1 — they tell completely different stories and get mashed together. Always use relative step-back positioning.

2. **Trusting `fiscal_year` on comparative columns.** A 10-K filed in 2025 for FY2024 includes a comparative column for FY2023. Edgartools often tags BOTH columns with fiscal_year=2024 (the filing year). Never match prior-year rows by `fiscal_year - 1`; always step back from the latest row by fiscal-quarter count.

3. **Off-by-one fiscal labels from edgartools' heuristic.** Observed for DELL, AAPL, LITE, AVGO. Edgartools' internal heuristic sometimes labels Q3 periods as "Q4" when the filer has an unusual fiscal calendar. Anchoring + stepping bypasses this.

4. **Using `fiscal_quarter` for sort order.** `"Q1" < "Q2" < "Q3" < "Q4"` alphabetically — but the semantic order within a fiscal year is the same as alphabetical, only because of coincidence. Always sort by `end_date`.

5. **Ignoring mismatches in the UI.** The whole point of this method is that data disagrees with itself sometimes. If the UI hides mismatches, users silently get wrong labels. Always surface them, even as a small footnote.

6. **Rebuilding topline to "fix" labels.** Resist the urge. The calculator parquet is the consumer's source of truth; labels are fixed on the read path per this skill, not the write path. Rebuilding adds cost and often creates new bugs. The only acceptable reason to rebuild topline is if raw VALUES (revenue, etc.) are wrong, not labels.

## Success criteria

A component built with this skill should:

- Display the latest quarter's label **exactly** as edgartools reports it (no transformation).
- Derive all prior labels deterministically via `step_back()`.
- Show a mismatch note whenever the display label disagrees with edgartools' historical label for that row.
- Never produce duplicate fiscal labels within a single ticker's series (e.g. two "FY2025-Q4" rows).
- Never require a topline rebuild to fix label issues — fixes live in the read path.
- Align columns by relative position across tickers, with per-ticker absolute labels shown inside cells.

## Reference implementation

The sector heatmap at `backend/app/api/routers/v1/data.py :: sector_heatmap()` follows this skill end-to-end. Review it when implementing any new multi-ticker quarterly view as a template for the stepping, mismatch tracking, and response shape.
