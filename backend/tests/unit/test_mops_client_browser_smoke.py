"""CDP launcher smoke tests — verify the helper module is importable and
its public functions behave when CDP Chrome isn't reachable. No real
browser is launched."""

from __future__ import annotations

import pytest


def test_cdp_running_returns_bool():
    from backend.app.services.taiwan.mops_client_browser import cdp_is_running
    assert isinstance(cdp_is_running(port=59999), bool)  # port unlikely bound


def test_ensure_cdp_running_raises_when_chrome_missing(monkeypatch, tmp_path):
    """If the Chrome binary path is invalid and no CDP is already up,
    ensure_cdp_running must raise (not silently hang)."""
    from backend.app.services.taiwan import mops_client_browser as mcb

    # Force the "no CDP reachable" branch; point at a non-existent binary.
    monkeypatch.setattr(mcb, "cdp_is_running", lambda port=None: False)

    with pytest.raises((RuntimeError, FileNotFoundError, OSError)):
        mcb.ensure_cdp_running(
            port=59998,
            profile_dir=tmp_path,
            chrome="/nonexistent/chrome-xyz",
            startup_timeout=1.0,
        )
