"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { calendarClient, type CalendarEvent } from "@/lib/api/calendarClient";
import CalendarView, { type Filters } from "./CalendarView";
import MyCalendarView from "./MyCalendarView";

type Tab = "earnings" | "personal";

const DEFAULT_FILTERS: Filters = {
  market:   "ALL",
  ticker:   "",
  range:    "next30",
  showPast: true,
};

export default function CalendarContainer() {
  const [tab, setTab] = useState<Tab>("earnings");
  return (
    <div>
      <div className="flex items-center gap-1 px-8 pt-6">
        <TabButton active={tab === "earnings"} onClick={() => setTab("earnings")}>
          Earnings Calendar
        </TabButton>
        <TabButton active={tab === "personal"} onClick={() => setTab("personal")}>
          My Calendar
        </TabButton>
      </div>
      {tab === "earnings"
        ? <EarningsCalendarSection />
        : <MyCalendarView />}
    </div>
  );
}


function TabButton({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors " +
        (active
          ? "border-indigo-600 text-indigo-700"
          : "border-transparent text-slate-500 hover:text-slate-800")
      }
    >
      {children}
    </button>
  );
}


function EarningsCalendarSection() {
  // Browser-detected timezone. User can override via the View's selector.
  const browserTz = useMemo(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    [],
  );
  const [tz, setTz] = useState<string>(browserTz);

  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [events, setEvents]   = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Two calls: upcoming + recent (so we get both sides of "today"
      // without depending on the parquet having upcoming-status rows yet).
      const [up, recent] = await Promise.all([
        calendarClient.upcoming(30, filters.market === "ALL" ? undefined : filters.market),
        filters.showPast
          ? calendarClient.recent(180, filters.market === "ALL" ? undefined : filters.market)
          : Promise.resolve({ success: true as const, data: [] }),
      ]);
      let merged: CalendarEvent[] = [...(up.data || []), ...(recent.data || [])];

      if (filters.ticker.trim()) {
        const want = filters.ticker.trim().toUpperCase();
        merged = merged.filter((e) => e.ticker.toUpperCase().includes(want));
      }

      // Sort: future events ascending (soonest first), then past descending.
      const now = Date.now();
      merged.sort((a, b) => {
        const ta = new Date(a.release_datetime_utc).getTime();
        const tb = new Date(b.release_datetime_utc).getTime();
        const aFuture = ta >= now;
        const bFuture = tb >= now;
        if (aFuture !== bFuture) return aFuture ? -1 : 1; // future first
        return aFuture ? ta - tb : tb - ta;
      });

      setEvents(merged);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  return (
    <CalendarView
      events={events}
      loading={loading}
      error={error}
      filters={filters}
      onFiltersChange={setFilters}
      tz={tz}
      browserTz={browserTz}
      onTzChange={setTz}
      onRefresh={fetchEvents}
    />
  );
}
