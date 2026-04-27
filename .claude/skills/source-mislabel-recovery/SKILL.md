---
name: source-mislabel-recovery
description: Procedure for handling source-side data quality issues at company IR sites — wrong-file-uploaded, period-suffix typos, broken URLs, etc. Use whenever the data-quality framework's `period_continuity` check flags a missing period AND the extractor is known to work for adjacent quarters. Walks through verifying the issue type (extractor bug vs source bug), confirming wrong-upload via cross-source hash, sourcing fallback data from sibling PDFs (Presentation, Press Release, Financial Statements), recording the issue in `_source_issues.json` for UI surfacing, and surfacing a user-facing banner.
version: 1.0
last_validated_at: 2026-04-27
conditions: []
prerequisites: [data-quality-invariants]
tags: [data-quality, recovery, procedure, ir-extraction]
---

# Source Mislabel Recovery Procedure

When `python -m backend.app.services.data_quality.runner` flags a missing period that you can't immediately attribute to an extractor bug, follow this procedure. Every step is mandatory; skipping the verification step is how mis-attributed bugs land in the data.

## The 5-step procedure

### 1. Distinguish: extractor bug vs source bug

Open the source PDF (the one we cached locally for the affected period). Read its title page and any "MediaTek 4Q25 Earnings Call" / "MediaTek 2025-Q4 Investor Conference" type identifier.

- If the PDF's stated period **matches** the period we expected (URL / storage path) → **extractor bug**. The fix is in our code: extend the regex / anchor / period-detection logic. Example: 3Q21 + 4Q22 + 2Q25 in MediaTek's guidance — three different phrasing variants the regex didn't cover.
- If the PDF's stated period **does NOT match** → **source bug** (the company uploaded a wrong file). Continue to step 2.

### 2. Cross-check: confirm wrong-upload via SHA-256

When you suspect a wrong upload, the cheapest confirmation is a hash comparison with another quarter's same-file-type at its OWN URL.

```python
import hashlib
from pathlib import Path
suspected = Path("backend/data/financials/raw/2454.TW/2023/Q3/transcript.pdf")
the_actual = Path("backend/data/financials/raw/2454.TW/2024/Q3/transcript.pdf")
print(hashlib.sha256(suspected.read_bytes()).hexdigest()[:16])
print(hashlib.sha256(the_actual.read_bytes()).hexdigest()[:16])
```

Three outcomes:
- **Identical hashes** → confirmed: company uploaded the same file at both URLs. Document this as an `wrong_file_uploaded_at_source` issue.
- **Different but suspect content** → company uploaded a *different* wrong file (rare). Same workflow, but record `wrong_file_uploaded_at_source` with a note that the duplicate isn't byte-identical.
- **Hashes are distinct AND content reads correctly** → not a source bug after all; revisit step 1.

### 3. Quarantine the bad file locally

Rename the file so the next backfill does not silently re-extract bad content into silver:

```python
from pathlib import Path
bad = Path("backend/data/financials/raw/2454.TW/2023/Q3/transcript.pdf")
bad.rename(bad.with_suffix(".pdf.WRONG_CONTENT_AT_SOURCE_actually_3Q24"))
```

Suffix convention: `.pdf.WRONG_CONTENT_AT_SOURCE_actually_{actual_period}` so a future agent can read what's wrong without opening the file.

### 4. Source fallback data from sibling PDFs

Each company's IR site usually publishes 4-7 PDFs per quarter. When the transcript is bad, the **same forward guidance numbers usually appear in the Presentation deck** (Business Outlook slide). When the press release is bad, the financial statements PDF carries the audited numbers. Cross-reference the company's section in `docs/ir_websites_knowledge_base.md` for the per-company file inventory.

For MediaTek specifically:

| Affected file | Has guidance? | Has financials? | Fallback source |
|---|---|---|---|
| Transcript | ✅ (CFO reads) | partial (revenue + GM) | Presentation slide (Business Outlook page) |
| Press Release | ❌ | ✅ (full P&L table p. 4-5) | Financial Statements PDF |
| Presentation | ✅ (Business Outlook slide) | partial (charts only) | Transcript text |
| Financial Statements | ❌ | ✅ (audited TIFRS) | Press Release table |

