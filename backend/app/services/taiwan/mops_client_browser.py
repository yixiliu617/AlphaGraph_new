"""CDP Chrome launcher for the Taiwan MOPS scraper.

The Playwright MopsClient expects a Chrome instance running in CDP mode
against a persistent user profile. This helper starts that Chrome on
demand and health-checks the debug port before returning.

Profile dir, CDP port, and Chrome binary are overridable via env vars so
a Docker container can point at a sibling Chromium install.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

import requests as _requests  # used only to probe the debug port

logger = logging.getLogger(__name__)

_DEFAULT_PORT = int(os.environ.get("ALPHAGRAPH_SCRAPER_CDP_PORT", "9222"))
_DEFAULT_PROFILE = Path(
    os.environ.get(
        "ALPHAGRAPH_SCRAPER_PROFILE",
        str(Path.home() / ".alphagraph_scraper_profile"),
    )
)
_DEFAULT_CHROME = os.environ.get(
    "ALPHAGRAPH_SCRAPER_CHROME",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if os.name == "nt"
    else "/usr/bin/google-chrome",
)


def cdp_is_running(port: int = _DEFAULT_PORT) -> bool:
    try:
        r = _requests.get(f"http://localhost:{port}/json/version", timeout=1)
        return r.status_code == 200
    except _requests.RequestException:
        return False


def ensure_cdp_running(
    *,
    port: int = _DEFAULT_PORT,
    profile_dir: Path = _DEFAULT_PROFILE,
    chrome: str = _DEFAULT_CHROME,
    startup_timeout: float = 15.0,
) -> None:
    """Launch CDP Chrome if it isn't already answering on `port`.

    Idempotent — safe to call before every scraper tick.
    """
    if cdp_is_running(port):
        return

    profile_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Launching CDP Chrome port=%d profile=%s", port, profile_dir)
    subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        if cdp_is_running(port):
            return
        time.sleep(0.3)

    raise RuntimeError(
        f"CDP Chrome did not come up on :{port} within {startup_timeout}s "
        f"(profile={profile_dir}, chrome={chrome})"
    )
