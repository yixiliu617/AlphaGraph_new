/**
 * batchTranscribeClient
 *
 * Tier detection, SSE consumption (Tier 1), per-file orchestration loop
 * for Tier 2/3, and the per-tier output write-back logic.
 *
 *   Tier 1 (local backend): backend writes transcripts to disk directly.
 *   Tier 2 (Chromium):      browser writes via File System Access API.
 *   Tier 3 (FF/Safari):     browser triggers a download per file.
 */

import { notesClient } from "./notesClient";

export type Tier = 1 | 2 | 3;

export interface ScanFile {
  name:            string;
  path:            string;        // absolute on backend (Tier 1) OR webkitRelativePath (Tier 2/3)
  size_mb:         number;
  duration_sec:    number;
  eta_sec:         number;
  transcript_name: string;
  status:          "queued" | "in_flight" | "done" | "error";
}

export interface ScanSkip { name: string; reason: string; }

export interface ScanComplete {
  folder:        string;
  queued_count:  number;
  skipped_count: number;
  queued:        ScanFile[];
  skipped:       ScanSkip[];
}

export type BatchEvent =
  | { kind: "scan_complete"; data: ScanComplete }
  | { kind: "file_start";    data: { index: number; name: string; eta_sec: number } }
  | { kind: "file_progress"; data: { index: number; name: string; percent: number; stage: string } }
  | { kind: "file_done";     data: { index: number; name: string; transcript_path: string; elapsed_sec: number; note_id: string } }
  | { kind: "file_error";    data: { index: number; name: string; error: string } }
  | { kind: "batch_done";    data: { total: number; succeeded: number; failed: number; skipped: number; total_elapsed_sec: number } }
  | { kind: "batch_error";   data: { error: string } };

// ---------------------------------------------------------------------------
// Tier detection
// ---------------------------------------------------------------------------

export function detectTier(): Tier {
  if (typeof window === "undefined") return 3;
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1" || host === "::1") return 1;
  if ("showDirectoryPicker" in window) return 2;
  return 3;
}

export function tierLabel(tier: Tier): string {
  if (tier === 1) return "Local mode -- transcripts saved directly to folder";
  if (tier === 2) return "Browser mode -- transcripts written to picked folder";
  return            "Browser mode -- transcripts download to your Downloads folder";
}

// ---------------------------------------------------------------------------
// Tier 1: SSE consumer
// ---------------------------------------------------------------------------

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

interface RunBatchTier1Args {
  folder_path:          string;
  translation_language: string;
  note_type:            string;
  language:             string | null;
  concurrency:          number;
  generate_review?:     boolean;
  signal?:              AbortSignal;
  onEvent:              (ev: BatchEvent) => void;
}

export async function runBatchTier1(args: RunBatchTier1Args): Promise<void> {
  const resp = await fetch(`${API_BASE}/notes/batch-transcribe-folder`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      folder_path:          args.folder_path,
      translation_language: args.translation_language,
      note_type:            args.note_type,
      language:             args.language,
      concurrency:          args.concurrency,
      generate_review:      args.generate_review ?? false,
    }),
    signal:  args.signal,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`Batch endpoint ${resp.status}: ${text.slice(0, 300)}`);
  }
  if (!resp.body) throw new Error("Batch endpoint returned no body");

  // Manual SSE parser. We POST a body, which EventSource doesn't support.
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx = buf.indexOf("\n\n");
    while (idx !== -1) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = parseSseFrame(chunk);
      if (ev) args.onEvent(ev);
      idx = buf.indexOf("\n\n");
    }
  }
}

