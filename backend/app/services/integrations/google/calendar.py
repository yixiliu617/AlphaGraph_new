"""
Google Calendar adapter — incremental sync via syncToken.

Sync strategy:
  - First run: fetch all events with `timeMin` set to ~30 days ago.
    Save Google's syncToken from the final page.
  - Subsequent runs: pass the syncToken back; Google returns only
    events that have CHANGED since (insert / update / cancellation
    tombstone). Same syncToken until Google rotates it.
  - 410 GONE on syncToken: token expired (Google rotates after ~7
    days of inactivity). Fall back to a full resync.

API:
  GET https://www.googleapis.com/calendar/v3/users/me/calendarList
       — list calendars accessible to this user
  GET https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events
       — paginated; pageToken for paging, syncToken for incremental
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from sqlalchemy import select, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.models.orm.calendar_event_orm import UserCalendarEvent
from backend.app.models.orm.credential_orm import UserCredential
from backend.app.services.integrations.base import (
    BaseIntegrationAdapter, SyncResult,
)


_BASE = "https://www.googleapis.com/calendar/v3"
_BACKFILL_DAYS = 30   # how far back to look on first sync


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(d: dict) -> tuple[Optional[datetime], bool]:
    """Google event start/end is `{dateTime: ...}` for timed events,
    `{date: 'YYYY-MM-DD'}` for all-day events. Returns (dt, all_day)."""
    if not d:
        return None, False
    if "dateTime" in d and d["dateTime"]:
        # ISO 8601 with offset — parse to UTC.
        try:
            dt = datetime.fromisoformat(d["dateTime"].replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc), False
        except ValueError:
            return None, False
    if "date" in d and d["date"]:
        # All-day: store at midnight UTC of that date.
        try:
            dt = datetime.fromisoformat(d["date"]).replace(tzinfo=timezone.utc)
            return dt, True
        except ValueError:
            return None, False
    return None, False


def _normalise_event(
    cred: UserCredential,
    calendar_id: str,
    event: dict,
) -> Optional[dict]:
    """Map a Google Calendar event JSON to our row shape. Returns None
    for events we can't parse (no start time, etc.)."""
    start_at, all_day = _parse_dt(event.get("start", {}))
    end_at, _         = _parse_dt(event.get("end", {}))
    if start_at is None and event.get("status") != "cancelled":
        return None  # malformed; skip

    raw_status = event.get("status", "confirmed")
    status = (
        "cancelled" if raw_status == "cancelled"
        else "tentative" if raw_status == "tentative"
        else "confirmed"
    )

    attendees = event.get("attendees") or []
    norm_attendees = [
        {
            "email": a.get("email"),
            "name":  a.get("displayName"),
            "response_status": a.get("responseStatus"),
            "is_self": bool(a.get("self")),
            "is_organizer": bool(a.get("organizer")),
        }
        for a in attendees
    ]

    organizer = event.get("organizer") or {}
    norm_organizer = {
        "email": organizer.get("email"),
        "name":  organizer.get("displayName"),
        "is_self": bool(organizer.get("self")),
    } if organizer else None

    last_modified = None
    if event.get("updated"):
        try:
            last_modified = datetime.fromisoformat(
                event["updated"].replace("Z", "+00:00"),
            ).astimezone(timezone.utc)
        except ValueError:
            pass

    return {
        "user_id":              cred.user_id,
        "source_credential_id": cred.id,
        "source_event_id":      event["id"],
        "source_calendar_id":   calendar_id,
        "provider":             "google",
        "title":                event.get("summary"),
        "description":          event.get("description"),
        "location":             event.get("location"),
        "html_link":            event.get("htmlLink"),
        "start_at":             start_at or _now(),  # cancelled events may lack start
        "end_at":               end_at,
        "all_day":              all_day,
        "attendees":            norm_attendees if norm_attendees else None,
        "organizer":            norm_organizer,
        "status":               status,
        "recurrence_master_id": event.get("recurringEventId"),
        "last_modified_at":     last_modified,
        "last_synced_at":       _now(),
        "raw_payload":          event,
    }


