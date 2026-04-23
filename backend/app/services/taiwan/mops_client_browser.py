"""Playwright fallback for MOPS.

Uses the existing CDP Chrome profile from the project's `web-scraping` skill
(~/.alphagraph_scraper_profile), connecting over a short-lived browser if
it is not already running. Shared across scrapers.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests as _requests  # used only for port check

from backend.app.services.taiwan.mops_client import MopsFetchResult

logger = logging.getLogger(__name__)

_CDP_PORT = int(os.environ.get("ALPHAGRAPH_SCRAPER_CDP_PORT", "9222"))
_CDP_PROFILE = Path(
    os.environ.get("ALPHAGRAPH_SCRAPER_PROFILE", str(Path.home() / ".alphagraph_scraper_profile"))
)
_CDP_CHROME = os.environ.get(
    "ALPHAGRAPH_SCRAPER_CHROME",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe" if os.name == "nt" else "/usr/bin/google-chrome",
)


def _cdp_running() -> bool:
    try:
        r = _requests.get(f"http://localhost:{_CDP_PORT}/json/version", timeout=1)
        return r.status_code == 200
    except _requests.RequestException:
        return False


def _start_cdp_chrome() -> None:
    if _cdp_running():
        return
    _CDP_PROFILE.mkdir(parents=True, exist_ok=True)
    logger.info("Launching CDP Chrome at port=%d profile=%s", _CDP_PORT, _CDP_PROFILE)
    subprocess.Popen(
        [
            _CDP_CHROME,
            f"--remote-debugging-port={_CDP_PORT}",
            f"--user-data-dir={_CDP_PROFILE}",
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait up to 10s for the debug port to come up.
    deadline = time.time() + 10
    while time.time() < deadline:
        if _cdp_running():
            return
        time.sleep(0.3)
    raise RuntimeError(f"CDP Chrome did not come up on port {_CDP_PORT} within 10s")


def browser_fetch(url: str, *, method: str, data: Optional[dict], timeout: float) -> MopsFetchResult:
    from playwright.sync_api import sync_playwright

    _start_cdp_chrome()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{_CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            if method == "POST":
                # Playwright doesn't have a direct POST-and-load, so we use fetch()
                # inside the page.
                page.goto("about:blank")
                js = (
                    "async (args) => {"
                    "  const body = new URLSearchParams(args.data).toString();"
                    "  const r = await fetch(args.url, {method:'POST', body, headers:{'Content-Type':'application/x-www-form-urlencoded'}});"
                    "  return {status: r.status, text: await r.text()};"
                    "}"
                )
                result = page.evaluate(js, {"url": url, "data": data or {}})
                return MopsFetchResult(
                    status_code=int(result["status"]),
                    text=result["text"],
                    used_browser=True,
                )
            else:
                resp = page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
                text = page.content()
                status = resp.status if resp else 200
                return MopsFetchResult(status_code=status, text=text, used_browser=True)
        finally:
            page.close()
