# Recording Sidebar — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the live recording UI out of the floating `RecordingPopup` overlay and into the existing right-hand sidebar of the note editor, so recording shares space with (and replaces) the search panel instead of covering the editor.

**Architecture:** The note editor page already has a 2/3 + 1/3 split. The right 1/3 currently renders either `PostMeetingWizard` (when `note.summary_status` is in the wizard set) or `NoteSearchPanel` (default). Phase 1 adds a third branch: when `showRecordingPopup` is true, render a new `RecordingPanel` instead. `RecordingPanel` is the same component as `RecordingPopup` with the fixed-overlay shell stripped off — all WebSocket, audio-capture, and state logic is unchanged. The floating `<RecordingPopup />` render at the bottom of `NotesEditorView` is removed.

**Tech Stack:** Next.js 15 App Router, React 19, TypeScript, Zustand store, Tailwind CSS v4, `lucide-react` icons. No test framework is configured for the frontend — verification is `npx tsc --noEmit` (type check) plus a manual smoke test in `next dev`.

**Scope note:** Phase 1 is UI container only. Phases 2 and 3 (post-recording intelligence panel, AI job cards) are out of scope.

---

## Kickoff Reading

Read these before touching code, in order:

1. `docs/notes_recording_ux_design.md` — the spec. Phase 1 section (lines ~121-126) is the authoritative scope for this plan.
2. `frontend/src/components/domain/notes/RecordingPopup.tsx` — the current recording UI. `RecordingPanel` (Task 2) is this file with the overlay shell stripped off. Understand how WebSocket + audio capture flow so you trust the copy is safe.
3. `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx` — the only file that needs editing (Task 3). Note the existing right-panel branch on `showWizard`.
4. `frontend/src/app/(dashboard)/notes/[id]/NotesEditorContainer.tsx` and `frontend/src/app/(dashboard)/notes/[id]/store.ts` — confirm `showRecordingPopup`, `setShowRecordingPopup`, `onCloseRecording`, `onRecordingComplete` all stay with the names this plan assumes.
5. *Optional:* `.claude/skills/meeting-transcription/SKILL.md` — background on the WebSocket transcription protocol. Not required for Phase 1 (no protocol changes), but handy if a message type looks unfamiliar.

---

## File Structure

- **Create:** `frontend/src/components/domain/notes/RecordingPanel.tsx` — Sidebar-resident recording UI. Exports `RecordingPanel` with the same `Props` as `RecordingPopup` (`{ noteId, onClose, onComplete }`). Internally identical to `RecordingPopup`: same WebSocket protocol, same `MediaRecorder`/`AudioContext` capture, same line buffering, same `stop & polish` / `stop only` buttons. The only differences are:
  - Outermost container uses `h-full w-full flex flex-col bg-white` instead of `fixed bottom-6 right-6 z-50 w-[520px] max-h-[650px] ... rounded-xl shadow-2xl overflow-hidden`.
  - No outer rounded corners or drop shadow (the sidebar already has `border-l border-slate-200`).
  - Header keeps the dark `bg-slate-900` bar so the recording state is still visually prominent.
  - Transcript scroll area keeps `flex-1 overflow-y-auto` so it fills remaining vertical space regardless of sidebar height.
- **Modify:** `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx` — Replace the floating render with a third branch inside the existing right-panel conditional. Priority order becomes: recording → wizard → search.
- **Delete:** `frontend/src/components/domain/notes/RecordingPopup.tsx` — Replaced by `RecordingPanel`. No other file imports it (verified below in Task 1 Step 1).

