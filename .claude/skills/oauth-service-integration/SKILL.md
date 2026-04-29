---
name: oauth-service-integration
description: End-to-end procedure for adding a new Google or Microsoft OAuth-backed service integration to AlphaGraph (Calendar, Mail, Notes, Drive, Docs, etc.). Covers provider-app registration (Google Cloud Console, Microsoft Entra ID / Azure AD), scope choice, AlphaGraph backend wiring (oauth_scopes.py + adapter + sync_runner + API), frontend page wiring, testing flow, and the dozen edge cases we hit during the Google Calendar + Outlook Calendar + OneNote rollout (refresh-token loss, OIDC iss-claim mismatch on Microsoft `common` tenant, Notes.Read.All silently 401 for personal MSA accounts, Microsoft incremental-consent silently skipping the consent screen, /me/onenote/pages rejected for many-section accounts, CORS cookie issue, redirect_uri trailing-slash mismatch, etc.). Use whenever you are wiring up a new third-party service that uses Google or Microsoft OAuth, OR diagnosing why an existing one stopped working.
version: 1.0
last_validated_at: 2026-04-28
conditions: []
prerequisites: []
tags: [oauth, integrations, google, microsoft, graph, authlib, sync, procedure]
---

# OAuth Service Integration

End-to-end recipe for plugging a new Google or Microsoft service (Calendar, Mail, Notes, Drive, Docs, …) into AlphaGraph. Covers everything from provider-app registration through frontend wiring, plus the edge cases we already paid for once.

The integration is data-driven — every service is one entry in `backend/app/services/auth/oauth_scopes.py` plus one adapter file. Routers and the sync runner pick services up automatically.

## When to use this skill

- Adding a new Google or Microsoft service that wasn't in `oauth_scopes.py` before.
- Diagnosing why an existing connection 401s, returns no data, or is stuck not syncing.
- Onboarding a new developer who needs to set up the Google + Microsoft OAuth clients on their own Cloud / Azure tenants.

If you're integrating a non-Google / non-Microsoft service (Slack, Notion, Linear), the **flow shape** still applies — only the provider-side console steps differ.

## What "done" looks like

- A new service-id (`<provider>.<service>`, e.g. `microsoft.outlook_mail`) is registered in `oauth_scopes.py`.
- Connecting via `/api/v1/connections/connect/<service_id>` redirects through provider consent and returns to a working callback that stores an encrypted credential.
- A sync adapter pulls data into Postgres on demand (`POST /api/v1/<service>/sync`) and on schedule.
- The frontend has a tab/page that lists what was synced.
- Refresh-token rotation works (verify by waiting >1h then triggering a sync).
- The token-encryption key, OAuth client IDs, and OAuth client secrets are in `.env`, NOT in code.

## High-level architecture

```
User clicks "Connect"  →  /connections/connect/<svc>  →  provider consent
                                                       →  /connections/callback/<svc>
                                                       →  upsert UserCredential (encrypted)

Sync tick / "Sync now"  →  sync_runner.sync_credential(cred)
                        →  refresh_access_token if expired
                        →  adapter.sync(db, cred, access_token)
                        →  upsert rows into service-specific table

Frontend tab            →  GET /api/v1/me/<service>/<resource>
                        →  reads from service-specific table
```

Files involved (use as a template when adding a new service):

| Layer | File | What it does |
|---|---|---|
| Scope registry | `backend/app/services/auth/oauth_scopes.py` | One dict entry per service |
| ORM | `backend/app/models/orm/<resource>_orm.py` | Postgres table for the synced data |
| Migration | `backend/alembic/versions/00NN_*.py` | Creates the table |
| Adapter | `backend/app/services/integrations/<provider>/<service>.py` | `sync(db, cred, access_token) → SyncResult` |
| Sync runner | `backend/app/services/integrations/sync_runner.py` | `_ADAPTERS` dict picks the adapter by service-id |
| API | `backend/app/api/routers/v1/me_<service>.py` | List / search / sync-now endpoints |
| Frontend client | `frontend/src/lib/api/<service>Client.ts` | Typed fetch wrappers |
| Frontend page | `frontend/src/app/(dashboard)/<service>/page.tsx` | UI |

