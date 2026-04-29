/**
 * meCalendarClient — read the signed-in user's synced calendar events
 * (Google + Outlook merged), via /api/v1/me/calendar/*.
 *
 * Distinct from `calendarClient` which serves the global Earnings
 * Calendar (corporate earnings dates, not the user's personal events).
 */

import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface MeAttendee {
  email: string | null;
  name:  string | null;
  response_status?: string | null;
  is_self?: boolean;
  is_organizer?: boolean;
}

export interface MeOrganizer {
  email: string | null;
  name:  string | null;
}

export interface MeCalendarEvent {
  id:          string;
  provider:    "google" | "microsoft";
  title:       string | null;
  location:    string | null;
  html_link:   string | null;
  start_at:    string;          // ISO with timezone offset
  end_at:      string | null;
  all_day:     boolean;
  attendees:   MeAttendee[];
  organizer:   MeOrganizer | null;
  description: string | null;   // capped at 500 chars server-side
}

export interface SyncResultRow {
  service:  string;
  ok:       boolean;
  inserted: number;
  updated:  number;
  error:    string | null;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const meCalendarClient = {
  async upcoming(days = 7, includePastToday = false) {
    const params = new URLSearchParams({
      days: String(days),
      include_past_today: String(includePastToday),
    });
    return apiRequest<{ events: MeCalendarEvent[] }>(
      `/me/calendar/events?${params}`,
    );
  },
  async syncNow() {
    return apiRequest<{ results: SyncResultRow[] }>(
      `/me/calendar/sync`,
      "POST",
    );
  },
};
