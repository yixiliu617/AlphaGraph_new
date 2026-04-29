"use client";

/**
 * BatchTranscribeModal -- folder-batch transcription.
 *
 * State machine: PICK -> SCAN -> CONFIRM -> RUNNING -> DONE.
 *
 * Tier 1 (local):     paste folder path; backend writes transcripts directly.
 * Tier 2 (Chromium):  showDirectoryPicker; browser writes via FS Access API.
 * Tier 3 (else):      <input webkitdirectory>; transcripts download to Downloads/.
 */

import { useEffect, useRef, useState } from "react";
import { Folder, Loader2, X } from "lucide-react";

import {
  detectTier, tierLabel, probeFile,
  runBatchTier1, runBatchClient,
  type Tier, type ScanFile, type ScanSkip, type BatchEvent,
} from "@/lib/api/batchTranscribeClient";

// FS Access API: lib.dom in modern tsconfig already provides
// FileSystemDirectoryHandle / FileSystemFileHandle / FileSystemWritableFileStream.
// We only need to extend Window with showDirectoryPicker, which lib.dom omits.
declare global {
  interface Window {
    showDirectoryPicker?: (opts?: { mode?: "read" | "readwrite" }) => Promise<FileSystemDirectoryHandle>;
  }
}

const ALLOWED = [
  ".wav", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".webm",
  ".mp4", ".mov", ".mkv", ".avi", ".m4v",
];

type State = "PICK" | "SCAN" | "CONFIRM" | "RUNNING" | "DONE";

type RowState = {
  name:         string;
  status:       "queued" | "in_flight" | "done" | "error";
  percent?:     number;
  stage?:       string;
  elapsed_sec?: number;
  error?:       string;
  eta_sec?:     number;
};

interface Props {
  onClose:    () => void;
  onComplete: () => void;
}

