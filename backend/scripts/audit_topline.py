"""
Topline parquet audit — cross-statement validation across all US tickers.

Loads income_statement / balance_sheet / cash_flow parquets per ticker and
runs a battery of plausibility checks. The goal is to surface AMD-style
extraction bugs that the existing per-statement _validate misses (notably:
cash-flow concept-disambiguation failures that produce a wildly wrong
Annual which then cascades into a derived Q4 with the opposite sign).

Severity tiers:
  CRITICAL : almost certainly a parsing bug -- needs investigation
  WARN     : implausible but possible (e.g. one bad quarter, one-off charge)
  INFO     : potentially noteworthy (large YoY moves, missing optional fields)

Output:
  backend/data/filing_data/audit_topline_report.json   -- machine-readable
  backend/data/filing_data/audit_topline_report.md     -- human-readable

Run:
  python -m backend.scripts.audit_topline
  python -m backend.scripts.audit_topline --tickers AMD MSFT NVDA
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# ASCII-only output per CLAUDE.md print-statement rule (Windows cp950).

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TOPLINE_DIR  = _PROJECT_ROOT / "backend" / "data" / "filing_data" / "topline"
_REPORT_JSON  = _PROJECT_ROOT / "backend" / "data" / "filing_data" / "audit_topline_report.json"
_REPORT_MD    = _PROJECT_ROOT / "backend" / "data" / "filing_data" / "audit_topline_report.md"


# ---------------------------------------------------------------------------
# Violation model
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    severity: str           # CRITICAL | WARN | INFO
    ticker:   str
    rule:     str
    message:  str
    statement: str = ""     # income_statement | balance_sheet | cash_flow | cross
    period:    str = ""     # FYxxxx-Qy or FYxxxx
    metric:    str = ""
    expected:  str = ""
    actual:    str = ""

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v != ""}


@dataclass
class TickerAudit:
    ticker: str
    rows_income:   int = 0
    rows_balance:  int = 0
    rows_cashflow: int = 0
    violations: list[Violation] = field(default_factory=list)

    @property
    def crit_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "CRITICAL")

    @property
    def warn_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "WARN")

    @property
    def info_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "INFO")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load(ticker: str, statement_dir: str) -> pd.DataFrame | None:
    p = _TOPLINE_DIR / statement_dir / f"ticker={ticker}.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)


def _all_tickers() -> list[str]:
    p = _TOPLINE_DIR / "income_statement"
    if not p.exists():
        return []
    return sorted(f.stem.replace("ticker=", "") for f in p.glob("ticker=*.parquet"))


# ---------------------------------------------------------------------------
# Rule helpers
# ---------------------------------------------------------------------------

def _annual_vs_quarters(
    df: pd.DataFrame, metric: str, ticker: str, statement: str,
    *, threshold_pct: float, severity_at: float | None = None,
) -> list[Violation]:
    """Annual row's value should ~= sum(Q1..Q4) within `threshold_pct` percent.
    `severity_at`: when |pct_diff| exceeds this, escalate to CRITICAL.
    Otherwise WARN."""
    out: list[Violation] = []
    if metric not in df.columns:
        return out
    annual = df[df["fiscal_quarter"] == "Annual"]
    for _, ann in annual.iterrows():
        ann_v  = ann.get(metric)
        ann_ps = ann.get("period_start")
        ann_fy = ann.get("fiscal_year")
        if pd.isna(ann_v) or ann_v == 0:
            continue
        # Q1+Q2+Q3 share period_start with the Annual row.
        q123_mask = (df["period_start"] == ann_ps) & (df["fiscal_quarter"].isin(["Q1", "Q2", "Q3"]))
        q4_mask   = (df["fiscal_year"] == ann_fy) & (df["fiscal_quarter"] == "Q4")
        # Skip if no quarterly rows exist at all for this FY -- common at the
        # history edge where we have a 10-K but the matching 10-Qs are out of
        # the build window. Not a bug, just sparsity.
        n_q_rows = int(q123_mask.sum() + q4_mask.sum())
        if n_q_rows == 0:
            continue
        # Only count NON-NaN quarterly values toward the sum. If we have <4
        # quarterly rows for this metric, this isn't a true reconciliation;
        # downgrade to INFO so it doesn't drown the report.
        q_vals = pd.concat([df[q123_mask][metric], df[q4_mask][metric]]).dropna()
        if q_vals.empty:
            continue
        q_sum = q_vals.sum()
        diff_pct = abs(q_sum - ann_v) / abs(ann_v) * 100
        if diff_pct <= threshold_pct:
            continue
        partial_period = len(q_vals) < 4
        if partial_period:
            sev = "INFO"   # incomplete quarterly history; cannot reliably reconcile
        elif severity_at is not None and diff_pct >= severity_at:
            sev = "CRITICAL"
        else:
            sev = "WARN"
        out.append(Violation(
            severity=sev, ticker=ticker, rule="annual_vs_quarters",
            statement=statement, period=f"FY{int(ann_fy)}", metric=metric,
            expected=f"{q_sum:.0f} (sum of {len(q_vals)} non-null quarters)",
            actual=f"{ann_v:.0f} (annual)",
            message=(f"FY{int(ann_fy)} {metric}: Annual={ann_v:.0f} vs Q-sum={q_sum:.0f} "
                     f"({diff_pct:.1f}% discrepancy"
                     + (f"; only {len(q_vals)}/4 quarters present)" if partial_period else ")")),
        ))
    return out


def _nan_coverage(
    df: pd.DataFrame, metric: str, ticker: str, statement: str, *, max_nan_frac: float,
) -> list[Violation]:
    """Flag if a column is mostly NaN. Quarterly + Annual rows only."""
    if metric not in df.columns:
        return [Violation(
            severity="WARN", ticker=ticker, rule="missing_column",
            statement=statement, metric=metric,
            message=f"{metric} column not present in {statement}",
        )]
    rows = df[df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4", "Annual"])]
    if rows.empty:
        return []
    nan_frac = rows[metric].isna().mean()
    if nan_frac > max_nan_frac:
        sev = "WARN" if nan_frac < 0.95 else "CRITICAL"
        return [Violation(
            severity=sev, ticker=ticker, rule="nan_coverage",
            statement=statement, metric=metric,
            actual=f"{nan_frac*100:.0f}% NaN over {len(rows)} rows",
            message=f"{metric}: {nan_frac*100:.0f}% NaN over {len(rows)} rows ({statement})",
        )]
    return []


def _q4_sign_anomaly(df: pd.DataFrame, metric: str, ticker: str, statement: str) -> list[Violation]:
    """Q4 sign opposite to Q1+Q2+Q3 average AND magnitude > 2x annual sum
    suggests a derivation cascade from a wrong annual.

    Noise floor: only fire when |Q4| AND |Q1-Q3 avg| are both >= 100 (i.e.
    100 million) to avoid flagging tiny rounding-noise differences at the
    edges of history."""
    out: list[Violation] = []
    if metric not in df.columns:
        return out
    NOISE_FLOOR_M = 100.0
    for _, q4 in df[df["fiscal_quarter"] == "Q4"].iterrows():
        ann_ps = q4.get("period_start")
        # Q4 has its own period_start (Q3_end + 1 day), so find Q1-Q3 by fiscal_year.
        fy = q4.get("fiscal_year")
        q123 = df[(df["fiscal_year"] == fy) & (df["fiscal_quarter"].isin(["Q1", "Q2", "Q3"]))]
        if q123.empty:
            continue
        q4_v   = q4.get(metric)
        q123_v = q123[metric].dropna()
        if pd.isna(q4_v) or q123_v.empty:
            continue
        q123_mean = q123_v.mean()
        if abs(q123_mean) < NOISE_FLOOR_M or abs(q4_v) < NOISE_FLOOR_M:
            continue
        sign_q4   = 1 if q4_v   >= 0 else -1
        sign_q123 = 1 if q123_mean >= 0 else -1
        if sign_q4 != sign_q123 and abs(q4_v) > 2 * abs(q123_mean):
            out.append(Violation(
                severity="CRITICAL", ticker=ticker, rule="q4_sign_anomaly",
                statement=statement, period=f"FY{int(fy)}-Q4", metric=metric,
                actual=f"Q4={q4_v:.0f}, Q1-Q3 avg={q123_mean:.0f}",
                message=(f"FY{int(fy)} {metric}: derived Q4={q4_v:.0f} has opposite sign and "
                         f">2x magnitude of Q1-Q3 avg ({q123_mean:.0f}) -- suggests bad Annual"),
            ))
    return out


def _ratio_range(
    df: pd.DataFrame, num: str, den: str, ticker: str, statement: str,
    *, low: float, high: float, label: str,
) -> list[Violation]:
    """Flag rows where num/den ratio falls outside [low, high]."""
    out: list[Violation] = []
    if num not in df.columns or den not in df.columns:
        return out
    rows = df[df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])]
    for _, r in rows.iterrows():
        n, d = r.get(num), r.get(den)
        if pd.isna(n) or pd.isna(d) or d == 0:
            continue
        ratio = n / d
        if ratio < low or ratio > high:
            out.append(Violation(
                severity="WARN", ticker=ticker, rule="ratio_range",
                statement=statement, period=str(r.get("fiscal_quarter", "")) + " " + str(r.get("fiscal_year", "")),
                metric=label,
                actual=f"{ratio*100:.0f}% (n={n:.0f}, d={d:.0f})",
                expected=f"[{low*100:.0f}%, {high*100:.0f}%]",
                message=(f"{label} = {n:.0f}/{d:.0f} = {ratio*100:.0f}% "
                         f"(outside [{low*100:.0f}%, {high*100:.0f}%]) "
                         f"at {r.get('fiscal_quarter')} FY{r.get('fiscal_year')}"),
            ))
    return out


def _sign_must_be(
    df: pd.DataFrame, metric: str, ticker: str, statement: str,
    *, must_be_negative: bool = False, must_be_positive: bool = False,
) -> list[Violation]:
    """E.g. capex must be negative (it's an outflow)."""
    out: list[Violation] = []
    if metric not in df.columns:
        return out
    rows = df[df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4", "Annual"])]
    for _, r in rows.iterrows():
        v = r.get(metric)
        if pd.isna(v) or v == 0:
            continue
        bad = (must_be_negative and v > 0) or (must_be_positive and v < 0)
        if not bad:
            continue
        out.append(Violation(
            severity="WARN", ticker=ticker, rule="sign_violation",
            statement=statement,
            period=f"{r.get('fiscal_quarter','')} FY{r.get('fiscal_year','')}",
            metric=metric, actual=f"{v:.0f}",
            expected=("negative" if must_be_negative else "positive"),
            message=f"{metric}={v:.0f} at {r.get('fiscal_quarter')} FY{r.get('fiscal_year')} "
                    f"(expected {'negative' if must_be_negative else 'positive'})",
        ))
    return out


def _cross_statement_ni_check(income: pd.DataFrame, cf: pd.DataFrame, ticker: str) -> list[Violation]:
    """Net income from income statement should match net income from CF
    reconciliation start (when both are present). We don't have NI on the CF
    parquet directly today, so this is currently a no-op but kept as a hook."""
    return []


# ---------------------------------------------------------------------------
# Per-ticker audit
# ---------------------------------------------------------------------------

def audit_ticker(ticker: str) -> TickerAudit:
    a = TickerAudit(ticker=ticker)

    income = _load(ticker, "income_statement")
    balance = _load(ticker, "balance_sheet")
    cf      = _load(ticker, "cash_flow")

    if income is None and balance is None and cf is None:
        a.violations.append(Violation(
            severity="CRITICAL", ticker=ticker, rule="no_parquets",
            message=f"No topline parquets found for {ticker}",
        ))
        return a

    # ---- Income statement ----
    if income is not None:
        a.rows_income = len(income)
        # Annual = sum of quarters: revenue, net_income, operating_income (5% threshold;
        # CRITICAL at 25%+).
        for m in ("revenue", "net_income", "operating_income", "gross_profit"):
            a.violations += _annual_vs_quarters(
                income, m, ticker, "income_statement",
                threshold_pct=5.0, severity_at=25.0,
            )
        # gross_profit identity: revenue - cost_of_revenue ~= gross_profit (1% tol).
        if {"revenue", "cost_of_revenue", "gross_profit"} <= set(income.columns):
            for _, r in income[income["fiscal_quarter"].isin(["Q1","Q2","Q3","Q4"])].iterrows():
                rev, cogs, gp = r.get("revenue"), r.get("cost_of_revenue"), r.get("gross_profit")
                if any(pd.isna(x) for x in (rev, cogs, gp)) or rev == 0:
                    continue
                implied = rev - cogs
                if abs(implied - gp) / abs(rev) > 0.01:
                    a.violations.append(Violation(
                        severity="WARN", ticker=ticker, rule="gross_profit_identity",
                        statement="income_statement",
                        period=f"{r.get('fiscal_quarter')} FY{r.get('fiscal_year')}",
                        message=(f"FY{r.get('fiscal_year')}-{r.get('fiscal_quarter')}: "
                                 f"revenue ({rev:.0f}) - cost_of_revenue ({cogs:.0f}) = {implied:.0f}, "
                                 f"gross_profit reported = {gp:.0f}"),
                    ))

    # ---- Cash flow ----
    if cf is not None:
        a.rows_cashflow = len(cf)
        # Annual = sum of quarters for ALL CF metrics. This is the AMD smoking gun.
        for m in ("operating_cf", "investing_cf", "financing_cf", "capex", "depreciation"):
            a.violations += _annual_vs_quarters(
                cf, m, ticker, "cash_flow",
                threshold_pct=5.0, severity_at=25.0,
            )
        # Q4 sign anomaly (derivation cascade from bad Annual).
        for m in ("operating_cf", "investing_cf", "financing_cf", "depreciation"):
            a.violations += _q4_sign_anomaly(cf, m, ticker, "cash_flow")
        # NaN coverage on the most basic CF metrics.
        for m in ("operating_cf",):
            a.violations += _nan_coverage(cf, m, ticker, "cash_flow", max_nan_frac=0.5)
        # depreciation: missing for >50% rows -> WARN (mapping likely incomplete)
        a.violations += _nan_coverage(cf, "depreciation", ticker, "cash_flow", max_nan_frac=0.5)
        # Capex MUST be <= 0 (outflow). ORCL FY... etc. exceptions are real, but
        # flag is still useful.
        a.violations += _sign_must_be(cf, "capex", ticker, "cash_flow", must_be_negative=True)

    # ---- Balance sheet ----
    if balance is not None:
        a.rows_balance = len(balance)
        # Total assets must be > 0
        if "total_assets" in balance.columns:
            zero = balance[(balance["total_assets"].notna()) & (balance["total_assets"] <= 0)]
            for _, r in zero.iterrows():
                a.violations.append(Violation(
                    severity="CRITICAL", ticker=ticker, rule="non_positive_assets",
                    statement="balance_sheet",
                    period=str(r.get("period_end")),
                    actual=f"{r.get('total_assets')}",
                    message=f"total_assets={r.get('total_assets')} at {r.get('period_end')}",
                ))

    # ---- Cross-statement: OCF / revenue plausibility ----
    if (
        income is not None and cf is not None
        and {"period_end", "fiscal_quarter", "revenue"} <= set(income.columns)
        and {"period_end", "fiscal_quarter", "operating_cf"} <= set(cf.columns)
    ):
        # Match by fiscal_quarter + period_end. Defensive col selection: only
        # take what exists.
        inc_cols = [c for c in ("period_end", "fiscal_quarter", "fiscal_year",
                                "revenue", "net_income") if c in income.columns]
        cf_cols  = [c for c in ("period_end", "fiscal_quarter", "operating_cf") if c in cf.columns]
        merged = pd.merge(
            income[inc_cols], cf[cf_cols],
            on=["period_end", "fiscal_quarter"], how="inner",
        )
        # OCF / revenue plausibility: operating CF as a fraction of revenue.
        # For most software/semis: -10% to +60% is normal. Outside that range
        # for >=2 quarters -> WARN.
        outside = 0
        examples: list[str] = []
        for _, r in merged[merged["fiscal_quarter"].isin(["Q1","Q2","Q3","Q4"])].iterrows():
            rev, ocf = r.get("revenue"), r.get("operating_cf")
            if pd.isna(rev) or pd.isna(ocf) or rev == 0:
                continue
            ratio = ocf / rev
            if ratio < -0.30 or ratio > 1.50:
                outside += 1
                if len(examples) < 3:
                    examples.append(f"{r['fiscal_quarter']} FY{int(r['fiscal_year'])}: "
                                    f"OCF/rev={ratio*100:.0f}%")
        if outside >= 2:
            a.violations.append(Violation(
                severity="WARN", ticker=ticker, rule="ocf_to_revenue_outlier",
                statement="cross",
                actual=f"{outside} quarters outside [-30%, +150%]",
                message=f"{outside} quarters with OCF/revenue outside [-30%, +150%]: " + "; ".join(examples),
            ))

    return a


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _render_md(audits: list[TickerAudit]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# Topline audit report")
    lines.append("")
    lines.append(f"Generated: {now}")
    lines.append(f"Tickers audited: {len(audits)}")
    crit = sum(a.crit_count for a in audits)
    warn = sum(a.warn_count for a in audits)
    info = sum(a.info_count for a in audits)
    lines.append(f"Total violations: CRITICAL={crit}  WARN={warn}  INFO={info}")
    lines.append("")
    # Per-ticker summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Ticker | Income | Cashflow | Balance | CRIT | WARN | INFO |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for a in sorted(audits, key=lambda x: (-x.crit_count, -x.warn_count, x.ticker)):
        lines.append(f"| {a.ticker} | {a.rows_income} | {a.rows_cashflow} | {a.rows_balance} | "
                     f"{a.crit_count} | {a.warn_count} | {a.info_count} |")
    lines.append("")
    # Detail per ticker (only those with violations)
    lines.append("## Details")
    lines.append("")
    for a in sorted(audits, key=lambda x: (-x.crit_count, -x.warn_count, x.ticker)):
        if not a.violations:
            continue
        lines.append(f"### {a.ticker}  (CRIT={a.crit_count}, WARN={a.warn_count}, INFO={a.info_count})")
        for v in a.violations:
            tag = f"`{v.severity}`"
            scope = f"[{v.statement}]" if v.statement else ""
            lines.append(f"- {tag} {scope} **{v.rule}**: {v.message}")
        lines.append("")
    if all(not a.violations for a in audits):
        lines.append("All tickers passed.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Audit topline parquets for extraction bugs.")
    ap.add_argument("--tickers", nargs="*", default=None,
                    help="Subset of tickers to audit (default: all on disk).")
    args = ap.parse_args()

    tickers = args.tickers or _all_tickers()
    if not tickers:
        print("[audit] no tickers found.")
        return 1

    print(f"[audit] auditing {len(tickers)} ticker(s)")
    audits = [audit_ticker(t) for t in tickers]

    # JSON output
    _REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tickers_audited": len(audits),
        "tickers": {
            a.ticker: {
                "rows_income":   a.rows_income,
                "rows_balance":  a.rows_balance,
                "rows_cashflow": a.rows_cashflow,
                "violations":    [v.as_dict() for v in a.violations],
            } for a in audits
        },
    }
    _REPORT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # Markdown output
    _REPORT_MD.write_text(_render_md(audits), encoding="utf-8")

    crit = sum(a.crit_count for a in audits)
    warn = sum(a.warn_count for a in audits)
    info = sum(a.info_count for a in audits)
    print(f"[audit] CRITICAL={crit}  WARN={warn}  INFO={info}")
    print(f"[audit] wrote {_REPORT_JSON}")
    print(f"[audit] wrote {_REPORT_MD}")

    # Per-ticker top-line for the run log.
    for a in sorted(audits, key=lambda x: (-x.crit_count, -x.warn_count, x.ticker)):
        if not a.violations:
            continue
        print(f"  {a.ticker:<6} CRIT={a.crit_count:<2} WARN={a.warn_count:<2} INFO={a.info_count:<2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