function parseSseFrame(text: string): BatchEvent | null {
  let kind = "";
  const dataLines: string[] = [];
  for (const line of text.split("\n")) {
    if (line.startsWith("event: ")) kind = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  if (!kind || dataLines.length === 0) return null;
  try {
    const data = JSON.parse(dataLines.join("\n"));
    return { kind, data } as BatchEvent;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Tier 2/3: client-side orchestration loop
// ---------------------------------------------------------------------------

interface RunBatchClientArgs {
  files:                File[];                       // already filtered (allowed extensions)
  dirHandle?:           FileSystemDirectoryHandle;    // Tier 2 only
  translation_language: string;
  note_type:            string;
  language:             string | null;
  concurrency:          number;
  signal?:              AbortSignal;
  onEvent:              (ev: BatchEvent) => void;
  /** Probed at scan time -- map filename -> {duration_sec, eta_sec, transcript_name}. */
  probedDurations:      Map<string, { duration_sec: number; eta_sec: number; transcript_name: string }>;
}

export async function runBatchClient(args: RunBatchClientArgs): Promise<void> {
  const BIG_FILE_SECONDS = 90 * 60;
  const startCap = Math.max(1, Math.min(4, args.concurrency));

  // Emit a synthetic scan_complete so the modal's progress UI lights up
  // the same way it does for Tier 1.
  args.onEvent({
    kind: "scan_complete",
    data: {
      folder:        "(browser-picked)",
      queued_count:  args.files.length,
      skipped_count: 0,
      queued: args.files.map((f) => {
        const probe = args.probedDurations.get(f.name);
        return {
          name:            f.name,
          path:            (f as unknown as { webkitRelativePath?: string }).webkitRelativePath || f.name,
          size_mb:         +(f.size / (1024 * 1024)).toFixed(2),
          duration_sec:    probe?.duration_sec ?? 0,
          eta_sec:         probe?.eta_sec ?? 30,
          transcript_name: probe?.transcript_name ?? `${stem(f.name)}_transcript.docx`,
          status:          "queued",
        };
      }),
      skipped: [],
    },
  });

  let cap = startCap;
  let succeeded = 0;
  let failed = 0;
  let activeWorkers = 0;
  const inflightDurations: number[] = [];
  const startedAt = Date.now();

  async function processOne(index: number, file: File) {
    activeWorkers += 1;
    const probe = args.probedDurations.get(file.name);
    inflightDurations.push(probe?.duration_sec ?? 0);
    args.onEvent({
      kind: "file_start",
      data: { index, name: file.name, eta_sec: probe?.eta_sec ?? 30 },
    });
    const fileStart = Date.now();
    try {
      args.onEvent({ kind: "file_progress", data: { index, name: file.name, percent: 5, stage: "uploading" } });
      const res = await notesClient.uploadTranscribeAudio(file, {
        title:                stem(file.name),
        language:             (args.language ?? "auto") as "auto" | "zh" | "ja" | "ko" | "en",
        translation_language: args.translation_language,
        note_type:            args.note_type,
      });
      args.onEvent({ kind: "file_progress", data: { index, name: file.name, percent: 70, stage: "writing_doc" } });

      // Pull the .docx and write/download it.
      const docxResp = await fetch(
        `${API_BASE}/notes/${res.data.note_id}/export.docx`,
      );
      if (!docxResp.ok) throw new Error(`export.docx ${docxResp.status}`);
      const blob = await docxResp.blob();

      const transcriptName = probe?.transcript_name ?? `${stem(file.name)}_transcript.docx`;
      if (args.dirHandle) {
        // Tier 2
        const transcriptsDir = await args.dirHandle.getDirectoryHandle("transcripts", { create: true });
        const fh = await transcriptsDir.getFileHandle(transcriptName, { create: true });
        const w = await fh.createWritable();
        await w.write(blob);
        await w.close();
      } else {
        // Tier 3: trigger a browser download
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = transcriptName;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      }

      succeeded += 1;
      args.onEvent({
        kind: "file_done",
        data: {
          index, name: file.name,
          transcript_path: transcriptName,
          elapsed_sec:     (Date.now() - fileStart) / 1000,
          note_id:         res.data.note_id,
        },
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // 429 auto-throttle
      if (/429|rate/i.test(msg)) cap = Math.max(1, Math.floor(cap / 2));
      failed += 1;
      args.onEvent({ kind: "file_error", data: { index, name: file.name, error: msg } });
    } finally {
      const dur = probe?.duration_sec ?? 0;
      const idx = inflightDurations.indexOf(dur);
      if (idx !== -1) inflightDurations.splice(idx, 1);
      activeWorkers -= 1;
    }
  }

  // Dispatcher: launch workers up to `cap`, throttling on big files / 429s.
  const tasks: Promise<void>[] = [];
  for (const { index, file } of args.files.map((f, i) => ({ index: i, file: f }))) {
    if (args.signal?.aborted) break;
    while (
      activeWorkers >= cap ||
      inflightDurations.some((d) => d > BIG_FILE_SECONDS)
    ) {
      if (args.signal?.aborted) break;
      await sleep(50);
    }
    if (args.signal?.aborted) break;
    tasks.push(processOne(index, file));
  }
  await Promise.all(tasks);

  args.onEvent({
    kind: "batch_done",
    data: {
      total:             args.files.length,
      succeeded, failed,
      skipped:           0,
      total_elapsed_sec: (Date.now() - startedAt) / 1000,
    },
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sleep(ms: number) { return new Promise<void>((r) => setTimeout(r, ms)); }

function stem(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot > 0 ? filename.slice(0, dot) : filename;
}

// ---------------------------------------------------------------------------
// Probe (Tier 2/3)
// ---------------------------------------------------------------------------

export async function probeFile(file: File): Promise<{ duration_sec: number; eta_sec: number }> {
  const fd = new FormData();
  fd.append("audio", file);
  const resp = await fetch(`${API_BASE}/notes/probe-audio`, { method: "POST", body: fd });
  if (!resp.ok) {
    return { duration_sec: 0, eta_sec: 30 };
  }
  const json = await resp.json();
  return {
    duration_sec: json?.data?.duration_seconds ?? 0,
    eta_sec:      json?.data?.estimated_transcribe_seconds ?? 30,
  };
}
