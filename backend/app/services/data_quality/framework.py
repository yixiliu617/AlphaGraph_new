"""
Data-quality check framework — minimum-viable, dependency-free.

A `Check` is a named, parameterised function over a DataFrame that returns
a `CheckResult`. The framework's job is just to:
  - hold the registry of checks per dataset
  - run them, catching exceptions
  - report a structured pass/warn/fail outcome with violating-row samples

Design choices:
  - One Check produces one CheckResult per run (not per violating row) —
    keeps the report digestible. Sample violating rows go in `details`.
  - Severity is a hint to the runner (and any UI) about how to display, not
    enforced. A failed `error` check will exit non-zero in CLI mode; a
    failed `warn` check just gets logged.
  - No side effects on the DataFrame. Checks read; they don't mutate.
"""

from __future__ import annotations

import enum
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

import pandas as pd


class Severity(str, enum.Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class CheckResult:
    """Outcome of running a single Check against a single dataset."""
    check_name: str
    dataset: str
    status: str                           # "pass" | "warn" | "fail" | "error"
    severity: Severity
    message: str
    affected_count: int = 0
    duration_ms: float = 0.0
    sample: List[dict] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "check_name": self.check_name,
            "dataset": self.dataset,
            "status": self.status,
            "severity": self.severity.value,
            "message": self.message,
            "affected_count": self.affected_count,
            "duration_ms": round(self.duration_ms, 1),
            "sample": self.sample,
            "details": self.details,
        }


# A check is a callable: (df: DataFrame, **kwargs) -> CheckResult
# We wrap it in a Check dataclass so the registry can declare metadata
# (params, severity, description) alongside the runner.

@dataclass
class Check:
    name: str
    description: str
    runner: Callable[..., CheckResult]
    severity: Severity = Severity.WARN
    params: dict = field(default_factory=dict)

    def run(self, df: pd.DataFrame, dataset_name: str) -> CheckResult:
        t0 = time.perf_counter()
        try:
            result = self.runner(df, dataset_name=dataset_name, **self.params)
            result.severity = self.severity
            result.duration_ms = (time.perf_counter() - t0) * 1000
            return result
        except Exception as exc:  # pragma: no cover - defensive
            return CheckResult(
                check_name=self.name,
                dataset=dataset_name,
                status="error",
                severity=Severity.ERROR,
                message=f"check raised {type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - t0) * 1000,
                details={"traceback": traceback.format_exc()},
            )
