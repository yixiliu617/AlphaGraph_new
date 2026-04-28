"""Empirically measure parse_press_release recall on the existing
earnings_releases parquets. Used before Task 5 ships to confirm the
regex patterns hit the >=95% recall acceptance bar from the spec.

Run:
    python -m backend.scripts.probe_press_release_parser
    python -m backend.scripts.probe_press_release_parser --debug-misses webcast_url
    python -m backend.scripts.probe_press_release_parser --debug-misses dial_in_phone
    python -m backend.scripts.probe_press_release_parser --debug-misses dial_in_pin
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.calendar.enrichment.press_release_parser import (  # noqa: E402
    parse_press_release,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--debug-misses",
        choices=("webcast_url", "dial_in_phone", "dial_in_pin"),
        default=None,
        help="Dump first 5 sample rows where this field is null. Prints first 1500 chars of text_raw.",
    )
    args = ap.parse_args()

    rel_dir = PROJECT_ROOT / "backend" / "data" / "earnings_releases"
    if not rel_dir.exists():
        print("[probe] earnings_releases dir not found")
        return 1

    counts = {"total": 0, "webcast_url": 0, "dial_in_phone": 0, "dial_in_pin": 0}
    misses: list[tuple[str, str]] = []  # (ticker, text_raw)
    for parquet in sorted(rel_dir.glob("ticker=*.parquet")):
        try:
            df = pd.read_parquet(parquet)
        except Exception as exc:
            print(f"[probe] {parquet.name}: read failed: {exc}")
            continue
        if "items" in df.columns:
            df = df[df["items"].astype(str).str.contains("2.02", na=False)]
        if df.empty:
            continue
        ticker = parquet.stem.replace("ticker=", "")
        for _, row in df.iterrows():
            text = str(row.get("text_raw") or "")
            if not text:
                continue
            counts["total"] += 1
            out = parse_press_release(text)
            for k in ("webcast_url", "dial_in_phone", "dial_in_pin"):
                if out[k]:
                    counts[k] += 1
            if args.debug_misses and not out[args.debug_misses] and len(misses) < 5:
                misses.append((ticker, text))

    n = counts["total"]
    print(f"[probe] total Item-2.02 rows scanned: {n}")
    if n == 0:
        return 1
    for k in ("webcast_url", "dial_in_phone", "dial_in_pin"):
        pct = counts[k] / n * 100
        print(f"[probe]   {k:<18} populated: {counts[k]:>4}/{n} ({pct:.1f}%)")

    target = int(0.95 * n)
    ok = all(counts[k] >= int(0.80 * n) for k in ("webcast_url", "dial_in_phone", "dial_in_pin"))
    print(f"[probe] target (>=95%): {target}; achieved ok={ok}")

    if args.debug_misses and misses:
        print(f"\n[probe] === DEBUG MISSES for {args.debug_misses} ===")
        for i, (ticker, text) in enumerate(misses, 1):
            print(f"\n[probe] --- miss #{i} ticker={ticker} (text_raw first 1500 chars) ---")
            print(text[:1500])
            print(f"[probe] --- end miss #{i} ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
