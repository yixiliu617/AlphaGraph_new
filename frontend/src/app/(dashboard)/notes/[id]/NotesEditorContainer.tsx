"use client";

// ---------------------------------------------------------------------------
// NotesEditorContainer — SMART layer for the note detail/editor page.
// Fetches the note, handles auto-save, coordinates wizard state.
// ---------------------------------------------------------------------------

import { useEffect, useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { Editor } from "@tiptap/react";
import { notesClient, type PolishedSegment, type TranscriptLine, type MeetingSummary } from "@/lib/api/notesClient";
import { useNotesStore } from "@/store/useNotesStore";
import { useNoteEditorStore } from "./store";
import NotesEditorView from "./NotesEditorView";
import {
  buildRawTranscriptSectionNodes,
  buildPolishedTranscriptSectionNodes,
  buildUserNotesHeadingNodes,
  buildAISummarySectionNodes,
  insertOrReplaceSection,
} from "@/components/domain/notes/editorSectionBuilder";

const AUTO_SAVE_DELAY_MS = 1500;

interface Props {
  noteId: string;
}

export default function NotesEditorContainer({ noteId }: Props) {
  const router = useRouter();
  const { updateNote } = useNotesStore();
  const {
    note, isSaving, isDirty, showRecordingPopup, showUrlIngestModal,
    setNote, clearNote, setSaving, setDirty, setShowRecordingPopup,
    setShowUrlIngestModal, patchNote,
  } = useNoteEditorStore();

  // Auto-save timer
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // TipTap editor instance (populated via RichTextEditor's onEditorReady callback).
  const editorRef = useRef<Editor | null>(null);
  // Tracks which note IDs we've already auto-rebuilt — prevents the effect
  // from running again on subsequent note edits / re-renders. Used so a note
  // landing in /notes/<id> with polished_transcript_meta.segments populated
  // but editor_content still the empty default doc gets its sections drawn
  // exactly once on first mount. Affects the upload-transcribe flow where
  // the backend persists segments/summary but doesn't itself build Tiptap JSON.
  const autoRebuiltFor = useRef<Set<string>>(new Set());
  const [editorReadyTick, setEditorReadyTick] = useState(0);
  const handleEditorReady = useCallback((editor: Editor) => {
    editorRef.current = editor;
    // Bump a state value so the auto-rebuild effect can react when both the
    // editor instance and the note are available (refs don't trigger renders).
    setEditorReadyTick((n) => n + 1);
  }, []);

  // Load note on mount / noteId change — clear first so stale note never shows
  useEffect(() => {
    clearNote();
    notesClient.get(noteId).then((res) => {
      if (res.success && res.data) setNote(res.data);
    });
  }, [noteId, setNote, clearNote]);

  // Auto-build editor sections from saved polished data once both the note
  // and the editor are mounted, IF the editor content is empty but the note
  // already has segments saved (the upload-transcribe path leaves it like
  // this). One-shot per note ID so user edits aren't clobbered on re-render.
  useEffect(() => {
    if (!note || !editorRef.current) return;
    if (autoRebuiltFor.current.has(note.note_id)) return;

    const meta = note.polished_transcript_meta;
    const segments = (meta?.segments ?? []) as PolishedSegment[];
    const summary  = (meta?.summary  ?? null) as MeetingSummary | null;
    if (segments.length === 0 && !summary) return;

    // Detect empty editor: editor_content is `{type: "doc", content: []}` (or missing).
    const ec = note.editor_content as { content?: unknown[] } | null | undefined;
    const editorIsEmpty = !ec || !Array.isArray(ec.content) || ec.content.length === 0;
    if (!editorIsEmpty) return;

    autoRebuiltFor.current.add(note.note_id);

    const editor = editorRef.current;
    const isBilingual = Boolean(meta?.is_bilingual);
    const lines: TranscriptLine[] = segments.map((s, idx) => ({
      line_id: idx + 1,
      timestamp: s.timestamp,
      speaker_label: s.speaker || "",
      speaker_name: null,
      text: s.text_original,
      is_flagged: false,
      is_interim: false,
    }));
    insertOrReplaceSection(editor, "user_notes", buildUserNotesHeadingNodes());
    if (summary) {
      insertOrReplaceSection(editor, "ai_summary", buildAISummarySectionNodes(summary));
    }
    if (lines.length > 0) {
      insertOrReplaceSection(editor, "raw_transcript", buildRawTranscriptSectionNodes(lines));
    }
    if (segments.length > 0) {
      insertOrReplaceSection(
        editor,
        "polished_transcript",
        buildPolishedTranscriptSectionNodes(segments, isBilingual, (meta?.translation_label) || "English"),
      );
    }
    // Mark dirty so the just-built editor_content gets persisted.
    setDirty(true);
  }, [note, editorReadyTick, setDirty]);

  // Core save — called by both the debounced auto-save and the Ctrl+S
  // force-save. Sends every editable field; the backend patches whatever
  // is present. Using a ref so the keybinding effect below doesn't have to
  // re-register on every note change.
  const runSave = useCallback(async () => {
    if (!note) return;
    setSaving(true);
    const res = await notesClient.update(note.note_id, {
      title: note.title,
      note_type: note.note_type,
      editor_content: note.editor_content,
      editor_plain_text: note.editor_plain_text,
      company_tickers: note.company_tickers,
      meeting_date: note.meeting_date ?? undefined,
    });
    if (res.success && res.data) {
      updateNote(res.data);
      setDirty(false);
    }
    setSaving(false);
  }, [note, setSaving, updateNote, setDirty]);

  // Auto-save whenever the note is dirty (debounced).
  const scheduleSave = useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(runSave, AUTO_SAVE_DELAY_MS);
  }, [runSave]);

  useEffect(() => {
    if (isDirty) scheduleSave();
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [isDirty, scheduleSave]);

  // Ctrl+S / Cmd+S force-save — flushes the pending debounced save
  // immediately, also fires when the note is clean (cheap no-op PUT that
  // gives the user the visual "Saved" confirmation).
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        if (saveTimer.current) {
          clearTimeout(saveTimer.current);
          saveTimer.current = null;
        }
        runSave();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [runSave]);

  // Editor content change
  const handleContentChange = useCallback(
    (json: Record<string, unknown>, plainText: string) => {
      patchNote({ editor_content: json, editor_plain_text: plainText });
    },
    [patchNote]
  );

  const handleTitleChange = useCallback(
    (title: string) => patchNote({ title }),
    [patchNote]
  );

  // Notion-style header edits — all route through patchNote → isDirty → scheduleSave.
  const handleMeetingDateChange = useCallback(
    (date: string | null) => patchNote({ meeting_date: date }),
    [patchNote]
  );
  const handleTickersChange = useCallback(
    (tickers: string[]) => patchNote({ company_tickers: tickers }),
    [patchNote]
  );
  const handleNoteTypeChange = useCallback(
    (noteType: string) => patchNote({ note_type: noteType }),
    [patchNote]
  );

  // Delta wizard: approve/edit/dismiss
  const handleDelta = useCallback(
    async (deltaId: string, action: "approve" | "edit" | "dismiss", editedText?: string) => {
      if (!note) return;
      const res = await notesClient.processDelta(note.note_id, deltaId, action, editedText);
      if (res.success && res.data) {
        setNote(res.data);
        updateNote(res.data);
      }
    },
    [note, setNote, updateNote]
  );

  // Speaker step
  const handleSaveSpeakers = useCallback(
    async (mappings: { label: string; name: string; role?: string }[]) => {
      if (!note) return;
      const res = await notesClient.saveSpeakers(note.note_id, mappings);
      if (res.success && res.data) { setNote(res.data); updateNote(res.data); }
    },
    [note, setNote, updateNote]
  );

  // Topic extraction step
  const handleExtractTopics = useCallback(
    async (topics: string[]) => {
      if (!note) return;
      const res = await notesClient.extractTopics(note.note_id, topics);
      if (res.success && res.data) { setNote(res.data); updateNote(res.data); }
    },
    [note, setNote, updateNote]
  );

  // Start AI summary without recording — jump straight to topic selection
  const handleStartAISummary = useCallback(() => {
    patchNote({ summary_status: "awaiting_topics" });
  }, [patchNote]);

  // Unstick a note that's sitting in the legacy AWAITING_APPROVAL state
  // (the deprecated delta-vs-previous review step).
  const handleMarkComplete = useCallback(async () => {
    if (!note) return;
    const res = await notesClient.markSummaryComplete(note.note_id);
    if (res.success && res.data) {
      setNote(res.data);
      updateNote(res.data);
    }
  }, [note, setNote, updateNote]);

  // After recording stops: persist transcript lines server-side so the wizard
  // and AI-analysis modules can read them from the DB, then update local state,
  // then auto-insert the three sections (user_notes / raw / polished) into
  // the main editor so the user can see their recording output in place.
  const handleRecordingComplete = useCallback(
    async (
      lines: TranscriptLine[],
      durationSeconds: number,
      polished: {
        segments: PolishedSegment[];
        language: string;
        is_bilingual: boolean;
        key_topics: string[];
        summary: MeetingSummary | null;
      } | null,
    ) => {
      if (!note) return;

      const res = await notesClient.saveTranscript(note.note_id, lines, durationSeconds);
      if (res.success && res.data) {
        setNote(res.data);
        updateNote(res.data);
      } else {
        patchNote({
          transcript_lines: lines,
          duration_seconds: durationSeconds,
          summary_status: "complete",
        });
      }

      // Auto-insert the four sections into the main editor (both variants):
      // user notes (heading only) -> AI summary -> raw transcript -> polished.
      // insertOrReplaceSection appends new sections in call order, so the
      // resulting layout matches the order of these calls.
      if (editorRef.current) {
        const editor = editorRef.current;
        insertOrReplaceSection(editor, "user_notes", buildUserNotesHeadingNodes());
        if (polished && polished.summary) {
          insertOrReplaceSection(
            editor,
            "ai_summary",
            buildAISummarySectionNodes(polished.summary),
          );
        }
        insertOrReplaceSection(
          editor,
          "raw_transcript",
          buildRawTranscriptSectionNodes(lines),
        );
        if (polished && polished.segments.length > 0) {
          insertOrReplaceSection(
            editor,
            "polished_transcript",
            buildPolishedTranscriptSectionNodes(polished.segments, polished.is_bilingual, (note?.polished_transcript_meta?.translation_label) || "English"),
          );
        }
      }

      setShowRecordingPopup(false);
    },
    [note, setNote, updateNote, patchNote, setShowRecordingPopup]
  );

  // Re-run the text-only summary Gemini call against the note's existing
  // transcript segments. Cheap (~$0.001-0.01, no audio). After the backend
  // updates polished_transcript_meta.summary, pull the fresh note and
  // regenerate the editor sections from it.
  const [isRegeneratingSummary, setIsRegeneratingSummary] = useState(false);
  const [isRetranscribing, setIsRetranscribing] = useState(false);
  const [isConvertingChinese, setIsConvertingChinese] = useState(false);

  // Toggle the note's Chinese script variant via local zhconv (no LLM cost).
  // Backend updates segments + key_topics + editor_content + markdown in one
  // call; we then refresh the note state and rebuild the editor sections.
  const handleConvertChinese = useCallback(async (to: "hans" | "hant") => {
    if (!note) return;
    setIsConvertingChinese(true);
    try {
      const res = await notesClient.convertChineseVariant(note.note_id, to);
      if (res.success && res.data) {
        const fresh = await notesClient.get(note.note_id);
        if (fresh.success && fresh.data) {
          setNote(fresh.data);
          updateNote(fresh.data);
          if (editorRef.current) {
            const editor = editorRef.current;
            const meta = fresh.data.polished_transcript_meta;
            const segments = (meta?.segments ?? []) as PolishedSegment[];
            const summary  = (meta?.summary  ?? null) as MeetingSummary | null;
            const isBilingual = Boolean(meta?.is_bilingual);
            insertOrReplaceSection(editor, "user_notes", buildUserNotesHeadingNodes());
            if (summary) {
              insertOrReplaceSection(editor, "ai_summary", buildAISummarySectionNodes(summary));
            }
            if (segments.length > 0) {
              insertOrReplaceSection(
                editor,
                "polished_transcript",
                buildPolishedTranscriptSectionNodes(segments, isBilingual, (meta?.translation_label) || "English"),
              );
            }
          }
        }
      } else {
        window.alert(`Convert failed: ${res.error || "unknown error"}`);
      }
    } catch (err) {
      window.alert(`Convert error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsConvertingChinese(false);
    }
  }, [note, setNote, updateNote]);

  // Cut audio from a chosen timestamp, re-run Gemini on just the tail, and
  // splice the new segments back into the existing polished transcript.
  // The backend handles all the splicing + editor_content rebuild; we just
  // refresh the note from the server and let the editor re-mount with new
  // content. Costs another Gemini call, but only for the bad/missing tail
  // (not the already-good earlier segments).
  const handleRetranscribeFrom = useCallback(async (startSeconds: number) => {
    if (!note) return;
    setIsRetranscribing(true);
    try {
      const res = await notesClient.retranscribeFrom(note.note_id, startSeconds);
      if (res.success && res.data) {
        // Pull fresh note (now has new segments + rebuilt editor_content).
        const fresh = await notesClient.get(note.note_id);
        if (fresh.success && fresh.data) {
          setNote(fresh.data);
          updateNote(fresh.data);
          // Rebuild the editor sections in-place so the user sees the new
          // splice without a hard reload.
          if (editorRef.current) {
            const editor = editorRef.current;
            const meta = fresh.data.polished_transcript_meta;
            const segments = (meta?.segments ?? []) as PolishedSegment[];
            const summary = (meta?.summary ?? null) as MeetingSummary | null;
            const isBilingual = Boolean(meta?.is_bilingual);
            insertOrReplaceSection(editor, "user_notes", buildUserNotesHeadingNodes());
            if (summary) {
              insertOrReplaceSection(editor, "ai_summary", buildAISummarySectionNodes(summary));
            }
            if (segments.length > 0) {
              insertOrReplaceSection(
                editor,
                "polished_transcript",
                buildPolishedTranscriptSectionNodes(segments, isBilingual, (meta?.translation_label) || "English"),
              );
            }
          }
        }
        window.alert(
          `Retranscribe done.\n` +
            `Replaced ${res.data.dropped} segment(s) at-or-after ${startSeconds}s; ` +
            `added ${res.data.added} new segments.\n` +
            (res.data.gemini_seconds != null
              ? `Gemini time: ${res.data.gemini_seconds.toFixed(1)}s`
              : ""),
        );
      } else {
        window.alert(`Retranscribe failed: ${res.error || "unknown error"}`);
      }
    } catch (err) {
      window.alert(`Retranscribe error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsRetranscribing(false);
    }
  }, [note, setNote, updateNote]);
  const handleRegenerateSummary = useCallback(async () => {
    if (!note) return;
    setIsRegeneratingSummary(true);
    try {
      const res = await notesClient.regenerateSummary(note.note_id);
      if (res.success && res.data) {
        setNote(res.data);
        updateNote(res.data);

        // Re-render the editor sections from the fresh data without another
        // round-trip. Reuses the same builders used by recording/ingest.
        if (editorRef.current) {
          const editor = editorRef.current;
          const meta = res.data.polished_transcript_meta;
          const segments = (meta?.segments ?? []) as PolishedSegment[];
          const summary = (meta?.summary ?? null) as MeetingSummary | null;
          const isBilingual = Boolean(meta?.is_bilingual);

          const lines: TranscriptLine[] = segments.map((s, idx) => ({
            line_id: idx + 1,
            timestamp: s.timestamp,
            speaker_label: s.speaker || "",
            speaker_name: null,
            text: s.text_original,
            is_flagged: false,
            is_interim: false,
          }));

          insertOrReplaceSection(editor, "user_notes", buildUserNotesHeadingNodes());
          if (summary) {
            insertOrReplaceSection(editor, "ai_summary", buildAISummarySectionNodes(summary));
          }
          if (lines.length > 0) {
            insertOrReplaceSection(editor, "raw_transcript", buildRawTranscriptSectionNodes(lines));
          }
          if (segments.length > 0) {
            insertOrReplaceSection(
              editor,
              "polished_transcript",
              buildPolishedTranscriptSectionNodes(segments, isBilingual, (meta?.translation_label) || "English"),
            );
          }
        }
      } else {
        console.error("regenerate-summary failed", res);
      }
    } finally {
      setIsRegeneratingSummary(false);
    }
  }, [note, setNote, updateNote]);

  // Rebuild the editor's AI sections from the already-saved polished data.
  // Used when a previous ingest completed but the editor didn't get populated
  // (e.g. Gemini returned truncated JSON and only later got repaired server-side).
  // NO Gemini call, NO token spend — just re-runs the client-side builders.
  const handleRegenerateSections = useCallback(async () => {
    if (!note) return;
    // Pull the freshest copy of the note in case fields were updated server-side
    // (e.g. after running the backend repair script).
    const fresh = await notesClient.get(note.note_id);
    const source = fresh.success && fresh.data ? fresh.data : note;
    if (fresh.success && fresh.data) {
      setNote(fresh.data);
      updateNote(fresh.data);
    }

    const meta = source.polished_transcript_meta;
    const segments = (meta?.segments ?? []) as PolishedSegment[];
    const summary = (meta?.summary ?? null) as MeetingSummary | null;
    const isBilingual = Boolean(meta?.is_bilingual);

    // Synthesise transcript lines from the polished segments so the raw
    // transcript section gets populated too.
    const lines: TranscriptLine[] = segments.map((s, idx) => ({
      line_id: idx + 1,
      timestamp: s.timestamp,
      speaker_label: s.speaker || "",
      speaker_name: null,
      text: s.text_original,
      is_flagged: false,
      is_interim: false,
    }));

    if (editorRef.current) {
      const editor = editorRef.current;
      insertOrReplaceSection(editor, "user_notes", buildUserNotesHeadingNodes());
      if (summary) {
        insertOrReplaceSection(editor, "ai_summary", buildAISummarySectionNodes(summary));
      }
      if (lines.length > 0) {
        insertOrReplaceSection(editor, "raw_transcript", buildRawTranscriptSectionNodes(lines));
      }
      if (segments.length > 0) {
        insertOrReplaceSection(
          editor,
          "polished_transcript",
          buildPolishedTranscriptSectionNodes(segments, isBilingual, (meta?.translation_label) || "English"),
        );
      }
    }
  }, [note, setNote, updateNote]);

  // URL ingest: same output shape as recording, plus a source_url to persist.
  // The backend already wrote polished_transcript + meta + source_url, so we
  // refresh from the server and then run the same editor-insert flow recording
  // uses.
  const handleUrlIngestComplete = useCallback(
    async (
      lines: TranscriptLine[],
      durationSeconds: number,
      polished: {
        segments: PolishedSegment[];
        language: string;
        is_bilingual: boolean;
        key_topics: string[];
        summary: MeetingSummary | null;
      } | null,
      _sourceUrl: string,
    ) => {
      if (!note) return;

      const fresh = await notesClient.get(note.note_id);
      if (fresh.success && fresh.data) {
        setNote(fresh.data);
        updateNote(fresh.data);
      }

      if (editorRef.current) {
        const editor = editorRef.current;
        insertOrReplaceSection(editor, "user_notes", buildUserNotesHeadingNodes());
        if (polished && polished.summary) {
          insertOrReplaceSection(editor, "ai_summary", buildAISummarySectionNodes(polished.summary));
        }
        insertOrReplaceSection(editor, "raw_transcript", buildRawTranscriptSectionNodes(lines));
        if (polished && polished.segments.length > 0) {
          insertOrReplaceSection(
            editor,
            "polished_transcript",
            buildPolishedTranscriptSectionNodes(polished.segments, polished.is_bilingual, (note?.polished_transcript_meta?.translation_label) || "English"),
          );
        }
      }

      setShowUrlIngestModal(false);
      // Silence the unused-duration lint; recording carries duration but URL
      // ingest doesn't (yet). Kept for signature parity.
      void durationSeconds;
    },
    [note, setNote, updateNote, setShowUrlIngestModal],
  );

  if (!note) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-slate-400">
        Loading note…
      </div>
    );
  }

  return (
    <NotesEditorView
      note={note}
      isSaving={isSaving}
      showRecordingPopup={showRecordingPopup}
      showUrlIngestModal={showUrlIngestModal}
      onBack={() => router.push("/notes")}
      onTitleChange={handleTitleChange}
      onMeetingDateChange={handleMeetingDateChange}
      onTickersChange={handleTickersChange}
      onNoteTypeChange={handleNoteTypeChange}
      onContentChange={handleContentChange}
      onOpenRecording={() => setShowRecordingPopup(true)}
      onCloseRecording={() => setShowRecordingPopup(false)}
      onRecordingComplete={handleRecordingComplete}
      onOpenUrlIngest={() => setShowUrlIngestModal(true)}
      onCloseUrlIngest={() => setShowUrlIngestModal(false)}
      onUrlIngestComplete={handleUrlIngestComplete}
      onRegenerateSections={handleRegenerateSections}
      onRegenerateSummary={handleRegenerateSummary}
      isRegeneratingSummary={isRegeneratingSummary}
      onRetranscribeFrom={handleRetranscribeFrom}
      isRetranscribing={isRetranscribing}
      onConvertChinese={handleConvertChinese}
      isConvertingChinese={isConvertingChinese}
      onSaveSpeakers={handleSaveSpeakers}
      onExtractTopics={handleExtractTopics}
      onDelta={handleDelta}
      onMarkComplete={handleMarkComplete}
      onStartAISummary={handleStartAISummary}
      onEditorReady={handleEditorReady}
    />
  );
}
