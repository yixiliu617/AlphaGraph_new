"""
Server-side Tiptap document builder.

Mirrors `frontend/src/components/domain/notes/editorSectionBuilder.ts` so the
upload-transcribe path (and any other server flow that produces polished
transcript data) can save a fully-rendered `editor_content` JSON tree
directly to the DB. Previously the frontend `editorSectionBuilder.ts` ran
client-side after the user navigated to the new note; that left a fragile
window where `editor_content` was empty until the auto-rebuild effect ran,
and it required two storage paths to stay in sync (markdown
`polished_transcript` + JSON `editor_content`).

The output of `build_editor_doc_from_polish_meta(...)` is byte-compatible
with the JSON that `editor.getJSON()` would emit on the frontend after
running the same JS builders -- the frontend can render it without any
post-processing, and saving it server-side eliminates the sync bug we hit
on 2026-04-25 (note.id vs note.note_id ate three paid Gemini calls).

Section IDs ("user_notes", "ai_summary", "raw_transcript",
"polished_transcript") are kept identical to the JS side so the
`insertOrReplaceSection` logic in the frontend continues to recognise and
replace them on subsequent regenerate calls.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Pure Tiptap node helpers — every public function returns a plain dict that
# is valid Tiptap JSON. The frontend's editor.setContent({...}) accepts these
# unchanged.
# ---------------------------------------------------------------------------

def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _bold(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "marks": [{"type": "bold"}]}


def _paragraph(text: str = "") -> dict[str, Any]:
    return {
        "type": "paragraph",
        "content": [_text(text)] if text else [],
    }


def _paragraph_nodes(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "paragraph", "content": nodes}


def _heading(level: int, text: str, *, section_id: str | None = None) -> dict[str, Any]:
    attrs: dict[str, Any] = {"level": level}
    if section_id:
        attrs["sectionId"] = section_id
    return {"type": "heading", "attrs": attrs, "content": [_text(text)]}


def _hr() -> dict[str, Any]:
    return {"type": "horizontalRule"}


def _header_cell(text: str) -> dict[str, Any]:
    return {"type": "tableHeader", "content": [_paragraph(text)]}


def _data_cell(text: str) -> dict[str, Any]:
    return {"type": "tableCell", "content": [_paragraph(text)]}


def _table_row(cells: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "tableRow", "content": cells}


def _bullet_list(items: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "type": "bulletList",
        "content": [{"type": "listItem", "content": content} for content in items],
    }


def _ordered_list(items: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "type": "orderedList",
        "content": [{"type": "listItem", "content": content} for content in items],
    }


def _blockquote(text: str) -> dict[str, Any]:
    return {"type": "blockquote", "content": [_paragraph(text)]}


# ---------------------------------------------------------------------------
# Bilingual transcript table — same shape that buildBilingualTableJson emits
# in editorSectionBuilder.ts.
#
# rows: each row supplies `timestamp`, `text_original`, `text_english`.
# bilingual: when True, render a 3-column table; when False, render 2 columns
#            (Time / Text). The polish pipeline sets is_bilingual=True for
#            zh/ja/ko meetings (English translation provided per segment).
# ---------------------------------------------------------------------------

def build_bilingual_table(
    rows: list[dict[str, str]],
    *,
    bilingual: bool,
    translation_label: str = "English",
) -> dict[str, Any]:
    """Build the Tiptap table JSON.

    `translation_label` controls the third column header when bilingual=True
    (e.g. "English", "简体中文", "繁體中文", "日本語", "한국어", "Arabic", "French").
    Defaults to "English" so legacy callers / data without
    translation_label continue to render the same.
    """
    if bilingual:
        header = _table_row([
            _header_cell("Time"),
            _header_cell("原文"),
            _header_cell(translation_label or "English"),
        ])
        body = [
            _table_row([
                _data_cell(r.get("timestamp", "") or ""),
                _data_cell(r.get("text_original", "") or ""),
                _data_cell(r.get("text_english", "") or ""),
            ])
            for r in rows
        ]
    else:
        header = _table_row([_header_cell("Time"), _header_cell("Text")])
        body = [
            _table_row([
                _data_cell(r.get("timestamp", "") or ""),
                _data_cell(r.get("text_original", "") or ""),
            ])
            for r in rows
        ]
    return {"type": "table", "content": [header, *body]}


# ---------------------------------------------------------------------------
# Section composers
# ---------------------------------------------------------------------------

def build_user_notes_heading() -> list[dict[str, Any]]:
    """Just the section heading -- the user's own body is empty initially."""
    return [_heading(2, "Your Notes", section_id="user_notes")]


def build_polished_transcript_section(
    segments: list[dict[str, Any]],
    *,
    is_bilingual: bool,
    translation_label: str = "English",
) -> list[dict[str, Any]]:
    rows = [
        {
            "timestamp":     s.get("timestamp", "") or "",
            "text_original": s.get("text_original", "") or "",
            "text_english":  s.get("text_english", "") or "",
        }
        for s in segments
    ]
    return [
        _hr(),
        _heading(2, "Polished Transcript", section_id="polished_transcript"),
        build_bilingual_table(rows, bilingual=is_bilingual, translation_label=translation_label),
    ]


