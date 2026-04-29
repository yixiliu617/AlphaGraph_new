"use client";

/**
 * SyncedNotesContainer — entry point for /synced-notes (OneNote, etc.).
 *
 * Two-pane layout:
 *   - Left: notebook filter + searchable list of notes
 *   - Right: full content of the selected note
 *
 * Distinct from /notes which serves AlphaGraph's own meeting-transcript
 * notes. This page renders only third-party synced notes (currently
 * OneNote — adapter at backend/app/services/integrations/microsoft/onenote.py).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle, BookOpen, ExternalLink, Loader2, RefreshCw, Search,
} from "lucide-react";
import {
  meNotesClient, type NoteFull, type NoteSummary,
} from "@/lib/api/meNotesClient";


export default function SyncedNotesContainer() {
  const [notes, setNotes]               = useState<NoteSummary[]>([]);
  const [notebooks, setNotebooks]       = useState<string[]>([]);
  const [selectedNotebook, setNotebook] = useState<string | "">("");
  const [query, setQuery]               = useState<string>("");
  const [selectedId, setSelectedId]     = useState<string | null>(null);
  const [selected, setSelected]         = useState<NoteFull | null>(null);
  const [loading, setLoading]           = useState(true);
  const [error, setError]               = useState<string | null>(null);
  const [syncing, setSyncing]           = useState(false);
  const [syncMsg, setSyncMsg]           = useState<string | null>(null);

  const fetchList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (query.trim().length >= 2) {
        const r = await meNotesClient.search(query.trim());
        setNotes(r.notes);
      } else {
        const r = await meNotesClient.list(selectedNotebook || undefined);
        setNotes(r.notes);
        setNotebooks(r.notebooks);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [query, selectedNotebook]);

  useEffect(() => { fetchList(); }, [fetchList]);

  // Auto-load the first note in the list if nothing is selected yet
  useEffect(() => {
    if (!selectedId && notes.length > 0) {
      setSelectedId(notes[0].id);
    }
  }, [notes, selectedId]);

  // Fetch full content of the selected note
  useEffect(() => {
    if (!selectedId) { setSelected(null); return; }
    let cancelled = false;
    meNotesClient.get(selectedId)
      .then((n) => { if (!cancelled) setSelected(n); })
      .catch(() => { if (!cancelled) setSelected(null); });
    return () => { cancelled = true; };
  }, [selectedId]);

  const onSyncNow = useCallback(async () => {
    setSyncing(true); setSyncMsg(null);
    try {
      const r = await meNotesClient.syncNow();
      const totalIns = r.results.reduce((s, x) => s + (x.inserted ?? 0), 0);
      const totalUpd = r.results.reduce((s, x) => s + (x.updated  ?? 0), 0);
      setSyncMsg(`Sync complete. +${totalIns} new · ${totalUpd} updated.`);
      await fetchList();
    } catch (e) {
      setSyncMsg(`Sync failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSyncing(false);
    }
  }, [fetchList]);

  return (
    <div className="px-8 py-6 space-y-4">
      {/* Header + toolbar */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <BookOpen size={18} className="text-indigo-600" />
          <h2 className="text-lg font-semibold text-slate-900">OneNote</h2>
          <span className="text-xs text-slate-500">— synced from Microsoft</span>
        </div>
        <button
          type="button"
          onClick={onSyncNow}
          disabled={syncing}
          className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium border border-slate-300 rounded hover:bg-slate-50 disabled:opacity-50"
        >
          {syncing
            ? <Loader2 size={13} className="animate-spin" />
            : <RefreshCw size={13} />}
          Sync now
        </button>
      </div>

      {syncMsg && (
        <div className="text-xs px-3 py-2 bg-indigo-50 border border-indigo-200 rounded text-indigo-900">
          {syncMsg}
        </div>
      )}
      {error && (
        <div className="text-xs px-3 py-2 bg-rose-50 border border-rose-200 rounded text-rose-800 flex items-center gap-1">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      <div className="grid grid-cols-12 gap-4 min-h-[60vh]">
        {/* List pane */}
        <div className="col-span-4 bg-white border border-slate-200 rounded-md flex flex-col">
          {/* Search + notebook filter */}
          <div className="px-3 py-2 border-b border-slate-200 space-y-2">
            <div className="relative">
              <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                placeholder="Search notes..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="w-full pl-7 pr-2 py-1 text-xs border border-slate-300 rounded focus:outline-none focus:border-indigo-500"
              />
            </div>
            {notebooks.length > 0 && (
              <select
                value={selectedNotebook}
                onChange={(e) => setNotebook(e.target.value)}
                className="w-full px-2 py-1 text-xs border border-slate-300 rounded"
              >
                <option value="">All notebooks</option>
                {notebooks.map((nb) => (
                  <option key={nb} value={nb}>{nb}</option>
                ))}
              </select>
            )}
          </div>

          {/* List */}
          <div className="flex-1 overflow-y-auto">
            {loading && (
              <div className="flex items-center justify-center h-48">
                <Loader2 className="animate-spin text-slate-400" />
              </div>
            )}
            {!loading && notes.length === 0 && (
              <div className="text-xs text-slate-500 p-4 text-center">
                {query
                  ? `No notes match "${query}"`
                  : "No notes synced yet. Click Sync now after connecting OneNote."}
              </div>
            )}
            <ul className="divide-y divide-slate-100">
              {notes.map((n) => (
                <li
                  key={n.id}
                  onClick={() => setSelectedId(n.id)}
                  className={
                    "px-3 py-2 cursor-pointer hover:bg-slate-50 " +
                    (selectedId === n.id ? "bg-indigo-50/50" : "")
                  }
                >
                  <div className="text-xs font-medium text-slate-900 truncate">
                    {n.title || "(untitled)"}
                  </div>
                  <div className="text-[10px] text-slate-500 mt-0.5 flex items-center gap-1">
                    <span className="truncate">{n.notebook || "—"}</span>
                    <span>·</span>
                    <span>{n.last_modified ? new Date(n.last_modified).toLocaleDateString() : "—"}</span>
                  </div>
                  {n.preview && (
                    <div className="text-[10px] text-slate-400 mt-0.5 line-clamp-2">{n.preview}</div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Detail pane */}
        <div className="col-span-8 bg-white border border-slate-200 rounded-md p-4 overflow-y-auto">
          {selected ? <NoteDetail note={selected} /> : (
            <div className="text-xs text-slate-500 text-center pt-12">
              Select a note from the list
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


function NoteDetail({ note }: { note: NoteFull }) {
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-base font-semibold text-slate-900 leading-snug">
          {note.title || "(untitled)"}
        </h3>
        {note.page_link && (
          <a href={note.page_link} target="_blank" rel="noreferrer"
             className="text-xs text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1 shrink-0">
            Open in OneNote <ExternalLink size={10} />
          </a>
        )}
      </div>

      <div className="text-xs text-slate-500 flex items-center gap-3">
        {note.notebook && <span>📓 {note.notebook}</span>}
        {note.section && <span>📂 {note.section}</span>}
        {note.last_modified && (
          <span>· Modified {new Date(note.last_modified).toLocaleString()}</span>
        )}
        {note.truncated && (
          <span className="text-amber-600">· Truncated (large page)</span>
        )}
      </div>

      {/* Render OneNote HTML directly. Sandboxed iframe would be safer
          for hostile content; for the user's own notes, direct render is
          acceptable. */}
      {note.content_html ? (
        <div
          className="prose prose-sm max-w-none mt-4 text-sm text-slate-700"
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: note.content_html }}
        />
      ) : note.content_text ? (
        <pre className="text-xs whitespace-pre-wrap text-slate-700 mt-4 font-sans">
          {note.content_text}
        </pre>
      ) : (
        <div className="text-xs text-slate-400 italic mt-4">No content fetched.</div>
      )}
    </div>
  );
}
