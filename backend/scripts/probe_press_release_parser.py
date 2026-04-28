"""Empirically measure parse_press_release recall on the existing
earnings_releases parquets. Used before Task 5 ships to confirm the
regex patterns hit the >=95% recall acceptance bar from the spec.

Run:
    python -m backend.scripts.probe_press_release_parser
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.calendar.enrichment.press_release_parser import (  # noqa: E402
    parse_press_release,
)


def main() -> int:
    rel_dir = PROJECT_ROOT / "backend" / "data" / "earnings_releases"
    if not rel_dir.exists():
        print("[probe] earnings_releases dir not found")
        return 1

    counts = {"total": 0, "webcast_url": 0, "dial_in_phone": 0, "dial_in_pin": 0}
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
        for _, row in df.iterrows():
            text = str(row.get("text_raw") or "")
            if not text:
                continue
            counts["total"] += 1
            out = parse_press_release(text)
            for k in ("webcast_url", "dial_in_phone", "dial_in_pin"):
                if out[k]:
                    counts[k] += 1

    n = counts["total"]
    print(f"[probe] total Item-2.02 rows scanned: {n}")
    if n == 0:
        return 1
    for k in ("webcast_url", "dial_in_phone", "dial_in_pin"):
        pct = counts[k] / n * 100
        print(f"[probe]   {k:<18} populated: {counts[k]:>4}/{n} ({pct:.1f}%)")

    target = int(0.95 * n)
    ok = all(counts[k] >= int(0.80 * n) for k in ("webcast_url", "dial_in_phone", "dial_in_pin"))
    print(f"[probe] target (>=95%): {target}; achieved {ok=}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
