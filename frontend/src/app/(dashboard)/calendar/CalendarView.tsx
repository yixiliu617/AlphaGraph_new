"use client";

import { CalendarDays, ExternalLink, RefreshCw, Search, AlertCircle } from "lucide-react";
import type { CalendarEvent } from "@/lib/api/calendarClient";

export type Filters = {
  market:   "ALL" | "US" | "TW" | "JP" | "KR";
  ticker:   string;
  range:    "next7" | "next30" | "next90";
  showPast: boolean;
};

interface Props {
  events: CalendarEvent[];
  loading: boolean;
  error: string | null;
  filters: Filters;
  onFiltersChange: (next: Filters) => void;
  tz: string;
  browserTz: string;
  onTzChange: (tz: string) => void;
  onRefresh: () => void;
}

// IANA timezones we offer in the dropdown. Keep small for MVP — settings page
// can expose the full list later.
const TZ_PRESETS = [
  "America/New_York",
  "America/Los_Angeles",
  "America/Chicago",
  "Europe/London",
  "Asia/Taipei",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Asia/Shanghai",
  "UTC",
];

const MARKETS: Filters["market"][] = ["ALL", "US", "TW", "JP", "KR"];

export default function CalendarView({
  events, loading, error, filters, onFiltersChange,
  tz, browserTz, onTzChange, onRefresh,
}: Props) {
  const now = Date.now();
  const upcoming = events.filter((e) => new Date(e.release_datetime_utc).getTime() >= now);
  const past     = events.filter((e) => new Date(e.release_datetime_utc).getTime() <  now);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <header className="shrink-0 border-b border-slate-200 bg-white px-6 py-3.5 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <CalendarDays size={18} className="text-indigo-600" />
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-slate-900">
              Earnings Calendar
            </h2>
            <p className="text-[11px] text-slate-500 leading-tight">
              {events.length.toLocaleString()} events ·  {upcoming.length} upcoming ·  {past.length} past
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Ticker filter */}
          <div className="relative">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
            <input
              type="text"
              placeholder="Filter ticker (e.g. NVDA)"
              value={filters.ticker}
              onChange={(e) => onFiltersChange({ ...filters, ticker: e.target.value })}
              className="pl-7 pr-2.5 py-1 text-xs border border-slate-300 rounded w-44
                         focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* Market filter */}
          <select
            value={filters.market}
            onChange={(e) => onFiltersChange({ ...filters, market: e.target.value as Filters["market"] })}
            className="text-xs border border-slate-300 rounded px-2 py-1 bg-white"
          >
            {MARKETS.map((m) => (
              <option key={m} value={m}>{m === "ALL" ? "All markets" : m}</option>
            ))}
          </select>

          {/* Timezone */}
          <select
            value={tz}
            onChange={(e) => onTzChange(e.target.value)}
            className="text-xs border border-slate-300 rounded px-2 py-1 bg-white"
            title={`Browser detected: ${browserTz}`}
          >
            {!TZ_PRESETS.includes(tz) && <option value={tz}>{tz}</option>}
            {TZ_PRESETS.map((z) => (
              <option key={z} value={z}>
                {z === browserTz ? `${z} (browser)` : z}
              </option>
            ))}
          </select>

          <button
            onClick={onRefresh}
            disabled={loading}
            className="text-xs px-2.5 py-1 border border-slate-300 rounded hover:bg-slate-50
                       disabled:opacity-50 flex items-center gap-1.5"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </header>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {error && (
          <div className="mb-4 flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded text-xs text-red-800">
            <AlertCircle size={14} className="shrink-0 mt-0.5" />
            <div>
              <div className="font-semibold">Failed to load calendar</div>
              <div className="font-mono mt-1">{error}</div>
            </div>
          </div>
        )}

        {loading && events.length === 0 && (
          <div className="text-center text-xs text-slate-500 py-12">Loading events...</div>
        )}

        {!loading && events.length === 0 && !error && (
          <div className="text-center text-xs text-slate-500 py-12">
            No events match the current filters.
          </div>
        )}

        {/* Upcoming section */}
        {upcoming.length > 0 && (
          <Section title="Upcoming" subtitle={`${upcoming.length} event${upcoming.length === 1 ? "" : "s"}`}>
            <EventTable events={upcoming} tz={tz} highlight="upcoming" />
          </Section>
        )}

        {/* Past section */}
        {filters.showPast && past.length > 0 && (
          <Section
            title="Past"
            subtitle={`${past.length} event${past.length === 1 ? "" : "s"} (newest first)`}
          >
            <EventTable events={past} tz={tz} highlight="past" />
          </Section>
        )}

        {/* Toggle past visibility */}
        <div className="mt-4 flex justify-center">
          <button
            onClick={() => onFiltersChange({ ...filters, showPast: !filters.showPast })}
            className="text-[11px] text-indigo-600 hover:text-indigo-800 underline"
          >
            {filters.showPast ? "Hide past events" : "Show past events"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Section({ title, subtitle, children }: {
  title: string; subtitle?: string; children: React.ReactNode;
}) {
  return (
    <section className="mb-6">
      <div className="flex items-baseline gap-2 mb-2">
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {subtitle && <span className="text-[11px] text-slate-500">{subtitle}</span>}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Event table
// ---------------------------------------------------------------------------

function fmtDateInTz(iso: string, tz: string): { date: string; time: string } {
  try {
    const d = new Date(iso);
    const dateFmt = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, year: "numeric", month: "short", day: "2-digit", weekday: "short",
    });
    const timeFmt = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false,
    });
    return { date: dateFmt.format(d), time: timeFmt.format(d) };
  } catch {
    return { date: iso.slice(0, 10), time: "" };
  }
}