export default function BatchTranscribeModal({ onClose, onComplete }: Props) {
  const [tier, setTier] = useState<Tier>(1);
  const [tierOverride, setTierOverride] = useState<Tier | null>(null);
  const effectiveTier = tierOverride ?? tier;

  const [state, setState] = useState<State>("PICK");
  const [error, setError] = useState<string | null>(null);

  // Pick state
  const [folderPath, setFolderPath]     = useState<string>("");
  const [pickedFiles, setPickedFiles]   = useState<File[]>([]);
  const [dirHandle, setDirHandle]       = useState<FileSystemDirectoryHandle | null>(null);

  // Scan results
  const [scanQueued,  setScanQueued]  = useState<ScanFile[]>([]);
  const [scanSkipped, setScanSkipped] = useState<ScanSkip[]>([]);
  const [scanFolder,  setScanFolder]  = useState<string>("");

  // Whole-batch options
  const [translation, setTranslation] = useState<string>("en");
  const [noteType,    setNoteType]    = useState<string>("meeting_transcript");
  const [generateReview, setGenerateReview] = useState<boolean>(true);
  const language: string | null = null; // future: expose audio-language picker for batch too

  // RUNNING state -- per-file row state keyed by index
  const [rows, setRows] = useState<Record<number, RowState>>({});
  const abortRef = useRef<AbortController | null>(null);
  // SCAN state holds its own AbortController so the user can bail out
  // of a slow scan (e.g. ffprobe walking many large videos).
  const scanAbortRef = useRef<AbortController | null>(null);

  // DONE summary
  const [summary, setSummary] = useState<{ succeeded: number; failed: number; skipped: number; total_elapsed_sec: number } | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setTier(detectTier()); }, []);

  // ---------- PICK handlers ----------

  async function pickViaFsa() {
    setError(null);
    try {
      if (!window.showDirectoryPicker) throw new Error("Browser does not support showDirectoryPicker.");
      const handle = await window.showDirectoryPicker({ mode: "readwrite" });
      const files: File[] = [];
      // FileSystemDirectoryHandle is async-iterable per spec, but lib.dom
      // omits the `values()` method type. Cast to access it.
      const dir = handle as unknown as { values: () => AsyncIterable<FileSystemHandle> };
      for await (const child of dir.values()) {
        if (child.kind !== "file") continue;
        const f = await (child as FileSystemFileHandle).getFile();
        if (ALLOWED.some((ext) => f.name.toLowerCase().endsWith(ext))) {
          files.push(f);
        }
      }
      setDirHandle(handle);
      setPickedFiles(files);
      await runScanTier23(files, handle.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function pickViaInput(ev: React.ChangeEvent<HTMLInputElement>) {
    const fl = ev.target.files;
    if (!fl) return;
    const files: File[] = [];
    for (let i = 0; i < fl.length; i += 1) {
      const f = fl[i];
      if (ALLOWED.some((ext) => f.name.toLowerCase().endsWith(ext))) files.push(f);
    }
    setPickedFiles(files);
    void runScanTier23(files, "(browser-picked folder)");
  }

  async function runScanTier1() {
    if (!folderPath.trim()) { setError("Please paste a folder path."); return; }
    setState("SCAN");
    setError(null);
    scanAbortRef.current = new AbortController();
    const ac = scanAbortRef.current;
    let scanReceived = false;
    let userCancelled = false;
    try {
      await runBatchTier1({
        folder_path:          folderPath.trim(),
        translation_language: translation,
        note_type:            noteType,
        language,
        concurrency:          1,
        signal:               ac.signal,
        onEvent: (ev) => {
          if (ev.kind === "scan_complete") {
            scanReceived = true;
            setScanQueued(ev.data.queued);
            setScanSkipped(ev.data.skipped);
            setScanFolder(ev.data.folder);
            ac.abort();   // we only wanted the scan; cancel the rest
          }
        },
      }).catch((err) => {
        // AbortError is expected (we aborted after scan_complete or user
        // cancelled). Anything else gets re-thrown.
        if ((err as Error)?.name !== "AbortError") throw err;
        if (!scanReceived) userCancelled = true;
      });
      if (userCancelled) {
        setState("PICK");
        return;
      }
      if (!scanReceived) throw new Error("Scan did not return any files. Check the folder path.");
      setState("CONFIRM");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("PICK");
    }
  }

  function handleCancelScan() {
    scanAbortRef.current?.abort();
    setState("PICK");
  }

  async function runScanTier23(files: File[], folderLabel: string) {
    setState("SCAN");
    setError(null);
    try {
      // Probe each file in parallel.
      const probes = await Promise.all(files.map(async (f) => {
        const p = await probeFile(f);
        const dot = f.name.lastIndexOf(".");
        const stemName = dot > 0 ? f.name.slice(0, dot) : f.name;
        return {
          file: f,
          probe: { ...p, transcript_name: `${stemName}_transcript.docx` },
        };
      }));
      const queued: ScanFile[] = probes.map(({ file, probe }) => ({
        name:            file.name,
        path:            (file as unknown as { webkitRelativePath?: string }).webkitRelativePath || file.name,
        size_mb:         +(file.size / (1024 * 1024)).toFixed(2),
        duration_sec:    probe.duration_sec,
        eta_sec:         probe.eta_sec,
        transcript_name: probe.transcript_name,
        status:          "queued",
      }));
      setScanQueued(queued);
      setScanSkipped([]);
      setScanFolder(folderLabel);
      setState("CONFIRM");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("PICK");
    }
  }

  // ---------- RUNNING handlers ----------

  async function handleStart() {
    setState("RUNNING");
    setError(null);
    setSummary(null);

    const initial: Record<number, RowState> = {};
    scanQueued.forEach((q, i) => {
      initial[i] = { name: q.name, status: "queued", eta_sec: q.eta_sec };
    });
    setRows(initial);

    abortRef.current = new AbortController();

    const onEvent = (ev: BatchEvent) => {
      switch (ev.kind) {
        case "scan_complete":
          setScanQueued(ev.data.queued);
          setScanSkipped(ev.data.skipped);
          break;
        case "file_start":
          setRows((r) => ({ ...r, [ev.data.index]: { ...(r[ev.data.index] || {}), name: ev.data.name, status: "in_flight", eta_sec: ev.data.eta_sec } }));
          break;
        case "file_progress":
          setRows((r) => ({ ...r, [ev.data.index]: { ...(r[ev.data.index] || { name: ev.data.name, status: "in_flight" }), percent: ev.data.percent, stage: ev.data.stage } }));
          break;
        case "file_done":
          setRows((r) => ({ ...r, [ev.data.index]: { ...(r[ev.data.index] || { name: ev.data.name, status: "in_flight" }), status: "done", percent: 100, elapsed_sec: ev.data.elapsed_sec } }));
          break;
        case "file_error":
          setRows((r) => ({ ...r, [ev.data.index]: { ...(r[ev.data.index] || { name: ev.data.name, status: "in_flight" }), status: "error", error: ev.data.error } }));
          break;
        case "batch_done":
          setSummary(ev.data);
          setState("DONE");
          break;
        case "batch_error":
          setError(ev.data.error);
          setState("DONE");
          break;
      }
    };

    try {
      if (effectiveTier === 1) {
        await runBatchTier1({
          folder_path:          folderPath.trim(),
          translation_language: translation,
          note_type:            noteType,
          language,
          concurrency:          2,
          generate_review:      generateReview,
          signal:               abortRef.current.signal,
          onEvent,
        });
      } else {
        const probedMap = new Map<string, { duration_sec: number; eta_sec: number; transcript_name: string }>();
        scanQueued.forEach((q) => probedMap.set(q.name, {
          duration_sec: q.duration_sec, eta_sec: q.eta_sec, transcript_name: q.transcript_name,
        }));
        await runBatchClient({
          files:                pickedFiles,
          dirHandle:            dirHandle ?? undefined,
          translation_language: translation,
          note_type:            noteType,
          language,
          concurrency:          2,
          signal:               abortRef.current.signal,
          probedDurations:      probedMap,
          onEvent,
        });
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setError(e instanceof Error ? e.message : String(e));
      setState("DONE");
    }
  }

  function handleCancel() {
    // Fire the abort first so the SSE fetch starts winding down.
    abortRef.current?.abort();
    // Flip the UI immediately so the user sees their click landed --
    // don't wait for the abort to round-trip through the network.
    const succeeded = Object.values(rows).filter((r) => r.status === "done").length;
    const failed    = Object.values(rows).filter((r) => r.status === "error").length;
    setSummary({ succeeded, failed, skipped: 0, total_elapsed_sec: 0 });
    setState("DONE");
  }

  // ---------- Render ----------

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4"
      onClick={(e) => { if (e.target === e.currentTarget && state !== "RUNNING") onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl overflow-hidden">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200">
          <div className="flex items-center gap-2">
            <Folder size={16} className="text-indigo-600" />
            <h2 className="text-sm font-semibold text-slate-800">Batch transcribe folder</h2>
            <span className={`ml-3 text-[10px] px-2 py-0.5 rounded-full
              ${effectiveTier === 1 ? "bg-emerald-50 text-emerald-700" :
                effectiveTier === 2 ? "bg-amber-50 text-amber-700" :
                                      "bg-orange-50 text-orange-700"}
            `}>
              {tierLabel(effectiveTier)}
            </span>
          </div>
          <button onClick={onClose} disabled={state === "RUNNING"}
                  className="text-slate-400 hover:text-slate-600 disabled:opacity-40">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="p-5 space-y-4 min-h-[18rem]">
          {error && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
              {error}
            </div>
          )}

          {state === "PICK" && (
            <PickStateUi
              tier={effectiveTier}
              folderPath={folderPath} setFolderPath={setFolderPath}
              fileInputRef={fileInputRef}
              onPickFsa={pickViaFsa}
              onPickInput={pickViaInput}
              onConfirmTier1={runScanTier1}
            />
          )}

          {state === "SCAN" && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-sm text-indigo-600">
                <Loader2 size={14} className="animate-spin" />
                Scanning folder...
              </div>
              <p className="text-[11px] text-slate-500">
                Reading file durations via ffprobe. Large videos may take a few seconds each;
                this scan probes up to 8 files in parallel.
              </p>
              <div className="flex justify-end">
                <button onClick={handleCancelScan}
                        className="h-8 px-3 text-xs font-medium text-slate-600 hover:text-red-600 border border-slate-200 rounded-md">
                  Cancel scan
                </button>
              </div>
            </div>
          )}

          {state === "CONFIRM" && (
            <ConfirmStateUi
              scanQueued={scanQueued} scanSkipped={scanSkipped} folder={scanFolder}
              translation={translation} setTranslation={setTranslation}
              noteType={noteType} setNoteType={setNoteType}
              generateReview={generateReview} setGenerateReview={setGenerateReview}
              onStart={handleStart}
            />
          )}

          {state === "RUNNING" && (
            <RunningStateUi
              scanQueued={scanQueued} rows={rows}
              onCancel={handleCancel}
            />
          )}

          {state === "DONE" && (
            <DoneStateUi
              summary={summary} folder={scanFolder} tier={effectiveTier}
              onClose={() => { onComplete(); onClose(); }}
            />
          )}
        </div>

        {/* Tier override (advanced) */}
        <div className="px-5 pb-3">
          <details className="text-[10px] text-slate-400">
            <summary className="cursor-pointer">Advanced</summary>
            <div className="mt-2 flex items-center gap-2">
              <span>Override tier:</span>
              {[1, 2, 3].map((t) => (
                <button key={t}
                        onClick={() => setTierOverride(t as Tier)}
                        disabled={state === "RUNNING"}
                        className={`px-2 py-0.5 rounded-md border ${effectiveTier === t ? "bg-indigo-50 border-indigo-300 text-indigo-700" : "border-slate-200"}`}>
                  Tier {t}
                </button>
              ))}
              {tierOverride !== null && (
                <button onClick={() => setTierOverride(null)} className="text-slate-500 underline">reset</button>
              )}
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface PickStateProps {
  tier:            Tier;
  folderPath:      string;
  setFolderPath:   (s: string) => void;
  fileInputRef:    React.RefObject<HTMLInputElement | null>;
  onPickFsa:       () => void;
  onPickInput:     (ev: React.ChangeEvent<HTMLInputElement>) => void;
  onConfirmTier1:  () => void;
}

function PickStateUi({
  tier, folderPath, setFolderPath, fileInputRef, onPickFsa, onPickInput, onConfirmTier1,
}: PickStateProps) {
  if (tier === 1) {
    return (
      <div className="space-y-3">
        <label className="block text-[11px] font-semibold text-slate-600">Folder path on this machine</label>
        <input
          type="text"
          value={folderPath}
          onChange={(e) => setFolderPath(e.target.value)}
          placeholder="D:\recordings\Q1-earnings"
          className="w-full h-9 px-3 rounded-md border border-slate-200 bg-white text-sm font-mono outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
        />
        <button onClick={onConfirmTier1}
                className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700">
          Scan folder
        </button>
        <p className="text-[10px] text-slate-400">Subfolders are not scanned.</p>
      </div>
    );
  }
  if (tier === 2) {
    return (
      <div className="space-y-3">
        <button onClick={onPickFsa}
                className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700">
          Pick folder
        </button>
        <p className="text-[10px] text-slate-400">
          A native folder picker will open. Grant read+write so transcripts can be saved back.
          Subfolders are not scanned.
        </p>
      </div>
    );
  }
  // Tier 3
  return (
    <div className="space-y-3">
      <input
        ref={fileInputRef}
        type="file"
        // @ts-expect-error -- non-standard but supported in Chrome/Edge/Firefox/Safari
        webkitdirectory=""
        directory=""
        multiple
        className="hidden"
        onChange={onPickInput}
      />
      <button onClick={() => fileInputRef.current?.click()}
              className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700">
        Pick folder
      </button>
      <p className="text-[10px] text-slate-400">
        Transcripts will download to your browser&apos;s Downloads folder. Subfolders are not scanned.
      </p>
    </div>
  );
}

interface ConfirmStateProps {
  scanQueued:  ScanFile[];
  scanSkipped: ScanSkip[];
  folder:      string;
  translation: string;
  setTranslation: (s: string) => void;
  noteType:    string;
  setNoteType: (s: string) => void;
  generateReview: boolean;
  setGenerateReview: (b: boolean) => void;
  onStart:     () => void;
}

function ConfirmStateUi(props: ConfirmStateProps) {
  const totalEta = props.scanQueued.reduce((s, q) => s + q.eta_sec, 0);
  return (
    <div className="space-y-3">
      <div className="text-xs text-slate-600">
        Folder: <span className="font-mono">{props.folder}</span><br/>
        {props.scanQueued.length} to process; {props.scanSkipped.length} already done (skipped).
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-[10px] font-semibold text-slate-500 uppercase">Translation</span>
          <select value={props.translation} onChange={(e) => props.setTranslation(e.target.value)}
                  className="w-full h-9 px-2 rounded-md border border-slate-200 bg-white text-sm">
            <option value="none">(no translation)</option>
            <option value="en">English</option>
            <option value="zh-hans">Simplified Chinese</option>
            <option value="zh-hant">Traditional Chinese</option>
            <option value="ja">Japanese</option>
            <option value="ko">Korean</option>
          </select>
        </label>
        <label className="block">
          <span className="text-[10px] font-semibold text-slate-500 uppercase">Note type</span>
          <select value={props.noteType} onChange={(e) => props.setNoteType(e.target.value)}
                  className="w-full h-9 px-2 rounded-md border border-slate-200 bg-white text-sm">
            <option value="meeting_transcript">Meeting Transcript</option>
            <option value="earnings_call">Earnings Call</option>
            <option value="management_meeting">Mgmt Meeting</option>
            <option value="conference">Conference</option>
            <option value="internal">Internal</option>
          </select>
        </label>
      </div>
      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={props.generateReview}
          onChange={(e) => props.setGenerateReview(e.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
        />
        <span className="text-xs text-slate-700">
          <span className="font-semibold">Generate AI interview review</span>
          <span className="block text-[10px] text-slate-500">
            Adds a hedge-fund-PM-style review at the top of each .docx --
            interviewee strengths/weaknesses with first-principle reliability
            check, plus interviewer Q&amp;A review with suggested follow-ups.
            Costs ~30-60s and ~$0.01 extra per file.
          </span>
        </span>
      </label>
      <div className="border border-slate-200 rounded-md max-h-72 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-[10px] uppercase text-slate-500">
            <tr>
              <th className="text-left px-3 py-1.5">#</th>
              <th className="text-left">File</th>
              <th className="text-right">Size</th>
              <th className="text-right">Duration</th>
              <th className="text-right pr-3">ETA</th>
            </tr>
          </thead>
          <tbody>
            {props.scanQueued.map((q, i) => (
              <tr key={`q-${i}`} className="border-t border-slate-100">
                <td className="px-3 py-1.5">{i + 1}</td>
                <td className="font-mono">
                  {q.name}
                  {q.size_mb > 1024 && <span className="ml-2 text-amber-600">large file -- upload may take a while</span>}
                </td>
                <td className="text-right">{q.size_mb.toFixed(1)} MB</td>
                <td className="text-right">{fmtSec(q.duration_sec)}</td>
                <td className="text-right pr-3">~{fmtSec(q.eta_sec)}</td>
              </tr>
            ))}
            {props.scanSkipped.map((s, i) => (
              <tr key={`s-${i}`} className="border-t border-slate-100 text-slate-400">
                <td className="px-3 py-1.5">&#10003;</td>
                <td className="font-mono italic">{s.name} (already transcribed -- skipped)</td>
                <td/><td/><td/>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex justify-between items-center">
        <span className="text-xs text-slate-500">Total ETA: ~{fmtSec(totalEta)}</span>
        <button onClick={props.onStart}
                disabled={props.scanQueued.length === 0}
                className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700 disabled:bg-slate-300">
          Start transcription
        </button>
      </div>
    </div>
  );
}

interface RunningStateProps {
  scanQueued: ScanFile[];
  rows:       Record<number, RowState>;
  onCancel:   () => void;
}

function RunningStateUi(props: RunningStateProps) {
  const done   = Object.values(props.rows).filter((r) => r.status === "done").length;
  const failed = Object.values(props.rows).filter((r) => r.status === "error").length;
  const total  = props.scanQueued.length;
  const overall = total === 0 ? 0 : Math.round(((done + failed) / total) * 100);
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="flex-1 h-3 bg-slate-100 rounded-full overflow-hidden">
          <div className="h-3 bg-indigo-500" style={{ width: `${overall}%` }} />
        </div>
        <span className="text-xs text-slate-600 w-32 text-right">{done + failed}/{total} files</span>
      </div>
      <div className="border border-slate-200 rounded-md max-h-80 overflow-y-auto">
        <table className="w-full text-xs">
          <tbody>
            {props.scanQueued.map((q, i) => {
              const r = props.rows[i] || { name: q.name, status: "queued", eta_sec: q.eta_sec };
              const icon =
                r.status === "done"      ? <span className="text-emerald-600">&#10003;</span> :
                r.status === "in_flight" ? <span className="text-indigo-500">&#8635;</span> :
                r.status === "error"     ? <span className="text-red-500">&#10005;</span> :
                                           <span className="text-slate-400">&#8987;</span>;
              return (
                <tr key={i} className="border-t border-slate-100">
                  <td className="px-3 py-1.5 w-6">{icon}</td>
                  <td className="font-mono">{q.name}</td>
                  <td className="px-3 py-1.5">
                    {r.status === "in_flight" && (
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                          <div className="h-1.5 bg-indigo-400" style={{ width: `${r.percent ?? 5}%` }} />
                        </div>
                        <span className="text-[10px] text-slate-500 whitespace-nowrap">
                          {r.percent ?? 5}% &middot; {r.stage}
                        </span>
                      </div>
                    )}
                    {r.status === "done"   && <span className="text-emerald-700 text-[11px]">Done {r.elapsed_sec ? `in ${fmtSec(r.elapsed_sec)}` : ""}</span>}
                    {r.status === "error"  && <span className="text-red-600 text-[11px]">{r.error}</span>}
                    {r.status === "queued" && <span className="text-slate-400 text-[11px]">Queued</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex justify-end">
        <button onClick={props.onCancel} className="h-8 px-3 text-xs font-medium text-slate-600 hover:text-red-600">
          Cancel batch
        </button>
      </div>
    </div>
  );
}

interface DoneStateProps {
  summary: { succeeded: number; failed: number; skipped: number; total_elapsed_sec: number } | null;
  folder:  string;
  tier:    Tier;
  onClose: () => void;
}

function DoneStateUi(props: DoneStateProps) {
  return (
    <div className="space-y-3 text-sm">
      <div className="text-emerald-700 font-semibold">Batch complete.</div>
      {props.summary && (
        <ul className="text-xs space-y-1">
          <li>Succeeded: <b>{props.summary.succeeded}</b></li>
          <li>Failed:    <b>{props.summary.failed}</b></li>
          <li>Skipped:   <b>{props.summary.skipped}</b></li>
          <li>Elapsed:   <b>{fmtSec(props.summary.total_elapsed_sec)}</b></li>
        </ul>
      )}
      <p className="text-[11px] text-slate-500">
        {props.tier === 3
          ? "Transcripts downloaded to your browser's Downloads folder."
          : <>Transcripts written to <span className="font-mono">{props.folder}/transcripts/</span></>}
      </p>
      <div className="flex justify-end">
        <button onClick={props.onClose}
                className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700">
          Close
        </button>
      </div>
    </div>
  );
}

function fmtSec(s: number): string {
  if (!Number.isFinite(s) || s <= 0) return "0s";
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}
