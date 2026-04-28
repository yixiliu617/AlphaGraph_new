from unittest.mock import patch, MagicMock

import requests

from backend.app.services.calendar.enrichment.url_validator import validate_url


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
