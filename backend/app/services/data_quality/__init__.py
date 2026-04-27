"""Data quality framework — modular checks for every silver / guidance /
transcript dataset we onboard. See `framework.py` for the Check class,
`checks.py` for reusable primitives, `registry.py` for the per-dataset
check declarations, and `runner.py` for the orchestrator + CLI entry.

Top-level convenience re-exports:
"""
from .framework import Check, CheckResult, Severity
from .runner import run_all, run_for_dataset

__all__ = ["Check", "CheckResult", "Severity", "run_all", "run_for_dataset"]
