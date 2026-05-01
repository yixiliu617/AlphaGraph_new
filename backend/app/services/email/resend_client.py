"""
Thin Resend SDK wrapper. Two failure modes handled:
  - Missing RESEND_API_KEY -> log the email; useful in dev / tests.
  - Resend API error -> propagate as RuntimeError; caller decides retry.
"""
from __future__ import annotations
import logging
from typing import Optional
from backend.app.core.config import settings

logger = logging.getLogger(__name__)


class EmailNotConfiguredError(RuntimeError):
    pass


def _api_key() -> Optional[str]:
    """Indirection so tests can monkeypatch."""
    return settings.RESEND_API_KEY


def _resend_module():
    """Lazy import so dev environments without resend installed don't crash."""
    import resend
    return resend


def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    bcc: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> dict:
    """Send an email via Resend. If RESEND_API_KEY is unset, log the
    payload and return status='logged_not_sent' (useful in dev)."""
    if not _api_key():
        logger.info(
            "[EMAIL not sent -- RESEND_API_KEY missing] to=%s subject=%r html_len=%d",
            to, subject, len(html),
        )
        return {"status": "logged_not_sent"}

    resend = _resend_module()
    resend.api_key = _api_key()

    payload = {
        "from": settings.EMAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if bcc or settings.ADMIN_EMAIL_BCC:
        payload["bcc"] = [bcc or settings.ADMIN_EMAIL_BCC]
    if reply_to:
        payload["reply_to"] = reply_to

    response = resend.Emails.send(payload)
    return {"status": "sent", "id": response.get("id")}
