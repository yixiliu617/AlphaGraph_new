"use client";

/**
 * NoteHeaderBlock — the Notion-style header that sits above the note's
 * editor. Renders the title and inline-editable metadata (tickers, meeting
 * date, note type, source URL) so the user doesn't have to squint at a
 * cramped top-bar breadcrumb.
 *
 * All edits bubble up via callbacks; the parent container patches the note
 * in Zustand and the existing auto-save cycle pushes the change to the
 * backend within ~1.5s.
 */

import { useState } from "react";
import { Calendar, Hash, Tag, Link2, X, Clock } from "lucide-react";
import type { NoteStub } from "@/lib/api/notesClient";
import { useUniverseStore } from "@/store/useUniverseStore";

const NOTE_TYPES = [
  { value: "meeting_transcript", label: "Meeting Transcript" },
  { value: "earnings_call",      label: "Earnings Call" },
  { value: "management_meeting", label: "Management Meeting" },
  { value: "conference",         label: "Conference / NDR" },
  { value: "internal",           label: "Internal" },
];

interface Props {
  note: NoteStub;
  onTitleChange: (title: string) => void;
  onMeetingDateChange: (date: string | null) => void;
  onTickersChange: (tickers: string[]) => void;
  onNoteTypeChange: (noteType: string) => void;
}

function formatUpdatedAt(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const deltaMs = now.getTime() - d.getTime();
    const deltaMin = Math.floor(deltaMs / 60000);
    if (deltaMin < 1) return "just now";
    if (deltaMin < 60) return `${deltaMin}m ago`;
    const deltaHr = Math.floor(deltaMin / 60);
    if (deltaHr < 24) return `${deltaHr}h ago`;
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  } catch {
    return "";
  }
}

