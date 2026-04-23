"use client";

// ---------------------------------------------------------------------------
// NotesEditorContainer — SMART layer for the note detail/editor page.
// Fetches the note, handles auto-save, coordinates wizard state.
// ---------------------------------------------------------------------------

import { useEffect, useCallback, useRef } from "react";
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
  const handleEditorReady = useCallback((editor: Editor) => {
    editorRef.current = editor;
  }, []);

  // Load note on mount / noteId change — clear first so stale note never shows
  useEffect(() => {
    clearNote();
    notesClient.get(noteId).then((res) => {
      if (res.success && res.data) setNote(res.data);
    });
  }, [noteId, setNote, clearNote]);

  // Auto-save whenever content changes
  const scheduleSave = useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      if (!note) return;
      setSaving(true);
      const res = await notesClient.update(note.note_id, {
        title: note.title,
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
    }, AUTO_SAVE_DELAY_MS);
  }, [note, setSaving, updateNote, setDirty]);

  useEffect(() => {
    if (isDirty) scheduleSave();
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [isDirty, scheduleSave]);

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
            buildPolishedTranscriptSectionNodes(polished.segments, polished.is_bilingual),
          );
        }
      }

      setShowRecordingPopup(false);
    },
    [note, setNote, updateNote, patchNote, setShowRecordingPopup]
  );

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
          buildPolishedTranscriptSectionNodes(segments, isBilingual),
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
            buildPolishedTranscriptSectionNodes(polished.segments, polished.is_bilingual),
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
      onContentChange={handleContentChange}
      onOpenRecording={() => setShowRecordingPopup(true)}
      onCloseRecording={() => setShowRecordingPopup(false)}
      onRecordingComplete={handleRecordingComplete}
      onOpenUrlIngest={() => setShowUrlIngestModal(true)}
      onCloseUrlIngest={() => setShowUrlIngestModal(false)}
      onUrlIngestComplete={handleUrlIngestComplete}
      onRegenerateSections={handleRegenerateSections}
      onSaveSpeakers={handleSaveSpeakers}
      onExtractTopics={handleExtractTopics}
      onDelta={handleDelta}
      onMarkComplete={handleMarkComplete}
      onStartAISummary={handleStartAISummary}
      onEditorReady={handleEditorReady}
    />
  );
}
