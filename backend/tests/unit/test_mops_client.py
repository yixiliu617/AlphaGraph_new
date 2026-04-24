"""
Unit tests for the Playwright-based MopsClient.

We avoid touching real Playwright by short-circuiting `open()` and stubbing
`_ctx.request.post` / `_warm_origin`. The behaviours under test:
  - rate limiting spaces requests by at least `min_interval_seconds`
  - retries on retry-able status codes (429, 500, 502, 503, 504, 0)
  - retry budget is bounded (max_retries+1 total attempts)
  - HTML-on-200 (the MOPS WAF bounce page) triggers a retry
  - `_looks_like_json` correctly distinguishes JSON from HTML prefixes
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from backend.app.services.taiwan.mops_client import (
    MopsClient,
    MopsFetchResult,
    _looks_like_json,
)


def _fake_resp(status: int = 200, text: str = '{"code":200}'):
    r = MagicMock()
    r.status = status
    r.text.return_value = text
    return r


def _mk_client(**kwargs) -> MopsClient:
    """Return a client with Playwright lifecycle disabled for unit tests."""
    c = MopsClient(
        min_interval_seconds=kwargs.pop("min_interval_seconds", 0.0),
        max_retries=kwargs.pop("max_retries", 0),
        backoff_base=kwargs.pop("backoff_base", 0.01),
        timeout=kwargs.pop("timeout", 5.0),
    )
    # Bypass real browser: force `_ctx` and skip origin warming.
    c._ctx = MagicMock()
    c._warmed = True
    return c


def test_looks_like_json_prefix_gate():
    assert _looks_like_json('{"a": 1}')
    assert _looks_like_json('   [1,2,3]')
    assert not _looks_like_json("<html>error</html>")
    assert not _looks_like_json("")
    assert not _looks_like_json(None)  # type: ignore[arg-type]


def test_rate_limit_spaces_requests():
    c = _mk_client(min_interval_seconds=0.25)
    c._ctx.request.post.return_value = _fake_resp()
    t0 = time.perf_counter()
    c.post_json("/mops/api/x", {})
    c.post_json("/mops/api/x", {})
    elapsed = time.perf_counter() - t0
    assert elapsed >= 0.24, f"two calls completed in {elapsed:.3f}s — rate limit not honoured"


def test_retry_on_429_then_succeeds():
    c = _mk_client(max_retries=2)
    c._ctx.request.post.side_effect = [
        _fake_resp(status=429, text=""),
        _fake_resp(status=200, text='{"code":200,"result":{}}'),
    ]
    res = c.post_json("/mops/api/x", {})
    assert res.status_code == 200
    assert c._ctx.request.post.call_count == 2


def test_retry_gives_up_after_max():
    c = _mk_client(max_retries=2)
    c._ctx.request.post.return_value = _fake_resp(status=503, text="")
    res = c.post_json("/mops/api/x", {})
    assert res.status_code == 503
    assert c._ctx.request.post.call_count == 3  # 1 initial + 2 retries


def test_html_body_on_200_retries():
    """MOPS WAF sometimes returns 200 with an HTML bounce page. We must retry."""
    c = _mk_client(max_retries=1)
    c._ctx.request.post.side_effect = [
        _fake_resp(status=200, text="<html>FOR SECURITY REASONS</html>"),
        _fake_resp(status=200, text='{"code":200,"result":{}}'),
    ]
    res = c.post_json("/mops/api/x", {}, expect_json=True)
    assert res.status_code == 200
    assert res.text.startswith("{")
    assert c._ctx.request.post.call_count == 2


def test_json_parsing_convenience():
    c = _mk_client()
    c._ctx.request.post.return_value = _fake_resp(
        status=200, text='{"code":200,"result":{"data":[["115","3","1,000"]]}}',
    )
    res = c.post_json("/mops/api/x", {"company_id": "2330"})
    body = res.json()
    assert body["code"] == 200
    assert body["result"]["data"][0] == ["115", "3", "1,000"]


def test_raw_bytes_preserved_for_audit():
    c = _mk_client()
    c._ctx.request.post.return_value = _fake_resp(status=200, text='{"code":200}')
    res = c.post_json("/mops/api/x", {})
    assert res.raw_bytes == b'{"code":200}'
    assert res.encoding == "utf-8"


def test_exception_retries_then_fails():
    c = _mk_client(max_retries=1)
    c._ctx.request.post.side_effect = RuntimeError("net down")
    res = c.post_json("/mops/api/x", {})
    assert res.status_code == 0
    assert "net down" in res.text
    assert c._ctx.request.post.call_count == 2
