/**
 * calendarClient — fetches earnings-calendar events from
 * backend/data/earnings_calendar/events.parquet via the /calendar/* router.
 *
 * BASE is "/calendar" — apiRequest prepends http://localhost:8000/api/v1.
 *
 * Status conventions (from backend storage.py):
 *   "upcoming"  — future event, date may shift
 *   "confirmed" — future event with regulator-confirmed date
 *   "done"      — event already happened (most rows in MVP)
 */

import { apiRequest } from "./base";

type AR<T> = { success: boolean; data: T; error?: string; meta?: Record<string, unknown> };

export interface CalendarEvent {
  ticker: string;
  market: "US" | "TW" | "JP" | "KR";
  fiscal_period: string;          // "FY2026-Q3" | "AS-OF-2025-12-31" (fallback)
  release_datetime_utc: string;   // ISO string, UTC
  release_local_tz: string;       // IANA tz, e.g. "America/New_York"
  status: "upcoming" | "confirmed" | "done";

  press_release_url: string | null;
  filing_url: string | null;
  webcast_url: string | null;
  transcript_url: string | null;
  dial_in_phone: string | null;
  dial_in_pin: string | null;

  source: string;                 // "edgar_8k" | "mops_material_info" | "nasdaq_calendar" | ...
  source_id: string;

  // Cross-source verification (used for upcoming events).
  // null/empty for past / single-source rows.
  verification:
    | "nasdaq+yahoo_match"
    | "nasdaq_only"
    | "yahoo_only"
    | "date_disagreement"
    | ""
    | null;

  // NASDAQ-rich fields. null for non-NASDAQ rows (past 8-K events, etc.)
  time_of_day_code:        "BMO" | "AMC" | "TBD" | "" | null;
  eps_forecast:            number | null;
  eps_estimates_count:     number | null;
  market_cap:              number | null;
  last_year_eps:           number | null;
  last_year_report_date:   string | null;   // YYYY-MM-DD

  first_seen_at: string;
  last_updated_at: string;
}

export interface ListParams {
  from?: string;     // YYYY-MM-DD
  to?: string;
  market?: "US" | "TW" | "JP" | "KR";
  ticker?: string;
  status?: "upcoming" | "confirmed" | "done";
  limit?: number;
}

const BASE = "/calendar";

export const calendarClient = {
  list(params?: ListParams): Promise<AR<CalendarEvent[]>> {
    const qs = new URLSearchParams();
    if (params?.from)   qs.set("from",   params.from);
    if (params?.to)     qs.set("to",     params.to);
    if (params?.market) qs.set("market", params.market);
    if (params?.ticker) qs.set("ticker", params.ticker);
    if (params?.status) qs.set("status", params.status);
    if (params?.limit)  qs.set("limit",  String(params.limit));
    const q = qs.toString();
    return apiRequest<AR<CalendarEvent[]>>(`${BASE}/events${q ? `?${q}` : ""}`);
  },

  upcoming(days = 30, market?: ListParams["market"]): Promise<AR<CalendarEvent[]>> {
    const qs = new URLSearchParams({ days: String(days) });
    if (market) qs.set("market", market);
    return apiRequest<AR<CalendarEvent[]>>(`${BASE}/events/upcoming?${qs.toString()}`);
  },

  recent(days = 14, market?: ListParams["market"]): Promise<AR<CalendarEvent[]>> {
    const qs = new URLSearchParams({ days: String(days) });
    if (market) qs.set("market", market);
    return apiRequest<AR<CalendarEvent[]>>(`${BASE}/events/recent?${qs.toString()}`);
  },

  forTicker(symbol: string, limit = 50): Promise<AR<CalendarEvent[]>> {
    return apiRequest<AR<CalendarEvent[]>>(
      `${BASE}/events/ticker/${encodeURIComponent(symbol)}?limit=${limit}`,
    );
  },
};
