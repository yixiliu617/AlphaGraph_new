"""
Data quality invariants framework.

Usage from calculator._validate():

    from .data_quality import run_rules
    report = run_rules(wide_df, ticker)
    warnings_.extend(report.warning_messages())
    if not report.passed:
        # optionally block the build for severity=fail rules
        ...

Add new checks by appending to RULES in rules.py.
Mark known anomalies (stock splits, restatements) in exceptions.py.
"""

from .runner import run_rules, QualityReport, QualityViolation

__all__ = ["run_rules", "QualityReport", "QualityViolation"]