function MarketBadge({ market }: { market: CalendarEvent["market"] }) {
  const colors = {
    US: "bg-blue-50 text-blue-700 border-blue-200",
    TW: "bg-emerald-50 text-emerald-700 border-emerald-200",
    JP: "bg-rose-50 text-rose-700 border-rose-200",
    KR: "bg-amber-50 text-amber-700 border-amber-200",
  } as const;
  return (
    <span className={`px-1.5 py-0.5 text-[9px] font-mono font-semibold uppercase border rounded ${colors[market] ?? "bg-slate-50 text-slate-700 border-slate-200"}`}>
      {market}
    </span>
  );
}

function StatusDot({ status }: { status: CalendarEvent["status"] }) {
  const colors = {
    upcoming:  "bg-amber-400",
    confirmed: "bg-emerald-500",
    done:      "bg-slate-300",
  } as const;
  return (
    <span
      className={`inline-block w-1.5 h-1.5 rounded-full ${colors[status] ?? "bg-slate-300"}`}
      title={status}
    />
  );
}

function VerificationBadge({ v }: { v: CalendarEvent["verification"] }) {
  if (!v) return null;
  const cfg: Record<string, { color: string; label: string; tip: string }> = {
    "nasdaq+yahoo_match":  { color: "bg-emerald-50 text-emerald-700 border-emerald-200",
                              label: "verified", tip: "NASDAQ and Yahoo Finance agree on the date" },
    "nasdaq_only":         { color: "bg-slate-50 text-slate-600 border-slate-200",
                              label: "nasdaq",   tip: "NASDAQ only; Yahoo did not confirm" },
    "yahoo_only":          { color: "bg-blue-50 text-blue-700 border-blue-200",
                              label: "yahoo",    tip: "Yahoo only; NASDAQ did not list" },
    "date_disagreement":   { color: "bg-amber-50 text-amber-700 border-amber-200",
                              label: "conflict", tip: "NASDAQ and Yahoo disagree on the date by >1 day" },
  };
  const c = cfg[v];
  if (!c) return null;
  return (
    <span
      title={c.tip}
      className={`px-1.5 py-0.5 text-[9px] font-mono uppercase border rounded ${c.color}`}
    >
      {c.label}
    </span>
  );
}

function EventTable({
  events, tz, highlight,
}: {
  events: CalendarEvent[]; tz: string; highlight: "upcoming" | "past";
}) {
  return (
    <div className="border border-slate-200 rounded overflow-hidden bg-white">
      <table className="w-full text-xs">
        <thead className="bg-slate-50 text-slate-600">
          <tr className="border-b border-slate-200">
            <th className="text-left px-3 py-2 w-32 font-semibold">Date</th>
            <th className="text-left px-3 py-2 w-16 font-semibold">Time</th>
            <th className="text-left px-3 py-2 w-12 font-semibold">Mkt</th>
            <th className="text-left px-3 py-2 w-20 font-semibold">Ticker</th>
            <th className="text-left px-3 py-2 w-28 font-semibold">Period</th>
            <th className="text-left px-3 py-2 font-semibold">Source</th>
            <th className="text-right px-3 py-2 w-32 font-semibold">Links</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e, i) => {
            const { date, time } = fmtDateInTz(e.release_datetime_utc, tz);
            const isFuture = highlight === "upcoming";
            return (
              <tr
                key={`${e.ticker}-${e.fiscal_period}-${i}`}
                className={`border-b border-slate-100 last:border-0 hover:bg-indigo-50/40 ${isFuture ? "bg-amber-50/30" : ""}`}
              >
                <td className="px-3 py-2 whitespace-nowrap text-slate-700">{date}</td>
                <td className="px-3 py-2 whitespace-nowrap font-mono text-[10px] text-slate-500">{time}</td>
                <td className="px-3 py-2"><MarketBadge market={e.market} /></td>
                <td className="px-3 py-2 font-semibold text-slate-900">{e.ticker}</td>
                <td className="px-3 py-2 font-mono text-[10px] text-slate-600 whitespace-nowrap">
                  {e.fiscal_period.startsWith("AS-OF") ? (
                    <span className="text-slate-400">{e.fiscal_period.replace("AS-OF-", "")}</span>
                  ) : (
                    e.fiscal_period
                  )}
                </td>
                <td className="px-3 py-2 text-[10px] text-slate-500">
                  <span className="flex items-center gap-1.5">
                    <StatusDot status={e.status} />
                    <span className="font-mono">{e.source}</span>
                    <VerificationBadge v={e.verification} />
                  </span>
                </td>
                <td className="px-3 py-2 text-right">
                  <span className="inline-flex gap-2 justify-end">
                    {e.press_release_url && (
                      <a
                        href={e.press_release_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-0.5 text-[10px]"
                        title="Press release / 8-K filing"
                      >
                        <ExternalLink size={10} /> 8-K
                      </a>
                    )}
                    {e.webcast_url && (
                      <a
                        href={e.webcast_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-0.5 text-[10px]"
                        title="Webcast"
                      >
                        <ExternalLink size={10} /> Live
                      </a>
                    )}
                    {e.transcript_url && (
                      <a
                        href={e.transcript_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-0.5 text-[10px]"
                        title="Transcript"
                      >
                        <ExternalLink size={10} /> Transcript
                      </a>
                    )}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
