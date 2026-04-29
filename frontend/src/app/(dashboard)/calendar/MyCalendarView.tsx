"use client";

/**
 * MyCalendarView — the signed-in user's synced personal calendar
 * (Google Calendar + Outlook Calendar merged into one timeline).
 *
 * Sister component to CalendarView (the Earnings Calendar). Lives in
 * the same /calendar page, switched via a top-of-page tab toggle in
 * CalendarContainer.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CalendarDays, ExternalLink, Loader2, MapPin, RefreshCw, Users, AlertCircle,
} from "lucide-react";
import {
  meCalendarClient, type MeCalendarEvent,
} from "@/lib/api/meCalendarClient";


type Range = "next7" | "next14" | "next30" | "next90";


export default function MyCalendarView() {
  const [range, setRange]     = useState<Range>("next7");
  const [events, setEvents]   = useState<MeCalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const days = range === "next7" ? 7 : range === "next14" ? 14 : range === "next30" ? 30 : 90;

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await meCalendarClient.upcoming(days, true);
      setEvents(r.events || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { fetchEvents(); }, [fetchEvents]);

  const onSyncNow = useCallback(async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const r = await meCalendarClient.syncNow();
      const ok = r.results.filter((x) => x.ok);
      const totalIns = r.results.reduce((s, x) => s + (x.inserted ?? 0), 0);
      const totalUpd = r.results.reduce((s, x) => s + (x.updated  ?? 0), 0);
      setSyncMsg(
        `Synced ${ok.length}/${r.results.length} sources. +${totalIns} new · ${totalUpd} updated.`,
      );
      await fetchEvents();
    } catch (e) {
      setSyncMsg(`Sync failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSyncing(false);
    }
  }, [fetchEvents]);

  // Group events by ISO date (in user's local TZ) for the day-headers
  const grouped = useMemo(() => {
    const groups = new Map<string, MeCalendarEvent[]>();
    for (const e of events) {
      const d = new Date(e.start_at);
      const key = d.toLocaleDateString(undefined, {
        weekday: "long", year: "numeric", month: "short", day: "numeric",
      });
      const arr = groups.get(key) ?? [];
      arr.push(e);
      groups.set(key, arr);
    }
    return Array.from(groups.entries());
  }, [events]);

  return (
    <div className="px-8 py-6 space-y-4">
      {/* Header + toolbar */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <CalendarDays size={18} className="text-indigo-600" />
          <h2 className="text-lg font-semibold text-slate-900">My Calendar</h2>
          <span className="text-xs text-slate-500">— synced from Google + Outlook</span>
        </div>
        <div className="flex items-center gap-2">
          <RangePicker value={range} onChange={setRange} />
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

      {/* Loading / empty / list */}
      {loading && (
        <div className="flex items-center justify-center h-72 bg-slate-50 rounded-md">
          <Loader2 className="animate-spin text-slate-400" />
        </div>
      )}

      {!loading && !error && events.length === 0 && (
        <div className="text-sm text-slate-500 px-4 py-12 bg-slate-50 border border-slate-200 rounded text-center">
          <p className="mb-2">No events in the next {days} days.</p>
          <p className="text-xs text-slate-400">
            Connect a calendar at <code className="font-mono">/api/v1/connections/connect/google.calendar</code> if you haven't, then click Sync now.
          </p>
        </div>
      )}

      {!loading && !error && events.length > 0 && (
        <div className="space-y-4">
          {grouped.map(([dayLabel, dayEvents]) => (
            <div key={dayLabel} className="bg-white border border-slate-200 rounded-md overflow-hidden">
              <div className="px-4 py-2 bg-slate-50 border-b border-slate-200">
                <h3 className="text-xs font-semibold text-slate-700">{dayLabel}</h3>
              </div>
              <ul className="divide-y divide-slate-100">
                {dayEvents.map((e) => <EventRow key={e.id} e={e} />)}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function RangePicker({ value, onChange }: { value: Range; onChange: (v: Range) => void }) {
  const opts: { v: Range; label: string }[] = [
    { v: "next7",  label: "Next 7 days" },
    { v: "next14", label: "Next 14 days" },
    { v: "next30", label: "Next 30 days" },
    { v: "next90", label: "Next 90 days" },
  ];
  return (
    <div className="flex rounded-md border border-slate-300 overflow-hidden text-xs">
      {opts.map(({ v, label }, i) => (
        <button
          key={v}
          type="button"
          onClick={() => onChange(v)}
          className={
            "px-2.5 py-1 " +
            (value === v
              ? "bg-slate-700 text-white"
              : "bg-white text-slate-600 hover:bg-slate-50") +
            (i > 0 ? " border-l border-slate-300" : "")
          }
        >
          {label}
        </button>
      ))}
    </div>
  );
}


function EventRow({ e }: { e: MeCalendarEvent }) {
  const start = new Date(e.start_at);
  const end   = e.end_at ? new Date(e.end_at) : null;
  const time  = e.all_day
    ? "All day"
    : start.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
      + (end ? " – " + end.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "");

  const providerLabel = e.provider === "google" ? "Google" : "Outlook";
  const providerCls   = e.provider === "google"
    ? "text-indigo-700 bg-indigo-50 border-indigo-200"
    : "text-sky-700 bg-sky-50 border-sky-200";

  return (
    <li className="px-4 py-2.5 hover:bg-slate-50 flex items-start gap-3">
      <div className="w-32 shrink-0 text-[11px] font-mono text-slate-500 pt-0.5">
        {time}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm text-slate-900 truncate">{e.title || "(untitled)"}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded border ${providerCls}`}>
            {providerLabel}
          </span>
          {e.html_link && (
            <a href={e.html_link} target="_blank" rel="noreferrer"
               className="text-slate-400 hover:text-indigo-600">
              <ExternalLink size={11} />
            </a>
          )}
        </div>
        {(e.location || (e.attendees && e.attendees.length > 0)) && (
          <div className="mt-1 flex items-center gap-3 text-[11px] text-slate-500">
            {e.location && (
              <span className="flex items-center gap-1 truncate">
                <MapPin size={10} /> <span className="truncate max-w-[260px]">{e.location}</span>
              </span>
            )}
            {e.attendees && e.attendees.length > 0 && (
              <span className="flex items-center gap-1">
                <Users size={10} /> {e.attendees.length}
              </span>
            )}
          </div>
        )}
      </div>
    </li>
  );
}
