"""
Integration tests for the ux_variant column on MeetingNote.
Verifies default value, explicit set, round-trip through ORM/domain.
"""

import pytest

from backend.app.services.notes_service import NotesService
# Ensure note ORM is registered for create_all in the sqlite_session fixture
import backend.app.models.orm.note_orm  # noqa: F401


TENANT = "Institutional_L1"


def test_create_note_defaults_to_variant_a(sqlite_session):
    svc = NotesService(sqlite_session)
    note = svc.create_note(
        tenant_id=TENANT,
        title="Default variant test",
        note_type="internal",
        company_tickers=["NVDA"],
    )
    assert note.ux_variant == "A"


def test_create_note_explicit_variant_b(sqlite_session):
    svc = NotesService(sqlite_session)
    note = svc.create_note(
        tenant_id=TENANT,
        title="B variant test",
        note_type="internal",
        company_tickers=["NVDA"],
        ux_variant="B",
    )
    assert note.ux_variant == "B"


def test_variant_round_trips_via_get(sqlite_session):
    svc = NotesService(sqlite_session)
    created = svc.create_note(
        tenant_id=TENANT,
        title="Round trip",
        note_type="internal",
        company_tickers=["NVDA"],
        ux_variant="B",
    )
    fetched = svc.get_note(created.note_id, TENANT)
    assert fetched is not None
    assert fetched.ux_variant == "B"
