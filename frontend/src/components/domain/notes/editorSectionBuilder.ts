/**
 * editorSectionBuilder — TipTap JSON builders + insert-or-replace helper for
 * the three auto-generated sections that Save Both adds to the main editor:
 *
 *   - "user_notes"           — heading + divider (content is the user's own
 *                              notes; we never overwrite the body, only ensure
 *                              the heading/divider exist at the top)
 *   - "raw_transcript"       — heading + divider + bilingual/monolingual table
 *   - "polished_transcript"  — heading + divider + bilingual/monolingual table
 *
 * Section identity lives on the <h2> via the `sectionId` attr (see
 * sectionHeadingExtension.ts). Insertion logic walks the top-level doc
 * looking for that sectionId; if found, it replaces every node from the
 * matched heading up to (but not including) the next section heading — so
 * the user's edits *outside* that range are preserved. If not found, the
 * helper appends the section to the end of the document.
 */

import type { Editor } from "@tiptap/react";
import type { TranscriptLine, PolishedSegment } from "@/lib/api/notesClient";
import type { SectionId } from "./sectionHeadingExtension";

type Json = Record<string, unknown>;

// ---------------------------------------------------------------------------
// TipTap JSON builders (pure)
// ---------------------------------------------------------------------------

function textNode(text: string): Json {
  return { type: "text", text };
}

function paragraph(text: string): Json {
  return { type: "paragraph", content: text ? [textNode(text)] : [] };
}

function headerCell(text: string): Json {
  return {
    type: "tableHeader",
    content: [paragraph(text)],
  };
}

function dataCell(text: string): Json {
  return {
    type: "tableCell",
    content: [paragraph(text)],
  };
}

function row(cells: Json[]): Json {
  return { type: "tableRow", content: cells };
}

function sectionHeading(sectionId: SectionId, text: string): Json {
  return {
    type: "heading",
    attrs: { level: 2, sectionId },
    content: [textNode(text)],
  };
}

function horizontalRule(): Json {
  return { type: "horizontalRule" };
}

/**
 * Build the TipTap table JSON for a bilingual transcript.
 * If `bilingual` is true, renders 3 columns: Time | Original | English.
 * Otherwise 2 columns: Time | Text.
 */
export function buildBilingualTableJson(
  rows: { timestamp: string; textOriginal: string; textEnglish: string }[],
  bilingual: boolean,
): Json {
  const header = bilingual
    ? row([headerCell("Time"), headerCell("原文"), headerCell("English")])
    : row([headerCell("Time"), headerCell("Text")]);

  const bodyRows = rows.map((r) =>
    bilingual
      ? row([dataCell(r.timestamp), dataCell(r.textOriginal), dataCell(r.textEnglish)])
      : row([dataCell(r.timestamp), dataCell(r.textOriginal)]),
  );

  return { type: "table", content: [header, ...bodyRows] };
}

/**
 * Build the nodes for the raw-transcript section (heading + hr + table).
 * Expects `lines` in the shape the WebSocket + DB carry.
 */
export function buildRawTranscriptSectionNodes(lines: TranscriptLine[]): Json[] {
  // Skip interim lines — only final ones get persisted as part of the editor.
  const finalLines = lines.filter((l) => !l.is_interim);

  // Detect bilingual: any line carries a non-empty `translation` and a non-English language.
  // Access via a loose cast because the core TranscriptLine type doesn't declare these two
  // optional fields today (they are added at runtime by the live_v2 transcript messages).
  const loose = finalLines as unknown as {
    translation?: string;
    language?: string;
    timestamp: string;
    text: string;
  }[];
  const bilingual = loose.some((l) => Boolean(l.translation) && l.language && l.language !== "en");

  const rows = loose.map((l) => ({
    timestamp: l.timestamp,
    textOriginal: l.text ?? "",
    textEnglish: l.translation ?? "",
  }));

  return [
    horizontalRule(),
    sectionHeading("raw_transcript", "Raw Live Transcript"),
    buildBilingualTableJson(rows, bilingual),
  ];
}

