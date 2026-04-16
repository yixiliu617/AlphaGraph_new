"""
Rule runner — executes all RULES against a calculated-layer DataFrame and
assembles a structured QualityReport.

Used by:
    - calculator._validate() on every build (violations go into _build_report.json)
    - GET /data/quality-report API endpoint (serves the current report to the UI)

The runner NEVER raises on a rule failure — it collects violations and lets
the caller decide whether to block the build. Rules themselves may raise if
misconfigured; those errors propagate with a descriptive message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any

import pandas as pd

from .rules import RULES, Rule
from .exceptions import is_suppressed

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class QualityViolation:
    """One rule-violation occurrence, tied to a specific row."""

    rule:       str
    severity:   str
    ticker:     str
    end_date:   str       # ISO YYYY-MM-DD
    message:    str
    suppressed: bool = False   # True if matched KNOWN_EXCEPTIONS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QualityReport:
    """Aggregated quality report for one ticker (or one merged build)."""

    ticker:            str
    rows_checked:      int
    rules_evaluated:   int
    rules_skipped:     list[str] = field(default_factory=list)    # missing columns
    violations:        list[QualityViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Build-blocking pass/fail: true only when no unsuppressed FAIL-severity violations remain."""
        return not any(
            v.severity == "fail" and not v.suppressed for v in self.violations
        )

    @property
    def fail_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "fail" and not v.suppressed)

    @property
    def warn_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warn" and not v.suppressed)

    def warning_messages(self) -> list[str]:
        """Flat list of 'rule_name: message' strings suitable for legacy build reports."""
        return [
            f"{v.rule} ({v.severity}): {v.message}"
            for v in self.violations
            if not v.suppressed
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker":          self.ticker,
            "rows_checked":    self.rows_checked,
            "rules_evaluated": self.rules_evaluated,
            "rules_skipped":   self.rules_skipped,
            "passed":          self.passed,
            "fail_count":      self.fail_count,
            "warn_count":      self.warn_count,
            "violations":      [v.to_dict() for v in self.violations],
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_rules(df: pd.DataFrame, ticker: str) -> QualityReport:
    """
    Evaluate every rule in RULES against ``df`` and return an aggregated report.

    Behavior:
        - Rules whose ``applies_to`` columns aren't present are skipped
          (and logged in ``rules_skipped``), not failed.
        - Violations matching an entry in ``KNOWN_EXCEPTIONS`` are kept in
          the report but marked ``suppressed=True`` — they still appear for
          audit, but they don't count toward fail/warn totals.
        - Rule authors can return an empty DataFrame (``.iloc[0:0]``) to mean
          "rule passed, no violations".
    """
    report = QualityReport(ticker=ticker, rows_checked=len(df), rules_evaluated=0)

    if df.empty:
        return report

    for rule in RULES:
        # Skip if required columns are missing
        missing = [c for c in rule.applies_to if c not in df.columns]
        if missing:
            report.rules_skipped.append(f"{rule.name} (missing: {', '.join(missing)})")
            continue

        report.rules_evaluated += 1

        try:
            violating_rows = rule.check(df)
        except Exception as exc:
            # A buggy rule should surface loudly, but never crash the build.
            log.error("Rule %s raised during execution: %s", rule.name, exc)
            report.violations.append(
                QualityViolation(
                    rule=rule.name,
                    severity="warn",
                    ticker=ticker,
                    end_date="",
                    message=f"rule crashed: {exc}",
                )
            )
            continue

        if violating_rows is None or violating_rows.empty:
            continue

        for _, row in violating_rows.iterrows():
            end_date_iso = _row_end_date(row)
            violation = QualityViolation(
                rule=rule.name,
                severity=rule.severity,
                ticker=ticker,
                end_date=end_date_iso,
                message=_safe_format(rule, row),
                suppressed=is_suppressed(ticker, rule.name, end_date_iso),
            )
            report.violations.append(violation)

    return report


def _row_end_date(row: pd.Series) -> str:
    """Pull a YYYY-MM-DD string from a row's end_date (Timestamp or string)."""
    v = row.get("end_date")
    if v is None:
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return str(v)[:10]


def _safe_format(rule: Rule, row: pd.Series) -> str:
    """Call rule.message defensively; fall back to the rule description on error."""
    try:
        return rule.message(row)
    except Exception as exc:
        return f"{rule.description} (formatter error: {exc})"
