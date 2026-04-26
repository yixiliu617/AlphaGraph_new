"use client";

/**
 * AudioUploadModal — drag-and-drop an audio file (or click to browse), pick
 * a language hint, hit Transcribe. Backend runs the same Gemini 2.5 Flash
 * polish pipeline used by live recording, then we navigate the user into
 * the freshly-created note.
 *
 * The default language is "auto" — the backend extracts the first 10 sec
 * via ffmpeg and runs SenseVoice for language ID before passing the right
 * vocabulary file to Gemini. User can override (zh / ja / ko / en) if they
 * already know the language; that skips the detection step and is faster /
 * more reliable for low-quality audio where SenseVoice flips between zh/ja.
 */

import { useCallback, useRef, useState } from "react";
import { Loader2, Mic, Upload, X } from "lucide-react";
import { notesClient } from "@/lib/api/notesClient";

const ACCEPTED_EXTENSIONS = [".wav", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".webm"];

const LANGUAGE_OPTIONS: { key: "auto" | "zh" | "ja" | "ko" | "en"; label: string; hint: string }[] = [
  { key: "auto", label: "Auto-detect",                  hint: "SenseVoice on first 10 sec" },
  { key: "zh",   label: "Chinese (with English)",       hint: "ZH/EN vocab loaded" },
  { key: "ja",   label: "Japanese (with English)",      hint: "JA/EN vocab loaded" },
  { key: "ko",   label: "Korean (with English)",        hint: "KO/EN vocab loaded" },
  { key: "en",   label: "English",                      hint: "default vocab" },
];

// "other" reveals a text input where the user can type any language name
// (e.g. "French", "Arabic", "Vietnamese", "Thai", "Persian"). The free-form
// string is passed through to the backend prompt as-is — Gemini handles
// pretty much any human language reasonably well.
type TranslationKey = "none" | "en" | "zh-hans" | "zh-hant" | "ja" | "ko" | "other";
const TRANSLATION_OPTIONS: { key: TranslationKey; label: string }[] = [
  { key: "none",    label: "(no translation)" },
  { key: "en",      label: "English" },
  { key: "zh-hans", label: "简体中文 (Simplified Chinese)" },
  { key: "zh-hant", label: "繁體中文 (Traditional Chinese)" },
  { key: "ja",      label: "日本語 (Japanese)" },
  { key: "ko",      label: "한국어 (Korean)" },
  { key: "other",   label: "Other (type your own…)" },
];

const NOTE_TYPE_OPTIONS = [
  { value: "meeting_transcript", label: "Meeting Transcript" },
  { value: "earnings_call",      label: "Earnings Call"      },
  { value: "management_meeting", label: "Mgmt Meeting"       },
  { value: "conference",         label: "Conference"         },
  { value: "internal",           label: "Internal"           },
];

interface Props {
  onClose:    () => void;
  onComplete: (noteId: string) => void;
}

