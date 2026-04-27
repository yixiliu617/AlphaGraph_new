"""
Runner — execute checks against the registry, return a structured report.

Two entry points:
  - `run_all()`        — run every registered dataset, return list of CheckResult dicts
  - `run_for_dataset(name)` — run just one dataset by registry key (e.g. 'umc.facts')
  - `python -m backend.app.services.data_quality.runner` — CLI table output
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from .framework import CheckResult, Severity
from .registry import DATASETS


def _load(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    return pd.read_parquet(path)


def _is_taiwan_filename(name: str) -> bool:
    """True if the parquet filename indicates a Taiwan ticker (`.TW`).

    Matches both `2330.TW.parquet` (daily) and `2330.TW_15m.parquet` (intraday).
    """
    stem = name.split("_15m")[0]
    return stem.endswith(".TW") or stem.endswith(".TWO")


def _filter_paths(paths: list[Path], market_filter: Optional[str]) -> list[Path]:
    """Filter a list of price parquets to one market.

    - "us" -> non-Taiwan files
    - "tw" -> Taiwan files (`.TW` / `.TWO`)
    -  None -> no filter
    """
    if market_filter is None:
        return paths
    if market_filter == "tw":
        return [p for p in paths if _is_taiwan_filename(p.stem)]
    if market_filter == "us":
        return [p for p in paths if not _is_taiwan_filename(p.stem)]
    return paths


def _ticker_from_filename(name: str) -> str:
    """Extract a ticker from a prices parquet filename.
    NVDA.parquet -> NVDA;  2330.TW.parquet -> 2330.TW;
    NVDA_15m.parquet -> NVDA;  2330.TW_15m.parquet -> 2330.TW.
    """
    stem = Path(name).stem
    if stem.endswith("_15m"):
        stem = stem[: -len("_15m")]
    return stem


def run_for_dataset(dataset_name: str) -> list[dict]:
    """Run every registered Check against one dataset. Returns list of
    CheckResult dicts (status field: pass / warn / fail / error).

    Two registry shapes are supported:

    1. Single-file dataset (legacy): spec has `path`. The parquet at that
       path is loaded once and every check runs against it.
    2. Multi-file dataset: spec has `path_glob` (and optionally
       `path_glob_filter` = "us" / "tw"). Every matching parquet is loaded
       and every check runs per-file. Each result's `dataset` is annotated
       with the ticker so failures are pinpointable.
    """
    if dataset_name not in DATASETS:
        return [{
            "check_name": "registry_lookup",
            "dataset": dataset_name,
            "status": "error",
            "severity": "error",
            "message": f"unknown dataset; valid keys: {sorted(DATASETS.keys())}",
        }]
    spec = DATASETS[dataset_name]

    if "path_glob" in spec:
        files = [Path(p) for p in glob.glob(spec["path_glob"])]
        files = _filter_paths(files, spec.get("path_glob_filter"))
        if not files:
            return [{
                "check_name": "path_glob_match",
                "dataset": dataset_name,
                "status": "fail",
                "severity": "error",
                "message": f"no parquets matched glob {spec['path_glob']!r}"
                           f" with filter={spec.get('path_glob_filter')!r}",
            }]
        out: list[dict] = []
        for f in sorted(files):
            df = _load(f)
            if df is None:
                out.append({
                    "check_name": "parquet_exists",
                    "dataset": f"{dataset_name}/{_ticker_from_filename(f.name)}",
                    "status": "fail",
                    "severity": "error",
                    "message": f"parquet not found at {f}",
                })
                continue
            tag = f"{dataset_name}/{_ticker_from_filename(f.name)}"
            for chk in spec["checks"]:
                r = chk.run(df, tag).to_dict()
                out.append(r)
        return out

    df = _load(spec["path"])
    if df is None:
        return [{
            "check_name": "parquet_exists",
            "dataset": dataset_name,
            "status": "fail",
            "severity": "error",
            "message": f"parquet not found at {spec['path']}",
        }]
    return [chk.run(df, dataset_name).to_dict() for chk in spec["checks"]]


def run_all() -> list[dict]:
    """Run every registered dataset's checks. Order: as declared in registry."""
    out: list[dict] = []
    for name in DATASETS.keys():
        out.extend(run_for_dataset(name))
    return out


# CLI ----------------------------------------------------------------------

_STATUS_COLOR = {"pass": "\033[32m", "warn": "\033[33m", "fail": "\033[31m", "error": "\033[35m"}
_RESET = "\033[0m"


def _print_report(results: list[dict], color: bool = True) -> None:
    by_dataset: dict[str, list[dict]] = {}
    for r in results:
        by_dataset.setdefault(r["dataset"], []).append(r)
    summary = {"pass": 0, "warn": 0, "fail": 0, "error": 0}
    for dataset, checks in by_dataset.items():
        print(f"\n=== {dataset} ===")
        for r in checks:
            status = r["status"]
            summary[status] = summary.get(status, 0) + 1
            tag = (_STATUS_COLOR.get(status, "") if color else "") + status.upper().ljust(5) + (_RESET if color else "")
            print(f"  [{tag}] {r['check_name']:50} {r['message']}")
            if r.get("affected_count"):
                print(f"          affected: {r['affected_count']}")
            if r.get("sample"):
                for s in r["sample"][:3]:
                    print(f"          - {s}")
    print()
    print("=" * 60)
    print(f"SUMMARY: pass={summary.get('pass',0)}  warn={summary.get('warn',0)}  "
          f"fail={summary.get('fail',0)}  error={summary.get('error',0)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    color = "--no-color" not in args
    args = [a for a in args if a != "--no-color"]
    if "--json" in args:
        results = run_all()
        json.dump(results, sys.stdout, indent=2, default=str)
        sys.exit(0)
    if args:
        results = []
        for ds in args:
            results.extend(run_for_dataset(ds))
    else:
        results = run_all()
    _print_report(results, color=color)
    # Non-zero exit if any fails or errors
    bad = sum(1 for r in results if r["status"] in ("fail", "error"))
    sys.exit(1 if bad else 0)