The OAuth machinery itself (`/connections/...` routes, callback, token refresh, encryption) is already built in `backend/app/api/routers/v1/connections.py` + `backend/app/services/auth/credential_service.py`. **You should not need to touch those files when adding a new service** — only the table above.

## Part A: Provider-app setup (one-time per provider, per environment)

Skip this part if the AlphaGraph Google + Microsoft apps are already registered for the environment you're working in. Otherwise the variables below need to land in `.env`.

### A.1 — Google Cloud Console

1. **Go to** https://console.cloud.google.com/, create or select a project.
2. **APIs & Services → Library**: enable each API the new service needs (e.g. "Google Calendar API", "Gmail API", "Google Docs API", "Google Drive API"). Enable them BEFORE the consent screen step or the scope picker won't show your scopes.
3. **APIs & Services → OAuth consent screen**:
   - User type: External
   - App name: AlphaGraph
   - Developer email: yours
   - Scopes: add the scopes you'll request. Sensitive (`gmail.readonly`, `documents.readonly`, etc.) and Restricted (`gmail.readonly`) scopes show a warning — that's fine for dev (Testing mode allows up to 100 test users without verification).
   - Test users: add your email here. **Without this, you get `Error 403: access_denied`** at consent time.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Type: Web application
   - Authorized redirect URIs: `http://localhost:8000/api/v1/connections/callback/google` (this is the Authlib OAuth callback path; ALL google.* services share it).
   - **Click the outer Save button** at the bottom of the page after editing redirect URIs. The inner row's "Save" only commits the row, not the form. (We hit `redirect_uri_mismatch` because of this once.)
5. **Copy** the client ID + secret into `.env`:
   ```
   GOOGLE_OAUTH_CLIENT_ID=xxxxxx.apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxx
   ```

### A.2 — Microsoft Entra ID (Azure AD)

1. **Go to** https://entra.microsoft.com/ → Identity → Applications → App registrations → New registration.
2. **Name**: AlphaGraph. **Supported account types**: "Accounts in any organizational directory and personal Microsoft accounts" (this enables `outlook.com` / `live.com` / `hotmail.com` in addition to work/school accounts; required for solo-dev use).
3. **Redirect URI**: Web, `http://localhost:8000/api/v1/connections/callback/microsoft`. (All microsoft.* services share this — same shape as Google.)
4. **Certificates & secrets → Client secrets → New client secret**: Description "alphagraph-dev", expiry 24 months. **Copy the *Value* column immediately** — Azure shows it once. The "Secret ID" is NOT what you want.
5. **API permissions**: add the Graph delegated scopes you need, e.g. `User.Read`, `Calendars.Read`, `Mail.Read`, `Notes.Read`, `Files.Read.All`, `offline_access`. Click **Grant admin consent** (only relevant for tenant accounts; harmless for personal).
6. **Overview tab**: copy Application (client) ID into `.env`:
   ```
   MICROSOFT_OAUTH_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   MICROSOFT_OAUTH_CLIENT_SECRET=<the Value from step 4>
   MICROSOFT_OAUTH_TENANT=common
   ```
   `common` lets both org accounts and personal MSA accounts sign in.

### A.3 — Token encryption key

Generate once per environment. Persist forever (rotating loses access to all stored credentials):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add to `.env`:

```
TOKEN_ENCRYPTION_KEY=<the generated key>
SECRET_KEY=<any 32+ byte random string for JWT + session signing>
```

For rotation, `TOKEN_ENCRYPTION_KEYS` accepts a comma-separated list (oldest last); see `backend/app/services/auth/encryption.py`.

## Part B: Adding a new service to AlphaGraph

### B.1 — Choose the right scope

This is where most personal-account quirks bite. Match the table:

| Goal | Provider | Scope to request | Notes |
|---|---|---|---|
| Read user's calendars | Google | `https://www.googleapis.com/auth/calendar.readonly` | |
| Read user's calendars | Microsoft | `Calendars.Read` | |
| Read Gmail | Google | `https://www.googleapis.com/auth/gmail.readonly` | RESTRICTED — needs CASA audit before public launch. Testing-mode is fine for dev. |
| Read Outlook mail | Microsoft | `Mail.Read` | |
| Read OneNote (personal accounts) | Microsoft | `Notes.Read` | **NOT `Notes.Read.All`** — see edge case E.4. |
| Read OneNote (org accounts only) | Microsoft | `Notes.Read.All` | If you need shared notebooks AND only support work/school accounts. |
| Read OneDrive | Microsoft | `Files.Read.All` | |
| Read Google Docs content | Google | `documents.readonly` + `drive.metadata.readonly` | First reads doc bodies; second is needed to LIST docs. |

