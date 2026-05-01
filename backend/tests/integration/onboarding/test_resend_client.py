from unittest.mock import patch, MagicMock
from backend.app.services.email.resend_client import send_email, EmailNotConfiguredError


def test_send_email_logs_when_no_api_key(caplog, monkeypatch):
    monkeypatch.setattr("backend.app.services.email.resend_client._api_key", lambda: None)
    import logging
    caplog.set_level(logging.INFO)
    result = send_email(
        to="recipient@example.com",
        subject="Hi",
        html="<p>Hello</p>",
    )
    assert result["status"] == "logged_not_sent"
    assert any("recipient@example.com" in r.message for r in caplog.records)


def test_send_email_calls_resend_when_configured(monkeypatch):
    fake_resend = MagicMock()
    fake_resend.Emails.send.return_value = {"id": "email-id-123"}
    monkeypatch.setattr("backend.app.services.email.resend_client._api_key", lambda: "re_test")
    monkeypatch.setattr("backend.app.services.email.resend_client._resend_module", lambda: fake_resend)
    result = send_email(
        to="recipient@example.com",
        subject="Hi",
        html="<p>Hello</p>",
        bcc="admin@example.com",
    )
    assert result["status"] == "sent"
    assert result["id"] == "email-id-123"
    fake_resend.Emails.send.assert_called_once()
    payload = fake_resend.Emails.send.call_args[0][0]
    assert payload["to"] == ["recipient@example.com"]
    assert payload["bcc"] == ["admin@example.com"]
    assert "Hello" in payload["html"]
