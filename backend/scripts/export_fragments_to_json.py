"""
Export Data Fragments to JSON
==============================

Reads all DataFragments from the SQL database and saves each one as a
pretty-printed JSON file in backend/data/fragment_debug/.

File naming:
  causal_{location}_{fragment_id_short}.json   — for causal/text fragments
  chart_{exhibit_title_short}_{fragment_id_short}.json  — for chart fragments

Deduplicates by (source, exact_location): only the most recently created
fragment is kept when the same source+location appears multiple times
(happens when the pipeline is run repeatedly during development).

Usage:
  python -m backend.scripts.export_fragments_to_json
  python -m backend.scripts.export_fragments_to_json --all   # include duplicates
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.db.session import SessionLocal
from backend.app.models.orm.fragment_orm import FragmentORM

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "fragment_debug"

_UNSAFE = re.compile(r'[^\w\-]')
_WS     = re.compile(r'_+')


def _slug(text: str, max_len: int = 50) -> str:
    s = _UNSAFE.sub('_', str(text).strip())
    s = _WS.sub('_', s).strip('_')
    return s[:max_len]


def export(include_duplicates: bool = False) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    session = SessionLocal()
    try:
        rows = session.query(FragmentORM).order_by(FragmentORM.created_at.asc()).all()
    finally:
        session.close()

    print(f"Found {len(rows)} total fragment(s) in DB.")

    # Deduplicate: keep last (most recent) per (source, exact_location)
    if not include_duplicates:
        seen: dict[tuple, FragmentORM] = {}
        for r in rows:
            key = (r.source, r.exact_location)
            seen[key] = r        # later rows overwrite earlier — keeps most recent
        rows = list(seen.values())
        print(f"After deduplication: {len(rows)} unique fragment(s).")

    # Clear old exports
    for old in OUTPUT_DIR.glob("*.json"):
        old.unlink()

    chart_count  = 0
    causal_count = 0

    for r in rows:
        content = r.content if isinstance(r.content, dict) else json.loads(r.content)
        metrics = content.get("extracted_metrics", {})

        fid_short = str(r.fragment_id)[:8]
        is_chart  = str(r.source).endswith(".png")

        if is_chart:
            title_slug = _slug(metrics.get("chart_title") or r.source, 50)
            filename   = f"chart_{title_slug}_{fid_short}.json"
            chart_count += 1
        else:
            loc_slug = _slug(r.exact_location or "fragment", 30)
            filename  = f"causal_{loc_slug}_{fid_short}.json"
            causal_count += 1

        payload = {
            "fragment_id":         str(r.fragment_id),
            "tenant_id":           r.tenant_id,
            "source_type":         r.source_type,
            "source":              r.source,
            "exact_location":      r.exact_location,
            "reason_for_extraction": r.reason_for_extraction,
            "created_at":          r.created_at.isoformat() if r.created_at else None,
            "raw_text":            content.get("raw_text", ""),
            "extracted_metrics":   metrics,
        }

        out_path = OUTPUT_DIR / filename
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Wrote: {filename}")

    print(f"\nDone. {causal_count} causal + {chart_count} chart = {causal_count + chart_count} files")
    print(f"Output folder: {OUTPUT_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export DB fragments to JSON debug files")
    parser.add_argument("--all", dest="all_duplicates", action="store_true",
                        help="Include duplicate fragments (same source+location from multiple runs)")
    args = parser.parse_args()
    export(include_duplicates=args.all_duplicates)


if __name__ == "__main__":
    main()
