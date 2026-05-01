"""
Public waitlist endpoint -- no auth required. Anyone can apply for access.
Idempotent on email: re-submitting an existing email returns the existing status.
"""
from __future__ import annotations
import logging
from typing import Optional
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from backend.app.db.phase2_session import get_phase2_session
from backend.app.models.orm.waitlist_orm import WaitlistEntry
from backend.app.services.email.resend_client import send_email
from backend.app.services.email.templates.waitlist_received import render_waitlist_received
from backend.app.services.email.templates.admin_new_waitlist_signup import render_admin_new_waitlist_signup

router = APIRouter()
logger = logging.getLogger(__name__)


class WaitlistApplyIn(BaseModel):
    email:               EmailStr
    full_name:           Optional[str] = None
    self_reported_role:  Optional[str] = None
    self_reported_firm:  Optional[str] = None
    note:                Optional[str] = None
    referrer:            Optional[str] = None


class WaitlistApplyOut(BaseModel):
    email: str
    status: str


@router.post("", response_model=WaitlistApplyOut, status_code=201)
def apply_to_waitlist(
    payload: WaitlistApplyIn,
    response: Response,
    db: Session = Depends(get_phase2_session),
):
    existing = (
        db.query(WaitlistEntry)
          .filter(WaitlistEntry.email == payload.email)
          .first()
    )
    if existing:
        # Idempotent: don't duplicate-create or error.
        response.status_code = 200
        return WaitlistApplyOut(email=existing.email, status=existing.status)

    entry = WaitlistEntry(
        email              = payload.email,
        full_name          = payload.full_name,
        self_reported_role = payload.self_reported_role,
        self_reported_firm = payload.self_reported_firm,
        note               = payload.note,
        referrer           = payload.referrer,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    # Send confirmation to applicant + notify Sharon. Best-effort; don't fail
    # the request if email delivery has an issue (waitlist write is the source of truth).
    try:
        confirmation = render_waitlist_received(full_name=payload.full_name)
        send_email(to=payload.email, subject=confirmation["subject"], html=confirmation["html"])
        admin = render_admin_new_waitlist_signup(
            applicant_email=payload.email,
            applicant_name=payload.full_name,
            role=payload.self_reported_role,
            firm=payload.self_reported_firm,
        )
        from backend.app.core.config import settings
        if settings.ADMIN_EMAIL_BCC:
            send_email(to=settings.ADMIN_EMAIL_BCC, subject=admin["subject"], html=admin["html"])
    except Exception as e:  # noqa: BLE001
        logger.warning("waitlist email send failed (non-fatal): %s", e)

    return WaitlistApplyOut(email=entry.email, status=entry.status)
