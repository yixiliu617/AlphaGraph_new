"""
OAuth scope registry — what each integrated service requires.

`SERVICES` maps a service-id ("google.calendar", "microsoft.outlook_mail",
etc.) to:
  - provider     : "google" | "microsoft"
  - display_name : shown in the connect button + the "Connected
                   accounts" page
  - scopes       : list of OAuth scopes to request
  - sync_minutes : recommended cron interval for the sync worker

Adding a new service = one entry here + one adapter in
`backend/app/services/integrations/<provider>/<service>.py`. The connect
flow router and the sync runner are both data-driven from this map, so
no router-side or scheduler-side change is needed when introducing a
new service.

The scopes here are the MINIMUM needed for the listed feature. We
follow the principle of least privilege — don't request `calendar`
(read+write) when `calendar.readonly` does the job. Users see exactly
what they're granting on Google's / Microsoft's consent screen, so
unnecessary scopes hurt conversion.
"""
from __future__ import annotations

from typing import TypedDict


class ServiceSpec(TypedDict):
    provider:     str
    display_name: str
    scopes:       list[str]
    sync_minutes: int


SERVICES: dict[str, ServiceSpec] = {
    # ---- Google -----------------------------------------------------
    "google.calendar": {
        "provider":     "google",
        "display_name": "Google Calendar",
        "scopes": [
            "openid", "email", "profile",
            "https://www.googleapis.com/auth/calendar.readonly",
        ],
        "sync_minutes": 30,
    },
    "google.gmail": {
        "provider":     "google",
        "display_name": "Gmail",
        # NOTE: gmail.readonly is a RESTRICTED scope — Google CASA
        # security audit required before publishing publicly.
        # Don't ship this to end users until the audit is done.
        "scopes": [
            "openid", "email", "profile",
            "https://www.googleapis.com/auth/gmail.readonly",
        ],
        "sync_minutes": 30,
    },
    "google.docs": {
        "provider":     "google",
        "display_name": "Google Docs",
        # documents.readonly = read content of a specific Doc by ID.
        # drive.metadata.readonly = list/search Docs files (Drive holds
        # the file metadata; Docs API holds the content). Together these
        # let us discover the user's Docs and ingest their text.
        # Both are SENSITIVE scopes — Google verification needed before
        # public launch (no CASA audit, just app review). For dev they
        # work in Testing mode for up to 100 test users.
        "scopes": [
            "openid", "email", "profile",
            "https://www.googleapis.com/auth/documents.readonly",
            "https://www.googleapis.com/auth/drive.metadata.readonly",
        ],
        "sync_minutes": 240,  # Research docs change less than mail/calendar
    },

    # ---- Microsoft (Graph API) --------------------------------------
    # Graph API uses ".default" or specific scopes from Microsoft Graph.
    # Per Microsoft, the "/.default" suffix means "all statically
    # configured scopes from the App Registration". We list explicit
    # scopes here for clarity (and to match what the consent screen
    # shows the user).
    # Microsoft scopes intentionally OMIT openid/email/profile. Including
    # them triggers Microsoft to issue an ID token, which Authlib then
    # tries to validate against the OIDC discovery doc — and Microsoft's
    # `common` tenant returns a discovery doc with a placeholder `iss`
    # that won't match the real tenant-GUID-based `iss` in the token.
    # We use `User.Read` + Graph /me for user identity instead, which
    # works the same across all tenants without any OIDC dance.
    # `offline_access` is still required for Microsoft to issue a refresh
    # token (different from Google, where access_type=offline does it).
    "microsoft.calendar": {
        "provider":     "microsoft",
        "display_name": "Outlook Calendar",
        "scopes": [
            "offline_access", "User.Read",
            "Calendars.Read",
        ],
        "sync_minutes": 30,
    },
    "microsoft.outlook_mail": {
        "provider":     "microsoft",
        "display_name": "Outlook Mail",
        "scopes": [
            "offline_access", "User.Read",
            "Mail.Read",
        ],
        "sync_minutes": 60,
    },
    "microsoft.onenote": {
        "provider":     "microsoft",
        "display_name": "OneNote",
        # Personal-account quirk: Notes.Read.All ("all notebooks the user
        # can access") sometimes returns 401 from the OneNote API for
        # outlook.com / live.com accounts even when the consent says
        # granted. Notes.Read ("just your own notebooks") is more
        # reliable across both org and personal accounts. Upgrade to
        # Notes.Read.All only when shared-notebook access is essential.
        "scopes": [
            "offline_access", "User.Read",
            "Notes.Read",
        ],
        "sync_minutes": 240,
    },
    "microsoft.onedrive": {
        "provider":     "microsoft",
        "display_name": "OneDrive",
        "scopes": [
            "offline_access", "User.Read",
            "Files.Read.All",
        ],
        "sync_minutes": 240,
    },
}


def get_service(service_id: str) -> ServiceSpec:
    spec = SERVICES.get(service_id)
    if spec is None:
        valid = ", ".join(sorted(SERVICES.keys()))
        raise KeyError(f"unknown service '{service_id}'. valid: {valid}")
    return spec


def list_services_for_provider(provider: str) -> list[tuple[str, ServiceSpec]]:
    return sorted(
        ((sid, spec) for sid, spec in SERVICES.items() if spec["provider"] == provider),
        key=lambda x: x[0],
    )
