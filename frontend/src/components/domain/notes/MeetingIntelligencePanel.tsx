"use client";

/**
 * MeetingIntelligencePanel — Variant-B sidebar view shown after the meeting
 * wizard/recording finishes. Plan 2 originally rendered the wizard's topic
 * fragments here; Plan 2.5 replaces that with a simpler "you've recorded"
 * metadata card plus a placeholder for the Plan 3 chat agent.
 *
 * The detailed meeting intelligence (storyline, key points, numbers, etc.)
 * now lives in the main editor as the "AI Summary" section.
 */

import { Brain, Sparkles, Clock, Globe, MessageSquare, Flag } from "lucide-react";
import type { NoteStub, MeetingSummary } from "@/lib/api/notesClient";

interface Props {
  note: NoteStub;
}

function formatDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}m ${String(secs).padStart(2, "0")}s`;
}

export default function MeetingIntelligencePanel({ note }: Props) {
  const meta = note.polished_transcript_meta;
  const summary: MeetingSummary | undefined = meta?.summary;
  const keyTopics: string[] = meta?.key_topics ?? [];
  const language = note.polished_transcript_language ?? meta?.segments?.[0] ? (note.polished_transcript_language ?? "") : "";
  const hasPolished = note.polished_transcript !== null && note.polished_transcript !== "";

  return (
    <div className="flex flex-col h-full overflow-hidden bg-slate-50">
      {/* Panel header */}
      <div className="px-4 py-3 border-b border-slate-200 bg-white shrink-0">
        <div className="flex items-center gap-2">
          <Brain size={14} className="text-indigo-600" />
          <span className="text-xs font-semibold text-slate-700">Meeting Intelligence</span>
          <span className="ml-auto text-[10px] text-slate-400 font-mono">Variant B</span>
        </div>
        <p className="mt-1 text-[10px] text-slate-400">
          Detailed AI summary is in the main editor (between your notes and the raw transcript).
        </p>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4 min-h-0">
        {/* Meeting metadata card */}
        {hasPolished ? (
          <section className="bg-white border border-slate-200 rounded-lg p-3 space-y-2.5">
            <div className="flex items-center gap-1.5">
              <Sparkles size={11} className="text-amber-500" />
              <h4 className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Recording</h4>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-xs text-slate-700">
                <Clock size={11} className="text-slate-400 shrink-0" />
                <span className="font-medium">Duration:</span>
                <span className="text-slate-600">{formatDuration(note.duration_seconds)}</span>
              </div>
              {language && (
                <div className="flex items-center gap-2 text-xs text-slate-700">
                  <Globe size={11} className="text-slate-400 shrink-0" />
                  <span className="font-medium">Language:</span>
                  <span className="text-slate-600 uppercase font-mono">{language}</span>
                </div>
              )}
            </div>

            {keyTopics.length > 0 && (
              <div>
                <div className="flex items-center gap-1.5 mt-2 mb-1">
                  <Flag size={11} className="text-slate-400" />
                  <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Key Topics</span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {keyTopics.map((t, i) => (
                    <span
                      key={i}
                      className="px-1.5 py-0.5 text-[10px] font-medium bg-indigo-50 text-indigo-700 rounded border border-indigo-100"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {summary && summary.key_points && summary.key_points.length > 0 && (
              <p className="text-[10px] text-slate-400 pt-1 border-t border-slate-100">
                {summary.key_points.length} key point
                {summary.key_points.length === 1 ? "" : "s"} in the AI summary below your notes.
              </p>
            )}
          </section>
        ) : (
          <section className="bg-white border border-slate-200 rounded-lg p-4 text-center space-y-2">
            <Brain size={24} className="text-slate-300 mx-auto" />
            <p className="text-sm font-medium text-slate-600">No recording yet.</p>
            <p className="text-[11px] text-slate-400">
              Click Record Audio above to capture a meeting. The AI summary and transcripts will appear in the main editor.
            </p>
          </section>
        )}

        {/* Chat placeholder — real chat agent lands in Plan 3 */}
        <section className="bg-slate-100 border border-dashed border-slate-300 rounded-lg p-4 text-center space-y-2">
          <MessageSquare size={20} className="text-slate-400 mx-auto" />
          <p className="text-xs font-medium text-slate-600">Chat agent coming soon</p>
          <p className="text-[10px] text-slate-400 max-w-[220px] mx-auto">
            You&apos;ll be able to ask questions about this meeting here and trigger on-demand analysis modules.
          </p>
        </section>
      </div>
    </div>
  );
}
