"""
Per-dataset check registry.

When a new dataset is onboarded (a new ticker, a new layer like
'guidance' for a company, a new fragment store), add an entry here
declaring the checks that should run against it. The runner picks up
every entry in DATASETS automatically.

Each dataset entry:
    {
      "path": <pathlib.Path to parquet>,
      "checks": [Check, Check, ...]
    }
"""

from __future__ import annotations

from pathlib import Path

from .framework import Check, Severity
from .checks import (
    period_continuity, period_label_format, identity_check, range_check,
    sign_consistency, source_period_match, share_sum, row_count_min,
    duplicate_key, trading_day_continuity,
)


_FINANCIALS = Path("backend/data/financials")


# Helper builders to keep the registry compact -----------------------------

def _continuity(period_col: str, group_col: str | None = None,
                expected_freq: str = "quarterly", severity: Severity = Severity.WARN) -> Check:
    name = f"period_continuity[{period_col}{f'/{group_col}' if group_col else ''}]"
    return Check(
        name=name,
        description=f"every {expected_freq} period between earliest and latest is present"
                    + (f" (grouped by {group_col})" if group_col else ""),
        runner=period_continuity,
        severity=severity,
        params={"period_col": period_col, "group_col": group_col, "expected_freq": expected_freq},
    )


def _row_min(n: int) -> Check:
    return Check(
        name="row_count_min",
        description=f"silver has at least {n} rows (catches empty/corrupt parquets)",
        runner=row_count_min,
        severity=Severity.ERROR,
        params={"minimum": n},
    )


def _label_format(period_col: str, allow_fy: bool = True) -> Check:
    return Check(
        name=f"period_label_format[{period_col}]",
        description=f"all values in {period_col} match {'NQYY/FYYY' if allow_fy else 'NQYY'} pattern",
        runner=period_label_format,
        severity=Severity.ERROR,
        params={"period_col": period_col, "allow_fy": allow_fy},
    )


def _identity(metric_col: str, value_col: str, period_col: str,
              lhs: str, rhs: list[str], op: str = "sum_abs",
              tolerance_pct: float = 0.5,
              dimension_col: str | None = "dimension") -> Check:
    return Check(
        name=f"identity[{lhs} = {op}({rhs})]",
        description=f"{lhs} ≈ {op}({rhs}) within {tolerance_pct}% per period",
        runner=identity_check,
        severity=Severity.WARN,
        params={
            "metric_col": metric_col, "value_col": value_col, "period_col": period_col,
            "formula": {"lhs": lhs, "rhs": rhs, "op": op},
            "tolerance_pct": tolerance_pct,
            "dimension_col": dimension_col,
        },
    )


def _share_sum(metric_prefix: str, tolerance: float = 1.5) -> Check:
    return Check(
        name=f"share_sum[{metric_prefix}]",
        description=f"every (metric, period) group with prefix {metric_prefix!r} sums to 100% ± {tolerance}pp",
        runner=share_sum,
        severity=Severity.WARN,
        params={"metric_col": "metric", "value_col": "value",
                "period_col": "period_label", "metric_prefix": metric_prefix,
                "tolerance": tolerance},
    )


def _source_period_match(source_pattern: str) -> Check:
    return Check(
        name="source_period_match",
        description="the period embedded in the `source` identifier agrees with `period_label` "
                    "(catches mislabeled source files like the MediaTek 2023Q3 transcript "
                    "that contained 3Q24 content at the 2023Q3 URL)",
        runner=source_period_match,
        severity=Severity.ERROR,
        params={"source_col": "source", "period_col": "period_label",
                "source_pattern": source_pattern},
    )


def _dup_key(*cols: str) -> Check:
    return Check(
        name="duplicate_key",
        description=f"no duplicates on dedup key {list(cols)}",
        runner=duplicate_key, severity=Severity.ERROR,
        params={"key_cols": cols},
    )


def _trading_continuity(exchange: str = "NYSE", date_col: str = "date",
                        grace: int = 5) -> Check:
    return Check(
        name=f"trading_day_continuity[{exchange}]",
        description=f"no missing {exchange} session days between earliest and latest "
                    f"in {date_col} (grace: {grace} days)",
        runner=trading_day_continuity, severity=Severity.WARN,
        params={"exchange": exchange, "date_col": date_col,
                "max_skipped_session_days": grace},
    )


def _range(metric_col: str, value_col: str, metric: str,
           min_val: float | None = None, max_val: float | None = None) -> Check:
    return Check(
        name=f"range[{metric}]",
        description=f"{metric} within [{min_val}, {max_val}]",
        runner=range_check, severity=Severity.WARN,
        params={"metric_col": metric_col, "value_col": value_col, "metric": metric,
                "min_val": min_val, "max_val": max_val},
    )


# Registry -----------------------------------------------------------------

