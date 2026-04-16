"use client";

/**
 * RecordingPopup — floating overlay for live meeting recording.
 *
 * Modes:
 *   wasapi  — server captures WASAPI system audio loopback (Zoom/Webex on same machine)
 *   browser — browser MediaRecorder captures microphone, sends to server
 *             (use when backend is remote or for in-person meetings)
 *
 * Protocol (WebSocket):
 *   Server → client: { type: "transcript", line_id, timestamp, speaker_label, text, is_interim }
 *   Server → client: { type: "stopped", note_id }
 *   Server → client: { type: "error", message }
 *   Client → server: { type: "stop" }
 *   Client → server: { type: "flag", line_id: N }
 *   Browser mode only — binary frames: raw PCM audio chunks
 *
 * After recording stops, calls onComplete with all final transcript lines + duration.
 */

import { useState, useRef, useEffect, useCallback } from "react";
import { X, Mic, Monitor, Flag, Square, Wifi } from "lucide-react";
import { notesClient, type TranscriptLine } from "@/lib/api/notesClient";

const LANGUAGES = [
  { value: "en-US", label: "English" },
  { value: "zh", label: "Chinese" },
  { value: "ja", label: "Japanese" },
  { value: "ko", label: "Korean" },
];

interface Props {
  noteId: string;
  onClose: () => void;
  onComplete: (lines: TranscriptLine[], durationSeconds: number) => void;
}

