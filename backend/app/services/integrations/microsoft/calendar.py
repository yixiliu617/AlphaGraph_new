"""
Outlook Calendar (Microsoft Graph) adapter — incremental sync via delta link.

Sync strategy:
  - First run: GET /me/calendarView?startDateTime=...&endDateTime=...
    with `Prefer: odata.track-changes`. The final page contains a
    `@odata.deltaLink` that captures the cursor for next time.
  - Subsequent runs: GET that delta link directly. Returns only
    insertions / updates / deletion tombstones.
  - 410 / "Resync required" error: drop the cursor, full resync.

Per Graph docs, calendarView delta only covers a fixed window; we keep
the window at "now -30d to now +180d" to align with the dashboard's
"upcoming" use case.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.models.orm.calendar_event_orm import UserCalendarEvent
from backend.app.models.orm.credential_orm import UserCredential
from backend.app.services.integrations.base import (
    BaseIntegrationAdapter, SyncResult,
)


_BASE = "https://graph.microsoft.com/v1.0"
_WINDOW_PAST_DAYS   = 30
_WINDOW_FUTURE_DAYS = 180


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(d: dict) -> tuple[Optional[datetime], bool]:
    """Graph event start/end is `{dateTime, timeZone}` for timed events.
    All-day events come back with a `isAllDay=true` event-level flag.
    Returns (utc datetime, all_day) — all_day is filled by caller."""
    if not d or not d.get("dateTime"):
        return None, False
    s = d["dateTime"]
    # Graph sometimes returns 7 fractional seconds; Python only accepts up to 6.
    if "." in s:
        head, _, frac = s.partition(".")
        s = f"{head}.{frac[:6]}"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc), False
    except ValueError:
        return None, False


def _normalise_event(
    cred: UserCredential,
    event: dict,
) -> Optional[dict]:
    if event.get("@removed"):
        # Tombstone — mark as cancelled so dashboards know to drop it.
        # Graph delta payload for removed: {"@removed": {"reason": "deleted"}, "id": "..."}
        return {
            "user_id":              cred.user_id,
            "source_credential_id": cred.id,
            "source_event_id":      event["id"],
            "source_calendar_id":   None,
            "provider":             "microsoft",
            "title":                None,
            "description":          None,
            "location":             None,
            "html_link":            None,
            "start_at":             _now(),
            "end_at":               None,
            "all_day":              False,
            "attendees":            None,
            "organizer":            None,
            "status":               "cancelled",
            "recurrence_master_id": None,
            "last_modified_at":     _now(),
            "last_synced_at":       _now(),
            "raw_payload":          event,
        }

    start_at, _ = _parse_dt(event.get("start") or {})
    end_at, _   = _parse_dt(event.get("end") or {})
    if start_at is None:
        return None

    is_all_day = bool(event.get("isAllDay"))
    is_cancelled = bool(event.get("isCancelled"))
    status = "cancelled" if is_cancelled else "confirmed"

    attendees = event.get("attendees") or []
    norm_attendees = []
    for a in attendees:
        em = a.get("emailAddress") or {}
        st = a.get("status") or {}
        norm_attendees.append({
            "email": em.get("address"),
            "name":  em.get("name"),
            "response_status": st.get("response"),
            "is_self": False,        # Graph doesn't flag self the same way
            "is_organizer": a.get("type") == "required" and st.get("response") == "organizer",
        })

    organizer = event.get("organizer") or {}
    em = (organizer.get("emailAddress") or {}) if organizer else {}
    norm_organizer = {
        "email": em.get("address"),
        "name":  em.get("name"),
    } if em else None

    last_modified = None
    if event.get("lastModifiedDateTime"):
        try:
            s = event["lastModifiedDateTime"]
            if "." in s:
                head, _, frac = s.partition(".")
                s = f"{head}.{frac[:6]}"
            last_modified = datetime.fromisoformat(
                s.replace("Z", "+00:00"),
            ).astimezone(timezone.utc)
        except ValueError:
            pass

    location_obj = event.get("location") or {}
    location = location_obj.get("displayName") if isinstance(location_obj, dict) else None

    return {
        "user_id":              cred.user_id,
        "source_credential_id": cred.id,
        "source_event_id":      event["id"],
        "source_calendar_id":   None,  # Graph delta doesn't expose calendar_id per event easily
        "provider":             "microsoft",
        "title":                event.get("subject"),
        "description":          (event.get("bodyPreview") or "")[:8000] or None,
        "location":             location,
        "html_link":            event.get("webLink"),
        "start_at":             start_at,
        "end_at":               end_at,
        "all_day":              is_all_day,
        "attendees":            norm_attendees if norm_attendees else None,
        "organizer":            norm_organizer,
        "status":               status,
        "recurrence_master_id": event.get("seriesMasterId"),
        "last_modified_at":     last_modified,
        "last_synced_at":       _now(),
        "raw_payload":          event,
    }


def _upsert_event(db: Session, row: dict) -> tuple[bool, bool]:
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
    inserted = (_now() - result[1]).total_seconds() < 2 if result[1] else True
    return inserted, not inserted


class OutlookCalendarAdapter(BaseIntegrationAdapter):
    service_id = "microsoft.calendar"

    def sync(
        self,
        db: Session,
        cred: UserCredential,
        access_token: str,
    ) -> SyncResult:
        result = SyncResult()
        # Note: $top is NOT supported on calendarView delta — use the
        # `Prefer: odata.maxpagesize` header instead. Multiple Prefer
        # values are comma-separated.
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Prefer": 'outlook.timezone="UTC", odata.maxpagesize=200',
        }

        # Either use the stored delta link, or kick off a fresh delta query.
        if cred.last_sync_cursor and cred.last_sync_cursor.startswith("https://"):
            url = cred.last_sync_cursor
            params = None
        else:
            url = f"{_BASE}/me/calendarView/delta"
            past = (_now() - timedelta(days=_WINDOW_PAST_DAYS)).isoformat()
            future = (_now() + timedelta(days=_WINDOW_FUTURE_DAYS)).isoformat()
            params = {
                "startDateTime": past.replace("+00:00", "Z"),
                "endDateTime":   future.replace("+00:00", "Z"),
            }

        delta_link: Optional[str] = None

        while url:
            try:
                r = requests.get(url, headers=headers, params=params, timeout=30)
            except requests.RequestException as e:
                result.error = f"delta page request failed: {e}"
                return result

            if r.status_code == 410:
                # Resync required — drop cursor, restart.
                result.full_resync_required = True
                cred.last_sync_cursor = None
                url = f"{_BASE}/me/calendarView/delta"
                past = (_now() - timedelta(days=_WINDOW_PAST_DAYS)).isoformat()
                future = (_now() + timedelta(days=_WINDOW_FUTURE_DAYS)).isoformat()
                params = {
                    "startDateTime": past.replace("+00:00", "Z"),
                    "endDateTime":   future.replace("+00:00", "Z"),
                }
                continue

            if r.status_code != 200:
                result.error = f"delta returned {r.status_code}: {r.text[:300]}"
                return result

            data = r.json()
            for event in data.get("value", []):
                row = _normalise_event(cred, event)
                if row is None:
                    result.skipped += 1
                    continue
                try:
                    ins, upd = _upsert_event(db, row)
                    if ins:
                        result.inserted += 1
                    elif upd:
                        result.updated += 1
                except Exception as e:  # noqa: BLE001
                    result.skipped += 1
                    result.details.setdefault("upsert_errors", []).append(
                        {"event_id": event.get("id"), "error": str(e)[:200]},
                    )

            # Graph pagination: @odata.nextLink for the next page; the
            # FINAL page carries @odata.deltaLink (the cursor for next time).
            next_link = data.get("@odata.nextLink")
            new_delta = data.get("@odata.deltaLink")
            if new_delta:
                delta_link = new_delta
            url = next_link
            params = None  # subsequent pages get all params in the URL

        if delta_link:
            result.new_cursor = delta_link

        return result
