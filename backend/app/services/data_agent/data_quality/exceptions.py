"""
Known data quality exceptions — per-ticker, per-rule, per-date suppressions.

When a rule correctly identifies an anomaly that's NOT a bug (legit stock
split, pandemic-era revenue cliff, one-off restatement), add it here rather
than watering down the rule.

Semantics:
    { ticker: { rule_name: set(end_date_iso_strings) } }

A violation is suppressed iff the ticker, rule name, AND end_date all match.
Unknown tickers or rule names are ignored (not an error).

Use the CLI helper to regenerate known stock-split exceptions from yfinance:
    python -m backend.app.services.data_agent.data_quality.exceptions --sync

(That helper is not implemented yet — add it when stock-split handling lands.)
"""

from __future__ import annotations

# Per-ticker, per-rule, set of end_date strings (YYYY-MM-DD) that are known-OK.
# Keep this sorted by ticker then by rule name for easy diffing.
KNOWN_EXCEPTIONS: dict[str, dict[str, set[str]]] = {
    # Stock splits used to live here (NVDA 2024-06-10 10-for-1, etc.) but are
    # now handled systematically by backend/app/services/data_agent/splits.py,
    # which retroactively adjusts shares and per-share values from yfinance
    # data. Exceptions file is reserved for genuine one-off anomalies that
    # can't be programmatically detected:
    #   - Pandemic-era revenue/opex cliffs (TSLA 2020-Q2, cruise lines)
    #   - Spinoffs where continuing-ops restatement creates a legit discontinuity
    #   - Corporate actions with no clean data source
    #
    # Example template:
    # "TSLA": {
    #     "revenue_no_cliff": {"2020-06-30"},   # COVID-19 production halt
    # },
}


def is_suppressed(ticker: str, rule_name: str, end_date_iso: str) -> bool:
    """Return True if this violation is on the known-exceptions list."""
    ticker_entry = KNOWN_EXCEPTIONS.get(ticker)
    if not ticker_entry:
        return False
    dates = ticker_entry.get(rule_name)
    if not dates:
        return False
    return end_date_iso in dates
