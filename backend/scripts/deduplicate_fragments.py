"""
Fragment Deduplication Tool
============================

Does three things in one pass:

  1. MIGRATE  — adds the `content_fingerprint` column to the DB if it doesn't
                exist yet (safe to run multiple times; skips if already present).

  2. BACKFILL — computes and stores fingerprints for every existing row that
                doesn't have one.

  3. DEDUPLICATE — within each fingerprint group, keeps the NEWEST fragment and
                   deletes all older copies.

Fingerprint key: SHA-256("{tenant_id}:{source_document_id}:{exact_location}")
  · source_document_id comes from extracted_metrics (UUID5 seeded from PDF
    filename — stable across runs).
  · exact_location is the page range ("pp. 1-3") or chart page ("p. 3").
  · This key correctly deduplicates across filename renames (old PNG naming
    vs. new {title}_{broker}_{date}.png) because both share the same
    source_document_id.

Usage:
  python -m backend.scripts.deduplicate_fragments          # dry-run (shows what would be deleted)
  python -m backend.scripts.deduplicate_fragments --apply  # actually deletes duplicates
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from backend.app.db.session import SessionLocal, engine
from backend.app.models.orm.fragment_orm import FragmentORM
from backend.app.adapters.db.postgres_adapter import compute_fragment_fingerprint


# ---------------------------------------------------------------------------
# Step 1 — Column migration
# ---------------------------------------------------------------------------

def _ensure_fingerprint_column() -> None:
    """Adds content_fingerprint column if it doesn't exist. No-op if present."""
    with engine.connect() as conn:
        db_url = str(engine.url)
        if db_url.startswith("sqlite"):
            # SQLite: check via PRAGMA
            result = conn.execute(text("PRAGMA table_info(data_fragments)")).fetchall()
            columns = [row[1] for row in result]
            if "content_fingerprint" not in columns:
                conn.execute(text(
                    "ALTER TABLE data_fragments ADD COLUMN content_fingerprint TEXT"
                ))
                conn.commit()
                print("[migrate] Added content_fingerprint column (SQLite).")
            else:
                print("[migrate] content_fingerprint column already exists.")
        else:
            # PostgreSQL
            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'data_fragments'
                  AND column_name = 'content_fingerprint'
            """)).fetchone()
            if not result:
                conn.execute(text(
                    "ALTER TABLE data_fragments ADD COLUMN content_fingerprint TEXT"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_data_fragments_content_fingerprint "
                    "ON data_fragments (content_fingerprint)"
                ))
                conn.commit()
                print("[migrate] Added content_fingerprint column + index (Postgres).")
            else:
                print("[migrate] content_fingerprint column already exists.")


# ---------------------------------------------------------------------------
# Step 2 — Backfill fingerprints
# ---------------------------------------------------------------------------

def _backfill_fingerprints(session) -> int:
    """Computes and saves fingerprints for rows that don't have one. Returns count."""
    rows = session.query(FragmentORM).filter(
        FragmentORM.content_fingerprint == None  # noqa: E711
    ).all()

    updated = 0
    for r in rows:
        content = r.content if isinstance(r.content, dict) else json.loads(r.content)
        metrics = content.get("extracted_metrics", {})
        source_doc_id = metrics.get("source_document_id") or r.source
        fp = compute_fragment_fingerprint(r.tenant_id, source_doc_id, r.exact_location)
        r.content_fingerprint = fp
        updated += 1

    if updated:
        session.commit()
    print(f"[backfill] Computed fingerprints for {updated} row(s).")
    return updated


# ---------------------------------------------------------------------------
# Step 3 — Deduplicate
# ---------------------------------------------------------------------------

def _find_duplicates(session) -> dict[str, list[FragmentORM]]:
    """
    Groups all rows by fingerprint. Returns groups with more than one member.
    Within each group, rows are sorted newest-first (index 0 = keeper).
    """
    rows = session.query(FragmentORM).order_by(FragmentORM.created_at.desc()).all()
    groups: dict[str, list[FragmentORM]] = defaultdict(list)
    for r in rows:
        fp = r.content_fingerprint or "NO_FINGERPRINT"
        groups[fp].append(r)
    return {fp: frags for fp, frags in groups.items() if len(frags) > 1}


def _deduplicate(session, apply: bool) -> tuple[int, int]:
    """
    Removes duplicate rows (keeps newest per fingerprint group).
    Returns (groups_found, rows_deleted).
    """
    dup_groups = _find_duplicates(session)
    total_to_delete = sum(len(v) - 1 for v in dup_groups.values())

    if not dup_groups:
        print("[dedup] No duplicates found.")
        return 0, 0

    print(f"\n[dedup] Found {len(dup_groups)} duplicate group(s), {total_to_delete} row(s) to delete.")
    print(f"        {'DRY RUN — pass --apply to delete' if not apply else 'APPLYING DELETIONS'}")
    print()

    deleted = 0
    for fp, frags in sorted(dup_groups.items()):
        keeper = frags[0]   # newest (list is sorted newest-first)
        dupes  = frags[1:]  # older copies

        # Determine a readable label for the group
        content = keeper.content if isinstance(keeper.content, dict) else json.loads(keeper.content)
        metrics = content.get("extracted_metrics", {})
        label = (
            metrics.get("chart_title")
            or metrics.get("source_pdf_filename")
            or keeper.source
        )
        print(f"  Group: {keeper.exact_location} | {label[:70]}")
        print(f"    KEEP  : {keeper.fragment_id}  (created {keeper.created_at})")
        for d in dupes:
            print(f"    DELETE: {d.fragment_id}  (created {d.created_at})")
            if apply:
                session.delete(d)
                deleted += 1
        print()

    if apply:
        session.commit()
        print(f"[dedup] Deleted {deleted} duplicate row(s).")
    else:
        print(f"[dedup] Dry run complete. {total_to_delete} row(s) would be deleted.")
        print("        Re-run with --apply to perform the deletion.")

    return len(dup_groups), deleted if apply else total_to_delete


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(session) -> None:
    total = session.query(FragmentORM).count()
    chart  = session.query(FragmentORM).filter(FragmentORM.source.like("%.png")).count()
    causal = total - chart
    print(f"\n[summary] DB now contains {total} fragment(s): {causal} causal, {chart} chart.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaGraph fragment deduplication tool")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually delete duplicate rows (default: dry run, just report)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("AlphaGraph Fragment Deduplication")
    print("=" * 60)

    # Step 1 — ensure column exists
    _ensure_fingerprint_column()

    session = SessionLocal()
    try:
        # Step 2 — backfill fingerprints
        _backfill_fingerprints(session)

        # Step 3 — deduplicate
        _deduplicate(session, apply=args.apply)

        # Summary
        _print_summary(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