Build / use a fallback extractor (e.g. `extract_guidance_from_presentation` for MediaTek lives in `backend/scripts/extractors/mediatek_transcript.py`). The fallback's `source` identifier MUST be distinct from the primary's so the silver layer's dedup keeps both records when they happen to coexist (e.g. `mediatek_presentation_3Q23` vs `mediatek_earnings_call_3Q23`). Downstream consumers can prefer one source via simple ordering.

### 5. Record the issue + surface in UI

Append an entry to the company's `_source_issues.json` (e.g. `backend/data/financials/raw/2454.TW/_source_issues.json`):

```json
{
  "period_label": "3Q23",
  "file_type": "transcript",
  "issue": "wrong_file_uploaded_at_source",
  "detected_on": "2026-04-27",
  "evidence": {
    "url": "https://...",
    "expected_period_in_filename": "2023Q3",
    "actual_period_in_pdf_title": "3Q24",
    "actual_event_date_in_pdf": "October 30, 2024",
    "cross_check": "byte-identical SHA-256 to the file at the 2024Q3 URL — confirmed company uploaded same file at both URLs",
    "sha256": "..."
  },
  "mitigation": {
    "primary": "transcript file quarantined locally as .WRONG_CONTENT_AT_SOURCE_actually_3Q24",
    "guidance_fallback": "guidance for 4Q23 sourced from the 3Q23 Presentation slide deck (Business Outlook, page 11). source identifier: mediatek_presentation_3Q23"
  },
  "user_facing_message": "<one short paragraph the user will see in the UI banner>",
  "recovery_options": [
    "Re-poll the URL periodically — the company may correct it",
    "Source from a third-party archive (SeekingAlpha, LSEG StreetEvents)",
    "Accept the gap; rely on fallback source"
  ]
}
```

Wire the UI to read `_source_issues.json` via the company's `/source-issues` endpoint (or include `source_issues` in the relevant tab's primary endpoint response). Render a yellow `[QUARTER · SOURCE ISSUE]` banner above the affected tab content with the `user_facing_message`.

Reference implementation: MediaTek's `/api/v1/mediatek/transcripts/quarters` endpoint returns `source_issues` alongside `quarters`; the frontend `TranscriptsTab` in `MediaTekPanel.tsx` renders a banner per affected quarter. Pattern can be cloned for any other company that hits the same issue.

## Anti-patterns

- **Don't keep extracting from the bad PDF.** Even if the data quality check passes for a fortuitous reason, you'll get garbage in silver that's near-impossible to spot later. Always quarantine.
- **Don't fabricate the missing data from prior-quarter guidance.** "We extrapolated from 2Q23 + 4Q23" is not extraction; it's interpolation, and it'll be cited as a source by the agent. The fallback source must be a real published document for that quarter.
- **Don't silently fix without recording.** A future agent re-running the extraction will not know what we found. Always append to `_source_issues.json` and the company's section in `docs/ir_websites_knowledge_base.md`.
- **Don't skip the cross-check.** A typo in the PDF title (rare but possible) is structurally different from a wrong-file-uploaded mistake. The fix differs: typo → leave the file in place, document that the title is misleading; wrong-upload → quarantine + fallback.

## When to give up

If the company has uploaded a wrong file AND the sibling PDFs (Presentation, Press Release, Financial Statements) for the same quarter ALSO don't carry the missing data, accept the gap. Record the issue in `_source_issues.json` with `mitigation: {"primary": "no fallback available — gap accepted"}`. The period-continuity check will continue to flag this period; that's working as intended (the gap is real, surfaced to users via the banner).

## Companion check

The `period_continuity` check in `backend/app/services/data_quality/checks.py` is what catches these in the first place. Run it on every extraction backfill:

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=. python -m backend.app.services.data_quality.runner mediatek.guidance
```

A `fail` with `missing: ['3Q23']` is your trigger to start step 1 of this procedure.
