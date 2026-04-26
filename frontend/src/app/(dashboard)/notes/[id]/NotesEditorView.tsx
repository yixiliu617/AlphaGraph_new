"use client";

// ---------------------------------------------------------------------------
// NotesEditorView — DUMB layer. Pure JSX; orchestrates the 2/3 + 1/3 layout.
// ---------------------------------------------------------------------------

import { useRef, useEffect } from "react";
import { ArrowLeft, Mic, Save, CheckCircle, Sparkles, Link2, RefreshCw, Loader2, Download } from "lucide-react";
import type { Editor } from "@tiptap/react";
import type { NoteStub, TranscriptLine, PolishedSegment, MeetingSummary } from "@/lib/api/notesClient";
import RichTextEditor from "@/components/domain/notes/RichTextEditor";
import NoteSearchPanel from "@/components/domain/notes/NoteSearchPanel";
import RecordingPanel from "@/components/domain/notes/RecordingPanel";
import PostMeetingWizard from "@/components/domain/notes/PostMeetingWizard";
import MeetingIntelligencePanel from "@/components/domain/notes/MeetingIntelligencePanel";
import UrlIngestModal from "@/components/domain/notes/UrlIngestModal";
import NoteHeaderBlock from "@/components/domain/notes/NoteHeaderBlock";

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
  onMeetingDateChange: (date: string | null) => void;
  onTickersChange: (tickers: string[]) => void;
  onNoteTypeChange: (noteType: string) => void;
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
  /** Cut audio from a chosen timestamp and re-transcribe just that portion.
   * Used to recover from Gemini repetition loops without re-paying for the
   * already-good earlier segments. Frontend prompts for the start time. */
  onRetranscribeFrom: (startSeconds: number) => Promise<void>;
  isRetranscribing: boolean;
  /** Toggle Chinese script for the entire transcript (Simplified <-> Traditional).
   * Local zhconv conversion — no LLM cost. */
  onConvertChinese: (to: "hans" | "hant") => Promise<void>;
  isConvertingChinese: boolean;
  isRegeneratingSummary: boolean;
}

export default function NotesEditorView({
  note, isSaving, showRecordingPopup, showUrlIngestModal,
  onBack, onTitleChange, onMeetingDateChange, onTickersChange, onNoteTypeChange,
  onContentChange,
  onOpenRecording, onCloseRecording, onRecordingComplete,
  onOpenUrlIngest, onCloseUrlIngest, onUrlIngestComplete, onRegenerateSections,
  onRegenerateSummary, isRegeneratingSummary,
  onRetranscribeFrom, isRetranscribing,
  onConvertChinese, isConvertingChinese,
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
      {/* Top bar — navigation + actions only. Title + metadata live in the
       * NoteHeaderBlock below, Notion-style. */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-200 bg-white shrink-0 shadow-sm">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={onBack}
            className="p-1.5 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
            title="Back to Notes library"
          >
            <ArrowLeft size={16} />
          </button>
          {/* Small breadcrumb title — helps orientate when scrolled.
           * Full editable title lives in NoteHeaderBlock. */}
          <span className="text-sm font-medium text-slate-500 truncate max-w-md">
            {note.title || "Untitled"}
          </span>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          {/* Save indicator — click "Saved" / "Saving" text or hit Ctrl/Cmd+S to force-save */}
          <div
            className="flex items-center gap-1.5 text-xs text-slate-400"
            title="Auto-saves as you type. Press Ctrl+S (or ⌘+S on Mac) to save immediately."
          >
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

          {/* Chinese script toggle — only shows when the note actually has Chinese
           * content. Uses zhconv server-side (no LLM cost) to convert text_original,
           * text_english, speaker labels, and key_topics in one shot. The current
           * variant is tracked in polished_transcript_meta.chinese_variant. */}
          {(() => {
            const segs = note.polished_transcript_meta?.segments ?? [];
            const sample = segs.slice(0, 8).map((s) => `${s.text_original ?? ""}${s.text_english ?? ""}`).join("");
            const hasChinese = /[一-鿿]/.test(sample);
            if (!hasChinese) return null;
            const current = note.polished_transcript_meta?.chinese_variant ?? "hans";
            const target: "hans" | "hant" = current === "hans" ? "hant" : "hans";
            return (
              <button
                onClick={() => onConvertChinese(target)}
                disabled={isConvertingChinese}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200 rounded-md hover:bg-amber-100 disabled:opacity-60 transition-colors"
                title={`Convert all Chinese in this transcript to ${target === "hans" ? "Simplified (简体)" : "Traditional (繁體)"}`}
              >
                {isConvertingChinese ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                {isConvertingChinese
                  ? "Converting…"
                  : `Show as ${target === "hans" ? "简体" : "繁體"}`}
              </button>
            );
          })()}

          {/* Download button — exports the polished transcript as a Word document.
           * Bilingual notes get a 3-column Time / Original / English table;
           * monolingual notes get paragraphs with timestamps. */}
          {(note.polished_transcript_meta?.segments?.length ?? 0) > 0 && (
            <a
              href={`http://localhost:8000/api/v1/notes/${note.note_id}/export.docx`}
              download
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-md hover:bg-emerald-100 transition-colors"
              title="Download polished transcript as a Word document"
            >
              <Download size={13} />
              Download .docx
            </a>
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
          {/* Notion-style header block with inline-editable title + metadata.
           * Sits above the audio player and editor so the user sees a clear
           * page title when the note opens. */}
          <NoteHeaderBlock
            note={note}
            onTitleChange={onTitleChange}
            onMeetingDateChange={onMeetingDateChange}
            onTickersChange={onTickersChange}
            onNoteTypeChange={onNoteTypeChange}
          />

          {/* Coverage-gap banner -- surfaces audio sections that Gemini skipped
           * or failed on (>5 min between consecutive segment timestamps).
           * Each gap is a one-click "Retranscribe from {start_label}" button. */}
          {(note.polished_transcript_meta?.coverage_gaps?.length ?? 0) > 0 && (
            <div className="px-6 py-3 border-b border-amber-200 bg-amber-50">
              <div className="text-[11px] font-semibold text-amber-900 mb-1.5 flex items-center gap-1.5">
                <RefreshCw size={12} />
                Transcript coverage gaps detected ({note.polished_transcript_meta!.coverage_gaps!.length})
              </div>
              <div className="text-[10px] text-amber-800 mb-2">
                The transcript is missing the audio segments below. Click any gap to re-run Gemini on just that range and splice it back in.
              </div>
              <div className="flex flex-wrap gap-1.5">
                {note.polished_transcript_meta!.coverage_gaps!.map((g, i) => {
                  const mins = (g.duration_sec / 60).toFixed(1);
                  const label =
                    g.kind === "lead"
                      ? `Start of audio → ${g.end_label} (${mins} min)`
                      : g.kind === "tail"
                        ? `${g.start_label} → end of audio (${mins} min)`
                        : `${g.start_label} → ${g.end_label} (${mins} min)`;
                  return (
                    <button
                      key={i}
                      onClick={() => onRetranscribeFrom(g.start_sec)}
                      disabled={isRetranscribing}
                      className="text-[10px] font-medium px-2 py-1 rounded-md border border-amber-300 bg-white text-amber-800 hover:bg-amber-100 disabled:opacity-60"
                      title={`Cuts audio from ${g.start_label} and re-runs Gemini on just that portion`}
                    >
                      {isRetranscribing ? <Loader2 size={10} className="inline animate-spin mr-1" /> : null}
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

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
