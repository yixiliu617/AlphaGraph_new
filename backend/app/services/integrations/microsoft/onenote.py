"""
OneNote adapter — Microsoft Graph.

Sync strategy:
  - List all sections across all notebooks (one request per page of
    sections; usually fits in one). The global `/me/onenote/pages`
    endpoint rejects accounts with many sections (error 20266) and
    explicitly tells you to use per-section listing instead.
  - For each section, list pages (`$orderby=lastModifiedDateTime desc`),
    optionally filtered by `lastModifiedDateTime gt {cursor}` for
    incremental syncs.
  - For each new/changed page, fetch its HTML content.
  - Cap content_html at ~500 KB to avoid pathological pages with
    embedded base64 images. Anything larger gets `content_truncated=True`.
  - Cap at `_PAGES_PER_TICK` per run to keep a single sync bounded;
    re-running will pick up the rest on first sync.

Endpoints (Microsoft Graph v1.0):
  GET /me/onenote/sections?$expand=parentNotebook
       — list of sections with their notebook context
  GET /me/onenote/sections/{section_id}/pages
       — pages within a section (metadata only)
  GET /me/onenote/pages/{id}/content
       — page's HTML (text/html). Multipart with binary attachments
       if the page has handwriting/images. We accept text/html and
       fetch content as plain text.

Rate-limits / caveats:
  - Graph throttles after ~10 RPS per app per tenant.
  - Personal MSA accounts (outlook.com) silently reject Notes.Read.All
    even after consent — see oauth_scopes.py for the scope choice
    rationale.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional

import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.models.orm.note_synced_orm import UserNote
from backend.app.models.orm.credential_orm import UserCredential
from backend.app.services.integrations.base import (
    BaseIntegrationAdapter, SyncResult,
)


_BASE = "https://graph.microsoft.com/v1.0"
_PAGES_PER_TICK = 100      # cap pages per sync run to avoid API blowups
_CONTENT_BYTE_CAP = 500_000  # 500 KB plaintext limit before truncation


class _TextExtractor(HTMLParser):
    """Strip HTML tags + collapse whitespace. Tiny, no deps."""
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._chunks.append(data)

    def get_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._chunks)).strip()


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        # HTMLParser can raise on weird input; fall back to a regex strip.
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    return p.get_text()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if "." in s:
            head, _, frac = s.partition(".")
            s = f"{head}.{frac[:6]}"
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _normalise_page(
    cred: UserCredential,
    page: dict,
    content_html: Optional[str],
) -> dict:
    parent_section = page.get("parentSection") or {}
    parent_nb      = page.get("parentNotebook") or {}

    truncated = False
    if content_html and len(content_html) > _CONTENT_BYTE_CAP:
        content_html = content_html[:_CONTENT_BYTE_CAP]
        truncated = True
    content_text = _html_to_text(content_html or "")
    if len(content_text) > _CONTENT_BYTE_CAP:
        content_text = content_text[:_CONTENT_BYTE_CAP]
        truncated = True

    links = page.get("links") or {}
    page_link = (links.get("oneNoteWebUrl") or {}).get("href") or (
        links.get("oneNoteClientUrl") or {}
    ).get("href")

    return {
        "user_id":              cred.user_id,
        "source_credential_id": cred.id,
        "source_note_id":       page["id"],
        "provider":             "microsoft",
        "service":              "microsoft.onenote",
        "title":                page.get("title"),
        "notebook_id":          parent_nb.get("id"),
        "notebook_name":        parent_nb.get("displayName"),
        "section_id":           parent_section.get("id"),
        "section_name":         parent_section.get("displayName"),
        "page_link":            page_link,
        "content_html":         content_html,
        "content_text":         content_text or None,
        "content_truncated":    truncated,
        "created_at_remote":       _parse_dt(page.get("createdDateTime")),
        "last_modified_at_remote": _parse_dt(page.get("lastModifiedDateTime")),
        "last_synced_at":          _now(),
        "raw_payload":             page,
    }


def _upsert_note(db: Session, row: dict) -> tuple[bool, bool]:
    stmt = pg_insert(UserNote).values(**row)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_user_note_source",
        set_={
            "title":                   stmt.excluded.title,
            "notebook_id":             stmt.excluded.notebook_id,
            "notebook_name":           stmt.excluded.notebook_name,
            "section_id":              stmt.excluded.section_id,
            "section_name":            stmt.excluded.section_name,
            "page_link":               stmt.excluded.page_link,
            "content_html":            stmt.excluded.content_html,
            "content_text":            stmt.excluded.content_text,
            "content_truncated":       stmt.excluded.content_truncated,
            "created_at_remote":       stmt.excluded.created_at_remote,
            "last_modified_at_remote": stmt.excluded.last_modified_at_remote,
            "last_synced_at":          stmt.excluded.last_synced_at,
            "raw_payload":             stmt.excluded.raw_payload,
            "updated_at":              _now(),
        },
    ).returning(UserNote.id, UserNote.created_at)
    result = db.execute(stmt).fetchone()
    if result is None:
        return False, False
    inserted = (_now() - result[1]).total_seconds() < 2 if result[1] else True
    return inserted, not inserted


def _list_all_sections(headers: dict) -> list[dict] | str:
    """Walk `/me/onenote/sections` and return every section with its
    parent notebook context attached. Returns a list, or an error
    string if any page of the listing fails.
    """
    sections: list[dict] = []
    url = f"{_BASE}/me/onenote/sections"
    params: Optional[dict] = {
        "$expand": "parentNotebook",
        "$top":    "100",
        "$select": "id,displayName,parentNotebook",
    }
    while url:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
        except requests.RequestException as e:
            return f"section list request failed: {e}"
        if r.status_code != 200:
            return f"section list returned {r.status_code}: {r.text[:300]}"
        data = r.json()
        sections.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None  # nextLink has params baked in
    return sections


class OneNoteAdapter(BaseIntegrationAdapter):
    service_id = "microsoft.onenote"

    def sync(
        self,
        db: Session,
        cred: UserCredential,
        access_token: str,
    ) -> SyncResult:
        result = SyncResult()
        headers = {"Authorization": f"Bearer {access_token}"}

        sections = _list_all_sections(headers)
        if isinstance(sections, str):
            result.error = sections
            return result

        cursor_filter: Optional[str] = None
        if cred.last_sync_cursor:
            cursor_filter = f"lastModifiedDateTime gt {cred.last_sync_cursor}"

        pages_processed = 0
        most_recent_modified: Optional[datetime] = None

        for section in sections:
            if pages_processed >= _PAGES_PER_TICK:
                break

            section_id = section["id"]
            section_name = section.get("displayName")
            parent_nb = section.get("parentNotebook") or {}

            url: Optional[str] = f"{_BASE}/me/onenote/sections/{section_id}/pages"
            params: Optional[dict] = {
                "$orderby": "lastModifiedDateTime desc",
                "$top":     "100",
                "$select":  "id,title,createdDateTime,lastModifiedDateTime,links",
            }
            if cursor_filter:
                params["$filter"] = cursor_filter

            while url and pages_processed < _PAGES_PER_TICK:
                try:
                    r = requests.get(url, headers=headers, params=params, timeout=30)
                except requests.RequestException as e:
                    result.details.setdefault("section_errors", []).append({
                        "section_id": section_id, "error": str(e)[:200],
                    })
                    break

                if r.status_code != 200:
                    # Soft-fail one section so a single bad notebook doesn't
                    # take down the whole sync.
                    result.details.setdefault("section_errors", []).append({
                        "section_id": section_id,
                        "status":     r.status_code,
                        "body":       r.text[:200],
                    })
                    break

                data = r.json()
                pages = data.get("value", [])

                for page in pages:
                    if pages_processed >= _PAGES_PER_TICK:
                        break

                    # Inject parent context (we already know it from the
                    # section iteration; cheaper than $expand on every page).
                    page["parentSection"] = {
                        "id":          section_id,
                        "displayName": section_name,
                    }
                    page["parentNotebook"] = {
                        "id":          parent_nb.get("id"),
                        "displayName": parent_nb.get("displayName"),
                    }

                    content_html: Optional[str] = None
                    content_url = f"{_BASE}/me/onenote/pages/{page['id']}/content"
                    try:
                        cr = requests.get(content_url, headers=headers, timeout=30)
                        if cr.status_code == 200:
                            content_html = cr.text
                        elif cr.status_code in (403, 404):
                            # Page moved / deleted between list and content fetch.
                            result.skipped += 1
                            continue
                    except requests.RequestException:
                        content_html = None

                    row = _normalise_page(cred, page, content_html)
                    modified = row.get("last_modified_at_remote")
                    if modified and (most_recent_modified is None or modified > most_recent_modified):
                        most_recent_modified = modified

                    try:
                        ins, upd = _upsert_note(db, row)
                        if ins:
                            result.inserted += 1
                        elif upd:
                            result.updated += 1
                    except Exception as e:  # noqa: BLE001
                        result.skipped += 1
                        result.details.setdefault("upsert_errors", []).append(
                            {"page_id": page.get("id"), "error": str(e)[:200]},
                        )
                    pages_processed += 1

                url = data.get("@odata.nextLink")
                params = None  # nextLink has all params baked in

        if most_recent_modified:
            cursor = most_recent_modified.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:23] + "Z"
            result.new_cursor = cursor

        return result
