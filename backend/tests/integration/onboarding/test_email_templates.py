from backend.app.services.email.templates.waitlist_received import render_waitlist_received
from backend.app.services.email.templates.waitlist_approved import render_waitlist_approved
from backend.app.services.email.templates.waitlist_referral_invite import render_waitlist_referral_invite
from backend.app.services.email.templates.admin_new_waitlist_signup import render_admin_new_waitlist_signup


def test_waitlist_received():
    out = render_waitlist_received(full_name="Alice")
    assert "Alice" in out["html"]
    assert "thank" in out["subject"].lower() or "received" in out["subject"].lower()


def test_waitlist_approved():
    out = render_waitlist_approved(full_name="Bob", signin_url="https://alphagraph.com/signin")
    assert "Bob" in out["html"]
    assert "https://alphagraph.com/signin" in out["html"]


def test_waitlist_referral_invite():
    out = render_waitlist_referral_invite(
        invitee_name="Carol",
        inviter_name="Alice",
        inviter_message="Thought you'd like this.",
        signin_url="https://alphagraph.com/signin",
    )
    assert "Alice" in out["html"]
    assert "Thought you'd like this." in out["html"]


def test_admin_new_waitlist_signup():
    out = render_admin_new_waitlist_signup(
        applicant_email="newuser@example.com",
        applicant_name="Dan",
        role="Buyside Analyst",
        firm="Acme Capital",
    )
    assert "newuser@example.com" in out["html"]
    assert "Dan" in out["html"]
    assert "/admin/waitlist" in out["html"]
