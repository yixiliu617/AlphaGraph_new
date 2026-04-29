"use client";

// ---------------------------------------------------------------------------
// NotesView — DUMB layer. Pure JSX; no API or store imports.
//
// Layout matches reference screenshot:
//   ┌─ page header: title + search/filter bar + New Note button + total count ─┐
//   │ ┌─ group section (EARNINGS CALLS) ───────────────────────────────────── ┐ │
//   │ │   ▼  NVDA Q1 FY26 Earnings Call  │ [NVDA] [MSFT] │ [AI EARNINGS] │ … │ │
//   │ │   ▼  TSMC Q4 2025 Earnings       │ [TSM]         │ [AI EARNINGS] │ … │ │
//   │ └──────────────────────────────────────────────────────────────────────┘ │
//   │ ┌─ group section (MANAGEMENT MEETINGS) ──────────────────────────────── ┐ │
//   └────────────────────────────────────────────────────────────────────────── ┘
// ---------------------------------------------------------------------------

import { useState } from "react";
import {
  NotebookPen, Search, Plus, Mic, Trash2, Upload, FolderOpen,
  ChevronDown, ChevronRight, SlidersHorizontal,
  FileText, ExternalLink, X, Loader2, Sparkles, Quote as QuoteIcon,
} from "lucide-react";
import type { NoteStub } from "@/lib/api/notesClient";
import type {
  EarningsReleaseStub,
  EarningsReleaseDetail,
} from "@/lib/api/earningsClient";
import type {
  ResearchQueryResponse,
  ResearchFinding,
} from "@/lib/api/researchClient";
import NoteCreationModal from "@/components/domain/notes/NoteCreationModal";
import AudioUploadModal from "@/components/domain/notes/AudioUploadModal";
import BatchTranscribeModal from "@/components/domain/notes/BatchTranscribeModal";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NOTE_GROUPS = [
  {
    type:  "meeting_transcript",
    label: "Meeting Transcripts",
    path:  "MEETING TYPE › MEETING TRANSCRIPT",
  },
  {
    type:  "earnings_call",
    label: "Earnings Calls",
    path:  "MEETING TYPE › EARNINGS CALL",
  },
  {
    type:  "management_meeting",
    label: "Management Meetings",
    path:  "MEETING TYPE › MANAGEMENT MEETING",
  },
  {
    type:  "conference",
    label: "Conferences & NDR",
    path:  "MEETING TYPE › CONFERENCE",
  },
  {
    type:  "internal",
    label: "Internal Notes",
    path:  "MEETING TYPE › INTERNAL",
  },
] as const;

const AI_STATUS_LABELS: Record<string, string> = {
  awaiting_speakers: "Speakers",
  awaiting_topics:   "Topics",
  extracting:        "Extracting",
  awaiting_approval: "Review",
  complete:          "AI ✓",
};

const AI_STATUS_COLORS: Record<string, string> = {
  awaiting_speakers: "bg-yellow-50 text-yellow-700 border-yellow-200",
  awaiting_topics:   "bg-yellow-50 text-yellow-700 border-yellow-200",
  extracting:        "bg-blue-50  text-blue-700  border-blue-200",
  awaiting_approval: "bg-orange-50 text-orange-700 border-orange-200",
  complete:          "bg-green-50 text-green-700 border-green-200",
};

function formatDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  notes:           NoteStub[];
  isLoading:       boolean;
  searchQuery:     string;
  filterTicker:    string;
  filterType:      string;
  showCreateModal: boolean;
  // Earnings releases section
  earnings:           EarningsReleaseStub[];
  earningsLoading:    boolean;
  openRelease:        EarningsReleaseDetail | null;
  openReleaseLoading: boolean;
  onOpenRelease:      (releaseId: string) => void;
  onCloseRelease:     () => void;
  // Research query section (top of tab)
  researchResult:     ResearchQueryResponse | null;
  researchLoading:    boolean;
  researchError:      string | null;
  onResearchQuery:    (ticker: string, question: string, lookbackYears: number) => void;
  onClearResearch:    () => void;
  onSearchChange:        (v: string) => void;
  onFilterTickerChange:  (v: string) => void;
  onFilterTypeChange:    (v: string) => void;
  onOpenCreate:  () => void;
  onCloseCreate: () => void;
  onCreate: (payload: {
    title: string; note_type: string; company_tickers: string[]; meeting_date?: string;
    ux_variant: "A" | "B";
  }) => void;
  onDelete: (noteId: string) => void;
  onOpen:   (noteId: string) => void;
  // Audio upload modal
  showUploadModal:   boolean;
  onOpenUpload:      () => void;
  onCloseUpload:     () => void;
  onUploadComplete:  (noteId: string) => void;
  // Batch-folder modal
  showBatchModal:    boolean;
  onOpenBatch:       () => void;
  onCloseBatch:      () => void;
  onBatchComplete:   () => void;
}

