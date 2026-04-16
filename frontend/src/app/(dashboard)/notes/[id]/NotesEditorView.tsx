"use client";

// ---------------------------------------------------------------------------
// NotesEditorView — DUMB layer. Pure JSX; orchestrates the 2/3 + 1/3 layout.
// ---------------------------------------------------------------------------

import { ArrowLeft, Mic, Save, CheckCircle, Sparkles } from "lucide-react";
import type { NoteStub, TranscriptLine } from "@/lib/api/notesClient";
import RichTextEditor from "@/components/domain/notes/RichTextEditor";
import NoteSearchPanel from "@/components/domain/notes/NoteSearchPanel";
import RecordingPopup from "@/components/domain/notes/RecordingPopup";
import PostMeetingWizard from "@/components/domain/notes/PostMeetingWizard";

const NOTE_TYPE_LABELS: Record<string, string> = {
  earnings_call: "Earnings Call",
  management_meeting: "Mgmt Meeting",
  conference: "Conference",
  internal: "Internal",
};

const NOTE_TYPE_COLORS: Record<string, string> = {
  earnings_call: "bg-blue-50 text-blue-700 border border-blue-100",
  management_meeting: "bg-violet-50 text-violet-700 border border-violet-100",
  conference: "bg-amber-50 text-amber-700 border border-amber-100",
  internal: "bg-slate-100 text-slate-600 border border-slate-200",
};

const WIZARD_STATUSES = new Set([
  "awaiting_speakers", "awaiting_topics", "extracting", "awaiting_approval", "complete"
]);

interface Props {
  note: NoteStub;
  isSaving: boolean;
  showRecordingPopup: boolean;
  onBack: () => void;
  onTitleChange: (title: string) => void;
  onContentChange: (json: Record<string, unknown>, plainText: string) => void;
  onOpenRecording: () => void;
  onCloseRecording: () => void;
  onRecordingComplete: (lines: TranscriptLine[], durationSeconds: number) => void;
  onSaveSpeakers: (mappings: { label: string; name: string; role?: string }[]) => Promise<void>;
  onExtractTopics: (topics: string[]) => Promise<void>;
  onDelta: (deltaId: string, action: "approve" | "edit" | "dismiss", editedText?: string) => Promise<void>;
  onStartAISummary: () => void;
}

export default function NotesEditorView({
  note, isSaving, showRecordingPopup,
  onBack, onTitleChange, onContentChange,
  onOpenRecording, onCloseRecording, onRecordingComplete,
  onSaveSpeakers, onExtractTopics, onDelta, onStartAISummary,
}: Props) {
  const showWizard = WIZARD_STATUSES.has(note.summary_status);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-200 bg-white shrink-0 shadow-sm">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={onBack}
            className="p-1.5 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
          >
            <ArrowLeft size={16} />
          </button>

          {/* Editable title */}
          <input
            type="text"
            value={note.title}
            onChange={(e) => onTitleChange(e.target.value)}
            className="text-base font-semibold text-slate-900 bg-transparent border-none outline-none focus:bg-slate-50 focus:px-2 rounded-md transition-all min-w-0 flex-1 max-w-sm"
          />

          {/* Meeting date */}
          {note.meeting_date && (
            <span className="text-[11px] text-slate-400 shrink-0 tabular-nums">
              {new Date(note.meeting_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
            </span>
          )}

          {/* Company pills */}
          <div className="flex items-center gap-1.5">
            {note.company_tickers.map((t) => (
              <span key={t} className="px-2 py-0.5 text-[10px] font-mono font-semibold bg-indigo-50 text-indigo-700 rounded border border-indigo-100">
                {t}
              </span>
            ))}
          </div>

          {/* Note type badge */}
          <span className={`px-2 py-0.5 text-[10px] font-medium rounded-full ${NOTE_TYPE_COLORS[note.note_type] ?? "bg-slate-100 text-slate-500"}`}>
            {NOTE_TYPE_LABELS[note.note_type] ?? note.note_type}
          </span>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          {/* Save indicator */}
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            {isSaving ? (
              <>
                <Save size={12} className="animate-pulse text-indigo-400" />
                <span>Saving…</span>
              </>
            ) : (
              <>
                <CheckCircle size={12} className="text-green-500" />
                <span>Saved</span>
              </>
            )}
          </div>

          {/* AI Summary button — only when wizard is not active */}
          {!showWizard && note.summary_status !== "complete" && (
            <button
              onClick={onStartAISummary}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200 rounded-md hover:bg-amber-100 transition-colors"
            >
              <Sparkles size={13} />
              AI Summary
            </button>
          )}

          {/* Record button */}
          <button
            onClick={onOpenRecording}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-red-50 text-red-600 border border-red-200 rounded-md hover:bg-red-100 transition-colors"
          >
            <Mic size={13} />
            Record Audio
          </button>
        </div>
      </div>

      {/* Main content area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left — Rich text editor (2/3) */}
        <div className="flex-[2] border-r border-slate-200 overflow-y-auto bg-white">
          <RichTextEditor
            initialContent={note.editor_content}
            onChange={onContentChange}
          />
        </div>

        {/* Right — Search panel OR Post-meeting wizard (1/3) */}
        <div className="flex-[1] flex flex-col overflow-hidden bg-slate-50 border-l border-slate-200 min-w-0">
          {showWizard ? (
            <PostMeetingWizard
              note={note}
              onSaveSpeakers={onSaveSpeakers}
              onExtractTopics={onExtractTopics}
              onDelta={onDelta}
            />
          ) : (
            <NoteSearchPanel
              contextTickers={note.company_tickers}
              contextNoteType={note.note_type}
            />
          )}
        </div>
      </div>

      {/* Recording popup */}
      {showRecordingPopup && (
        <RecordingPopup
          noteId={note.note_id}
          onClose={onCloseRecording}
          onComplete={onRecordingComplete}
        />
      )}
    </div>
  );
}