export default function AudioUploadModal({ onClose, onComplete }: Props) {
  const [file,      setFile]      = useState<File | null>(null);
  const [duration,  setDuration]  = useState<number | null>(null);   // seconds, null until decoded
  const [title,     setTitle]     = useState<string>("");
  const [language,  setLanguage]  = useState<"auto" | "zh" | "ja" | "ko" | "en">("auto");
  const [translation, setTranslation] = useState<TranslationKey>("en");
  const [translationOther, setTranslationOther] = useState<string>("");   // free-form when translation==="other"
  const [noteType,  setNoteType]  = useState<string>("meeting_transcript");
  const [dragActive, setDragActive] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [statusMsg,  setStatusMsg]  = useState<string>("");
  const [error,      setError]      = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const acceptFile = useCallback((f: File) => {
    const lower = f.name.toLowerCase();
    if (!ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext))) {
      setError(`Unsupported file type. Allowed: ${ACCEPTED_EXTENSIONS.join(" ")}`);
      return;
    }
    setError(null);
    setFile(f);
    setDuration(null);
    if (!title) {
      // Default title = filename without extension
      const dot = f.name.lastIndexOf(".");
      setTitle(dot > 0 ? f.name.slice(0, dot) : f.name);
    }
    // Decode duration via HTMLAudioElement so the modal can show the file
    // length and pick correct status messages without trusting the server's
    // pacing. Same-origin blob URL — no upload yet.
    try {
      const url = URL.createObjectURL(f);
      const a = new Audio();
      a.preload = "metadata";
      a.onloadedmetadata = () => {
        if (Number.isFinite(a.duration)) setDuration(a.duration);
        URL.revokeObjectURL(url);
      };
      a.onerror = () => URL.revokeObjectURL(url);
      a.src = url;
    } catch {
      // Failed to decode — leave duration null; status messages fall back
      // to the duration-agnostic schedule.
    }
  }, [title]);

  function fmtDuration(sec: number): string {
    const total = Math.round(sec);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h > 0 ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
  }

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault(); e.stopPropagation();
    setDragActive(false);
    const f = e.dataTransfer.files?.[0];
    if (f) acceptFile(f);
  };

  const handleSubmit = async () => {
    if (!file || submitting) return;
    setSubmitting(true);
    setError(null);
    setStatusMsg("Uploading audio…");

    try {
      // Quick UI cue while the backend runs the long Gemini call. The actual
      // pipeline stages are not streamed to us; we schedule rolling messages
      // based on the audio's known duration so we don't claim "splitting"
      // when nothing is being split.
      const stages: { delay: number; msg: string }[] = [
        { delay: 1500, msg: "Detecting language…" },
        { delay: 8000, msg: "Polishing transcript with Gemini 2.5 Flash…" },
      ];
      // The smart wrapper splits only when audio is >55 min. Anything
      // shorter rides through as a single Gemini call — which can still
      // take 1-3 min for a 30-40 min file, but it isn't "splitting".
      if (duration && duration > 55 * 60) {
        stages.push({ delay: 60000, msg: "Long audio — splitting at silence and processing chunks…" });
        stages.push({ delay: 180000, msg: "Still processing chunks…" });
      } else {
        // Mid-length fallback so the user sees fresh activity around the
        // ~1 min mark even on a 30-min audio.
        stages.push({ delay: 90000, msg: "Still polishing — Gemini is processing the full audio…" });
      }
      const timers = stages.map((s) => setTimeout(() => setStatusMsg(s.msg), s.delay));

      // If user picked "Other", send the typed string as the translation
      // language. Backend prompt accepts any free-form name (e.g. "French",
      // "Arabic", "Vietnamese") and passes it straight to Gemini.
      const xlate: string =
        translation === "other"
          ? translationOther.trim()
          : translation;
      if (translation === "other" && !xlate) {
        setError("Please type a translation language, or pick another option.");
        setSubmitting(false);
        return;
      }

      const res = await notesClient.uploadTranscribeAudio(file, {
        title: title || undefined,
        language,
        translation_language: xlate,    // backend accepts arbitrary strings (see notesClient typedef)
        note_type: noteType,
      });

      timers.forEach(clearTimeout);
      const d = res.data;
      const lang = `${d.language}${d.is_bilingual ? "/en" : ""}`;
      const timing = (() => {
        const parts: string[] = [];
        if (typeof d.gemini_seconds === "number") parts.push(`Gemini ${d.gemini_seconds.toFixed(1)}s`);
        if (typeof d.total_seconds  === "number" && d.total_seconds  !== d.gemini_seconds)
          parts.push(`total ${d.total_seconds.toFixed(1)}s`);
        if ((d.chunk_count ?? 1) > 1) parts.push(`${d.chunk_count} chunks`);
        return parts.length ? ` · ${parts.join(" · ")}` : "";
      })();
      setStatusMsg(`Done · ${d.segments} segments · ${lang}${timing}`);
      onComplete(d.note_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatusMsg("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4"
         onClick={(e) => { if (e.target === e.currentTarget && !submitting) onClose(); }}>
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg overflow-hidden">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200">
          <div className="flex items-center gap-2">
            <Mic size={16} className="text-indigo-600" />
            <h2 className="text-sm font-semibold text-slate-800">
              Upload audio to transcribe
            </h2>
          </div>
          <button
            onClick={onClose}
            disabled={submitting}
            className="text-slate-400 hover:text-slate-600 disabled:opacity-40"
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* Drop zone */}
          <div
            onDragEnter={(e) => { e.preventDefault(); setDragActive(true); }}
            onDragOver={(e)  => { e.preventDefault(); setDragActive(true); }}
            onDragLeave={(e) => { e.preventDefault(); setDragActive(false); }}
            onDrop={handleDrop}
            onClick={() => !submitting && fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
              dragActive
                ? "border-indigo-500 bg-indigo-50"
                : file
                  ? "border-emerald-400 bg-emerald-50/40"
                  : "border-slate-300 bg-slate-50 hover:border-slate-400 hover:bg-slate-100"
            } ${submitting ? "pointer-events-none opacity-60" : ""}`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_EXTENSIONS.join(",")}
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) acceptFile(f); }}
            />
            <Upload size={20} className={`mx-auto mb-2 ${file ? "text-emerald-600" : "text-slate-400"}`} />
            {file ? (
              <>
                <div className="text-xs font-semibold text-emerald-700">{file.name}</div>
                <div className="text-[10px] text-slate-500 mt-0.5">
                  {(file.size / 1024 / 1024).toFixed(1)} MB
                  {duration != null && (
                    <>
                      {" · "}
                      <span className="font-semibold text-slate-700">{fmtDuration(duration)}</span>
                      {duration > 55 * 60 && (
                        <span className="ml-1 text-amber-600">(will be split into chunks)</span>
                      )}
                    </>
                  )}
                  {" · click to replace"}
                </div>
              </>
            ) : (
              <>
                <div className="text-xs font-semibold text-slate-700">
                  Drop audio file here or click to browse
                </div>
                <div className="text-[10px] text-slate-400 mt-0.5">
                  {ACCEPTED_EXTENSIONS.join(" · ")}
                </div>
              </>
            )}
          </div>

          {/* Title */}
          <div>
            <label className="block text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
              Note title
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={submitting}
              placeholder="(defaults to filename)"
              className="w-full h-9 px-3 rounded-md border border-slate-200 bg-white text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          {/* Language (audio source) + Translation target */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
                Audio language
              </label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value as typeof language)}
                disabled={submitting}
                className="w-full h-9 px-2 rounded-md border border-slate-200 bg-white text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
              >
                {LANGUAGE_OPTIONS.map((o) => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
              <div className="text-[10px] text-slate-400 mt-1">
                {LANGUAGE_OPTIONS.find((o) => o.key === language)?.hint}
              </div>
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
                Translation
              </label>
              <select
                value={translation}
                onChange={(e) => setTranslation(e.target.value as TranslationKey)}
                disabled={submitting}
                className="w-full h-9 px-2 rounded-md border border-slate-200 bg-white text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
              >
                {TRANSLATION_OPTIONS.map((o) => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
              {translation === "other" ? (
                <input
                  type="text"
                  value={translationOther}
                  onChange={(e) => setTranslationOther(e.target.value)}
                  disabled={submitting}
                  maxLength={80}
                  placeholder="e.g. French, Arabic, Vietnamese, Thai, Persian, Hindi…"
                  className="mt-1.5 w-full h-9 px-3 rounded-md border border-indigo-200 bg-indigo-50 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                />
              ) : (
                <div className="text-[10px] text-slate-400 mt-1">
                  Side-by-side translation column. Pick "(no translation)" for monolingual transcripts.
                </div>
              )}
            </div>
          </div>

          {/* Note type — full row to free up grid space above */}
          <div>
            <label className="block text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
              Note type
            </label>
            <select
              value={noteType}
              onChange={(e) => setNoteType(e.target.value)}
              disabled={submitting}
              className="w-full h-9 px-2 rounded-md border border-slate-200 bg-white text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
            >
              {NOTE_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {/* Chinese script hint -- only relevant when source or translation is Chinese */}
          {(language === "zh" || translation === "zh-hans" || translation === "zh-hant") && (
            <div className="text-[11px] text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-md px-3 py-2">
              <span className="font-semibold">Chinese script:</span> new transcripts default to <b>Simplified (简体)</b>.
              After upload, the note view has a one-click toggle to switch the entire transcript between
              <b> Simplified (简体)</b> and <b>Traditional (繁體)</b>.
            </div>
          )}

          {/* Status / error */}
          {error && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
              {error}
            </div>
          )}
          {submitting && statusMsg && (
            <div className="flex items-center gap-2 text-xs text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-md px-3 py-2">
              <Loader2 size={14} className="animate-spin" />
              {statusMsg}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-t border-slate-200 bg-slate-50">
          <div className="text-[10px] text-slate-400">
            Long audio ({">"}30 min) may take 2-3 minutes to process.
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              disabled={submitting}
              className="h-8 px-3 text-xs font-medium text-slate-600 hover:text-slate-800 disabled:opacity-40"
            >
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={!file || submitting}
              className="h-8 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700 transition-colors disabled:bg-slate-300 disabled:cursor-not-allowed flex items-center gap-1.5"
            >
              {submitting ? <Loader2 size={13} className="animate-spin" /> : null}
              {submitting ? "Transcribing…" : "Transcribe"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
