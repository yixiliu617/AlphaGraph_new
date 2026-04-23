"use client";

// ---------------------------------------------------------------------------
// NotesEditorView — DUMB layer. Pure JSX; orchestrates the 2/3 + 1/3 layout.
// ---------------------------------------------------------------------------

import { useRef, useEffect } from "react";
import { ArrowLeft, Mic, Save, CheckCircle, Sparkles, Link2, RefreshCw } from "lucide-react";
import type { Editor } from "@tiptap/react";
import type { NoteStub, TranscriptLine, PolishedSegment, MeetingSummary } from "@/lib/api/notesClient";
import RichTextEditor from "@/components/domain/notes/RichTextEditor";
import NoteSearchPanel from "@/components/domain/notes/NoteSearchPanel";
import RecordingPanel from "@/components/domain/notes/RecordingPanel";
import PostMeetingWizard from "@/components/domain/notes/PostMeetingWizard";
import MeetingIntelligencePanel from "@/components/domain/notes/MeetingIntelligencePanel";
import UrlIngestModal from "@/components/domain/notes/UrlIngestModal";

const NOTE_TYPE_LABELS: Record<string, string> = {
  meeting_transcript: "Meeting Transcript",
  earnings_call: "Earnings Call",
  management_meeting: "Mgmt Meeting",
  conference: "Conference",
  internal: "Internal",
};

const NOTE_TYPE_COLORS: Record<string, string> = {
  meeting_transcript: "bg-green-50 text-green-700 border border-green-100",
  earnings_call: "bg-blue-50 text-blue-700 border border-blue-100",
  management_meeting: "bg-violet-50 text-violet-700 border border-violet-100",
  conference: "bg-amber-50 text-amber-700 border border-amber-100",
  internal: "bg-slate-100 text-slate-600 border border-slate-200",
};