// ---------------------------------------------------------------------------
// NoteRow — single row inside a group
// ---------------------------------------------------------------------------

function NoteRow({
  note, onOpen, onDelete,
}: { note: NoteStub; onOpen: (id: string) => void; onDelete: (id: string) => void }) {
  const hasAI   = note.summary_status !== "none";
  const aiLabel = hasAI ? (AI_STATUS_LABELS[note.summary_status] ?? "") : "";
  const aiColor = hasAI ? (AI_STATUS_COLORS[note.summary_status]  ?? "") : "";

  return (
    <div
      className="flex items-center gap-3 px-6 py-3 hover:bg-slate-50 group cursor-pointer transition-colors border-b border-slate-100 last:border-0"
      onClick={() => onOpen(note.note_id)}
    >
      {/* Expand chevron (visual only) */}
      <ChevronRight
        size={14}
        className="text-slate-300 shrink-0 group-hover:text-indigo-400 transition-colors"
      />

      {/* Title + subtitle */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-slate-800 group-hover:text-indigo-700 truncate transition-colors leading-tight">
          {note.title}
        </p>
        <p className="text-[11px] text-slate-400 truncate leading-tight mt-0.5">
          {note.meeting_date
            ? new Date(note.meeting_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
            : note.company_tickers.join(", ")}
        </p>
      </div>

      {/* Company ticker pills */}
      <div className="flex items-center gap-1 shrink-0">
        {note.company_tickers.slice(0, 3).map((t) => (
          <span
            key={t}
            className="px-1.5 py-0.5 text-[10px] font-mono font-bold bg-indigo-50 text-indigo-700 rounded border border-indigo-100"
          >
            {t}
          </span>
        ))}
        {note.company_tickers.length > 3 && (
          <span className="text-[10px] text-slate-400">+{note.company_tickers.length - 3}</span>
        )}
      </div>

      {/* A/B layout badge */}
      <span
        className={`shrink-0 px-1.5 py-0.5 text-[9px] font-bold rounded uppercase tracking-wide ${
          note.ux_variant === "B"
            ? "bg-violet-50 text-violet-700 border border-violet-200"
            : "bg-slate-50 text-slate-500 border border-slate-200"
        }`}
        title={note.ux_variant === "B" ? "New layout (experiment)" : "Classic layout"}
      >
        {note.ux_variant}
      </span>

      {/* AI status badge */}
      {hasAI && aiLabel && (
        <span className={`shrink-0 px-1.5 py-0.5 text-[9px] font-bold rounded border uppercase tracking-wide ${aiColor}`}>
          {aiLabel}
        </span>
      )}

      {/* Fragment count */}
      {note.fragment_ids.length > 0 && (
        <span className="shrink-0 px-1.5 py-0.5 text-[10px] font-mono font-semibold bg-slate-100 text-slate-500 rounded-full">
          {note.fragment_ids.length} frags
        </span>
      )}

      {/* Recording dot */}
      {note.recording_path && (
        <Mic size={12} className="shrink-0 text-red-400" />
      )}

      {/* Date */}
      <span className="shrink-0 text-[11px] text-slate-400 w-24 text-right tabular-nums">
        {formatDate(note.updated_at)}
      </span>

      {/* Delete — appears on row hover */}
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(note.note_id); }}
        className="shrink-0 p-1 text-slate-200 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-all rounded"
        title="Delete note"
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// NoteGroup — collapsible section
// ---------------------------------------------------------------------------

function NoteGroup({
  path, notes, onOpen, onDelete,
}: {
  path:    string;
  notes:   NoteStub[];
  onOpen:  (id: string) => void;
  onDelete:(id: string) => void;
}) {
  const [open, setOpen] = useState(true);

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Section header */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-5 py-3 bg-slate-50 hover:bg-slate-100 transition-colors border-b border-slate-200 text-left"
      >
        {open
          ? <ChevronDown  size={14} className="text-slate-400 shrink-0" />
          : <ChevronRight size={14} className="text-slate-400 shrink-0" />}

        <span className="text-[11px] font-bold text-slate-500 uppercase tracking-widest flex-1">
          {path}
        </span>

        <span className="text-[10px] font-semibold text-slate-400 bg-white border border-slate-200 px-2 py-0.5 rounded-full">
          {notes.length}
        </span>
      </button>

      {/* Rows */}
      {open && (
        <div>
          {notes.map((note) => (
            <NoteRow
              key={note.note_id}
              note={note}
              onOpen={onOpen}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EarningsRow — single row for an 8-K earnings release / CFO commentary
// ---------------------------------------------------------------------------

function EarningsRow({
  stub, onOpen,
}: { stub: EarningsReleaseStub; onOpen: (id: string) => void }) {
  const isPressRelease = stub.exhibit_norm === "EX-99.1";
  const badgeColor = isPressRelease
    ? "bg-blue-50 text-blue-700 border-blue-200"
    : "bg-emerald-50 text-emerald-700 border-emerald-200";

  return (
    <div
      className="flex items-center gap-3 px-6 py-3 hover:bg-slate-50 group cursor-pointer transition-colors border-b border-slate-100 last:border-0"
      onClick={() => onOpen(stub.id)}
    >
      <ChevronRight
        size={14}
        className="text-slate-300 shrink-0 group-hover:text-indigo-400 transition-colors"
      />

      <FileText size={14} className="text-slate-400 shrink-0" />

      {/* Title + subtitle */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-slate-800 group-hover:text-indigo-700 truncate transition-colors leading-tight">
          {stub.title}
        </p>
        <p className="text-[11px] text-slate-400 truncate leading-tight mt-0.5">
          Filed {stub.filing_date}
          {stub.fiscal_period && ` · ${stub.fiscal_period}`}
          {` · ${(stub.text_chars / 1024).toFixed(0)} kB`}
        </p>
      </div>

      {/* Ticker pill */}
      <span className="shrink-0 px-1.5 py-0.5 text-[10px] font-mono font-bold bg-indigo-50 text-indigo-700 rounded border border-indigo-100">
        {stub.ticker}
      </span>

      {/* Doc-type badge */}
      <span className={`shrink-0 px-1.5 py-0.5 text-[9px] font-bold rounded border uppercase tracking-wide ${badgeColor}`}>
        {isPressRelease ? "PRESS REL." : (stub.doc_type_label === "CFO Commentary" ? "CFO COMM." : stub.doc_type_label.toUpperCase())}
      </span>

      {/* Filing date (right-aligned) */}
      <span className="shrink-0 text-[11px] text-slate-400 w-24 text-right tabular-nums">
        {stub.filing_date}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EarningsGroup — collapsible section for earnings releases
// ---------------------------------------------------------------------------

function EarningsGroup({
  path, items, onOpen,
}: { path: string; items: EarningsReleaseStub[]; onOpen: (id: string) => void }) {
  const [open, setOpen] = useState(true);

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-5 py-3 bg-slate-50 hover:bg-slate-100 transition-colors border-b border-slate-200 text-left"
      >
        {open
          ? <ChevronDown  size={14} className="text-slate-400 shrink-0" />
          : <ChevronRight size={14} className="text-slate-400 shrink-0" />}

        <span className="text-[11px] font-bold text-slate-500 uppercase tracking-widest flex-1">
          {path}
        </span>

        <span className="text-[10px] font-semibold text-slate-400 bg-white border border-slate-200 px-2 py-0.5 rounded-full">
          {items.length}
        </span>
      </button>

      {open && (
        <div>
          {items.map((stub) => (
            <EarningsRow key={stub.id} stub={stub} onOpen={onOpen} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReleaseDetailModal — full-text viewer opened when a row is clicked
// ---------------------------------------------------------------------------

function ReleaseDetailModal({
  release, loading, onClose,
}: {
  release: EarningsReleaseDetail | null;
  loading: boolean;
  onClose: () => void;
}) {
  if (!release && !loading) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-slate-200 shrink-0">
          <div className="flex items-start gap-3 min-w-0">
            <FileText size={18} className="text-indigo-600 shrink-0 mt-0.5" />
            <div className="min-w-0">
              <h2 className="text-base font-bold text-slate-900 leading-tight truncate">
                {release?.title ?? "Loading…"}
              </h2>
              {release && (
                <p className="text-[11px] text-slate-500 mt-1 font-mono">
                  Filed {release.filing_date}
                  {release.fiscal_period && ` · ${release.fiscal_period}`}
                  {` · ${(release.text_chars / 1024).toFixed(1)} kB of text`}
                  {release.document && ` · ${release.document}`}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {release?.url && (
              <a
                href={release.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-[11px] font-semibold text-indigo-600 hover:text-indigo-700 border border-indigo-200 hover:border-indigo-300 bg-indigo-50 px-2 py-1 rounded transition-colors"
              >
                <ExternalLink size={11} /> EDGAR
              </a>
            )}
            <button
              onClick={onClose}
              className="p-1 text-slate-400 hover:text-slate-700 transition-colors"
              title="Close"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {loading ? (
            <div className="flex items-center justify-center gap-2 text-slate-400 py-20">
              <Loader2 size={16} className="animate-spin" /> Loading full text…
            </div>
          ) : release ? (
            <pre className="whitespace-pre-wrap text-sm text-slate-700 leading-relaxed font-sans">
              {release.text_raw}
            </pre>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ResearchPanel — natural-language Q&A over the earnings-release corpus.
// Lives above the earnings section at the top of the Notes tab.
// ---------------------------------------------------------------------------

function ResearchFindingCard({
  finding, onOpenSource,
}: {
  finding: ResearchFinding;
  onOpenSource: (releaseId: string) => void;
}) {
  const isPressRelease = finding.source_type === "press_release";
  const badgeColor = isPressRelease
    ? "bg-blue-50 text-blue-700 border-blue-200"
    : finding.source_type === "cfo_commentary"
    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
    : "bg-slate-50 text-slate-600 border-slate-200";

  const badgeLabel =
    finding.source_type === "press_release"   ? "PRESS REL." :
    finding.source_type === "cfo_commentary"  ? "CFO COMM."  :
    finding.source_type === "mdna"            ? "MD&A"       :
    finding.source_type.replace(/_/g, " ").toUpperCase();

  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4 hover:border-indigo-200 transition-colors">
      {/* Header: date + badges + open source button */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] font-mono text-slate-500">
            <span className="font-bold text-slate-700">{finding.filing_date}</span>
            {finding.fiscal_period && (
              <>
                <span>·</span>
                <span>{finding.fiscal_period}</span>
              </>
            )}
            <span>·</span>
            <span className="text-slate-400">{finding.ticker}</span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`px-1.5 py-0.5 text-[9px] font-bold rounded border uppercase tracking-wide ${badgeColor}`}>
            {badgeLabel}
          </span>
          <button
            onClick={() => onOpenSource(finding.source_id)}
            className="flex items-center gap-1 text-[10px] font-semibold text-indigo-600 hover:text-indigo-700 border border-indigo-200 hover:border-indigo-300 bg-indigo-50 px-2 py-0.5 rounded transition-colors"
            title="Open the source document"
          >
            <FileText size={10} /> Source
          </button>
        </div>
      </div>

      {/* Key points */}
      {finding.key_points.length > 0 && (
        <ul className="space-y-1.5 mb-3">
          {finding.key_points.map((kp, i) => (
            <li key={i} className="text-sm text-slate-700 leading-snug flex gap-2">
              <span className="text-indigo-400 font-bold shrink-0">·</span>
              <span>{kp}</span>
            </li>
          ))}
        </ul>
      )}

      {/* Quotes */}
      {finding.quotes.length > 0 && (
        <div className="space-y-2 border-l-2 border-slate-200 pl-3 mt-3">
          {finding.quotes.map((q, i) => (
            <div key={i} className="flex gap-2">
              <QuoteIcon size={10} className="text-slate-300 mt-1.5 shrink-0" />
              <div className="flex-1">
                <p className="text-[12px] italic text-slate-600 leading-relaxed">
                  &ldquo;{q.text}&rdquo;
                </p>
                {!q.verified && (
                  <span className="text-[9px] text-amber-600 font-mono mt-0.5 inline-block">
                    ⚠ could not verify literal match in source
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ResearchPanel({
  result, loading, error,
  onSubmit, onClear, onOpenSource,
}: {
  result: ResearchQueryResponse | null;
  loading: boolean;
  error:   string | null;
  onSubmit: (ticker: string, question: string, lookbackYears: number) => void;
  onClear:  () => void;
  onOpenSource: (releaseId: string) => void;
}) {
  const [ticker, setTicker]         = useState("");
  const [question, setQuestion]     = useState("");
  const [lookbackYears, setLookback] = useState(3);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticker.trim() || !question.trim() || loading) return;
    onSubmit(ticker, question, lookbackYears);
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-5 py-3 bg-gradient-to-r from-indigo-50 to-slate-50 border-b border-slate-200">
        <Sparkles size={14} className="text-indigo-600 shrink-0" />
        <span className="text-[11px] font-bold text-slate-700 uppercase tracking-widest flex-1">
          Ask the Earnings Corpus
        </span>
        {result && (
          <button
            onClick={onClear}
            className="text-[10px] font-semibold text-slate-400 hover:text-slate-700 transition-colors"
          >
            Clear
          </button>
        )}
      </div>

      {/* Query form */}
      <form onSubmit={handleSubmit} className="px-5 py-4 border-b border-slate-100">
        <div className="flex items-start gap-2">
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            placeholder="TICKER"
            className="w-24 h-9 px-3 rounded-md border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 font-mono uppercase shrink-0"
            disabled={loading}
          />
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. What did Micron say about business outlook?"
            className="flex-1 h-9 px-3 rounded-md border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
            disabled={loading}
          />
          <select
            value={lookbackYears}
            onChange={(e) => setLookback(Number(e.target.value))}
            className="h-9 px-2 rounded-md border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 text-slate-600 shrink-0"
            disabled={loading}
          >
            {[1, 2, 3, 5, 8].map((n) => (
              <option key={n} value={n}>{n}y</option>
            ))}
          </select>
          <button
            type="submit"
            disabled={loading || !ticker.trim() || !question.trim()}
            className="flex items-center gap-1.5 h-9 px-4 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white text-sm font-medium rounded-md transition-colors shadow-sm shrink-0"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {loading ? "Analyzing…" : "Ask"}
          </button>
        </div>
      </form>

      {/* Results / error / status */}
      {error && (
        <div className="px-5 py-3 bg-amber-50 border-b border-amber-200 text-[11px] text-amber-700">
          Query failed: {error}
        </div>
      )}

      {result && !loading && (
        <div className="px-5 py-4 space-y-3">
          {/* Summary stats */}
          <div className="flex items-center gap-3 text-[11px] font-mono text-slate-500 pb-2 border-b border-slate-100">
            <span>
              <span className="font-bold text-slate-700">{result.docs_with_hits}</span> relevant
              {" / "}
              <span className="text-slate-600">{result.docs_considered}</span> documents
            </span>
            {result.from_cache > 0 && (
              <span className="text-emerald-600">
                {result.from_cache} cached
              </span>
            )}
            {result.newly_extracted > 0 && (
              <span className="text-indigo-600">
                {result.newly_extracted} freshly extracted
              </span>
            )}
          </div>

          {/* Findings */}
          {result.findings.length === 0 ? (
            <p className="text-sm text-slate-500 py-4 text-center">
              No findings for this question in the selected time range.
            </p>
          ) : (
            <div className="space-y-3">
              {result.findings.map((f) => (
                <ResearchFindingCard
                  key={f.finding_id}
                  finding={f}
                  onOpenSource={onOpenSource}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// NotesView — main export
// ---------------------------------------------------------------------------

export default function NotesView({
  notes, isLoading, searchQuery, filterTicker, filterType,
  showCreateModal, onSearchChange, onFilterTickerChange, onFilterTypeChange,
  onOpenCreate, onCloseCreate, onCreate, onDelete, onOpen,
  earnings, earningsLoading, openRelease, openReleaseLoading,
  onOpenRelease, onCloseRelease,
  researchResult, researchLoading, researchError,
  onResearchQuery, onClearResearch,
  showUploadModal, onOpenUpload, onCloseUpload, onUploadComplete,
  showBatchModal, onOpenBatch, onCloseBatch, onBatchComplete,
}: Props) {
  const [showFilters, setShowFilters] = useState(false);

  // Build groups — only show groups that have matching notes
  const knownTypes = new Set(NOTE_GROUPS.map((g) => g.type));
  const groups = [
    ...NOTE_GROUPS.map((g) => ({
      ...g,
      notes: notes.filter((n) => n.note_type === g.type),
    })),
    {
      type: "__other__",
      label: "Other",
      path: "OTHER",
      notes: notes.filter((n) => !knownTypes.has(n.note_type as typeof NOTE_GROUPS[number]["type"])),
    },
  ].filter((g) => g.notes.length > 0);

  // Empty groups but filterType active → show unfiltered groups as empty hint
  const hasAny = notes.length > 0;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* ── Page header ── */}
      <div className="flex items-center justify-between px-8 py-5 bg-white border-b border-slate-200 shrink-0">
        <div className="flex items-center gap-3">
          <NotebookPen size={22} className="text-indigo-600 shrink-0" />
          <div>
            <h1 className="text-xl font-bold text-slate-900 leading-tight">Notes Library</h1>
            <p className="text-xs text-slate-500 mt-0.5">
              A centralised archive of all meeting notes, transcripts, and AI summaries.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Search */}
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder="Search by title, content, or ticker…"
              className="h-9 w-64 rounded-md border border-slate-200 bg-slate-50 pl-9 pr-3 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          {/* Filter toggle */}
          <button
            onClick={() => setShowFilters((v) => !v)}
            className={[
              "flex items-center gap-1.5 h-9 px-3 rounded-md border text-sm font-medium transition-colors",
              showFilters
                ? "border-indigo-500 bg-indigo-50 text-indigo-700"
                : "border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50",
            ].join(" ")}
          >
            <SlidersHorizontal size={14} />
            Filter
          </button>

          {/* Total count */}
          <span className="text-xs font-semibold text-slate-400 whitespace-nowrap">
            {notes.length} {notes.length === 1 ? "note" : "notes"}
          </span>

          {/* Upload Audio */}
          <button
            onClick={onOpenUpload}
            disabled={showBatchModal}
            className="flex items-center gap-2 h-9 px-3 border border-slate-200 text-slate-700 text-sm font-medium rounded-md hover:border-indigo-400 hover:bg-indigo-50 hover:text-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title="Drop an audio file -- runs the same Gemini polish pipeline as live recording"
          >
            <Upload size={14} />
            Upload Audio
          </button>

          {/* Batch folder */}
          <button
            onClick={onOpenBatch}
            disabled={showBatchModal}
            className="flex items-center gap-2 h-9 px-3 border border-slate-200 text-slate-700 text-sm font-medium rounded-md hover:border-indigo-400 hover:bg-indigo-50 hover:text-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title="Process every audio/video file in a folder; one transcript .docx per file"
          >
            <FolderOpen size={14} />
            Batch folder
          </button>

          {/* New Note */}
          <button
            onClick={onOpenCreate}
            className="flex items-center gap-2 h-9 px-4 bg-indigo-600 text-white text-sm font-medium rounded-md hover:bg-indigo-700 transition-colors shadow-sm"
          >
            <Plus size={15} />
            New Note
          </button>
        </div>
      </div>

      {/* ── Filter bar (expandable) ── */}
      {showFilters && (
        <div className="flex items-center gap-3 px-8 py-3 bg-white border-b border-slate-100 shrink-0">
          <label className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Filters</label>
          <input
            type="text"
            value={filterTicker}
            onChange={(e) => onFilterTickerChange(e.target.value.toUpperCase())}
            placeholder="Ticker (e.g. NVDA)"
            className="h-8 w-32 px-3 rounded-md border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 font-mono uppercase"
          />
          <select
            value={filterType}
            onChange={(e) => onFilterTypeChange(e.target.value)}
            className="h-8 px-3 rounded-md border border-slate-200 bg-slate-50 text-sm outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 text-slate-600"
          >
            <option value="">All types</option>
            <option value="meeting_transcript">Meeting Transcript</option>
            <option value="earnings_call">Earnings Call</option>
            <option value="management_meeting">Mgmt Meeting</option>
            <option value="conference">Conference</option>
            <option value="internal">Internal</option>
          </select>
          {(filterTicker || filterType) && (
            <button
              onClick={() => { onFilterTickerChange(""); onFilterTypeChange(""); }}
              className="text-xs text-indigo-600 hover:underline"
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      {/* ── Content ── */}
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-4">
        {/* Research query panel — natural-language Q&A over the earnings corpus. */}
        <ResearchPanel
          result={researchResult}
          loading={researchLoading}
          error={researchError}
          onSubmit={onResearchQuery}
          onClear={onClearResearch}
          onOpenSource={onOpenRelease}
        />

        {isLoading ? (
          // Loading skeleton
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="bg-white rounded-xl border border-slate-200 h-14 animate-pulse" />
            ))}
          </div>

        ) : !hasAny ? (
          // Empty state — only when there are no notes AND no earnings to show
          <div className="flex flex-col items-center justify-center gap-4 py-24">
            <div className="w-16 h-16 bg-indigo-50 rounded-2xl flex items-center justify-center">
              <NotebookPen size={26} className="text-indigo-400" />
            </div>
            <div className="text-center">
              <h3 className="text-base font-semibold text-slate-800">No notes yet</h3>
              <p className="text-sm text-slate-400 mt-1 max-w-sm">
                Create your first note to capture meeting intelligence, link companies, and build your knowledge base.
              </p>
            </div>
            <button
              onClick={onOpenCreate}
              className="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-md hover:bg-indigo-700 transition-colors shadow-sm"
            >
              <Plus size={15} />
              Create first note
            </button>
          </div>

        ) : groups.length === 0 ? (
          // Has notes but none match current filter
          <div className="flex flex-col items-center justify-center gap-3 py-20">
            <p className="text-sm text-slate-500">No notes match the current filters.</p>
            <button
              onClick={() => { onFilterTickerChange(""); onFilterTypeChange(""); onSearchChange(""); }}
              className="text-sm text-indigo-600 hover:underline"
            >
              Clear all filters
            </button>
          </div>

        ) : (
          // Grouped sections
          groups.map((group) => (
            <NoteGroup
              key={group.type}
              path={group.path}
              notes={group.notes}
              onOpen={onOpen}
              onDelete={onDelete}
            />
          ))
        )}

        {/* SEC Filings — rendered at bottom */}
        {earningsLoading ? (
          <div className="bg-white rounded-xl border border-slate-200 h-14 animate-pulse" />
        ) : earnings.length > 0 ? (
          <EarningsGroup
            path="SEC FILINGS › EARNINGS PRESS RELEASES & CFO COMMENTARY"
            items={earnings}
            onOpen={onOpenRelease}
          />
        ) : null}
      </div>

      {/* Creation Modal */}
      {showCreateModal && (
        <NoteCreationModal onClose={onCloseCreate} onCreate={onCreate} />
      )}
      {showUploadModal && (
        <AudioUploadModal onClose={onCloseUpload} onComplete={onUploadComplete} />
      )}
      {showBatchModal && (
        <BatchTranscribeModal onClose={onCloseBatch} onComplete={onBatchComplete} />
      )}

      {/* Earnings release detail modal — opened when a row in the
          earnings section is clicked. */}
      <ReleaseDetailModal
        release={openRelease}
        loading={openReleaseLoading}
        onClose={onCloseRelease}
      />
    </div>
  );
}
