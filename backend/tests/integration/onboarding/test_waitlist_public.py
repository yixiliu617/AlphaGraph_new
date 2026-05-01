from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_post_public_waitlist_creates_pending_entry():
    payload = {
        "email": "newperson@example.com",
        "full_name": "New Person",
        "self_reported_role": "Buyside Analyst",
        "self_reported_firm": "Example Capital",
        "note": "Coverage AI infra; want trustworthy fundamentals.",
    }
    r = client.post("/api/v1/public/waitlist", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "pending"
    assert data["email"] == "newperson@example.com"


def test_post_public_waitlist_idempotent_on_duplicate_email():
    """Submitting same email twice returns 200 (not error) with existing status."""
    payload = {"email": "dup@example.com", "full_name": "Dup"}
    r1 = client.post("/api/v1/public/waitlist", json=payload)
    r2 = client.post("/api/v1/public/waitlist", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 200  # already exists
    assert r2.json()["status"] == "pending"


def test_post_public_waitlist_rejects_invalid_email():
    r = client.post("/api/v1/public/waitlist", json={"email": "not-an-email"})
    assert r.status_code == 422