def _upsert_event(db: Session, row: dict) -> tuple[bool, bool]:
    """Insert-or-update one event row. Returns (inserted, updated)."""
    stmt = pg_insert(UserCalendarEvent).values(**row)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_user_calendar_event_source",
        set_={
            "title":               stmt.excluded.title,
            "description":         stmt.excluded.description,
            "location":            stmt.excluded.location,
            "html_link":           stmt.excluded.html_link,
            "start_at":            stmt.excluded.start_at,
            "end_at":              stmt.excluded.end_at,
            "all_day":             stmt.excluded.all_day,
            "attendees":           stmt.excluded.attendees,
            "organizer":           stmt.excluded.organizer,
            "status":              stmt.excluded.status,
            "recurrence_master_id": stmt.excluded.recurrence_master_id,
            "last_modified_at":    stmt.excluded.last_modified_at,
            "last_synced_at":      stmt.excluded.last_synced_at,
            "raw_payload":         stmt.excluded.raw_payload,
            "updated_at":          _now(),
        },
    ).returning(UserCalendarEvent.id, UserCalendarEvent.created_at)
    result = db.execute(stmt).fetchone()
    if result is None:
        return False, False
    # Heuristic: if created_at is within the last second, this was a
    # fresh insert; otherwise it was an update.
    inserted = (_now() - result[1]).total_seconds() < 2 if result[1] else True
    return inserted, not inserted


class GoogleCalendarAdapter(BaseIntegrationAdapter):
    service_id = "google.calendar"

    def sync(
        self,
        db: Session,
        cred: UserCredential,
        access_token: str,
    ) -> SyncResult:
        result = SyncResult()
        headers = {"Authorization": f"Bearer {access_token}"}

        # 1. Get the user's accessible calendars
        try:
            r = requests.get(
                f"{_BASE}/users/me/calendarList",
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
        except requests.HTTPError as e:
            result.error = f"calendarList failed: {e.response.status_code} {e.response.text[:300]}"
            return result
        calendars = r.json().get("items", [])

        # Restrict to user's primary by default; future enhancement: let
        # user pick which calendars to sync.
        primary = next((c for c in calendars if c.get("primary")), None)
        if primary is None and calendars:
            primary = calendars[0]
        if primary is None:
            result.error = "no calendars found for this account"
            return result

        calendar_id = primary["id"]

        # 2. Walk events with syncToken if available, otherwise full backfill.
        cursors_per_calendar: dict[str, str] = {}
        if cred.last_sync_cursor:
            try:
                cursors_per_calendar = json.loads(cred.last_sync_cursor)
            except (json.JSONDecodeError, TypeError):
                cursors_per_calendar = {}

        sync_token = cursors_per_calendar.get(calendar_id)
        params: dict[str, Any] = {
            "showDeleted":   "true",     # so cancellations come through
            "singleEvents":  "true",     # expand recurring instances
            "maxResults":    "250",
        }
        if sync_token:
            params["syncToken"] = sync_token
        else:
            # First run for this calendar: backfill _BACKFILL_DAYS days.
            params["timeMin"] = (_now() - timedelta(days=_BACKFILL_DAYS)).isoformat()

        new_sync_token: Optional[str] = None
        page_token: Optional[str] = None

        while True:
            page_params = dict(params)
            if page_token:
                page_params["pageToken"] = page_token
                # When paging, drop syncToken / timeMin per Google's contract.
                page_params.pop("syncToken", None)
                page_params.pop("timeMin", None)

            try:
                r = requests.get(
                    f"{_BASE}/calendars/{calendar_id}/events",
                    headers=headers,
                    params=page_params,
                    timeout=30,
                )
            except requests.RequestException as e:
                result.error = f"events page request failed: {e}"
                return result

            if r.status_code == 410:
                # Sync token expired — full resync needed.
                result.full_resync_required = True
                # Drop the cursor and start over without a syncToken.
                cursors_per_calendar.pop(calendar_id, None)
                params.pop("syncToken", None)
                params["timeMin"] = (_now() - timedelta(days=_BACKFILL_DAYS)).isoformat()
                page_token = None
                continue

            if r.status_code != 200:
                result.error = f"events page returned {r.status_code}: {r.text[:300]}"
                return result

            data = r.json()
            for event in data.get("items", []):
                row = _normalise_event(cred, calendar_id, event)
                if row is None:
                    result.skipped += 1
                    continue
                try:
                    ins, upd = _upsert_event(db, row)
                    if ins:
                        result.inserted += 1
                    elif upd:
                        result.updated += 1
                except Exception as e:  # noqa: BLE001 — defensive per row
                    result.skipped += 1
                    result.details.setdefault("upsert_errors", []).append(
                        {"event_id": event.get("id"), "error": str(e)[:200]},
                    )

            page_token = data.get("nextPageToken")
            if not page_token:
                # End of pages — Google sends nextSyncToken on the final page
                new_sync_token = data.get("nextSyncToken") or new_sync_token
                break

        if new_sync_token:
            cursors_per_calendar[calendar_id] = new_sync_token
            result.new_cursor = json.dumps(cursors_per_calendar)

        return result
