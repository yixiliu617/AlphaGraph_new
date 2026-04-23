"use client";

/**
 * UrlIngestModal — top-bar-triggered modal that populates the note from a
 * YouTube / podcast / video URL. Opens a WebSocket to the backend ingest
 * endpoint, streams status messages, and on `polished_transcript` calls the
 * same onComplete callback the live recording uses. The parent container
 * wires onComplete to the existing handleRecordingComplete so the 4
 * editor-section insert logic is reused verbatim.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { X, Link2, Loader2, Sparkles, Globe } from "lucide-react";
import {
  notesClient,
  type TranscriptLine,
  type PolishedSegment,
  type MeetingSummary,
} from "@/lib/api/notesClient";

const LANGUAGES = [
  { value: "auto", label: "Auto-Detect" },
  { value: "en",   label: "English" },
  { value: "zh",   label: "Chinese" },
  { value: "ja",   label: "Japanese" },
  { value: "ko",   label: "Korean" },
];

interface Props {
  noteId: string;
  onClose: () => void;
  onComplete: (
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
}

export default function UrlIngestModal({ noteId, onClose, onComplete }: Props) {
  const [url, setUrl] = useState("");
  const [language, setLanguage] = useState("auto");
  const [status, setStatus] = useState<"idle" | "running" | "error">("idle");
  const [statusLog, setStatusLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    return () => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.close();
      }
    };
  }, []);

  const addStatus = useCallback((msg: string) => {
    setStatusLog((prev) => [...prev, msg]);
  }, []);

  const handleStart = () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setError(null);
    setStatusLog([]);
    setStatus("running");

    const wsUrl = notesClient.ingestUrlWsUrl(noteId, trimmed, language);
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => addStatus("Connecting...");

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "status") {
          if (typeof msg.message === "string") addStatus(msg.message);
        } else if (msg.type === "polished_transcript") {
          const polished = {
            segments: (Array.isArray(msg.segments) ? msg.segments : []) as PolishedSegment[],
            language: typeof msg.language === "string" ? msg.language : "",
            is_bilingual: Boolean(msg.is_bilingual),
            key_topics: Array.isArray(msg.key_topics) ? msg.key_topics : [],
            summary: msg.summary && typeof msg.summary === "object" ? (msg.summary as MeetingSummary) : null,
          };
          // Synthesise raw transcript lines from the polished segments so
          // the raw-transcript editor section still gets populated.
          const lines: TranscriptLine[] = polished.segments.map((s, idx) => ({
            line_id: idx + 1,
            timestamp: s.timestamp,
            speaker_label: s.speaker || "",
            speaker_name: null,
            text: s.text_original,
            is_flagged: false,
            is_interim: false,
          }));
          onComplete(lines, 0, polished, url.trim());
        } else if (msg.type === "error") {
          setError(typeof msg.message === "string" ? msg.message : "Unknown error");
          setStatus("error");
        }
      } catch {
        /* non-JSON */
      }
    };

    ws.onerror = () => {
      setError("WebSocket connection failed.");
      setStatus("error");
    };

    ws.onclose = () => {
      wsRef.current = null;
    };
  };

  const isIdle = status === "idle";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget && isIdle) onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden border border-slate-200">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 bg-slate-50">
          <div className="flex items-center gap-2">
            <Link2 size={15} className="text-indigo-600" />
            <h3 className="text-sm font-semibold text-slate-900">Ingest from URL</h3>
          </div>
          <button onClick={onClose} disabled={!isIdle} className="p-1 text-slate-400 hover:text-slate-600 rounded-lg transition-colors disabled:opacity-40">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          {isIdle && (
            <>
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1.5 uppercase tracking-wider">
                  URL <span className="text-red-400">*</span>
                </label>
                <input
                  type="url"
                  autoFocus
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && url.trim()) handleStart(); }}
                  placeholder="https://www.youtube.com/watch?v=..."
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md bg-slate-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 text-slate-900 placeholder-slate-400"
                />
                <p className="mt-1 text-[10px] text-slate-400">
                  YouTube, Vimeo, SoundCloud, podcasts, direct MP3/MP4 links — anything yt-dlp supports.
                </p>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1.5 uppercase tracking-wider">
                  Language
                </label>
                <select
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md bg-slate-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 text-slate-900"
                >
                  {LANGUAGES.map((l) => (
                    <option key={l.value} value={l.value}>{l.label}</option>
                  ))}
                </select>
                <p className="mt-1 text-[10px] text-slate-400">
                  Auto works well for most cases. Pick a specific language to bias caption lookup.
                </p>
              </div>

              <div className="text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded-md p-2.5 space-y-1">
                <div className="flex items-center gap-1.5 font-semibold text-slate-700">
                  <Sparkles size={11} className="text-amber-500" />
                  How it works
                </div>
                <p>Tries manual captions first (fast, free). Falls back to audio download + Gemini if captions aren&apos;t available.</p>
              </div>
            </>
          )}

          {status === "running" && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm text-slate-700">
                <Loader2 size={14} className="animate-spin text-indigo-500" />
                <span>Processing…</span>
              </div>
              <div className="bg-slate-50 border border-slate-200 rounded-md p-2.5 max-h-48 overflow-y-auto space-y-1">
                {statusLog.map((s, i) => (
                  <p key={i} className="text-[11px] text-slate-600 font-mono leading-snug">
                    <span className="text-slate-400">•</span> {s}
                  </p>
                ))}
                {statusLog.length === 0 && (
                  <p className="text-[11px] text-slate-400">Waiting for first status message…</p>
                )}
              </div>
              <p className="text-[10px] text-slate-400 text-center">
                This can take 10 seconds (captions path) to a few minutes (audio path). Don&apos;t close the tab.
              </p>
            </div>
          )}

          {status === "error" && (
            <div className="space-y-3">
              <div className="px-3 py-2 bg-red-50 border border-red-200 rounded-md text-xs text-red-700">
                {error ?? "Ingest failed."}
              </div>
              {statusLog.length > 0 && (
                <details className="text-[11px] text-slate-500">
                  <summary className="cursor-pointer hover:text-slate-700">Show progress log</summary>
                  <div className="mt-1 bg-slate-50 border border-slate-200 rounded-md p-2 space-y-0.5">
                    {statusLog.map((s, i) => (
                      <p key={i} className="font-mono">• {s}</p>
                    ))}
                  </div>
                </details>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-200 bg-slate-50">
          {isIdle && (
            <>
              <button onClick={onClose} className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors">
                Cancel
              </button>
              <button
                onClick={handleStart}
                disabled={!url.trim()}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50 transition-colors shadow-sm"
              >
                <Globe size={14} />
                Extract Transcript
              </button>
            </>
          )}
          {status === "running" && (
            <span className="text-[11px] text-slate-400">Processing — modal will close automatically when done.</span>
          )}
          {status === "error" && (
            <button onClick={onClose} className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors">
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