**What does not change:**
- `NotesEditorContainer.tsx`: `showRecordingPopup` state, `onOpenRecording`, `onCloseRecording`, `onRecordingComplete` handlers stay as-is.
- `store.ts`: `showRecordingPopup` state key name is unchanged (renaming would be churn for zero user-visible benefit; it's a boolean that toggles the recording UI, whatever its container).
- Props on `NotesEditorView`: `showRecordingPopup: boolean` and the three recording handlers stay with the same names.
- `PostMeetingWizard`, `NoteSearchPanel`: untouched.
- WebSocket endpoints, `asr_worker.py`, `live_transcription.py`: untouched.

---

## Task 1: Verify `RecordingPopup` has no other importers

**Files:**
- Read only.

- [ ] **Step 1: Confirm the only importer is `NotesEditorView.tsx`**

Run (from repo root):

```bash
grep -rn "RecordingPopup" frontend/src --include='*.ts' --include='*.tsx'
```

Expected output — exactly these two lines (paths may differ slightly on Windows):

```
frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx:12:import RecordingPopup from "@/components/domain/notes/RecordingPopup";
frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx:217:        <RecordingPopup
frontend/src/components/domain/notes/RecordingPopup.tsx:... (the file itself)
```

If any other file imports `RecordingPopup`, stop and add a task to update it before proceeding. Otherwise continue.

- [ ] **Step 2: Confirm the sidebar store key name**

Run:

```bash
grep -n "showRecordingPopup" frontend/src/app/\(dashboard\)/notes/\[id\]/store.ts frontend/src/app/\(dashboard\)/notes/\[id\]/NotesEditorContainer.tsx
```

Expected: `showRecordingPopup` and `setShowRecordingPopup` defined in `store.ts`, used in `NotesEditorContainer.tsx`. We keep these names.

- [ ] **Step 3: No commit yet** — this is a read-only verification task.

---

## Task 2: Create `RecordingPanel.tsx`

**Files:**
- Create: `frontend/src/components/domain/notes/RecordingPanel.tsx`

This is a mechanical transform of `RecordingPopup.tsx`. All business logic is copied verbatim; only the outer shell changes.

- [ ] **Step 1: Write `RecordingPanel.tsx`**

Create the file with this exact content:

```tsx
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
import { notesClient, type TranscriptLine } from "@/lib/api/notesClient";

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
  onComplete: (lines: TranscriptLine[], durationSeconds: number) => void;
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
      onComplete(finalLines, duration);
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
    onComplete(finalLines, duration);
  }, [lines, duration, onComplete]);

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
```

**Notes on the diff vs. `RecordingPopup`:**
- Outer div class: `h-full w-full flex flex-col bg-white overflow-hidden` (was `fixed bottom-6 right-6 z-50 w-[520px] max-h-[650px] flex flex-col bg-white border border-slate-200 rounded-xl shadow-2xl overflow-hidden`).
- `flex-col` audio-source buttons (System / Mic) instead of side-by-side — sidebar is narrower than 520 px, so vertical stacking reads better.
- Language row uses `flex-1` select for narrow widths; the "Detected from first 3 seconds" hint moved to its own paragraph.
- Dropped the "Live draft + EN translation" trailing label on the recording banner — too long for a narrow column.
- Translation indent reduced from `ml-14` to `ml-10`.
- `shrink-0` added to every non-scroll region so the live-transcript area (`flex-1 ... min-h-0`) gets all remaining vertical space regardless of sidebar height.
- Console log prefix `[RecordingPopup]` → `[RecordingPanel]` so logs match the new component.
- `startRecording`'s `useCallback` deps include `audioSource` (the old file referenced `audioSource` inside the callback but did not list it — harmless with `useCallback` but a lint nit; fixed while we're here).

- [ ] **Step 2: Type-check the new file**

Run (from `frontend/`):

```bash
cd frontend && npx tsc --noEmit
```

Expected: exits 0. `RecordingPanel.tsx` compiles and `RecordingPopup.tsx` still compiles (we haven't deleted it yet).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/domain/notes/RecordingPanel.tsx
git commit -m "feat(notes): add RecordingPanel sidebar variant of RecordingPopup"
```

---

## Task 3: Wire `RecordingPanel` into the right sidebar and delete `RecordingPopup`

**Files:**
- Modify: `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx`
- Delete: `frontend/src/components/domain/notes/RecordingPopup.tsx`

- [ ] **Step 1: Update the import in `NotesEditorView.tsx`**

Change line 12 from:

```tsx
import RecordingPopup from "@/components/domain/notes/RecordingPopup";
```

to:

```tsx
import RecordingPanel from "@/components/domain/notes/RecordingPanel";
```

- [ ] **Step 2: Replace the right-panel conditional (lines ~197-212)**

Find this block:

```tsx
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
```

Replace with:

```tsx
        {/* Right — Recording panel OR Post-meeting wizard OR Search panel (1/3) */}
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
            />
          ) : (
            <NoteSearchPanel
              contextTickers={note.company_tickers}
              contextNoteType={note.note_type}
            />
          )}
        </div>
```

The priority is **recording → wizard → search**. During recording, the wizard UI would be irrelevant anyway (`summary_status` is still `none` until the recording completes and flips to `awaiting_speakers`), so in practice this branch only displaces `NoteSearchPanel`.

- [ ] **Step 3: Remove the floating popup render (lines ~215-222)**

Find and delete this block at the bottom of the component's JSX:

```tsx
      {/* Recording popup */}
      {showRecordingPopup && (
        <RecordingPopup
          noteId={note.note_id}
          onClose={onCloseRecording}
          onComplete={onRecordingComplete}
        />
      )}
```

After deletion, the outer element ends with just the `{/* Main content area */}` `<div>` followed by the closing `</div>` of the component's root `<div className="flex flex-col h-full overflow-hidden">`.

- [ ] **Step 4: Delete the old file**

```bash
rm frontend/src/components/domain/notes/RecordingPopup.tsx
```

(On Windows in git-bash this is the same command. If the shell insists, `git rm frontend/src/components/domain/notes/RecordingPopup.tsx` also works.)

- [ ] **Step 5: Type-check**

Run (from `frontend/`):

```bash
cd frontend && npx tsc --noEmit
```

Expected: exits 0. No dangling references to `RecordingPopup` remain.

- [ ] **Step 6: Grep verify**

Run (from repo root):

```bash
grep -rn "RecordingPopup" frontend/src --include='*.ts' --include='*.tsx'
```

Expected: **no output** (zero matches). The identifier `RecordingPopup` is fully removed from the frontend.

Note: `showRecordingPopup` / `setShowRecordingPopup` (boolean state in the store) intentionally keep their names; `grep RecordingPopup` will match those too. So if you see matches, confirm they are only the `showRecordingPopup` / `setShowRecordingPopup` identifiers — those are fine and expected. If any file still references the `RecordingPopup` component name, fix and re-run.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/\(dashboard\)/notes/\[id\]/NotesEditorView.tsx frontend/src/components/domain/notes/RecordingPopup.tsx
git commit -m "feat(notes): render recording UI in right sidebar instead of floating overlay"
```

