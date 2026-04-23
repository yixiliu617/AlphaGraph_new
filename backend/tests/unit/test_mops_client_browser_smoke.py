"""Playwright fallback availability smoke test. Skipped gracefully if the
machine has no Chrome / no Playwright installed. This is NOT a real MOPS call;
it only verifies our code can reach the CDP port or detect that it can't."""

import pytest


def test_cdp_detection_function_callable():
    from backend.app.services.taiwan.mops_client_browser import _cdp_running
    assert isinstance(_cdp_running(), bool)


def test_browser_fetch_raises_cleanly_if_chrome_missing(monkeypatch):
    from backend.app.services.taiwan import mops_client_browser as mcb

    monkeypatch.setattr(mcb, "_CDP_CHROME", "/nonexistent/chrome-binary-xyz")
    monkeypatch.setattr(mcb, "_cdp_running", lambda: False)

    with pytest.raises((RuntimeError, FileNotFoundError, OSError)):
        mcb.browser_fetch("https://mops.twse.com.tw/", method="GET", data=None, timeout=2.0)
