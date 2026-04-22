"use client";

/**
 * RecordingPanel — right-sidebar live recording UI for the note editor.
 *
 * Renders inside the note editor's right 1/3 sidebar while `showRecordingPopup`
 * is true. Replaces the legacy floating RecordingPopup overlay.
 *
 * Modes:
 *   live_v2 — Language-aware: SenseVoice live draft + Gemini V2 polish after meeting
 *   wasapi  — Legacy: server WASAPI loopback -> Deepgram
 *   browser — Legacy: browser mic -> Deepgram
 *
 * Protocol (WebSocket, live_v2 mode):
 *   Server -> client: { type: "status", status, message, language? }
 *   Server -> client: { type: "transcript", line_id, timestamp, text, draft: true }
 *   Server -> client: { type: "polished_transcript", text: "full markdown" }
 *   Client -> server: binary audio frames (PCM 16kHz mono int16)
 *   Client -> server: { type: "stop" }
 */

import { useState, useRef, useEffect, useCallback } from "react";
import { X, Mic, Monitor, Flag, Square, Wifi, Globe, Sparkles, Loader2 } from "lucide-react";
import { notesClient, type TranscriptLine, type PolishedSegment, type MeetingSummary } from "@/lib/api/notesClient";

const LANGUAGES = [
  { value: "auto", label: "Auto-Detect" },
  { value: "zh", label: "Chinese" },
  { value: "en", label: "English" },
  { value: "ja", label: "Japanese" },
  { value: "ko", label: "Korean" },
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
  ) => void;
}

