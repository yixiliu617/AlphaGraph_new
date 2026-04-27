"""
Reusable check primitives.

Each function takes a DataFrame plus parameters, returns a `CheckResult`.
Wrapped in a `Check` (with a name + severity + description) at registration
time in `registry.py`.

Inventory:
  - period_continuity      : no missing quarters in a covered range
  - period_label_format    : every period_label matches expected pattern
  - identity               : A + B = C (or A = B) within tolerance
  - range                  : value within [min, max]
  - sign_consistency       : metric values consistently positive or negative
  - null_rate              : null rate below threshold
  - source_period_match    : period embedded in source field == period_label
                             (catches mislabeled source files like the
                             MediaTek 3Q23 transcript that contains 3Q24 content)
  - share_sum              : segment-share rows for a given (metric, period)
                             sum to ~100%
  - row_count_min          : table has at least N rows (catches empty parquets
                             from a failed scrape)
  - duplicate_key          : no duplicates on the declared dedup-key columns
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

import pandas as pd

from .framework import CheckResult, Severity


_PERIOD_RE = re.compile(r"^(\d)Q(\d{2})$")
_FY_RE = re.compile(r"^FY(\d{2})$")


def _period_to_year_q(label: str) -> Optional[tuple[int, int]]:
    """'4Q25' -> (2025, 4); 'FY25' -> (2025, 5). Returns None on bad shape."""
    m = _PERIOD_RE.match(label)
    if m:
        yy = int(m.group(2))
        return (2000 + yy if yy < 50 else 1900 + yy, int(m.group(1)))
    fy = _FY_RE.match(label)
    if fy:
        yy = int(fy.group(1))
        return (2000 + yy if yy < 50 else 1900 + yy, 5)
    return None


def _enumerate_quarters(start: tuple[int, int], end: tuple[int, int]) -> List[str]:
    """All quarterly labels from start (year, q) to end (year, q) inclusive,
    in calendar order. FY entries (q=5) are not enumerated as part of the
    quarterly continuity scan; they're checked separately."""
    out = []
    y, q = start
    end_y, end_q = end
    while (y, q) <= (end_y, end_q):
        out.append(f"{q}Q{y % 100:02d}")
        q += 1
        if q > 4:
            q = 1
            y += 1
    return out


# ---------------------------------------------------------------------------
# 1. Period continuity
# ---------------------------------------------------------------------------

def period_continuity(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    period_col: str,
    group_col: Optional[str] = None,
    expected_freq: str = "quarterly",
) -> CheckResult:
    if period_col not in df.columns:
        return CheckResult(
            check_name="period_continuity",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message=f"period column '{period_col}' not present in {dataset_name} — skipping "
                    f"(available: {list(df.columns)[:8]}{'…' if len(df.columns) > 8 else ''})",
        )
    """Verify every quarter between the earliest and latest observed period
    is present (no holes). When `group_col` is given, runs per-group (e.g.
    per-metric or per-dimension) so different metrics with different
    coverage don't mask each other's gaps.

    Args:
        period_col: column holding period labels like '4Q25' or 'FY25'.
        group_col: optional column to partition by; missing-period detection
                   runs separately within each group.
        expected_freq: 'quarterly' (Q1..Q4 every year) or 'annual' (FY only).
    """
    if df.empty:
        return CheckResult(
            check_name="period_continuity",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message=f"dataset is empty (no rows on {period_col})",
        )

    all_missing: List[dict] = []
    groups_examined = 0

    grouped = [(None, df)] if group_col is None else df.groupby(group_col, dropna=False)
    for grp_val, sub in grouped:
        groups_examined += 1
        labels = sub[period_col].dropna().unique().tolist()
        # Filter to quarterly (skip FY entries — handled separately if needed)
        if expected_freq == "quarterly":
            qlabels = [l for l in labels if _PERIOD_RE.match(str(l))]
        else:
            qlabels = [l for l in labels if _FY_RE.match(str(l))]
        if not qlabels:
            continue
        keys = sorted({_period_to_year_q(l) for l in qlabels if _period_to_year_q(l)})
        if len(keys) < 2:
            continue  # nothing to check with one observation
        if expected_freq == "quarterly":
            expected = _enumerate_quarters(keys[0], keys[-1])
        else:
            expected = [f"FY{y % 100:02d}" for (y, _q) in keys[0:-1]]
            expected.append(f"FY{keys[-1][0] % 100:02d}")
        present = set(qlabels)
        missing = [p for p in expected if p not in present]
        if missing:
            all_missing.append({
                "group": str(grp_val) if grp_val is not None else "(all)",
                "earliest": expected[0],
                "latest": expected[-1],
                "expected_count": len(expected),
                "present_count": len(present & set(expected)),
                "missing": missing,
            })

    if not all_missing:
        return CheckResult(
            check_name="period_continuity",
            dataset=dataset_name, status="pass", severity=Severity.WARN,
            message=f"no gaps across {groups_examined} group(s)",
        )

    total_missing = sum(len(g["missing"]) for g in all_missing)
    sample = all_missing[:6]
    return CheckResult(
        check_name="period_continuity",
        dataset=dataset_name, status="fail", severity=Severity.WARN,
        message=f"{total_missing} missing period(s) across {len(all_missing)} group(s); "
                f"first gap: {all_missing[0]['group']} missing {all_missing[0]['missing'][:3]}",
        affected_count=total_missing,
        sample=sample,
        details={"groups_with_gaps": len(all_missing), "groups_examined": groups_examined},
    )


