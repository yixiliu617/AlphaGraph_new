import json
from unittest.mock import patch, MagicMock

import requests

from backend.app.services.calendar.enrichment.url_validator import (
    check_url, validate_url, ValidationResult, log_validation,
)


def _resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def test_head_200_returns_true():
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(200)) as h, \
         patch("backend.app.services.calendar.enrichment.url_validator.requests.get") as g:
        assert validate_url("https://a.com/x") is True
    h.assert_called_once()
    g.assert_not_called()


def test_head_405_falls_back_to_get_with_range():
    """Some CDNs reject HEAD; we fall back to GET with Range: bytes=0-0."""
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(405)), \
         patch("backend.app.services.calendar.enrichment.url_validator.requests.get",
               return_value=_resp(206)) as g:
        assert validate_url("https://a.com/x") is True
    # GET was called with Range header
    args, kwargs = g.call_args
    assert kwargs["headers"].get("Range") == "bytes=0-0"
    assert kwargs.get("stream") is True


def test_head_405_get_200_also_valid():
    """Range-unaware servers respond 200 OK to GET-with-Range."""
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(405)), \
         patch("backend.app.services.calendar.enrichment.url_validator.requests.get",
               return_value=_resp(200)):
        assert validate_url("https://a.com/x") is True


def test_both_fail_returns_false():
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(404)), \
         patch("backend.app.services.calendar.enrichment.url_validator.requests.get",
               return_value=_resp(404)):
        assert validate_url("https://a.com/x") is False


def test_head_timeout_falls_back_to_get():
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               side_effect=requests.exceptions.Timeout()), \
         patch("backend.app.services.calendar.enrichment.url_validator.requests.get",
               return_value=_resp(206)):
        assert validate_url("https://a.com/x") is True


def test_browser_user_agent_used():
    """Confirm the User-Agent looks like Chrome (not a Python identifier)."""
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(200)) as h:
        validate_url("https://a.com/x")
    ua = h.call_args.kwargs["headers"]["User-Agent"]
    assert "Mozilla" in ua and "Chrome" in ua


def test_check_url_head_200_returns_ok_state():
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(200)):
        result = check_url("https://a.com/x")
    assert result.valid is True
    assert result.status_code == 200
    assert result.state == "ok"
    assert result.method == "HEAD"


def test_check_url_403_is_cdn_block_not_rejection():
    """The new policy: CDN-block codes mean URL exists but bot-blocked us.
    Real browsers can probably open it. Accept."""
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(403)):
        result = check_url("https://investor.nvidia.com/q4")
    assert result.valid is True
    assert result.status_code == 403
    assert result.state == "cdn_block"


def test_check_url_404_is_rejected_as_not_found():
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(404)):
        result = check_url("https://a.com/missing")
    assert result.valid is False
    assert result.status_code == 404
    assert result.state == "not_found"


def test_check_url_500_accepted_as_server_error():
    """Server flaky; real users would get retries. Accept the URL anyway."""
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(500)):
        result = check_url("https://a.com/x")
    assert result.valid is True
    assert result.status_code == 500
    assert result.state == "server_error"


def test_check_url_405_falls_through_to_get_range():
    """405 is non-decisive on HEAD; falls through to GET. GET 206 -> ok_via_get."""
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               return_value=_resp(405)), \
         patch("backend.app.services.calendar.enrichment.url_validator.requests.get",
               return_value=_resp(206)):
        result = check_url("https://a.com/x")
    assert result.valid is True
    assert result.status_code == 206
    assert result.state == "ok"
    assert result.method == "GET_RANGE"


def test_check_url_connection_error_is_rejected():
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               side_effect=requests.exceptions.ConnectionError()):
        result = check_url("https://a.com/x")
    assert result.valid is False
    assert result.status_code is None
    assert result.state == "connection_failure"


def test_check_url_ssl_error_is_rejected():
    with patch("backend.app.services.calendar.enrichment.url_validator.requests.head",
               side_effect=requests.exceptions.SSLError()):
        result = check_url("https://a.com/x")
    assert result.valid is False
    assert result.state == "ssl_failure"


def test_validation_result_is_truthy():
    """ValidationResult should work in boolean contexts."""
    assert bool(ValidationResult(True, 200, "ok", "HEAD"))
    assert not bool(ValidationResult(False, 404, "not_found", "HEAD"))


def test_log_validation_appends_jsonl(tmp_path, monkeypatch):
    """log_validation writes a JSONL record to the configured path."""
    log_path = tmp_path / "test_log.jsonl"
    monkeypatch.setattr(
        "backend.app.services.calendar.enrichment.url_validator._VALIDATION_LOG",
        log_path,
    )
    log_validation(
        ValidationResult(True, 403, "cdn_block", "HEAD"),
        url="https://investor.nvidia.com/q4",
        ticker="NVDA",
        fiscal_period="FY2026-Q4",
        layer="a",
    )
    log_validation(
        ValidationResult(False, 404, "not_found", "HEAD"),
        url="https://example.com/missing",
        ticker="XYZ",
        fiscal_period="FY2026-Q1",
        layer="a",
    )
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["url"]   == "https://investor.nvidia.com/q4"
    assert rec0["state"] == "cdn_block"
    assert rec0["valid"] is True
    assert rec0["status_code"] == 403
    assert rec0["ticker"] == "NVDA"
    rec1 = json.loads(lines[1])
    assert rec1["valid"] is False
    assert rec1["state"] == "not_found"