// The wizard UI is shown for every step before completion.
const WIZARD_IN_PROGRESS_STATUSES = new Set([
  "awaiting_speakers", "awaiting_topics", "extracting", "awaiting_approval",
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
  onRecordingComplete: (
    lines: TranscriptLine[],
    durationSeconds: number,
    polished: {
      segments: PolishedSegment[];
      language: string;
      is_bilingual: boolean;
      key_topics: string[];
      summary: MeetingSummary | null;
    } | null,
  ) => void;
  onSaveSpeakers: (mappings: { label: string; name: string; role?: string }[]) => Promise<void>;
  onExtractTopics: (topics: string[]) => Promise<void>;
  onDelta: (deltaId: string, action: "approve" | "edit" | "dismiss", editedText?: string) => Promise<void>;
  onMarkComplete: () => Promise<void>;
  onStartAISummary: () => void;
  onEditorReady: (editor: Editor) => void;
  showUrlIngestModal: boolean;
  onOpenUrlIngest: () => void;
  onCloseUrlIngest: () => void;
  onUrlIngestComplete: (
    lines: TranscriptLine[],
    durationSeconds: number,
    polished: {
      segments: PolishedSegment[];
      language: string;
      is_bilingual: boolean;
      key_topics: string[];
      summary: MeetingSummary | null;
    } | null,
    sourceUrl: string,
  ) => void;
  onRegenerateSections: () => Promise<void>;
  onRegenerateSummary: () => Promise<void>;
  isRegeneratingSummary: boolean;
}

export default function NotesEditorView({
  note, isSaving, showRecordingPopup, showUrlIngestModal,
  onBack, onTitleChange, onContentChange,
  onOpenRecording, onCloseRecording, onRecordingComplete,
  onOpenUrlIngest, onCloseUrlIngest, onUrlIngestComplete, onRegenerateSections,
  onRegenerateSummary, isRegeneratingSummary,
  onSaveSpeakers, onExtractTopics, onDelta, onMarkComplete, onStartAISummary,
  onEditorReady,
}: Props) {
  const showWizard = WIZARD_IN_PROGRESS_STATUSES.has(note.summary_status);
  const showMeetingIntelligence = note.ux_variant === "B" && note.summary_status === "complete";
  const audioRef = useRef<HTMLAudioElement>(null);
  const editorWrapperRef = useRef<HTMLDivElement>(null);

  // Handle timestamp clicks from the Tiptap editor — seek audio to that time
  const handleTimestampSeek = (seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    const clamped = Math.min(seconds, audio.duration > 0 ? audio.duration - 1 : seconds);
    audio.currentTime = clamped;
    audio.play();
  };

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

          {/* Ingested-from chip — present when the note was populated from a URL */}
          {note.source_url && (
            <a
              href={note.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium bg-slate-50 text-slate-600 border border-slate-200 rounded-full hover:bg-slate-100 hover:text-indigo-700 transition-colors max-w-[220px] truncate"
              title={note.source_url}
            >
              <Link2 size={10} />
              <span className="truncate">Ingested from URL</span>
            </a>
          )}
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

          {/* Regenerate-from-saved button — only shown when the note has saved
           * polished data but the editor might be out of sync (e.g. truncated
           * initial parse, or user cleared the sections). Pulls polished_transcript_meta
           * from the DB and re-runs the client-side section builders — NO Gemini call,
           * NO token spend. */}
          {((note.polished_transcript_meta?.segments?.length ?? 0) > 0 ||
            Boolean(note.polished_transcript_meta?.summary?.storyline)) && (
            <>
              <button
                onClick={onRegenerateSections}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-slate-50 text-slate-700 border border-slate-200 rounded-md hover:bg-slate-100 transition-colors"
                title="Re-render AI summary + transcript sections from saved data (no Gemini call)"
              >
                <RefreshCw size={13} />
                Re-render
              </button>
              {/* Re-generate Summary — fresh text-only Gemini call on the saved
               * transcript segments. ~$0.001-0.01 per click, no audio re-run.
               * Useful after prompt improvements or on legacy notes whose
               * all_numbers entries are still plain strings. */}
              <button
                onClick={onRegenerateSummary}
                disabled={isRegeneratingSummary}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200 rounded-md hover:bg-amber-100 disabled:opacity-60 transition-colors"
                title="Re-run AI summary via Gemini on the existing transcript (cheap text-only call)"
              >
                <Sparkles size={13} className={isRegeneratingSummary ? "animate-pulse" : ""} />
                {isRegeneratingSummary ? "Re-generating…" : "Re-generate Summary"}
              </button>
            </>
          )}

          {/* Ingest URL button */}
          <button
            onClick={onOpenUrlIngest}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-md hover:bg-indigo-100 transition-colors"
          >
            <Link2 size={13} />
            Ingest URL
          </button>

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
          {/* Audio player for meeting recordings */}
          {note.recording_path && (
            <div className="px-6 pt-4 pb-2 border-b border-slate-100 bg-slate-50 sticky top-0 z-10">
              <div className="flex items-center gap-3">
                <Mic size={14} className="text-red-500 shrink-0" />
                <span className="text-xs font-medium text-slate-600">Recording</span>
                <audio
                  ref={audioRef}
                  controls
                  preload="metadata"
                  className="flex-1 h-8"
                  src={`http://localhost:8000/api/v1/notes/audio/${note.recording_path}`}
                >
                  Your browser does not support audio playback.
                </audio>
                <a
                  href={`http://localhost:8000/api/v1/notes/audio/${note.recording_path}`}
                  download
                  className="text-[10px] text-indigo-600 hover:underline shrink-0"
                >
                  Download
                </a>
              </div>
            </div>
          )}
          <div className="transcript-timestamps">
            <style>{`
              .transcript-timestamps .ts-seek {
                cursor: pointer !important;
              }
              .transcript-timestamps .ts-seek:hover {
                background: #c7d2fe !important;
                text-decoration: none;
              }
            `}</style>
            <RichTextEditor
              initialContent={note.editor_content}
              onChange={onContentChange}
              onTimestampClick={note.recording_path ? handleTimestampSeek : undefined}
              onEditorReady={onEditorReady}
            />
          </div>
        </div>

        {/* Right-sidebar branching
         *
         *   showRecordingPopup              -> RecordingPanel (live recording UI)
         *   showWizard (legacy in-progress) -> PostMeetingWizard (backward-compat only;
         *                                      new notes go straight from recording to
         *                                      summary_status === "complete")
         *   B + complete                    -> MeetingIntelligencePanel (simplified
         *                                      metadata card + chat placeholder)
         *   otherwise                       -> NoteSearchPanel (including A at complete;
         *                                      the detailed AI summary lives in the
         *                                      main editor, not in this panel)
         */}
        <div className="flex-[1] flex flex-col overflow-hidden bg-slate-50 border-l border-slate-200 min-w-0">
          {showRecordingPopup ? (
            <RecordingPanel
              noteId={note.note_id}
              onClose={onCloseRecording}
              onComplete={onRecordingComplete}
            />
          ) : showWizard ? (
            <PostMeetingWizard
              note={note}
              onSaveSpeakers={onSaveSpeakers}
              onExtractTopics={onExtractTopics}
              onDelta={onDelta}
              onMarkComplete={onMarkComplete}
            />
          ) : showMeetingIntelligence ? (
            <MeetingIntelligencePanel note={note} />
          ) : (
            <NoteSearchPanel
              contextTickers={note.company_tickers}
              contextNoteType={note.note_type}
            />
          )}
        </div>
      </div>

      {/* URL ingest modal */}
      {showUrlIngestModal && (
        <UrlIngestModal
          noteId={note.note_id}
          onClose={onCloseUrlIngest}
          onComplete={onUrlIngestComplete}
        />
      )}
    </div>
  );
}