# ---------------------------------------------------------------------------
# 2. Period label format
# ---------------------------------------------------------------------------

def period_label_format(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    period_col: str,
    allow_fy: bool = True,
) -> CheckResult:
    """Every value in `period_col` must match '{N}Q{YY}' (or 'FY{YY}' if allowed)."""
    if df.empty or period_col not in df.columns:
        return CheckResult(
            check_name="period_label_format",
            dataset=dataset_name, status="pass", severity=Severity.ERROR,
            message="empty or missing column",
        )
    labels = df[period_col].dropna().astype(str)
    bad = labels[~labels.apply(lambda s: bool(_PERIOD_RE.match(s) or (allow_fy and _FY_RE.match(s))))]
    if bad.empty:
        return CheckResult(
            check_name="period_label_format",
            dataset=dataset_name, status="pass", severity=Severity.ERROR,
            message=f"all {len(labels)} labels valid",
        )
    samples = bad.unique()[:8].tolist()
    return CheckResult(
        check_name="period_label_format",
        dataset=dataset_name, status="fail", severity=Severity.ERROR,
        message=f"{bad.nunique()} unique malformed label(s) (e.g. {samples[:3]})",
        affected_count=int(bad.nunique()),
        sample=[{"period_label": s} for s in samples],
    )


# ---------------------------------------------------------------------------
# 3. Identity (A + B == C, etc.)
# ---------------------------------------------------------------------------

