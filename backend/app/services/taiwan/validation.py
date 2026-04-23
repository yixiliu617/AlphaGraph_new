"""
Data-quality invariants for Taiwan scraper output. Flags, never drops — the
caller decides whether to persist a flagged row (normally yes; flags surface
in the UI and health report).
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum


class ValidationFlag(str, Enum):
    NEGATIVE_REVENUE = "negative_revenue"
    ABSURD_YOY = "absurd_yoy"
    FUTURE_PERIOD = "future_period"
    INVALID_PERIOD_FORMAT = "invalid_period_format"
    LARGE_AMENDMENT = "large_amendment"


_FISCAL_YM = re.compile(r"^(\d{4})-(\d{2})$")


def validate_monthly_revenue_row(row: dict) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    revenue = row.get("revenue_twd")
    if revenue is not None and revenue < 0:
        flags.append(ValidationFlag.NEGATIVE_REVENUE)

    yoy = row.get("yoy_pct")
    if yoy is not None and abs(yoy) > 10.0:  # > 1000 %
        flags.append(ValidationFlag.ABSURD_YOY)

    ym = row.get("fiscal_ym") or ""
    m = _FISCAL_YM.match(ym)
    if not m:
        flags.append(ValidationFlag.INVALID_PERIOD_FORMAT)
    else:
        y, mm = int(m.group(1)), int(m.group(2))
        if mm < 1 or mm > 12:
            flags.append(ValidationFlag.INVALID_PERIOD_FORMAT)
        else:
            today = date.today()
            if y > today.year or (y == today.year and mm > today.month + 1):
                flags.append(ValidationFlag.FUTURE_PERIOD)

    return flags


def is_large_amendment(prior_value: float, new_value: float, threshold: float = 0.5) -> bool:
    """Return True if new_value differs from prior_value by > threshold * prior_value."""
    if prior_value in (None, 0):
        return False
    return abs(new_value - prior_value) / abs(prior_value) > threshold