def build_raw_transcript_section(
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Optional -- only used when there were live transcript lines (recording flow)."""
    finals = [l for l in lines if not l.get("is_interim")]
    is_bilingual = any(
        bool(l.get("translation")) and (l.get("language") or "") and l["language"] != "en"
        for l in finals
    )
    rows = [
        {
            "timestamp":     l.get("timestamp", "") or "",
            "text_original": l.get("text", "") or "",
            "text_english":  l.get("translation", "") or "",
        }
        for l in finals
    ]
    return [
        _hr(),
        _heading(2, "Raw Live Transcript", section_id="raw_transcript"),
        build_bilingual_table(rows, bilingual=is_bilingual),
    ]


def build_ai_summary_section(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Mirror buildAISummarySectionNodes in editorSectionBuilder.ts.

    Each subsection is rendered only when its source field is present.
    Legacy string entries in `all_numbers` (pre-NumberMention refactor) are
    handled identically to the JS version.
    """
    nodes: list[dict[str, Any]] = [
        _hr(),
        _heading(2, "AI Summary", section_id="ai_summary"),
    ]

    storyline = (summary.get("storyline") or "").strip()
    if storyline:
        nodes.append(_heading(3, "Storyline"))
        nodes.append(_paragraph(storyline))

    key_points = summary.get("key_points") or []
    if key_points:
        nodes.append(_heading(3, "Key Points"))
        items: list[list[dict[str, Any]]] = []
        for kp in key_points:
            title = (kp.get("title") or "(untitled)").strip() or "(untitled)"
            item: list[dict[str, Any]] = [_paragraph_nodes([_bold(title)])]
            sub_points = kp.get("sub_points") or []
            if sub_points:
                sub_items: list[list[dict[str, Any]]] = []
                for sp in sub_points:
                    content: list[dict[str, Any]] = [_paragraph((sp.get("text") or ""))]
                    supp = (sp.get("supporting") or "").strip()
                    if supp:
                        content.append(_paragraph(supp))
                    sub_items.append(content)
                item.append(_bullet_list(sub_items))
            items.append(item)
        nodes.append(_ordered_list(items))

    all_numbers = summary.get("all_numbers") or []
    if all_numbers:
        nodes.append(_heading(3, "All Numbers Mentioned"))
        items: list[list[dict[str, Any]]] = []
        for entry in all_numbers:
            if isinstance(entry, str):
                items.append([_paragraph(entry)])
                continue
            label = (entry.get("label") or "").strip()
            value = (entry.get("value") or "").strip()
            label_text = f"{label}: {value}" if label else value
            content: list[dict[str, Any]] = [
                _paragraph_nodes([_bold(label_text or "(untitled)")]),
            ]
            quote = (entry.get("quote") or "").strip()
            if quote:
                content.append(_blockquote(quote))
            items.append(content)
        nodes.append(_bullet_list(items))

    recent = summary.get("recent_updates") or []
    if recent:
        nodes.append(_heading(3, "Recent Updates"))
        nodes.append(_bullet_list([[_paragraph(u)] for u in recent]))

    fm = summary.get("financial_metrics") or {}
    has_fm = bool(fm.get("revenue") or fm.get("profit") or fm.get("orders"))
    if has_fm:
        nodes.append(_heading(3, "Financial Metrics"))
        for label, key in (("Revenue", "revenue"), ("Profit", "profit"), ("Orders", "orders")):
            entries = fm.get(key) or []
            if entries:
                nodes.append(_heading(4, label))
                nodes.append(_bullet_list([[_paragraph(e)] for e in entries]))

    return nodes


# ---------------------------------------------------------------------------
# Top-level: assemble a fresh Tiptap doc from polished-transcript metadata.
# ---------------------------------------------------------------------------

def build_editor_doc_from_polish_meta(
    *,
    segments: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    is_bilingual: bool = False,
    raw_lines: list[dict[str, Any]] | None = None,
    translation_label: str = "English",
) -> dict[str, Any]:
    """Build a Tiptap `doc` JSON ready to assign to `editor_content`.

    The composition order matches what live-v2 + post-meeting-wizard produce
    on the frontend so re-render and re-generate-summary stay idempotent:

        Your Notes (heading only)
        ── (hr)
        AI Summary (only if `summary` has at least one populated field)
        ── (hr)
        Raw Live Transcript (only if `raw_lines` is provided)
        ── (hr)
        Polished Transcript

    If a section's data is empty/None it is skipped entirely (no orphan hr,
    no orphan heading). The resulting doc renders cleanly even when only
    `segments` is provided, which is the upload-transcribe path's case.
    """
    content: list[dict[str, Any]] = list(build_user_notes_heading())

    # Guard "summary is non-trivial": we don't want to render an empty
    # "AI Summary" heading when the upload path passes summary={}.
    if summary and any(
        summary.get(k)
        for k in ("storyline", "key_points", "all_numbers", "recent_updates")
    ) or (summary or {}).get("financial_metrics"):
        content.extend(build_ai_summary_section(summary or {}))

    if raw_lines:
        content.extend(build_raw_transcript_section(raw_lines))

    if segments:
        content.extend(build_polished_transcript_section(
            segments, is_bilingual=is_bilingual, translation_label=translation_label,
        ))

    return {"type": "doc", "content": content}