export default function NoteHeaderBlock({
  note,
  onTitleChange,
  onMeetingDateChange,
  onTickersChange,
  onNoteTypeChange,
}: Props) {
  const { tickers: universeTickers } = useUniverseStore();
  const [tickerInput, setTickerInput] = useState("");
  const [suggestions, setSuggestions] = useState<{ symbol: string; name: string }[]>([]);

  const addTicker = (raw: string) => {
    const v = raw.trim().toUpperCase();
    if (!v || note.company_tickers.includes(v)) {
      setTickerInput("");
      setSuggestions([]);
      return;
    }
    onTickersChange([...note.company_tickers, v]);
    setTickerInput("");
    setSuggestions([]);
  };

  const removeTicker = (t: string) => {
    onTickersChange(note.company_tickers.filter((x) => x !== t));
  };

  const handleTickerInput = (value: string) => {
    setTickerInput(value);
    const q = value.trim().toLowerCase();
    if (!q) {
      setSuggestions([]);
      return;
    }
    const matches = universeTickers
      .filter(
        (t) =>
          (t.symbol.toLowerCase().includes(q) || t.name.toLowerCase().includes(q)) &&
          !note.company_tickers.includes(t.symbol),
      )
      .slice(0, 6);
    setSuggestions(matches);
  };

  const handleTickerKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if ((e.key === "Enter" || e.key === ",") && tickerInput.trim()) {
      e.preventDefault();
      addTicker(tickerInput);
    }
    if (e.key === "Backspace" && !tickerInput && note.company_tickers.length > 0) {
      removeTicker(note.company_tickers[note.company_tickers.length - 1]);
    }
    if (e.key === "Escape") setSuggestions([]);
  };

  // Preserve any custom note_type the user added via the creation modal that's
  // not in the standard list — show it as a selectable option.
  const hasCustomType = !NOTE_TYPES.some((t) => t.value === note.note_type);

  return (
    <div className="px-10 pt-8 pb-4 border-b border-slate-100 bg-white">
      {/* Title — big, Notion-style */}
      <input
        type="text"
        value={note.title}
        onChange={(e) => onTitleChange(e.target.value)}
        placeholder="Untitled"
        className="w-full text-3xl font-bold text-slate-900 bg-transparent border-none outline-none placeholder-slate-300 focus:placeholder-slate-200 leading-tight"
      />

      {/* Metadata row — inline-editable properties */}
      <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
        {/* Companies / tickers */}
        <div className="flex items-center gap-2 min-w-0">
          <Hash size={14} className="text-slate-400 shrink-0" />
          <div className="flex flex-wrap items-center gap-1 relative">
            {note.company_tickers.map((t) => (
              <span
                key={t}
                className="flex items-center gap-1 px-2 py-0.5 text-xs font-mono font-semibold bg-indigo-50 text-indigo-700 rounded border border-indigo-100"
              >
                {t}
                <button
                  onClick={() => removeTicker(t)}
                  className="text-indigo-400 hover:text-indigo-700 transition-colors"
                  title="Remove"
                >
                  <X size={10} />
                </button>
              </span>
            ))}
            <div className="relative">
              <input
                type="text"
                value={tickerInput}
                onChange={(e) => handleTickerInput(e.target.value)}
                onKeyDown={handleTickerKeyDown}
                onBlur={() => {
                  if (tickerInput.trim()) addTicker(tickerInput);
                  setTimeout(() => setSuggestions([]), 120); // let click on suggestion register
                }}
                placeholder={note.company_tickers.length === 0 ? "Add ticker" : "+"}
                className="px-2 py-0.5 text-xs font-mono text-slate-700 placeholder-slate-400 bg-transparent border border-transparent hover:border-slate-200 focus:border-slate-300 rounded outline-none"
                size={Math.max(4, tickerInput.length || (note.company_tickers.length === 0 ? 10 : 4))}
              />
              {suggestions.length > 0 && (
                <div className="absolute top-full left-0 mt-1 w-64 bg-white border border-slate-200 rounded-lg shadow-lg z-10 overflow-hidden">
                  {suggestions.map((s) => (
                    <button
                      key={s.symbol}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        addTicker(s.symbol);
                      }}
                      className="w-full text-left px-3 py-2 text-xs hover:bg-indigo-50 hover:text-indigo-700 flex items-center gap-2 transition-colors"
                    >
                      <span className="font-mono font-semibold shrink-0">{s.symbol}</span>
                      <span className="text-slate-400 truncate">{s.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Meeting date */}
        <div className="flex items-center gap-2">
          <Calendar size={14} className="text-slate-400 shrink-0" />
          <input
            type="date"
            value={note.meeting_date ?? ""}
            onChange={(e) => onMeetingDateChange(e.target.value || null)}
            className="text-xs text-slate-700 bg-transparent border border-transparent hover:border-slate-200 focus:border-slate-300 rounded px-2 py-0.5 outline-none tabular-nums"
          />
        </div>

        {/* Note type */}
        <div className="flex items-center gap-2">
          <Tag size={14} className="text-slate-400 shrink-0" />
          <select
            value={note.note_type}
            onChange={(e) => onNoteTypeChange(e.target.value)}
            className="text-xs text-slate-700 bg-transparent border border-transparent hover:border-slate-200 focus:border-slate-300 rounded px-2 py-0.5 outline-none cursor-pointer"
          >
            {NOTE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
            {hasCustomType && (
              <option value={note.note_type}>{note.note_type}</option>
            )}
          </select>
        </div>

        {/* Source URL — read-only provenance marker */}
        {note.source_url && (
          <a
            href={note.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-xs text-slate-500 hover:text-indigo-600 transition-colors max-w-[280px]"
            title={note.source_url}
          >
            <Link2 size={14} className="text-slate-400 shrink-0" />
            <span className="truncate">{note.source_url}</span>
          </a>
        )}

        {/* Last edited */}
        {note.updated_at && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Clock size={14} className="text-slate-400 shrink-0" />
            <span>Edited {formatUpdatedAt(note.updated_at)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