/** Build the nodes for the polished-transcript section. */
export function buildPolishedTranscriptSectionNodes(
  segments: PolishedSegment[],
  bilingual: boolean,
): Json[] {
  const rows = segments.map((s) => ({
    timestamp: s.timestamp,
    textOriginal: s.text_original,
    textEnglish: s.text_english,
  }));

  return [
    horizontalRule(),
    sectionHeading("polished_transcript", "Polished Transcript"),
    buildBilingualTableJson(rows, bilingual),
  ];
}

/**
 * Build a minimal "Your Notes" heading. We do NOT include the body — the
 * user's own content stays untouched. This is used only when no user_notes
 * heading exists yet (first-ever insert); insertOrReplaceSection then places
 * this heading at the very top of the document.
 */
export function buildUserNotesHeadingNodes(): Json[] {
  return [sectionHeading("user_notes", "Your Notes")];
}

// ---------------------------------------------------------------------------
// Insert or replace logic
// ---------------------------------------------------------------------------

/**
 * Find the index (in `doc.content`) of the heading node carrying sectionId.
 * Returns -1 if none found.
 */
function findSectionIndex(doc: Json, sectionId: SectionId): number {
  const content = (doc.content as Json[] | undefined) ?? [];
  return content.findIndex((node) => {
    if (node?.type !== "heading") return false;
    const attrs = node.attrs as Json | undefined;
    return attrs?.sectionId === sectionId;
  });
}

/**
 * Find the index of the next section heading after `fromIndex` (exclusive).
 * Returns `content.length` if none found — meaning the current section runs
 * to the end of the document.
 */
function findNextSectionIndex(doc: Json, fromIndex: number): number {
  const content = (doc.content as Json[] | undefined) ?? [];
  for (let i = fromIndex + 1; i < content.length; i++) {
    const node = content[i];
    if (node?.type === "heading") {
      const attrs = node.attrs as Json | undefined;
      if (attrs?.sectionId) return i;
    }
  }
  return content.length;
}

/**
 * Replace or append the nodes belonging to `sectionId`. `nodes` should include
 * the section heading itself plus all following content for that section
 * (typically an hr in front, heading, then a table).
 *
 * Special case: `sectionId === "user_notes"` does NOT overwrite existing user
 * content — it only prepends the heading if no user_notes heading is found.
 */
export function insertOrReplaceSection(
  editor: Editor,
  sectionId: SectionId,
  nodes: Json[],
): void {
  const doc = editor.getJSON() as Json;
  const content = ((doc.content as Json[] | undefined) ?? []).slice();

  const matchIndex = findSectionIndex(doc, sectionId);

  if (sectionId === "user_notes") {
    // Never overwrite user's body. If heading missing, prepend just the heading.
    if (matchIndex === -1) {
      const newContent = [...nodes, ...content];
      editor.commands.setContent({ ...doc, content: newContent } as Json, true);
    }
    return;
  }

  if (matchIndex === -1) {
    // No existing section — append to end.
    const newContent = [...content, ...nodes];
    editor.commands.setContent({ ...doc, content: newContent } as Json, true);
    return;
  }

  // Replace from matchIndex up to (but not including) the next section heading.
  // We also drop the preceding <hr> if it was inserted by a previous call
  // (matchIndex - 1 is an hr we own), so the new hr in `nodes` takes its place.
  let start = matchIndex;
  if (matchIndex > 0 && content[matchIndex - 1]?.type === "horizontalRule") {
    start = matchIndex - 1;
  }
  const end = findNextSectionIndex(doc, matchIndex);

  const newContent = [...content.slice(0, start), ...nodes, ...content.slice(end)];
  editor.commands.setContent({ ...doc, content: newContent } as Json, false);
}