Always include `offline_access` for Microsoft (needed to issue a refresh token; `access_type=offline` is the Google equivalent and is set automatically by our connect router).

### B.2 — Register the service in `oauth_scopes.py`

Add an entry to `SERVICES` in `backend/app/services/auth/oauth_scopes.py`:

```python
"microsoft.outlook_mail": {
    "provider":     "microsoft",
    "display_name": "Outlook Mail",
    "scopes": [
        "offline_access", "User.Read",
        "Mail.Read",
    ],
    "sync_minutes": 60,
},
```

Service-id format: `<provider>.<service>`. Connect URL becomes `/api/v1/connections/connect/<service-id>`.

**Microsoft scopes intentionally OMIT `openid`/`email`/`profile`.** Including them triggers Authlib's OIDC validator on the access-token response, which rejects Microsoft's `common`-tenant ID tokens because the token's `iss` is the real tenant GUID but discovery returns a placeholder. We use `User.Read` + `Graph /me` for user identity instead — works on every tenant. (See edge case E.5.)

### B.3 — Build the ORM table + Alembic migration

Pattern: each synced resource gets its own table keyed by `(user_id, source_credential_id, source_<resource>_id)`, with a `provider` column for cross-service queries.

Example (synced notes):

```python
# backend/app/models/orm/note_synced_orm.py
class UserNote(Phase2Base):
    __tablename__ = "user_note"
    id                       = Column(UUID, primary_key=True, default=uuid4)
    user_id                  = Column(UUID, ForeignKey("app_user.id"), nullable=False)
    source_credential_id     = Column(UUID, ForeignKey("user_credential.id"), nullable=False)
    source_note_id           = Column(String, nullable=False)
    provider                 = Column(String, nullable=False)  # 'google' | 'microsoft'
    service                  = Column(String, nullable=False)  # 'microsoft.onenote'
    title                    = Column(String, nullable=True)
    notebook_id, notebook_name, section_id, section_name, page_link = ...
    content_html             = Column(Text, nullable=True)
    content_text             = Column(Text, nullable=True)
    content_truncated        = Column(Boolean, default=False)
    created_at_remote        = Column(DateTime(timezone=True), nullable=True)
    last_modified_at_remote  = Column(DateTime(timezone=True), nullable=True)
    last_synced_at           = Column(DateTime(timezone=True), nullable=True)
    raw_payload              = Column(JSONB, nullable=True)  # for forensic debugging
    __table_args__ = (UniqueConstraint("user_id", "source_credential_id", "source_note_id",
                                       name="uq_user_note_source"),)
```

Then a migration:

```bash
cd backend
POSTGRES_URI=postgresql+psycopg://alphagraph:alphagraph@localhost:5432/alphagraph \
    alembic revision -m "user_note table"
# edit the generated 00NN_*.py to add op.create_table(...)
POSTGRES_URI=postgresql+psycopg://alphagraph:alphagraph@localhost:5432/alphagraph \
    alembic upgrade head
```

**Why we set POSTGRES_URI on the command line**: alembic, run from `backend/`, doesn't pick up `backend/../.env` because pydantic-settings's `env_file` is relative to cwd. (Edge case E.7.)

Also: at the bottom of `backend/app/db/phase2_session.py`, add a side-effect import of the new ORM:

```python
import backend.app.models.orm.note_synced_orm  # noqa: F401
```

Without this, SQLAlchemy can't resolve `relationship()` string references at first DB query and you get `expression 'X' failed to locate a name`. (Edge case E.6.)

### B.4 — Build the adapter

Create `backend/app/services/integrations/<provider>/<service>.py`. Implements:

