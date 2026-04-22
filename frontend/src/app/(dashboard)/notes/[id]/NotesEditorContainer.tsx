"use client";

// ---------------------------------------------------------------------------
// NotesEditorContainer — SMART layer for the note detail/editor page.
// Fetches the note, handles auto-save, coordinates wizard state.
// ---------------------------------------------------------------------------

import { useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { notesClient } from "@/lib/api/notesClient";
import { useNotesStore } from "@/store/useNotesStore";
import { useNoteEditorStore } from "./store";
import NotesEditorView from "./NotesEditorView";

const AUTO_SAVE_DELAY_MS = 1500;

interface Props {
  noteId: string;
}

export default function NotesEditorContainer({ noteId }: Props) {
  const router = useRouter();
  const { updateNote } = useNotesStore();
  const {
    note, isSaving, isDirty, showRecordingPopup,
    setNote, clearNote, setSaving, setDirty, setShowRecordingPopup, patchNote,
  } = useNoteEditorStore();

  // Auto-save timer
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  // After recording stops: persist transcript lines server-side so the wizard
  // and AI-analysis modules can read them from the DB, then update local state.
  const handleRecordingComplete = useCallback(
    async (lines: import("@/lib/api/notesClient").TranscriptLine[], durationSeconds: number) => {
      if (!note) return;
      const res = await notesClient.saveTranscript(note.note_id, lines, durationSeconds);
      if (res.success && res.data) {
        setNote(res.data);
        updateNote(res.data);
      } else {
        // Fall back to local patch so the UI still advances even if the save failed
        patchNote({
          transcript_lines: lines,
          duration_seconds: durationSeconds,
          summary_status: "awaiting_speakers",
        });
      }
      setShowRecordingPopup(false);
    },
    [note, setNote, updateNote, patchNote, setShowRecordingPopup]
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
      onBack={() => router.push("/notes")}
      onTitleChange={handleTitleChange}
      onContentChange={handleContentChange}
      onOpenRecording={() => setShowRecordingPopup(true)}
      onCloseRecording={() => setShowRecordingPopup(false)}
      onRecordingComplete={handleRecordingComplete}
      onSaveSpeakers={handleSaveSpeakers}
      onExtractTopics={handleExtractTopics}
      onDelta={handleDelta}
      onStartAISummary={handleStartAISummary}
    />
  );
}