DATASETS: dict[str, dict] = {
    # ---------------------------------------------------------------- TSMC
    "tsmc.facts": {
        "path": _FINANCIALS / "quarterly_facts" / "2330.TW.parquet",
        "checks": [
            _row_min(100),
            _label_format("period_label", allow_fy=False),
            _continuity("period_label", group_col="metric", severity=Severity.WARN),
            _share_sum("revenue_share_by_"),
            _identity("metric", "value", "period_label",
                      "gross_profit", ["net_revenue", "cost_of_revenue"], op="sum",
                      tolerance_pct=1.0),
            _dup_key("ticker", "period_end", "metric", "dimension", "source"),
        ],
    },
    "tsmc.transcripts": {
        "path": _FINANCIALS / "transcripts" / "2330.TW.parquet",
        "checks": [
            _row_min(50),
            _continuity("period_label", severity=Severity.WARN),
            _source_period_match(r"earnings_call_(\dQ\d{2})"),
            _dup_key("ticker", "period_end", "turn_index", "source"),
        ],
    },
    "tsmc.guidance": {
        "path": _FINANCIALS / "guidance" / "2330.TW.parquet",
        "checks": [
            _row_min(20),
            Check(
                name="period_continuity[issued_in_period_label]",
                description="every issuing report between earliest and latest is present",
                runner=period_continuity, severity=Severity.WARN,
                params={"period_col": "issued_in_period_label", "group_col": "metric"},
            ),
            _label_format("for_period_label"),
            _label_format("issued_in_period_label", allow_fy=False),
        ],
    },

    # ---------------------------------------------------------------- UMC
    "umc.facts": {
        "path": _FINANCIALS / "quarterly_facts" / "2303.TW.parquet",
        "checks": [
            _row_min(100),
            _label_format("period_label", allow_fy=True),
            _continuity("period_label", group_col="metric", severity=Severity.WARN),
            _share_sum("revenue_share_by_"),
            _dup_key("ticker", "period_end", "metric", "dimension", "source"),
        ],
    },
    "umc.guidance": {
        "path": _FINANCIALS / "guidance" / "2303.TW.parquet",
        "checks": [
            _row_min(20),
            Check(
                name="period_continuity[issued_in_period_label]",
                description="every issuing report between earliest and latest is present",
                runner=period_continuity, severity=Severity.WARN,
                params={"period_col": "issued_in_period_label", "group_col": "metric"},
            ),
        ],
    },

    # ----------------------------------------------------------- MediaTek
    "mediatek.facts": {
        "path": _FINANCIALS / "quarterly_facts" / "2454.TW.parquet",
        "checks": [
            _row_min(100),
            _label_format("period_label", allow_fy=False),
            _continuity("period_label", group_col="metric", severity=Severity.WARN),
            _identity("metric", "value", "period_label",
                      "net_revenue", ["gross_profit", "cost_of_revenue"], op="sum",
                      tolerance_pct=1.0),
            _dup_key("ticker", "period_end", "metric", "dimension", "source"),
        ],
    },
    "mediatek.transcripts": {
        "path": _FINANCIALS / "transcripts" / "2454.TW.parquet",
        "checks": [
            _row_min(50),
            _continuity("period_label", severity=Severity.WARN),
            _source_period_match(r"earnings_call_(\dQ\d{2})"),
            _dup_key("ticker", "period_end", "turn_index", "source"),
        ],
    },
    "mediatek.guidance": {
        "path": _FINANCIALS / "guidance" / "2454.TW.parquet",
        "checks": [
            _row_min(20),
            Check(
                name="period_continuity[issued_in_period_label]",
                description="every issuing report between earliest and latest is present "
                            "(catches MediaTek-style source mislabel where a transcript URL "
                            "served the wrong quarter's PDF)",
                runner=period_continuity, severity=Severity.WARN,
                params={"period_col": "issued_in_period_label", "group_col": "metric"},
            ),
            _label_format("for_period_label"),
            _label_format("issued_in_period_label", allow_fy=False),
        ],
    },

    # ----------------------------------------------------------- Prices
    # One entry per market+frequency. The runner expands `path_glob` into a
    # per-file run so the registry stays compact as the universe grows toward
    # 2000 tickers. Each ticker file gets the same checks; results are
    # tagged with the file's ticker so issues are pinpointable.
    "prices.us_daily": {
        "path_glob": str(_FINANCIALS / "prices" / "*.parquet"),
        "path_glob_filter": "us",
        "checks": [
            _row_min(50),
            _trading_continuity(exchange="NYSE", date_col="date", grace=10),
            _dup_key("ticker", "date"),
        ],
    },
    "prices.taiwan_daily": {
        "path_glob": str(_FINANCIALS / "prices" / "*.parquet"),
        "path_glob_filter": "tw",
        "checks": [
            _row_min(50),
            # Yahoo's historical TWSE data has gaps -- ~5% of sessions are
            # missing in the 2016-2020 window. These are upstream data holes,
            # not bugs. Generous grace keeps the signal: current-window gaps
            # (where yfinance is solid) would still surface, while the
            # 10-year backfill wouldn't fire false alarms on every ticker.
            _trading_continuity(exchange="XTAI", date_col="date", grace=600),
            _dup_key("ticker", "date"),
        ],
    },
    "prices.us_intraday_15m": {
        "path_glob": str(_FINANCIALS / "prices" / "intraday" / "*_15m.parquet"),
        "path_glob_filter": "us",
        "checks": [
            _row_min(100),
            _dup_key("ticker", "ts_utc"),
        ],
    },
    "prices.taiwan_intraday_15m": {
        "path_glob": str(_FINANCIALS / "prices" / "intraday" / "*_15m.parquet"),
        "path_glob_filter": "tw",
        "checks": [
            _row_min(50),
            _dup_key("ticker", "ts_utc"),
        ],
    },
}
