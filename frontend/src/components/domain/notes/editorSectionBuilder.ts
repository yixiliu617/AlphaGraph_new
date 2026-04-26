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
import type {
  TranscriptLine,
  PolishedSegment,
  MeetingSummary,
  NumberMention,
} from "@/lib/api/notesClient";
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
 * If `bilingual` is true, renders 3 columns: Time | 原文 | <translationLabel>.
 * Otherwise 2 columns: Time | Text.
 *
 * `translationLabel` defaults to "English" so legacy notes whose meta
 * doesn't carry it render exactly as before.
 */
export function buildBilingualTableJson(
  rows: { timestamp: string; textOriginal: string; textEnglish: string }[],
  bilingual: boolean,
  translationLabel: string = "English",
): Json {
  const header = bilingual
    ? row([headerCell("Time"), headerCell("原文"), headerCell(translationLabel || "English")])
    : row([headerCell("Time"), headerCell("Text")]);

  const bodyRows = rows.map((r) =>
    bilingual
      ? row([dataCell(r.timestamp), dataCell(r.textOriginal), dataCell(r.textEnglish)])
      : row([dataCell(r.timestamp), dataCell(r.textOriginal)]),
  );

  return { type: "table", content: [header, ...bodyRows] };
}

// ---------------------------------------------------------------------------
// AI summary helpers
// ---------------------------------------------------------------------------

function heading3(text: string): Json {
  return {
    type: "heading",
    attrs: { level: 3 },
    content: [textNode(text)],
  };
}

function heading4(text: string): Json {
  return {
    type: "heading",
    attrs: { level: 4 },
    content: [textNode(text)],
  };
}

function boldText(text: string): Json {
  return { type: "text", text, marks: [{ type: "bold" }] };
}

function bulletList(items: Json[][]): Json {
  return {
    type: "bulletList",
    content: items.map((content) => ({
      type: "listItem",
      content,
    })),
  };
}

function orderedList(items: Json[][]): Json {
  return {
    type: "orderedList",
    content: items.map((content) => ({
      type: "listItem",
      content,
    })),
  };
}

function simpleBullet(text: string): Json[] {
  return [paragraph(text)];
}

/**
 * Build the nodes for the AI Summary section, rendered between the user's
 * notes and the raw live transcript. Expects a MeetingSummary object as
 * produced by the Gemini polish call; every field is optional and missing
 * ones are simply omitted from the rendered output.
 */
export function buildAISummarySectionNodes(summary: MeetingSummary): Json[] {
  const nodes: Json[] = [
    horizontalRule(),
    sectionHeading("ai_summary", "AI Summary"),
  ];

  // Storyline
  if (summary.storyline && summary.storyline.trim()) {
    nodes.push(heading3("Storyline"));
    nodes.push(paragraph(summary.storyline.trim()));
  }

  // Key Points — ordered list; each item has the title paragraph followed
  // by a nested bullet list of sub-points (text in bold + supporting below).
  if (summary.key_points && summary.key_points.length > 0) {
    nodes.push(heading3("Key Points"));
    const items: Json[][] = summary.key_points.map((kp) => {
      const item: Json[] = [
        {
          type: "paragraph",
          content: [boldText(kp.title || "(untitled)")],
        },
      ];
      if (kp.sub_points && kp.sub_points.length > 0) {
        const subItems: Json[][] = kp.sub_points.map((sp) => {
          const content: Json[] = [paragraph(sp.text || "")];
          if (sp.supporting && sp.supporting.trim()) {
            content.push(paragraph(sp.supporting.trim()));
          }
          return content;
        });
        item.push(bulletList(subItems));
      }
      return item;
    });
    nodes.push(orderedList(items));
  }

  // All numbers — new structured form: bold "label: value" line + blockquote
  // of the verbatim transcript sentence. Legacy string entries (from notes
  // written before the NumberMention refactor) are rendered as a single
  // paragraph with just the value.
  if (summary.all_numbers && summary.all_numbers.length > 0) {
    nodes.push(heading3("All Numbers Mentioned"));
    const items: Json[][] = (summary.all_numbers as Array<string | NumberMention>).map((entry) => {
      // Legacy plain-string form.
      if (typeof entry === "string") {
        return [paragraph(entry)];
      }
      // New structured form: { label, value, quote }.
      const n = entry as NumberMention;
      const labelText = n.label ? `${n.label}: ${n.value}` : n.value;
      const headerPara: Json = {
        type: "paragraph",
        content: [boldText(labelText || "(untitled)")],
      };
      const content: Json[] = [headerPara];
      if (n.quote && n.quote.trim()) {
        content.push({
          type: "blockquote",
          content: [paragraph(n.quote.trim())],
        });
      }
      return content;
    });
    nodes.push(bulletList(items));
  }

  // Recent updates
  if (summary.recent_updates && summary.recent_updates.length > 0) {
    nodes.push(heading3("Recent Updates"));
    nodes.push(bulletList(summary.recent_updates.map((u) => simpleBullet(u))));
  }

  // Financial metrics — three labelled sub-sections when present
  const fm = summary.financial_metrics;
  const hasFM = fm && (
    (fm.revenue?.length ?? 0) > 0 ||
    (fm.profit?.length ?? 0) > 0 ||
    (fm.orders?.length ?? 0) > 0
  );
  if (hasFM) {
    nodes.push(heading3("Financial Metrics"));
    if (fm.revenue && fm.revenue.length > 0) {
      nodes.push(heading4("Revenue"));
      nodes.push(bulletList(fm.revenue.map((x) => simpleBullet(x))));
    }
    if (fm.profit && fm.profit.length > 0) {
      nodes.push(heading4("Profit"));
      nodes.push(bulletList(fm.profit.map((x) => simpleBullet(x))));
    }
    if (fm.orders && fm.orders.length > 0) {
      nodes.push(heading4("Orders"));
      nodes.push(bulletList(fm.orders.map((x) => simpleBullet(x))));
    }
  }

  return nodes;
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
  translationLabel: string = "English",
): Json[] {
  const rows = segments.map((s) => ({
    timestamp: s.timestamp,
    textOriginal: s.text_original,
    textEnglish: s.text_english,
  }));

  return [
    horizontalRule(),
    sectionHeading("polished_transcript", "Polished Transcript"),
    buildBilingualTableJson(rows, bilingual, translationLabel),
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
  // emitUpdate=true so the editor's onUpdate fires → container's
  // handleContentChange → patchNote → isDirty → auto-save. Without this, the
  // replacement is visible on screen but never reaches the DB, so reopening
  // the note shows the pre-replace content.
  editor.commands.setContent({ ...doc, content: newContent } as Json, true);
}
