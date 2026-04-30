"""CLI: audit a batch-folder transcript run.

Usage:
    PYTHONPATH=. python -m backend.scripts.audit_batch_transcripts <folder> [--json]

Walks every audio/video source in <folder>, checks for matching opus + docx,
verifies sizes are reasonable, parses [MM:SS] timestamps in each .docx to
detect missing-tail or mid-stream gaps, and prints a markdown report.

Exit code:
    0 -- all files complete
    1 -- one or more files have issues (missing transcript, gaps, etc.)
"""
from __future__ import annotations

import argparse
import json
import sys

from backend.app.services.notes.batch_audit import (
    audit_folder, format_audit_report, report_to_dict,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a batch-folder transcript run.")
    parser.add_argument("folder", help="Folder that was processed by /batch-transcribe-folder")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of markdown")
    parser.add_argument("--save", action="store_true",
                        help="Also save the markdown report to <folder>/batch_report.md")
    args = parser.parse_args()

    try:
        report = audit_folder(args.folder)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        md = format_audit_report(report)
        print(md)
        if args.save:
            from pathlib import Path
            out = Path(args.folder) / "batch_report.md"
            out.write_text(md, encoding="utf-8")
            print(f"\n(report saved to {out})", file=sys.stderr)

    return 0 if report.summary.get("with_issues", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