def identity_check(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    metric_col: str,
    value_col: str,
    period_col: str,
    formula: dict[str, str],
    tolerance_pct: float = 0.5,
    dimension_col: Optional[str] = None,
) -> CheckResult:
    """Verify `lhs_metric` ≈ sum/expression-of `rhs_metrics` per period.

    formula example:
        {"lhs": "net_revenue", "rhs": ["gross_profit", "cost_of_revenue"], "op": "sum_abs"}
    """
    sub = df.copy()
    if dimension_col and dimension_col in sub.columns:
        sub = sub[sub[dimension_col].astype(str) == ""]
    pivot = (sub.groupby([period_col, metric_col])[value_col].mean()
                .unstack(metric_col))
    lhs = formula["lhs"]
    rhs = formula["rhs"]
    op = formula.get("op", "sum_abs")
    if lhs not in pivot.columns:
        return CheckResult(
            check_name="identity_check",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message=f"LHS metric '{lhs}' not present — skipping",
        )
    missing_rhs = [m for m in rhs if m not in pivot.columns]
    if missing_rhs:
        return CheckResult(
            check_name="identity_check",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message=f"RHS metric(s) {missing_rhs} not present — skipping",
        )

    if op == "sum_abs":
        rhs_total = sum(pivot[m].abs() for m in rhs)
    elif op == "sum":
        rhs_total = sum(pivot[m] for m in rhs)
    else:
        return CheckResult(
            check_name="identity_check",
            dataset=dataset_name, status="error", severity=Severity.ERROR,
            message=f"unknown op {op!r}",
        )

    diff = pivot[lhs] - rhs_total
    rel_err = (diff.abs() / pivot[lhs].abs()).fillna(0) * 100
    bad = rel_err[rel_err > tolerance_pct]
    if bad.empty:
        return CheckResult(
            check_name="identity_check",
            dataset=dataset_name, status="pass", severity=Severity.WARN,
            message=f"identity {lhs} ≈ {op}({rhs}) holds within {tolerance_pct}% across {len(pivot)} periods",
        )
    sample = [
        {"period": str(p), f"{lhs}": float(pivot.loc[p, lhs]),
         "rhs_total": float(rhs_total.loc[p]),
         "rel_err_pct": round(float(rel_err.loc[p]), 3)}
        for p in bad.index[:6]
    ]
    return CheckResult(
        check_name="identity_check",
        dataset=dataset_name, status="fail", severity=Severity.WARN,
        message=f"{len(bad)} period(s) breach {tolerance_pct}% identity tolerance",
        affected_count=len(bad), sample=sample,
        details={"lhs": lhs, "rhs": rhs, "op": op, "tolerance_pct": tolerance_pct},
    )


# ---------------------------------------------------------------------------
# 4. Range
# ---------------------------------------------------------------------------