---

## Task 4: Manual smoke test

No automated UI tests exist in this repo, so Phase 1 is verified by running the app and exercising the recording flow.

**Files:** none modified.

- [ ] **Step 1: Start backend**

In one terminal (from repo root):

```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

Expected: server comes up on port 8000 with the usual startup logs.

- [ ] **Step 2: Start frontend**

In another terminal (from repo root):

```bash
cd frontend && npm run dev
```

Expected: Next.js dev server on port 3000, compiled with no TypeScript errors.

- [ ] **Step 3: Exercise the flow in the browser**

Navigate to `http://localhost:3000/notes`, open any note (or create a new one), then verify each of the following in order. Tick off the sub-items as you go:

  - [ ] On load, the right sidebar shows `NoteSearchPanel` (the "All Sources" search UI).
  - [ ] Click **Record Audio** in the top bar.
  - [ ] The right sidebar switches to the recording panel: dark header with "Ready to record", Audio Source buttons (System / Mic stacked vertically), Language dropdown, red **Start Recording** button. The main editor area on the left remains visible and unchanged — no floating overlay covers it.
  - [ ] Click **Start Recording** with System Audio + Auto-Detect language. The header changes to show the red pulsing dot + `00:00:00` timer; the blue "Recording + live transcribing..." banner appears just below the header.
  - [ ] Wait ~10-30 s (first ASR load). Live transcript lines appear in the middle scroll region, each with a timestamp chip and (if non-English) an English translation below.
  - [ ] The transcript region scrolls as new lines arrive; older lines remain scrollable by dragging.
  - [ ] Click **Stop & AI Polish**. Header changes to amber "Polishing with Gemini..."; the indigo "Stopping recording..." banner appears briefly.
  - [ ] After polish finishes, the polished transcript preview appears at the bottom of the scroll region (green "Polished Transcript" heading), and a green **Save Both (Draft + Polished)** button appears at the bottom of the sidebar.
  - [ ] Click **Save Both**. The sidebar switches to `PostMeetingWizard` (awaiting_speakers step) — this is the existing wizard behaviour, unchanged.
  - [ ] Click the back arrow at top-left of the editor, then re-enter the same note. The wizard state persists (still showing awaiting_speakers).
  - [ ] Start a fresh new note, click **Record Audio**, then click the ✕ close button in the recording panel header *before* starting. The sidebar returns to `NoteSearchPanel`, no stray WebSocket messages, no errors.
  - [ ] Start recording, click **Stop (save audio only)**. Sidebar switches to wizard (awaiting_speakers), same as the polish path.
  - [ ] Repeat with Microphone audio source. Browser prompts for mic permission on first use. Live transcript still appears in the sidebar.

If any step fails, file the failure as a bug with a screenshot and steps, and revert Task 3's commit (`git revert HEAD`) rather than patching forward blindly — the two commits are small so a revert is cheap.

- [ ] **Step 4: No commit for this task** — it's verification only.

---

## Self-Review Checklist

**Spec coverage:**
- Phase 1 item 1 "Remove RecordingPopup floating component" → Task 3 Step 3 (JSX removal) + Step 4 (file deletion).
- Phase 1 item 2 "Add recording state to the right panel (replace search panel during recording)" → Task 3 Step 2 (new conditional branch).
- Phase 1 item 3 "Show recording controls + live transcript in the right panel" → Task 2 (RecordingPanel component with controls + transcript).
- Phase 1 item 4 "Keep same WebSocket logic, just change the UI container" → Task 2 Step 1 copies all WebSocket / audio-capture code verbatim from `RecordingPopup`; the diff is styling only.
- Phase 2 and Phase 3 items: **explicitly out of scope** and not in any task.

**Placeholder scan:** No `TBD`, `TODO`, "implement later", or unexplained `...` in code blocks. Every JSX block is complete.

**Type / name consistency:**
- `RecordingPanel` is the component name in Task 2, Task 3 Step 1 import, and Task 3 Step 2 JSX — all consistent.
- `showRecordingPopup`, `onCloseRecording`, `onRecordingComplete`, `noteId`, `onClose`, `onComplete` — consistent with `NotesEditorContainer.tsx`, `store.ts`, and the new component's `Props` interface.
- `useCallback` deps on `startRecording` include `audioSource` now (fix noted), which is also safe because `audioSource` is used inside the callback.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-22-recording-sidebar-phase-1.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