```python
class FooAdapter(BaseIntegrationAdapter):
    service_id = "microsoft.outlook_mail"

    def sync(self, db: Session, cred: UserCredential, access_token: str) -> SyncResult:
        # 1. Hit the provider API with `Authorization: Bearer {access_token}`
        # 2. Use cred.last_sync_cursor for incremental sync (delta-link / sync-token /
        #    timestamp filter — depending on what the provider offers)
        # 3. Upsert rows via pg_insert(...).on_conflict_do_update(...)
        # 4. Return SyncResult(inserted, updated, skipped, new_cursor, error)
        ...
```

Pick the right incremental cursor for the provider:

| Provider/API | Cursor type | Notes |
|---|---|---|
| Google Calendar `events.list` | `syncToken` | 410 GONE → wipe cursor and full-resync |
| Microsoft `/me/calendarView/delta` | `deltaLink` | Use `Prefer: odata.maxpagesize=200` header — `$top` is rejected on delta endpoints (E.10) |
| Microsoft OneNote pages | `lastModifiedDateTime` filter | No native delta; manage cursor yourself |

Iterate with `@odata.nextLink` (Microsoft) or `nextPageToken` (Google). Cap pages-per-tick to keep a single sync bounded.

### B.5 — Register the adapter in the sync runner

```python
# backend/app/services/integrations/sync_runner.py
_ADAPTERS = {
    "google.calendar":     GoogleCalendarAdapter,
    "microsoft.calendar":  OutlookCalendarAdapter,
    "microsoft.onenote":   OneNoteAdapter,
    "microsoft.outlook_mail": OutlookMailAdapter,   # ← new
}
```

That's the only file change needed for the sync runner — `sync_credential(db, cred)` and `sync_all_due()` are already data-driven from this dict.

### B.6 — API + frontend

API: `backend/app/api/routers/v1/me_<service>.py` with at minimum `GET /list`, `GET /{id}`, `POST /sync`. Wire it into `backend/main.py`:

```python
from backend.app.api.routers.v1 import me_notes
app.include_router(me_notes.router, prefix=f"{settings.API_V1_STR}/me/notes", tags=["me-notes"])
```

Frontend: typed client at `frontend/src/lib/api/<service>Client.ts` + page at `frontend/src/app/(dashboard)/<service>/page.tsx`. Crucial: the global fetch in `frontend/src/lib/api/base.ts` MUST set `credentials: "include"` so the auth cookie crosses origin (E.11).

## Part C: Testing flow

These five checks, in this order, will catch every problem we've seen.

### C.1 — Connect the service

```
Open: http://localhost:8000/api/v1/connections/connect/<service-id>
```