def range_check(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    metric_col: str,
    value_col: str,
    metric: str,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> CheckResult:
    """Filter to rows where metric==<metric>, verify value is within [min, max]."""
    sub = df[df[metric_col] == metric]
    if sub.empty:
        return CheckResult(
            check_name=f"range[{metric}]",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message=f"metric {metric} not present — skipping",
        )
    bad = sub[
        (min_val is not None) & (sub[value_col] < (min_val if min_val is not None else float('-inf'))) |
        (max_val is not None) & (sub[value_col] > (max_val if max_val is not None else float('inf')))
    ]
    if bad.empty:
        return CheckResult(
            check_name=f"range[{metric}]",
            dataset=dataset_name, status="pass", severity=Severity.WARN,
            message=f"all {len(sub)} {metric} values within [{min_val}, {max_val}]",
        )
    return CheckResult(
        check_name=f"range[{metric}]",
        dataset=dataset_name, status="fail", severity=Severity.WARN,
        message=f"{len(bad)} {metric} values out of range",
        affected_count=len(bad),
        sample=bad.head(5).to_dict(orient="records"),
        details={"metric": metric, "min_val": min_val, "max_val": max_val},
    )


# ---------------------------------------------------------------------------
# 5. Sign consistency
# ---------------------------------------------------------------------------

def sign_consistency(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    metric_col: str,
    value_col: str,
    metric: str,
    expected: str,        # "positive" | "negative" | "non_negative"
) -> CheckResult:
    sub = df[df[metric_col] == metric]
    if sub.empty:
        return CheckResult(
            check_name=f"sign[{metric}]",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message=f"metric {metric} not present — skipping",
        )
    if expected == "positive":
        bad = sub[sub[value_col] <= 0]
    elif expected == "non_negative":
        bad = sub[sub[value_col] < 0]
    elif expected == "negative":
        bad = sub[sub[value_col] >= 0]
    else:
        return CheckResult(
            check_name=f"sign[{metric}]",
            dataset=dataset_name, status="error", severity=Severity.ERROR,
            message=f"unknown expected={expected!r}",
        )
    if bad.empty:
        return CheckResult(
            check_name=f"sign[{metric}]",
            dataset=dataset_name, status="pass", severity=Severity.WARN,
            message=f"all {len(sub)} {metric} values are {expected}",
        )
    return CheckResult(
        check_name=f"sign[{metric}]",
        dataset=dataset_name, status="fail", severity=Severity.WARN,
        message=f"{len(bad)} {metric} values violate sign={expected}",
        affected_count=len(bad),
        sample=bad.head(5).to_dict(orient="records"),
    )


# ---------------------------------------------------------------------------
# 6. Source-period match
#    Catches the case where a downloaded PDF is mislabeled at source (e.g.
#    MediaTek's CDN had a 3Q24 file at the 3Q23 URL). For datasets that
#    embed the period in the `source` identifier, verify it matches the
#    period_label column.
# ---------------------------------------------------------------------------

def source_period_match(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    source_col: str,
    period_col: str,
    source_pattern: str,    # regex with one capturing group for the period
) -> CheckResult:
    if source_col not in df.columns or period_col not in df.columns:
        return CheckResult(
            check_name="source_period_match",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message=f"missing column ({source_col} or {period_col})",
        )
    rgx = re.compile(source_pattern)
    bad = []
    for _, row in df.iterrows():
        m = rgx.search(str(row[source_col]))
        if not m:
            continue
        embedded = m.group(1)
        if embedded != str(row[period_col]):
            bad.append({"source": str(row[source_col]),
                        "period_label_in_row": str(row[period_col]),
                        "period_in_source": embedded})
    if not bad:
        return CheckResult(
            check_name="source_period_match",
            dataset=dataset_name, status="pass", severity=Severity.ERROR,
            message=f"all source identifiers agree with their period_label ({len(df)} rows)",
        )
    return CheckResult(
        check_name="source_period_match",
        dataset=dataset_name, status="fail", severity=Severity.ERROR,
        message=f"{len(bad)} row(s) have source/period mismatch — likely mislabeled source file",
        affected_count=len(bad),
        sample=bad[:6],
    )


# ---------------------------------------------------------------------------
# 7. Share-sum (segment percentages must sum to ~100% per period)
# ---------------------------------------------------------------------------

def share_sum(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    metric_col: str,
    value_col: str,
    period_col: str,
    metric_prefix: str,
    tolerance: float = 1.5,        # ±1.5 percentage points
) -> CheckResult:
    sub = df[df[metric_col].astype(str).str.startswith(metric_prefix)]
    if sub.empty:
        return CheckResult(
            check_name=f"share_sum[{metric_prefix}]",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message=f"no metrics matching prefix {metric_prefix!r} — skipping",
        )
    # The silver layer may have the same (metric, period, dimension) tuple
    # contributed by multiple source reports (curQ + prevQ + YoY rolls).
    # De-dup by averaging across sources first, THEN sum the dimensions
    # per (metric, period). Otherwise N reports × M dimensions = N×100%.
    if "dimension" in sub.columns:
        per_dim = sub.groupby([metric_col, period_col, "dimension"], as_index=False)[value_col].mean()
    else:
        per_dim = sub
    sums = per_dim.groupby([metric_col, period_col], as_index=False)[value_col].sum()
    bad = sums[(sums[value_col] - 100).abs() > tolerance]
    if bad.empty:
        return CheckResult(
            check_name=f"share_sum[{metric_prefix}]",
            dataset=dataset_name, status="pass", severity=Severity.WARN,
            message=f"all {len(sums)} (metric, period) groups sum to 100% ± {tolerance}pp",
        )
    return CheckResult(
        check_name=f"share_sum[{metric_prefix}]",
        dataset=dataset_name, status="fail", severity=Severity.WARN,
        message=f"{len(bad)} (metric, period) groups breach 100% ± {tolerance}pp",
        affected_count=len(bad),
        sample=bad.head(6).to_dict(orient="records"),
    )


# ---------------------------------------------------------------------------
# 8. Row count minimum
# ---------------------------------------------------------------------------

def row_count_min(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    minimum: int,
) -> CheckResult:
    n = len(df)
    if n >= minimum:
        return CheckResult(
            check_name="row_count_min",
            dataset=dataset_name, status="pass", severity=Severity.ERROR,
            message=f"{n} rows ≥ minimum {minimum}",
        )
    return CheckResult(
        check_name="row_count_min",
        dataset=dataset_name, status="fail", severity=Severity.ERROR,
        message=f"only {n} rows; expected at least {minimum} (silver may be empty / corrupt)",
        affected_count=minimum - n,
    )


# ---------------------------------------------------------------------------
# 9. Duplicate key
# ---------------------------------------------------------------------------

def trading_day_continuity(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    date_col: str = "date",
    exchange: str = "NYSE",
    max_skipped_session_days: int = 5,
) -> CheckResult:
    """For prices datasets: every regular-session trading day between the
    earliest and latest date in `date_col` must be present.

    `exchange` is a `pandas_market_calendars` name (e.g. "NYSE", "XTAI" for
    Taiwan, "JPX" for Japan). Holidays + weekends are excluded automatically.

    `max_skipped_session_days` allows a small grace for an exchange that
    just opened (DELL pre-IPO, etc.) — we report missing days but only fail
    if the count exceeds the grace window. Useful at scale: a ticker that
    came public mid-window shouldn't fire a fail just because its earliest
    date isn't on the calendar's earliest session.
    """
    if date_col not in df.columns or df.empty:
        return CheckResult(
            check_name="trading_day_continuity",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message=f"missing column {date_col!r} or empty dataframe",
        )
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        return CheckResult(
            check_name="trading_day_continuity",
            dataset=dataset_name, status="warn", severity=Severity.WARN,
            message="pandas_market_calendars not installed",
        )
    try:
        cal = mcal.get_calendar(exchange)
    except Exception as e:
        return CheckResult(
            check_name="trading_day_continuity",
            dataset=dataset_name, status="error", severity=Severity.ERROR,
            message=f"unknown exchange {exchange!r}: {e}",
        )

    # Force ns precision on both sides; pandas_market_calendars returns
    # datetime64[us] while parquets often store datetime64[ms]. Without this
    # cast, Index.difference() reports every session as missing.
    dates = (
        pd.to_datetime(df[date_col])
        .dt.tz_localize(None)
        .dt.normalize()
        .unique()
    )
    dates = pd.DatetimeIndex(sorted(dates)).astype("datetime64[ns]")
    earliest = dates.min()
    latest = dates.max()
    sessions = cal.valid_days(start_date=earliest, end_date=latest)
    sessions_naive = (
        pd.DatetimeIndex(sessions).tz_localize(None).normalize().astype("datetime64[ns]")
    )

    missing = sessions_naive.difference(dates)
    if missing.empty:
        return CheckResult(
            check_name="trading_day_continuity",
            dataset=dataset_name, status="pass", severity=Severity.WARN,
            message=f"all {len(sessions_naive)} {exchange} sessions present "
                    f"between {earliest.date()} and {latest.date()}",
        )

    severity_status = "fail" if len(missing) > max_skipped_session_days else "warn"
    return CheckResult(
        check_name="trading_day_continuity",
        dataset=dataset_name, status=severity_status, severity=Severity.WARN,
        message=f"{len(missing)} {exchange} sessions missing between "
                f"{earliest.date()} and {latest.date()}",
        affected_count=len(missing),
        sample=[{"missing_date": str(d.date())} for d in missing[:10]],
        details={"exchange": exchange, "first_missing": str(missing[0].date()),
                 "last_missing": str(missing[-1].date())},
    )


def duplicate_key(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    key_cols: Iterable[str],
) -> CheckResult:
    cols = list(key_cols)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        return CheckResult(
            check_name="duplicate_key",
            dataset=dataset_name, status="warn", severity=Severity.ERROR,
            message=f"key columns missing: {missing}",
        )
    dups = df[df.duplicated(subset=cols, keep=False)]
    if dups.empty:
        return CheckResult(
            check_name="duplicate_key",
            dataset=dataset_name, status="pass", severity=Severity.ERROR,
            message=f"no duplicates on {cols}",
        )
    return CheckResult(
        check_name="duplicate_key",
        dataset=dataset_name, status="fail", severity=Severity.ERROR,
        message=f"{len(dups)} rows duplicate on key {cols}",
        affected_count=len(dups),
        sample=dups.head(5).to_dict(orient="records"),
        details={"key_cols": cols},
    )
