---
name: data-quality-invariants
description: Design a scalable data quality framework for a domain data pipeline — sign consistency, range checks, cross-metric identities, temporal-stability cliff detection — plus a pattern for handling retroactive adjustments (stock splits, restatements) that would otherwise fire false positives. Use when the user wants to harden a data pipeline against silently-wrong values, trace a bug back to its invariant, or generalize one-off checks into a reusable framework. Covers rule declaration, runner design, suppression of legitimate anomalies, and when to fix the source vs. add an exception.
version: 1.0
last_validated_at: 2026-04-28
conditions: []
prerequisites: []
tags: [data-quality, framework, design, invariants]
---

# Data Quality Invariants — Build & Maintain Skill

## What this Skill does

Turns a bag of ad-hoc `if` checks scattered across validation functions into a **declarative rules framework** where every invariant is one appended entry. Covers:

1. **Designing invariants** — what classes of check actually catch bugs (sign, range, cross-metric, temporal stability).
2. **Building the framework** — `Rule` dataclass, runner, structured report, exceptions file.
3. **Handling retroactive data** — stock splits and other corporate actions that make naive cliff detectors fire false positives.
4. **When to add a rule vs. fix the source** — don't paper over extraction bugs with warnings.
5. **Promoting client-side computations to a persistent layer** — the "two consumers" rule.

The output is a small Python package (`data_quality/`) plus a retroactive-adjustment module (`splits.py`-style), wired into the data build pipeline. Principles generalize to any domain data layer — financial filings, IoT telemetry, clickstream events.

## When to use this Skill

Trigger when any of:

- User reports that a table "looks wrong" and the wrong value is silently shipping (no error raised).
- User asks to add a sanity check ("EPS should never be negative when net income is positive").
- User wants to harden a data pipeline before opening it to more consumers.
- You notice a data bug and want to prevent the next one of the same shape.
- User is introducing a new data layer and asks "how do I make sure it's trustworthy?"

Do NOT use this Skill for:

