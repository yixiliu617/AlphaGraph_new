"""
Unit tests for the MOPS HTTP client. Network is mocked — no real MOPS calls.
"""
import time
from unittest.mock import patch, MagicMock

import pytest

from backend.app.services.taiwan.mops_client import MopsClient, MopsFetchResult


def _fake_response(status_code=200, text="<html>ok</html>", encoding="utf-8"):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    r.content = text.encode(encoding)
    r.encoding = encoding
    r.headers = {"Content-Type": "text/html; charset=utf-8"}
    return r


def test_rate_limit_spaces_requests():
    """Two quick calls must be spaced by at least `min_interval`."""
    client = MopsClient(min_interval_seconds=0.25, max_retries=0, timeout=5)
    with patch.object(client._session, "post", return_value=_fake_response()):
        t0 = time.perf_counter()
        client.post("https://mops/x", data={})
        client.post("https://mops/x", data={})
        elapsed = time.perf_counter() - t0
    assert elapsed >= 0.25, f"Two calls completed in {elapsed:.3f}s, expected >=0.25s"


def test_retry_on_429_then_succeeds():
    """One 429 followed by a 200 must return the 200."""
    client = MopsClient(min_interval_seconds=0.0, max_retries=2, backoff_base=0.01, timeout=5)
    responses = [_fake_response(status_code=429), _fake_response(status_code=200, text="<html>final</html>")]
    with patch.object(client._session, "post", side_effect=responses) as mock_post:
        result = client.post("https://mops/x", data={})
    assert isinstance(result, MopsFetchResult)
    assert result.status_code == 200
    assert "final" in result.text
    assert mock_post.call_count == 2


def test_retry_gives_up_after_max():
    """N+1 failures (all 503) yields MopsFetchResult with status 503 and used_browser=False."""
    client = MopsClient(min_interval_seconds=0.0, max_retries=2, backoff_base=0.01, timeout=5)
    with patch.object(client._session, "post", return_value=_fake_response(status_code=503)) as mock_post:
        result = client.post("https://mops/x", data={})
    assert result.status_code == 503
    assert mock_post.call_count == 3  # initial + 2 retries
    assert result.used_browser is False


def test_big5_fallback_when_utf8_garbled():
    """If utf-8 decode fails on bytes, fall back to big5."""
    client = MopsClient(min_interval_seconds=0.0, max_retries=0, timeout=5)
    # Big5-encoded '公司' (company) bytes
    big5_bytes = "公司".encode("big5")
    r = MagicMock()
    r.status_code = 200
    r.content = big5_bytes
    r.encoding = "utf-8"  # wrong declaration
    r.headers = {"Content-Type": "text/html"}
    r.text = big5_bytes.decode("latin-1")  # requests' fallback is latin-1 ≠ utf-8
    with patch.object(client._session, "post", return_value=r):
        result = client.post("https://mops/x", data={})
    assert "公司" in result.text


def test_playwright_fallback_on_403(monkeypatch):
    """HTTP 403 triggers the Playwright fallback (mocked)."""
    client = MopsClient(min_interval_seconds=0.0, max_retries=0, timeout=5)

    called = {"browser_fetched": False}

    def fake_browser_fetch(url, method, data):
        called["browser_fetched"] = True
        return MopsFetchResult(status_code=200, text="<html>browser-ok</html>", used_browser=True)

    monkeypatch.setattr(client, "_browser_fetch", fake_browser_fetch)

    with patch.object(client._session, "post", return_value=_fake_response(status_code=403)):
        result = client.post("https://mops/x", data={}, allow_browser_fallback=True)

    assert called["browser_fetched"]
    assert result.used_browser is True
    assert "browser-ok" in result.text


def test_browser_fallback_disabled_by_default_on_nonzero_but_non_403():
    """A 500 error does NOT trigger the browser fallback — only 403 / CAPTCHA markers do."""
    client = MopsClient(min_interval_seconds=0.0, max_retries=0, timeout=5)
    called = {"browser_fetched": False}

    def fake_browser_fetch(url, method, data):
        called["browser_fetched"] = True
        return MopsFetchResult(status_code=200, text="browser", used_browser=True)

    with patch.object(client, "_browser_fetch", fake_browser_fetch), \
         patch.object(client._session, "post", return_value=_fake_response(status_code=500)):
        result = client.post("https://mops/x", data={}, allow_browser_fallback=True)

    assert not called["browser_fetched"]
    assert result.status_code == 500