(There's no `/connections` UI page yet — paste the URL into the browser. We have a TODO to add a UI.)

Expected: provider consent screen → redirects to `http://localhost:3001/...` after acceptance. If you get an error, jump to the relevant edge case below.

### C.2 — Probe the API directly with the stored token

This is the fastest way to know whether the issue is auth, scope, or the adapter. Run:

```python
from dotenv import load_dotenv; load_dotenv(".env")
from backend.app.db.phase2_session import Phase2SessionLocal
from backend.app.models.orm.credential_orm import UserCredential
from backend.app.services.auth.credential_service import (
    get_decrypted_tokens, is_access_token_expired, refresh_access_token,
)
import requests

db = Phase2SessionLocal()
cred = (db.query(UserCredential)
        .filter(UserCredential.service == "<service-id>",
                UserCredential.revoked_at.is_(None))
        .order_by(UserCredential.created_at.desc()).first())

if is_access_token_expired(cred):
    refresh_access_token(db, cred); db.refresh(cred)
tok = get_decrypted_tokens(cred)["access_token"]
H = {"Authorization": f"Bearer {tok}"}

print("scopes in DB:", cred.scopes)
print("/me:", requests.get("https://graph.microsoft.com/v1.0/me", headers=H).status_code)
print("the resource:", requests.get("<endpoint you actually need>", headers=H).status_code)
```

If `/me` returns 200 but the resource endpoint returns 401, the token works but the *scope* is wrong. See edge cases E.4 (OneNote personal-account quirk) and E.12 (Microsoft incremental-consent silently skipping).

### C.3 — Run a sync via the runner

```python
from backend.app.services.integrations.sync_runner import sync_credential
result = sync_credential(db, cred)
print(result)
```

Verify rows in the table:

```python
from sqlalchemy import func
from backend.app.models.orm.<resource>_orm import <Resource>
print(db.query(func.count(<Resource>.id)).scalar())
```

### C.4 — Verify refresh-token rotation

The scary failure mode is "works for an hour, then 401s". To prevent that:
- Confirm `cred.refresh_token_encrypted` is non-NULL after connect.
- For Google, this requires `access_type=offline` AND `prompt=consent` on the FIRST connect (set in `connections.py` already; if missing, the user has to revoke + reconnect — see E.3).
- For Microsoft, this requires `offline_access` in scopes.

Force a refresh manually:

```python
refresh_access_token(db, cred)
```

Should return a new access token without errors.

### C.5 — Frontend round-trip

Visit the new page. Confirm:
- The API call from the page sends the auth cookie (Network tab → request shows `Cookie: ag_session=...`).
- The page renders synced rows.
- "Sync now" button triggers a fresh sync.

## Part D: Edge cases (the dozen we already paid for)

### E.1 — `redirect_uri_mismatch`

You added the right URL but didn't click the outer Save in Google Cloud Console; or you added the URL to a *different* OAuth client. Verify you're editing the client whose ID is in `.env`. Every google.* service shares the SAME callback URL: `/api/v1/connections/callback/google`.

### E.2 — `Error 403: access_denied`

User's email isn't on the OAuth consent screen → Audience → Test users list. Add it. (Applies in Testing mode only.)

### E.3 — Refresh token MISSING on first connect

Authlib by default doesn't forward `access_type` and `prompt` from `client_kwargs` to `authorize_redirect()`. They must be passed as positional kwargs. We already do this in `connections.py`:

```python
auth_extra = {}
if spec["provider"] == "google":
    auth_extra["access_type"] = "offline"
    auth_extra["prompt"] = "consent"
return await client.authorize_redirect(request, redirect_uri, **auth_extra)
```

If the user already consented WITHOUT these (early version of our app), Google won't re-issue a refresh token on subsequent consents. They have to revoke at https://myaccount.google.com/permissions and reconnect.

### E.4 — Microsoft OneNote 401 with `Notes.Read.All` on personal accounts ★

Symptom: `/me/onenote/notebooks` returns `40001 "The request does not contain a valid authentication token"` even though `/me` and `/me/calendars` succeed with the same token.

Cause: `Notes.Read.All` ("notebooks user can access, including shared") silently fails for `@outlook.com` / `@live.com` / `@hotmail.com` MSA accounts. Documented Microsoft quirk.

Fix: switch to `Notes.Read` ("just current user's notebooks") in `oauth_scopes.py`. Upgrade to `Notes.Read.All` only if shared-notebook access is required AND you only support work/school accounts.

### E.5 — Microsoft `common`-tenant ID-token `iss` mismatch ★

Symptom: `InvalidClaimError: 'iss'` when the OAuth callback runs.

Cause: Authlib sees `openid email profile` in scopes, fetches the OIDC discovery doc for `common`, validates the ID token's `iss` against the discovery document's `issuer` field — but `common`'s discovery returns a placeholder issuer (`https://login.microsoftonline.com/{tenantid}/v2.0`) while the actual issued ID token has the real tenant GUID baked in.

Fix: For Microsoft connect clients, omit `openid`/`email`/`profile` and skip OIDC discovery entirely:

```python
client = oauth.register(
    name="microsoft_<svc>",
    client_id=settings.MICROSOFT_OAUTH_CLIENT_ID,
    client_secret=settings.MICROSOFT_OAUTH_CLIENT_SECRET,
    authorize_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
    access_token_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
    # NO server_metadata_url
    client_kwargs={"scope": " ".join(spec["scopes"])},
)
```

For user identity, hit Graph `/me` after exchange instead of relying on the ID token. Already implemented in `connections.py`.

### E.6 — `Mapper expression 'X' failed to locate a name`

Phase 2 ORM relationships use string references (`relationship("UserAlert")`), which SQLAlchemy resolves lazily on first query. If only one ORM is imported, the others aren't loaded and the resolution fails.

Fix: side-effect imports at the bottom of `backend/app/db/phase2_session.py`:

```python
import backend.app.models.orm.user_orm           # noqa: F401
import backend.app.models.orm.alert_orm          # noqa: F401
import backend.app.models.orm.credential_orm     # noqa: F401
import backend.app.models.orm.calendar_event_orm # noqa: F401
import backend.app.models.orm.note_synced_orm    # noqa: F401
# ← add new ORMs here
```

### E.7 — alembic doesn't pick up `.env`

Symptom: `OperationalError: near "::": syntax error` (SQLite parsing a Postgres URL because Settings defaulted).

Cause: pydantic-settings's `env_file=".env"` is relative to current working directory. When alembic runs from `backend/`, it looks for `backend/.env` and finds nothing, so falls back to the default SQLite URL.

Fix: pass POSTGRES_URI explicitly on the command line:

```bash
POSTGRES_URI=postgresql+psycopg://alphagraph:alphagraph@localhost:5432/alphagraph \
    alembic upgrade head
```

### E.8 — `TOKEN_ENCRYPTION_KEY not set` from non-FastAPI scripts

Cause: `backend/main.py` runs `load_dotenv()` at the top so FastAPI requests work, but ad-hoc scripts run `from backend.app.services.auth.encryption import ...` without the dotenv hook firing.

Fix in scripts:

```python
from dotenv import load_dotenv
load_dotenv(".env")
# ...now imports that touch encryption work
```

### E.9 — pandas `NAType` not JSON-serializable

Hit when a sync writer or API serializer encounters a NaT/NaN. Wrap with `pd.isna()` (handles all pandas NA sentinels) inside try/except. Already fixed in earnings calendar; keep an eye out in new adapters.

### E.10 — Microsoft delta endpoint rejects `$top`

Symptom: `400 Bad Request` from `/me/calendarView/delta` when you append `$top=200`.

Fix: use the header form instead:

```python
H = {
    "Authorization": f"Bearer {access_token}",
    "Prefer": 'outlook.timezone="UTC", odata.maxpagesize=200',
}
```

### E.11 — Frontend gets `401 not authenticated` even though backend works in browser

Cause: cross-origin `fetch()` doesn't send cookies by default. The auth cookie set by `/api/v1/auth/google/login` lives on `localhost:8000`, but the frontend at `localhost:3001` calls `localhost:8000/api/v1/...` and Chrome strips cookies.

Fix in `frontend/src/lib/api/base.ts`:

```typescript
const response = await fetch(url, {
    method, headers, body,
    credentials: "include",   // ← critical
});
```

Backend side: CORS must already have `allow_credentials=True` (it does in `backend/main.py`).

### E.12 — Microsoft incremental consent silently skips re-consent screen ★

Symptom: you changed the scope in `oauth_scopes.py`, the user reconnected, but the new scope doesn't actually work. Trying `refresh_access_token` with the new scope explicitly returns `AADSTS70000: scopes requested are unauthorized`.

Cause: Microsoft saw an existing app grant for AlphaGraph and didn't re-prompt. Tokens were re-issued with the OLD scopes. The credential row's `scopes` column shows what Microsoft echoed back, not necessarily what's currently effective.

Fix: have the user fully revoke the app:

1. Open https://account.live.com/consent/Manage (personal accounts) or https://myapps.microsoft.com (org accounts).
2. Find AlphaGraph → Remove these permissions.
3. Reconnect via `/connections/connect/<svc>` — Microsoft now shows a fresh consent screen.

To diagnose without revoking: refresh-with-explicit-scope:

```python
import requests
from backend.app.core.config import settings
resp = requests.post(
    f"https://login.microsoftonline.com/{settings.MICROSOFT_OAUTH_TENANT}/oauth2/v2.0/token",
    data={
        "grant_type":    "refresh_token",
        "refresh_token": <decrypted refresh token>,
        "client_id":     settings.MICROSOFT_OAUTH_CLIENT_ID,
        "client_secret": settings.MICROSOFT_OAUTH_CLIENT_SECRET,
        "scope":         "offline_access User.Read <new scope>",
    },
)
print(resp.json())  # AADSTS70000 = consent missing for that scope
```

### E.13 — Microsoft `/me/onenote/pages` returns 20266 "max sections exceeded" ★

Symptom: `400 Bad Request` from the global pages endpoint when the user has many notebooks/sections.

Fix: switch to per-section iteration. Walk `/me/onenote/sections?$expand=parentNotebook` first, then for each section fetch `/me/onenote/sections/{id}/pages`. Inject parent-notebook info from the section iteration so you don't need `$expand=parentNotebook,parentSection` on every page. Pattern is already implemented in `OneNoteAdapter`.

### E.14 — Two uvicorn processes shadowing each other

Symptom: code changes don't take effect even after `--reload` says "reloaded".

Cause: a previous `uvicorn` is still bound to the same port (typically 127.0.0.1:8000), and the new one binds 0.0.0.0:8000 — both listen, but TCP routes localhost requests to the older one.

Fix:

```bash
netstat -ano | grep :8000     # find PIDs
taskkill /F /PID <old-pid>    # PowerShell / cmd
```

Then restart uvicorn. We've also seen the `--reload` watcher miss module-level dict mutations in `_ADAPTERS` etc. — full Ctrl+C restart is the cure.

### E.15 — Heuristic "inserted vs updated" off by ~98 on initial sync

Cause: `_upsert_note` distinguishes inserted from updated by checking whether `created_at` is within the last 2 seconds. With ~1.5s/page sync time, only the first 1-2 pages clear the threshold; the rest are mislabeled as "updated".

Status: not blocking — data is correct, just the per-row metric is wrong. Fix later by making the upsert use `RETURNING (xmax = 0) AS inserted` (Postgres trick that's TRUE for inserts).

## Part E: Output format

When this skill is invoked to add a new service, the expected deliverable is:

1. **Code changes** (in this order):
   - `oauth_scopes.py` — one new entry
   - `<resource>_orm.py` — new ORM (if a new resource type)
   - `alembic/versions/00NN_*.py` — migration
   - `phase2_session.py` — side-effect import added
   - `integrations/<provider>/<service>.py` — adapter
   - `sync_runner.py` — adapter registered in `_ADAPTERS`
   - `routers/v1/me_<service>.py` — API
   - `main.py` — router included
   - `frontend/src/lib/api/<service>Client.ts` — typed client
   - `frontend/src/app/(dashboard)/<service>/page.tsx` — UI

2. **Provider-app config** (only if scopes changed): document any new scope IDs added to Google Cloud Console / Azure App registrations in the PR description.

3. **Verification** (output as a checklist in the PR description):
   - [ ] alembic upgrade head ran cleanly
   - [ ] `/connections/connect/<svc>` redirects through provider consent
   - [ ] Probe script (C.2) returns 200 on the resource endpoint
   - [ ] `sync_credential(cred)` returns SyncResult with inserted > 0 (first run)
   - [ ] Refresh-token rotation works (force `refresh_access_token` and re-sync)
   - [ ] Frontend page renders synced rows
   - [ ] Run a SECOND sync — should be incremental (inserted=0 typically, only changed rows updated)

4. **Memory / docs updates**:
   - If a new edge case arose, append it to **Part D** of this skill.
   - If a provider adds a new account-type quirk, update the scope-choice table in **B.1**.
   - Bump `version` and `last_validated_at` in this file's frontmatter.

## Reference: where this skill lives in the codebase

- Scope registry: `backend/app/services/auth/oauth_scopes.py`
- Connect router: `backend/app/api/routers/v1/connections.py`
- Credential service: `backend/app/services/auth/credential_service.py`
- Encryption: `backend/app/services/auth/encryption.py`
- Sync runner: `backend/app/services/integrations/sync_runner.py`
- Base adapter: `backend/app/services/integrations/base.py`
- Existing reference adapters:
  - `backend/app/services/integrations/google/calendar.py` (syncToken pattern)
  - `backend/app/services/integrations/microsoft/calendar.py` (deltaLink + Prefer header)
  - `backend/app/services/integrations/microsoft/onenote.py` (per-section iteration)

## Change log

- **1.0 (2026-04-28)** — initial. Built from the Google Calendar + Outlook Calendar + OneNote rollout. 15 edge cases captured (★ = the three OneNote-specific ones).