export default function RecordingPopup({ noteId, onClose, onComplete }: Props) {
  const [mode, setMode] = useState<"wasapi" | "browser">("wasapi");
  const [language, setLanguage] = useState("en-US");
  const [isRecording, setIsRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [status, setStatus] = useState<"idle" | "connecting" | "recording" | "stopping">("idle");
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const durationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const startTimeRef = useRef<number>(0);

  // Auto-scroll transcript
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  // Duration ticker
  useEffect(() => {
    if (isRecording) {
      startTimeRef.current = Date.now();
      durationIntervalRef.current = setInterval(() => {
        setDuration(Math.floor((Date.now() - startTimeRef.current) / 1000));
      }, 1000);
    } else {
      if (durationIntervalRef.current) clearInterval(durationIntervalRef.current);
    }
    return () => { if (durationIntervalRef.current) clearInterval(durationIntervalRef.current); };
  }, [isRecording]);

  const startRecording = useCallback(async () => {
    setError(null);
    setLines([]);
    setStatus("connecting");

    const url = notesClient.recordingWsUrl(noteId, mode, language);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("recording");
      setIsRecording(true);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "transcript") {
          const line: TranscriptLine = {
            line_id: msg.line_id,
            timestamp: msg.timestamp,
            speaker_label: msg.speaker_label,
            speaker_name: null,
            text: msg.text,
            is_flagged: false,
            is_interim: msg.is_interim,
          };
          setLines((prev) => {
            // Replace interim line with same id, or append
            const existing = prev.findIndex((l) => l.line_id === line.line_id);
            if (existing !== -1) {
              const updated = [...prev];
              updated[existing] = line;
              return updated;
            }
            return [...prev, line];
          });
        } else if (msg.type === "error") {
          setError(msg.message);
          setStatus("idle");
          setIsRecording(false);
        } else if (msg.type === "stopped") {
          setStatus("idle");
          setIsRecording(false);
        } else if (msg.type === "flagged") {
          setLines((prev) =>
            prev.map((l) => l.line_id === msg.line_id ? { ...l, is_flagged: true } : l)
          );
        }
      } catch { /* non-JSON message */ }
    };

    ws.onerror = () => setError("WebSocket connection failed.");
    ws.onclose = () => {
      setStatus("idle");
      setIsRecording(false);
    };

    // Browser mode: capture microphone and stream PCM
    if (mode === "browser") {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
        mediaRecorderRef.current = mr;
        mr.ondataavailable = (e) => {
          if (ws.readyState === WebSocket.OPEN && e.data.size > 0) {
            e.data.arrayBuffer().then((buf) => ws.send(buf));
          }
        };
        mr.start(100); // 100ms chunks
      } catch (err) {
        setError(`Microphone access denied: ${err instanceof Error ? err.message : String(err)}`);
        ws.close();
      }
    }
  }, [noteId, mode, language]);

  const stopRecording = useCallback(() => {
    setStatus("stopping");
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "stop" }));
    }
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current?.stream?.getTracks().forEach((t) => t.stop());

    // Collect final lines (filter out interim) and notify parent
    setTimeout(() => {
      const finalLines = lines.filter((l) => !l.is_interim);
      onComplete(finalLines, duration);
    }, 500);
  }, [wsRef, lines, duration, onComplete]);

  const flagLine = useCallback((lineId: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "flag", line_id: lineId }));
    }
    // Optimistic update
    setLines((prev) => prev.map((l) => l.line_id === lineId ? { ...l, is_flagged: true } : l));
  }, []);

  const formatDuration = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  };

  return (
    <div className="fixed bottom-6 right-6 z-50 w-[480px] max-h-[600px] flex flex-col bg-white border border-slate-200 rounded-xl shadow-2xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-slate-900 text-white">
        <div className="flex items-center gap-3">
          {isRecording && (
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 bg-red-400 rounded-full animate-pulse" />
              <span className="text-xs font-semibold font-mono">{formatDuration(duration)}</span>
            </span>
          )}
          {status === "connecting" && (
            <span className="flex items-center gap-1.5 text-xs text-slate-300">
              <Wifi size={12} className="animate-pulse" />
              Connecting…
            </span>
          )}
          {status === "idle" && !isRecording && (
            <span className="text-xs text-slate-400">Ready to record</span>
          )}
        </div>
        <button onClick={onClose} className="p-1 text-slate-400 hover:text-white rounded-lg transition-colors">
          <X size={16} />
        </button>
      </div>

      {/* Controls */}
      {!isRecording && (
        <div className="px-4 py-3 border-b border-slate-100 space-y-3">
          {/* Mode toggle */}
          <div>
            <p className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mb-1.5">Audio Source</p>
            <div className="flex gap-2">
              <button
                onClick={() => setMode("wasapi")}
                className={`flex items-center gap-2 flex-1 px-3 py-2 text-xs font-medium rounded-md border transition-colors ${
                  mode === "wasapi"
                    ? "border-indigo-600 bg-indigo-600 text-white"
                    : "border-slate-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-600 bg-slate-50"
                }`}
              >
                <Monitor size={13} />
                System Audio
                <span className="ml-auto text-[9px] opacity-60">Zoom / Webex</span>
              </button>
              <button
                onClick={() => setMode("browser")}
                className={`flex items-center gap-2 flex-1 px-3 py-2 text-xs font-medium rounded-md border transition-colors ${
                  mode === "browser"
                    ? "border-indigo-600 bg-indigo-600 text-white"
                    : "border-slate-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-600 bg-slate-50"
                }`}
              >
                <Mic size={13} />
                Microphone
                <span className="ml-auto text-[9px] opacity-60">In-person</span>
              </button>
            </div>
          </div>

          {/* Language */}
          <div className="flex items-center gap-3">
            <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Language</label>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="text-xs border border-slate-200 rounded-md px-2 py-1 bg-slate-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
            >
              {LANGUAGES.map((l) => (
                <option key={l.value} value={l.value}>{l.label}</option>
              ))}
            </select>
          </div>

          {error && (
            <div className="px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600">
              {error}
            </div>
          )}

          <button
            onClick={startRecording}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-red-500 hover:bg-red-600 text-white text-sm font-semibold rounded-xl transition-colors"
          >
            <Mic size={16} />
            Start Recording
          </button>
        </div>
      )}

      {/* Live transcript */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-2 bg-slate-50">
        {lines.length === 0 && isRecording && (
          <div className="text-center text-xs text-slate-400 pt-4">
            Listening… transcript will appear here.
          </div>
        )}

        {lines.map((line) => (
          <div
            key={line.line_id}
            className={`group flex items-start gap-2 ${line.is_interim ? "opacity-50" : ""}`}
          >
            {/* Speaker badge */}
            <span className="shrink-0 mt-0.5 px-1.5 py-0.5 text-[9px] font-semibold bg-slate-200 text-slate-600 rounded font-mono">
              {line.speaker_label}
            </span>

            {/* Timestamp */}
            <span className="shrink-0 text-[9px] text-slate-400 mt-0.5 font-mono">{line.timestamp}</span>

            {/* Text */}
            <span className={`flex-1 text-xs leading-relaxed ${line.is_flagged ? "text-amber-700 font-medium" : "text-slate-700"}`}>
              {line.text}
            </span>

            {/* Flag button */}
            <button
              onClick={() => flagLine(line.line_id)}
              className={`shrink-0 opacity-0 group-hover:opacity-100 p-1 rounded transition-all ${
                line.is_flagged ? "text-amber-500 opacity-100" : "text-slate-300 hover:text-amber-500"
              }`}
              title="Flag as important"
            >
              <Flag size={12} />
            </button>
          </div>
        ))}
      </div>

      {/* Stop button */}
      {isRecording && (
        <div className="px-4 py-3 border-t border-slate-200 bg-white">
          <button
            onClick={stopRecording}
            disabled={status === "stopping"}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-slate-900 hover:bg-slate-700 text-white text-sm font-semibold rounded-xl transition-colors disabled:opacity-50"
          >
            <Square size={14} />
            {status === "stopping" ? "Stopping…" : "Stop & Process"}
          </button>
          <p className="text-center text-[10px] text-slate-400 mt-1.5">
            AI summarization wizard will start after stopping.
          </p>
        </div>
      )}
    </div>
  );
}