- Schema validation (that's Pydantic / JSON schema — a different problem).
- Row-count / freshness monitoring (Great Expectations, dbt tests).
- Auth / permissions validation.

## Core principles

1. **Rules are declarative, not imperative.** A rule is a `Rule(name, severity, check, message)` tuple — not a procedural validation function. Adding a check = appending one item to a list.
2. **Rules are pure functions.** `check: DataFrame → DataFrame of violations`. No side effects, no mutation, no I/O.
3. **Severity is binary: `warn` or `fail`.** `fail` can block the build; `warn` surfaces in the report. Don't invent a third level — "info" violations get ignored.
4. **Never crash the build on a rule bug.** The runner catches exceptions from individual rules, records them as violations, and continues. One broken rule should not take out the whole quality report.
5. **Suppressions are data, not code.** Legit anomalies (pandemic quarters, spinoffs) live in an `exceptions.py` mapping, not as `if ticker == 'TSLA'` branches inside rules.
6. **Fix the source before adding a rule.** A rule that only exists to catch a known extraction bug is a Band-Aid. Rules should encode truths about the domain, not compensate for upstream quality.
7. **Retroactive adjustments (splits, restatements) are handled separately.** Never fold split detection into "is this number sane" — it's a different concern with a different data source.

## Step-by-step instructions

### Step 1 — Inventory the invariants

Before writing code, list the invariants in plain English. Group them by type:

**Sign consistency** — "X must have the same sign as Y":
- EPS must match net income
- Dividend payments must be ≤ 0 in cash flow (it's an outflow)
- Depreciation expense is always positive on income statement

**Range** — "X must fall within [lo, hi]":
- Gross margin % ∈ [-50, 100]
- Revenue ≥ 0
- Shares outstanding > 0
- Tax rate ∈ [-100, 60]

**Cross-metric identity** — "X ≈ f(Y, Z)":
- Gross profit ≈ revenue − cost of revenue (within 2%)
- Operating income ≈ gross profit − operating expenses
- Free cash flow ≈ operating cash flow + capex

**Temporal stability** — "adjacent periods shouldn't discontinuous without explanation":
- Revenue shouldn't jump >5x between quarters
- Share count shouldn't jump >5x (unless there's a split)
- Margin shouldn't shift by >30pp unexpectedly

**Referential** — "every row must link to a valid parent":
- Every fragment must reference a known source document
- Every metric must have a non-null ticker

The goal of the inventory is to make the implicit explicit. Write them all down first, decide severity second.

### Step 2 — Create the package layout

```
<service>/data_quality/
├── __init__.py     # exports run_rules, QualityReport, QualityViolation
├── rules.py        # Rule dataclass + RULES list (the inventory from Step 1)
├── runner.py       # run_rules() + QualityReport dataclass
└── exceptions.py   # KNOWN_EXCEPTIONS map for legit anomalies
```

Keep the package flat. Don't introduce subdirectories per rule category — flatness beats taxonomy.

### Step 3 — Rule dataclass

```python
from dataclasses import dataclass
from typing import Callable, Literal
import pandas as pd

Severity = Literal["warn", "fail"]

@dataclass
class Rule:
    name: str                             # snake_case, unique across RULES
    severity: Severity
    description: str                       # one-line English summary
    applies_to: list[str]                  # columns the rule needs
    check: Callable[[pd.DataFrame], pd.DataFrame]   # returns violating rows
    message: Callable[[pd.Series], str]    # per-violation human string
```

**Must include in the return:** an `end_date` column (or equivalent period identifier) on the violating-rows DataFrame so the runner can cite which period failed.

### Step 4 — Write rules (one invariant per Rule)

For simple predicates, use lambdas:

```python
Rule(
    name="shares_basic_positive",
    severity="fail",
    description="shares_basic must be > 0",
    applies_to=["shares_basic"],
    check=lambda df: df[df["shares_basic"].notna() & (df["shares_basic"] <= 0)],
    message=lambda r: f"{r['end_date'].strftime('%Y-%m-%d')}: shares_basic={r['shares_basic']}",
),
```

For cross-row logic (temporal stability, cross-metric identities), use a named function:

```python
def _detect_revenue_cliff(df: pd.DataFrame, ratio_threshold: float = 5.0) -> pd.DataFrame:
    if "revenue" not in df.columns:
        return df.iloc[0:0]
    sorted_df = df.sort_values("end_date").copy()
    prev = sorted_df["revenue"].shift(1)
    ratio = np.where(
        (prev > 0) & (sorted_df["revenue"] > 0),
        np.maximum(sorted_df["revenue"] / prev, prev / sorted_df["revenue"]),
        np.nan,
    )
    sorted_df["_ratio"] = ratio
    return sorted_df[sorted_df["_ratio"] >= ratio_threshold].drop(columns=["_ratio"])

Rule(
    name="revenue_no_cliff",
    severity="warn",
    description="revenue shouldn't jump >5x between quarters",
    applies_to=["revenue"],
    check=_detect_revenue_cliff,
    message=lambda r: f"{r['end_date'].strftime('%Y-%m-%d')}: revenue cliff vs prior quarter",
),
```

Keep rules small. One invariant per Rule. A mega-rule that tests three things is harder to reason about, harder to suppress, and harder to fix when it trips.

### Step 5 — The runner

```python
@dataclass
class QualityViolation:
    rule: str
    severity: str
    ticker: str
    end_date: str         # ISO YYYY-MM-DD
    message: str
    suppressed: bool = False

@dataclass
class QualityReport:
    ticker: str
    rows_checked: int
    rules_evaluated: int
    rules_skipped: list[str]
    violations: list[QualityViolation]

    @property
    def passed(self) -> bool:
        return not any(
            v.severity == "fail" and not v.suppressed for v in self.violations
        )

def run_rules(df: pd.DataFrame, ticker: str) -> QualityReport:
    report = QualityReport(ticker=ticker, rows_checked=len(df), rules_evaluated=0, rules_skipped=[], violations=[])
    if df.empty:
        return report
    for rule in RULES:
        missing = [c for c in rule.applies_to if c not in df.columns]
        if missing:
            report.rules_skipped.append(f"{rule.name} (missing: {', '.join(missing)})")
            continue
        report.rules_evaluated += 1
        try:
            violating_rows = rule.check(df)
        except Exception as exc:
            # A buggy rule must NEVER crash the build.
            report.violations.append(QualityViolation(
                rule=rule.name, severity="warn", ticker=ticker,
                end_date="", message=f"rule crashed: {exc}",
            ))
            continue
        if violating_rows is None or violating_rows.empty:
            continue
        for _, row in violating_rows.iterrows():
            end_iso = _row_end_date(row)
            report.violations.append(QualityViolation(
                rule=rule.name,
                severity=rule.severity,
                ticker=ticker,
                end_date=end_iso,
                message=_safe_format(rule, row),
                suppressed=is_suppressed(ticker, rule.name, end_iso),
            ))
    return report
```

Key design points:

- **Missing columns = skip, not fail.** A rule that needs `gross_margin_pct` shouldn't flag a ticker whose income statement doesn't have gross profit yet.
- **Rule exceptions are violations, not crashes.** Log them as synthetic warn-severity entries with `"rule crashed: {exc}"`.
- **Suppressions stay in the report.** Mark `suppressed=True` instead of omitting. Auditors need to see what was hidden and why.

### Step 6 — Suppressions file

```python
# exceptions.py
KNOWN_EXCEPTIONS: dict[str, dict[str, set[str]]] = {
    # ticker → rule_name → {end_date_iso_strings}
    "TSLA": {
        "revenue_no_cliff": {"2020-06-30"},   # COVID-19 production halt
    },
}

def is_suppressed(ticker: str, rule_name: str, end_date_iso: str) -> bool:
    return end_date_iso in KNOWN_EXCEPTIONS.get(ticker, {}).get(rule_name, set())
```

**What belongs here:** documented one-off anomalies that a rule correctly identifies but aren't bugs.

**What does NOT belong here:** anything programmatically knowable. Stock splits are an anti-example — you could list the split dates statically, but that doesn't scale. Use a retroactive-adjustment module (Step 7) instead.

### Step 7 — Retroactive adjustments (the splits pattern)

If your domain has events that retroactively change reported values — stock splits, accounting restatements, currency rebasing — a cliff-detector rule will fire false positives on every event. The wrong fix is to pile up exceptions. The right fix is a **retroactive adjustment module**:

```
<service>/splits.py    # or restatements.py, rebases.py, etc.
```

Shape:

```python
@dataclass
class Split:
    date: pd.Timestamp
    ratio: float

class SplitsCache:
    """Per-ticker splits, backed by JSON, refreshed from yfinance."""
    def get_splits(self, ticker: str, force_refresh: bool = False) -> list[Split]: ...

def apply_split_adjustments(df: pd.DataFrame, ticker: str, cache: SplitsCache | None = None) -> pd.DataFrame:
    """
    For each split with ratio R on date D:
      - rows with end_date < D:  shares *= R,  eps /= R
      - rows with end_date >= D: unchanged
    Multiple splits compound in chronological order.
    """
```

Wire it into the build pipeline **before** validation runs, so the cliff rules see a continuous series:

```python
# topline_builder.py (or equivalent)
df = self._ytd_to_standalone(df, statement_type)
df = apply_split_adjustments(df, ticker)   # ← adjust BEFORE EPS recompute
df = self._recompute_eps(df)
```

**Critical ordering rules:**
1. Adjust **before** any downstream computation that depends on shares (EPS recalc, per-share metrics).
2. Adjust **before** quality rules run — so cliffs that legitimately exist (bugs) are still caught.
3. If you have a per-share recomputation step, run it **after** adjustment so it uses post-adjustment share counts.

**Graceful degradation:** if the splits source (yfinance) is unreachable, return empty list and log a warning. Never fail the build for a missing split. The data will have split artifacts, but the pipeline still runs and the cliff rules will flag them so the user knows to retry.

**Cache:** persist splits to a JSON file. Splits are immutable history — once fetched, they don't change (until the next split). Refresh every 30 days. `backend/data/filing_data/splits/splits.json` with schema:

```json
{
  "NVDA": {
    "fetched_at": "2026-04-12T16:46:39+00:00",
    "splits": [
      {"date": "2021-07-20", "ratio": 4.0},
      {"date": "2024-06-10", "ratio": 10.0}
    ]
  }
}
```

### Step 8 — Wire into the existing validation

Replace the ad-hoc `if` blocks with a single `run_rules` call:

```python
def _validate(self, df: pd.DataFrame, ticker: str) -> dict:
    from .data_quality import run_rules
    quality_report = run_rules(df, ticker)
    return {
        "warnings":       quality_report.warning_messages(),
        "spot_checks":    [...],  # keep if you have golden values
        "quality_report": quality_report.to_dict(),   # full structured version
    }
```

The legacy `warnings` list stays for backward compat; the new `quality_report` dict is the structured version that goes into API responses and frontend badges.

### Step 9 — Expose via API

```python
@router.get("/quality-report")
async def quality_report(ticker: str | None = None):
    status = _builder.status()
    if not status.get("built"):
        raise HTTPException(404, "layer not built")
    all_reports = {
        t: entry["validation"]["quality_report"]
        for t, entry in status["tickers"].items()
        if entry.get("validation", {}).get("quality_report")
    }
    if ticker:
        return {ticker: all_reports[ticker.upper()]}
    return all_reports
```

The frontend can now show a ✓/⚠ badge per ticker driven by this endpoint.

### Step 10 — Decide when to block the build

By default, `run_rules` **reports** but doesn't block. To enforce fail-severity rules as build blockers, add one line to the builder:

```python
validation = self._validate(wide_df, ticker)
if not validation["quality_report"]["passed"]:
    raise RuntimeError(f"{ticker}: {validation['quality_report']['fail_count']} fail-severity violations")
```

**Don't turn this on immediately.** Run a few real builds first to tune severity levels. You'll discover edge cases (legitimate negative tax rates for companies with big refunds, gross margin >100% for certain reinsurance contracts) that should be downgraded to `warn` or suppressed.

## The "two consumers" rule — when to promote derived metrics

Separate but related: if you're computing derived values **client-side** (React, Jupyter, a script), ask: **does more than one consumer want this?**

- One consumer (just the UI) — client-side is fine.
- Two or more consumers (UI + LLM agent, UI + export pipeline, UI + peer view) — **promote to the persistent layer.**

Why:
1. **Consistency** — two consumers can't drift if there's only one definition.
2. **Discoverability** — an agent tool or a future dashboard can query it via the normal metric API.
3. **Safety** — backend computation has access to data-gap checks (date tolerance, missing-quarter detection) that naive client-side `rows[i-4]` doesn't.
4. **Parquet columns are cheap** — ~3 floats × 40 quarters × 15 tickers = nothing.

The pattern to add a new derived metric to the persistent layer:

1. Add to `concept_map.py` — extend `COMPUTED_METRICS` or a new `DELTA_METRICS` dict, include in `TEMPORAL_METRICS` / `ALL_METRICS`.
2. Extend the calculator — one new helper (e.g. `_add_delta` next to `_add_growth`) + one call from `_compute_all`.
3. Rebuild — `CalculatedLayerBuilder().build()`.
4. Delete the client-side loop. Request the new metric by name.

## Known edge cases

| Problem | Root cause | Fix |
|---|---|---|
| Cliff rule fires on every stock split | Split detection mixed into sanity checks | Separate module (`splits.py`), retroactively adjust before validation |
| Every Q4 row has wrong share count | Q4 derived by subtraction; per-share fields skipped; fallback plugged in max | Apply split adjustment first so fallback has a consistent denomination, then recompute EPS |
| Rule crashes the build on a bad row | Unhandled exception in `check` | Runner catches per-rule exceptions, logs as synthetic warn violation |
| Same anomaly tripped on every rebuild | Exception hardcoded in rule body | Move to `exceptions.py` suppression map |
| Rule passes but data is still wrong | Invariant doesn't exist yet | Add a new Rule for this class of bug — the NEXT occurrence should be caught, not this one |
| Two consumers compute the delta differently | Metric lives in two UIs with two formulas | Promote to calculated layer, both consumers call one endpoint |
| Validation fails on first run, empty parquet | No rows → can't evaluate rules | Early-return empty QualityReport from runner on empty df |
| Legit negative numbers flagged | Rule too strict (e.g. tax rate refunds) | Widen range or downgrade severity, don't suppress per-row |

## Expected output

### Files created

```
backend/app/services/<service>/data_quality/
├── __init__.py          # exports
├── rules.py             # Rule dataclass + RULES list (10-15 rules typical)
├── runner.py            # run_rules() + QualityReport/Violation dataclasses
└── exceptions.py        # KNOWN_EXCEPTIONS map, is_suppressed() helper

backend/app/services/<service>/splits.py   # (or equivalent retroactive module)
backend/data/filing_data/splits/splits.json   # (created on first run)
```

### Files modified

- `<validator>.py` — one-line replacement of ad-hoc blocks with `run_rules(df, ticker)`
- `<api_router>.py` — new `/quality-report` endpoint
- `requirements.txt` — add `yfinance>=0.2.40` (or equivalent data source for your domain)

### Verification

After building a previously-problematic ticker:
- `quality_report.passed == True`
- `fail_count == 0`
- `warn_count` close to 0 (some legitimate `warn`-severity items OK)
- No unsuppressed violations listed

If NVDA goes from 24 warnings to 0, the split adjustment is working. If a new ticker shows 12 cliffs, the yfinance fetch failed silently — check `splits/splits.json`.

## Iteration discipline

- **Add rules in response to real bugs, not speculation.** A rule that has never caught anything is noise. The NVDA EPS-sign-flip bug → add `eps_sign_matches_net_income`. The share-count negative → add `shares_basic_positive`. Don't preemptively write 40 rules.
- **Tune severity when the first false positive appears.** Not before. `warn` is the safe default; promote to `fail` only after you've seen the rule hold up across several tickers without firing on legit data.
- **Never modify a rule to accommodate one bad ticker.** Add a suppression or fix the source.
- **Every rule that gets added, document the root-cause incident in the description.** "Added after NVDA Q2 FY25 YTD-subtraction bug produced EPS=−4.76 with NI=+16.6B." Future maintainers need to know why the rule exists.

## Related files in this repo (canonical implementation)

- `backend/app/services/data_agent/data_quality/rules.py` — 12 rules covering the patterns above
- `backend/app/services/data_agent/data_quality/runner.py` — `run_rules`, `QualityReport`, `QualityViolation`
- `backend/app/services/data_agent/data_quality/exceptions.py` — suppressions map
- `backend/app/services/data_agent/splits.py` — yfinance-backed splits cache + retroactive adjustment
- `backend/app/services/data_agent/calculator.py` `_validate()` — integration point
- `backend/app/api/routers/v1/data.py` — `GET /data/quality-report` endpoint
- `backend/data/filing_data/splits/splits.json` — live cache (gitignore candidate)