export default function RecordingPanel({ noteId, onClose, onComplete }: Props) {
  const [mode, setMode] = useState<"live_v2" | "wasapi" | "browser">("live_v2");
  const [audioSource, setAudioSource] = useState<"system" | "mic">("system");
  const [language, setLanguage] = useState("auto");
  const [isRecording, setIsRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [status, setStatus] = useState<"idle" | "connecting" | "recording" | "stopping" | "polishing">("idle");
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [detectedLang, setDetectedLang] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string>("");
  const [polishedText, setPolishedText] = useState<string | null>(null);
  const [polishedSegments, setPolishedSegments] = useState<PolishedSegment[]>([]);
  const [polishedLanguage, setPolishedLanguage] = useState<string>("");
  const [polishedIsBilingual, setPolishedIsBilingual] = useState<boolean>(false);
  const [polishedKeyTopics, setPolishedKeyTopics] = useState<string[]>([]);
  const [polishedSummary, setPolishedSummary] = useState<MeetingSummary | null>(null);
  const [bytesSent, setBytesSent] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const durationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const startTimeRef = useRef<number>(0);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines, statusMessage]);

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
    setPolishedText(null);
    setBytesSent(0);
    setDetectedLang(null);
    setStatusMessage("");
    setStatus("connecting");

    const wsMode = mode === "live_v2" ? "live_v2" : mode;
    const wsLang = language === "auto" ? "auto" : language;
    const audioSrc = mode === "live_v2" ? audioSource : (mode === "wasapi" ? "system" : "mic");
    const url = notesClient.recordingWsUrl(noteId, wsMode, wsLang) + `&audio_source=${audioSrc}`;
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
          const line: TranscriptLine & { translation?: string; language?: string } = {
            line_id: msg.line_id,
            timestamp: msg.timestamp,
            speaker_label: msg.speaker_label || "",
            speaker_name: null,
            text: msg.text,
            is_flagged: false,
            is_interim: msg.is_interim || false,
            translation: msg.translation || "",
            language: msg.language || "",
          };
          setLines((prev) => {
            const existing = prev.findIndex((l) => l.line_id === line.line_id);
            if (existing !== -1) {
              const updated = [...prev];
              updated[existing] = line;
              return updated;
            }
            return [...prev, line];
          });
        } else if (msg.type === "status") {
          setStatusMessage(msg.message || "");
          if (msg.status === "language_detected") {
            setDetectedLang(msg.language || null);
          } else if (msg.status === "processing") {
            setStatus("polishing");
          } else if (msg.status === "complete") {
            setStatus("idle");
            setIsRecording(false);
          } else if (msg.status === "error") {
            setError(msg.message);
          }
        } else if (msg.type === "polished_transcript") {
          setPolishedText(msg.text);
          setPolishedSegments(Array.isArray(msg.segments) ? msg.segments : []);
          setPolishedLanguage(typeof msg.language === "string" ? msg.language : "");
          setPolishedIsBilingual(Boolean(msg.is_bilingual));
          setPolishedKeyTopics(Array.isArray(msg.key_topics) ? msg.key_topics : []);
          setPolishedSummary(msg.summary && typeof msg.summary === "object" ? msg.summary as MeetingSummary : null);
          setStatus("idle");
          setIsRecording(false);
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
      } catch { /* non-JSON */ }
    };

    ws.onerror = () => setError("WebSocket connection failed.");
    ws.onclose = () => {
      if (status === "recording") {
        setStatus("idle");
        setIsRecording(false);
      }
    };

    if ((mode === "live_v2" && audioSource === "mic") || mode === "browser") {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        });

        const audioCtx = new AudioContext();
        if (audioCtx.state === "suspended") {
          await audioCtx.resume();
        }
        audioContextRef.current = audioCtx;
        const source = audioCtx.createMediaStreamSource(stream);
        const actualRate = audioCtx.sampleRate;
        console.log(`[RecordingPanel] AudioContext: rate=${actualRate}, state=${audioCtx.state}`);

        const processor = audioCtx.createScriptProcessor(8192, 1, 1);
        processorRef.current = processor;

        let byteCount = 0;
        processor.onaudioprocess = (e: AudioProcessingEvent) => {
          if (ws.readyState !== WebSocket.OPEN) return;
          const input = e.inputBuffer.getChannelData(0);

          const ratio = 16000 / actualRate;
          const outLen = Math.floor(input.length * ratio);
          const pcm16 = new Int16Array(outLen);
          for (let i = 0; i < outLen; i++) {
            const srcIdx = Math.min(Math.floor(i / ratio), input.length - 1);
            const sample = input[srcIdx];
            pcm16[i] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
          }

          try {
            ws.send(pcm16.buffer);
            byteCount += pcm16.buffer.byteLength;
            if (byteCount % 32000 < pcm16.buffer.byteLength) {
              setBytesSent(byteCount);
            }
          } catch {
            /* WebSocket closed */
          }
        };

        source.connect(processor);
        processor.connect(audioCtx.destination);

        mediaRecorderRef.current = {
          stop: () => {
            try {
              processor.disconnect();
              source.disconnect();
              stream.getTracks().forEach((t) => t.stop());
              audioCtx.close();
            } catch { /* already closed */ }
          },
        } as unknown as MediaRecorder;

      } catch (err) {
        setError(`Microphone access denied: ${err instanceof Error ? err.message : String(err)}`);
        ws.close();
      }
    }
  }, [noteId, mode, language, audioSource, status]);

  const stopRecording = useCallback((polish: boolean = false) => {
    setStatusMessage("Stopping recording...");

    if (mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current = null;
    }

    if (polish) {
      setStatus("polishing");
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "stop" }));
      }
    } else {
      setStatus("idle");
      setIsRecording(false);
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "stop_no_polish" }));
        setTimeout(() => wsRef.current?.close(), 500);
      }
      const finalLines = lines.filter((l) => !l.is_interim);
      onComplete(finalLines, duration, null);
    }
  }, [wsRef, lines, duration, onComplete]);

  const flagLine = useCallback((lineId: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "flag", line_id: lineId }));
    }
    setLines((prev) => prev.map((l) => l.line_id === lineId ? { ...l, is_flagged: true } : l));
  }, []);

  const handlePolishedDone = useCallback(() => {
    const finalLines = lines.filter((l) => !l.is_interim);
    onComplete(finalLines, duration, {
      segments: polishedSegments,
      language: polishedLanguage,
      is_bilingual: polishedIsBilingual,
      key_topics: polishedKeyTopics,
      summary: polishedSummary,
    });
  }, [lines, duration, onComplete, polishedSegments, polishedLanguage, polishedIsBilingual, polishedKeyTopics, polishedSummary]);

  const formatDuration = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  };

  return (
    <div className="h-full w-full flex flex-col bg-white overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-slate-900 text-white shrink-0">
        <div className="flex items-center gap-3 flex-wrap min-w-0">
          {isRecording && (
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 bg-red-400 rounded-full animate-pulse" />
              <span className="text-xs font-semibold font-mono">{formatDuration(duration)}</span>
            </span>
          )}
          {status === "connecting" && (
            <span className="flex items-center gap-1.5 text-xs text-slate-300">
              <Wifi size={12} className="animate-pulse" />
              Connecting...
            </span>
          )}
          {status === "polishing" && (
            <span className="flex items-center gap-1.5 text-xs text-amber-300">
              <Sparkles size={12} className="animate-pulse" />
              Polishing with Gemini...
            </span>
          )}
          {status === "idle" && !isRecording && !polishedText && (
            <span className="text-xs text-slate-400">Ready to record</span>
          )}
          {polishedText && (
            <span className="flex items-center gap-1.5 text-xs text-green-300">
              <Sparkles size={12} />
              Polished transcript ready
            </span>
          )}
          {isRecording && (
            <span className="text-[10px] text-slate-400 font-mono">
              {audioSource === "system" ? "system audio" : bytesSent > 0 ? `${(bytesSent / 1024).toFixed(0)}KB sent` : "waiting for mic..."}
            </span>
          )}
          {detectedLang && isRecording && (
            <span className="flex items-center gap-1 text-[10px] text-indigo-300 bg-indigo-900/40 px-2 py-0.5 rounded">
              <Globe size={10} />
              {detectedLang.toUpperCase()}
            </span>
          )}
        </div>
        <button onClick={onClose} className="p-1 text-slate-400 hover:text-white rounded-lg transition-colors shrink-0" title="Close recording panel">
          <X size={16} />
        </button>
      </div>

      {/* Controls (pre-recording) */}
      {!isRecording && status !== "polishing" && !polishedText && (
        <div className="px-4 py-3 border-b border-slate-100 space-y-3 shrink-0">
          <div>
            <p className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mb-1.5">Audio Source</p>
            <div className="flex flex-col gap-2">
              <button
                onClick={() => { setMode("live_v2"); setAudioSource("system"); }}
                className={`flex items-center gap-2 w-full px-3 py-2 text-xs font-medium rounded-md border transition-colors ${
                  mode === "live_v2" && audioSource === "system"
                    ? "border-indigo-600 bg-indigo-600 text-white"
                    : "border-slate-200 text-slate-600 hover:border-indigo-300 bg-slate-50"
                }`}
              >
                <Monitor size={13} />
                System Audio
                <span className="ml-auto text-[9px] opacity-60">Zoom / YouTube</span>
              </button>
              <button
                onClick={() => { setMode("live_v2"); setAudioSource("mic"); }}
                className={`flex items-center gap-2 w-full px-3 py-2 text-xs font-medium rounded-md border transition-colors ${
                  mode === "live_v2" && audioSource === "mic"
                    ? "border-indigo-600 bg-indigo-600 text-white"
                    : "border-slate-200 text-slate-600 hover:border-indigo-300 bg-slate-50"
                }`}
              >
                <Mic size={13} />
                Microphone
                <span className="ml-auto text-[9px] opacity-60">In-person</span>
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider shrink-0">Lang</label>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="text-xs border border-slate-200 rounded-md px-2 py-1 bg-slate-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 flex-1"
            >
              {LANGUAGES.map((l) => (
                <option key={l.value} value={l.value}>{l.label}</option>
              ))}
            </select>
          </div>
          {mode === "live_v2" && language === "auto" && (
            <p className="text-[9px] text-slate-400">Detected from first 3 seconds</p>
          )}

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

      {/* Recording status banner */}
      {isRecording && mode === "live_v2" && (
        <div className="px-4 py-1.5 bg-blue-50 border-b border-blue-200 flex items-center gap-2 shrink-0">
          <Mic size={10} className="text-blue-600 animate-pulse" />
          <span className="text-[10px] text-blue-700 font-medium truncate">{statusMessage || "Recording + live transcribing..."}</span>
        </div>
      )}

      {/* Polishing status banner */}
      {statusMessage && (status === "polishing" || status === "stopping") && !isRecording && (
        <div className="px-4 py-2 bg-indigo-50 border-b border-indigo-200 flex items-center gap-2 shrink-0">
          <Loader2 size={12} className="animate-spin text-indigo-500" />
          <span className="text-[10px] text-indigo-700 truncate">{statusMessage}</span>
        </div>
      )}

      {/* Live transcript — fills remaining vertical space */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-2 bg-slate-50 min-h-0">
        {lines.length === 0 && isRecording && (
          <div className="text-center text-xs text-slate-400 pt-8 space-y-2">
            <Mic size={24} className="mx-auto text-red-400 animate-pulse" />
            <p className="font-medium">Recording in progress</p>
            <p className="text-[10px] text-slate-300">Live transcript with English translation will appear every ~8 seconds. First transcript may take up to 30s while the ASR model loads.</p>
          </div>
        )}

        {lines.map((line) => (
          <div
            key={line.line_id}
            className={`group flex flex-col gap-0.5 py-1.5 border-b border-slate-100 ${line.is_interim ? "opacity-50" : ""}`}
          >
            <div className="flex items-start gap-2">
              <span className="shrink-0 mt-0.5 px-1.5 py-0.5 text-[9px] font-semibold bg-indigo-100 text-indigo-700 rounded font-mono">
                {line.timestamp}
              </span>
              {(line as unknown as Record<string, string>).language && (
                <span className="shrink-0 mt-0.5 px-1 py-0.5 text-[8px] font-bold bg-slate-100 text-slate-500 rounded uppercase">
                  {(line as unknown as Record<string, string>).language}
                </span>
              )}
              <span className={`flex-1 text-xs leading-relaxed ${line.is_flagged ? "text-amber-700 font-medium" : "text-slate-800"}`}>
                {line.text}
              </span>
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
            {(line as unknown as Record<string, string>).translation && (
              <div className="ml-10 text-[11px] text-blue-600 leading-relaxed italic">
                {(line as unknown as Record<string, string>).translation}
              </div>
            )}
          </div>
        ))}

        {polishedText && (
          <div className="mt-4 pt-4 border-t border-green-200">
            <div className="flex items-center gap-2 mb-2">
              <Sparkles size={12} className="text-green-600" />
              <span className="text-[10px] font-bold text-green-700 uppercase">Polished Transcript</span>
            </div>
            <div className="text-xs text-slate-700 leading-relaxed whitespace-pre-wrap max-h-60 overflow-y-auto">
              {polishedText.slice(0, 1000)}
              {polishedText.length > 1000 && (
                <span className="text-slate-400">... ({polishedText.length} chars total)</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Bottom action buttons */}
      {isRecording && (
        <div className="px-4 py-3 border-t border-slate-200 bg-white space-y-2 shrink-0">
          <button
            onClick={() => stopRecording(true)}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold rounded-xl transition-colors"
          >
            <Sparkles size={14} />
            Stop &amp; AI Polish
          </button>
          <button
            onClick={() => stopRecording(false)}
            className="w-full flex items-center justify-center gap-2 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-medium rounded-lg transition-colors"
          >
            <Square size={12} />
            Stop (save audio only)
          </button>
        </div>
      )}

      {polishedText && (
        <div className="px-4 py-3 border-t border-slate-200 bg-white space-y-2 shrink-0">
          <button
            onClick={handlePolishedDone}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-green-600 hover:bg-green-700 text-white text-sm font-semibold rounded-xl transition-colors"
          >
            <Sparkles size={14} />
            Save Both (Draft + Polished)
          </button>
          <p className="text-[9px] text-slate-400 text-center">
            Raw draft ({lines.filter(l => !l.is_interim).length} lines) and AI-polished version will both be saved to the note
          </p>
        </div>
      )}

      {status === "polishing" && (
        <div className="px-4 py-3 border-t border-slate-200 bg-white shrink-0">
          <div className="w-full flex items-center justify-center gap-2 py-2.5 text-sm text-slate-500">
            <Loader2 size={14} className="animate-spin" />
            Generating polished transcript...
          </div>
        </div>
      )}
    </div>
  );
}
