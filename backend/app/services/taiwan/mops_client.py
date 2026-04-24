"""
MopsClient — Playwright-based JSON client for the redesigned MOPS portal.

The 2024 MOPS redesign turned the site into a Vite SPA and added an
aggressive WAF that blocks any non-browser HTTP client regardless of
headers. The only working path is Playwright attached to a persistent
CDP Chrome profile, then using the browser context's `.request.post()`
to issue JSON calls that inherit the context's cookies and TLS fingerprint.

See `.claude/skills/taiwan-monthly-data-extraction/SKILL.md` for the
full design rationale, endpoint catalog, and corner cases.

Public API:
  with MopsClient() as client:
      body = client.post_json("/mops/api/t146sb05_detail", {"company_id": "2330"})
      # body is the parsed JSON dict (result["data"] has the rows).

The client is stateful and NOT thread-safe — construct one per scheduler
tick and iterate tickers sequentially inside it. It keeps one browser
context + page warm for the lifetime of the instance so every call reuses
the WAF-cleared session.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ORIGIN = "https://mops.twse.com.tw"
_WARM_URL = f"{_ORIGIN}/mops/#/"


@dataclass
class MopsFetchResult:
    """Normalised response from a MOPS JSON call.

    Preserved across the HTML-era and Playwright-era clients so downstream
    storage/scraper code does not need to change. For JSON calls:
      - text       = the raw JSON body as a string
      - raw_bytes  = the same body as UTF-8 bytes (for raw-capture audit)
      - used_browser = always True now (legacy field)
      - encoding   = "utf-8" (MOPS JSON is always UTF-8)
    """

    status_code: int
    text: str
    used_browser: bool = True
    raw_bytes: Optional[bytes] = None
    encoding: Optional[str] = "utf-8"

    def json(self) -> dict:
        """Parse self.text as JSON. Raises ValueError on malformed bodies."""
        return json.loads(self.text)


class MopsClient:
    """Browser-context JSON client for MOPS.

    Usage:
        with MopsClient() as c:
            body = c.post_json("/mops/api/t146sb05_detail", {"company_id": "2330"})

    The context manager form is preferred — it guarantees the page and
    browser context get released. The class also exposes explicit
    `open()` / `close()` for long-lived scheduler-jobs that want tighter
    control.
    """

    def __init__(
        self,
        *,
        min_interval_seconds: float = 1.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        timeout: float = 30.0,
        cdp_port: int = 9222,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self.cdp_port = cdp_port

        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._warmed = False
        self._last_request_time: float = 0.0
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> "MopsClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        """Start CDP Chrome if needed and connect Playwright to it."""
        if self._ctx is not None:
            return
        from backend.app.services.taiwan.mops_client_browser import ensure_cdp_running
        from playwright.sync_api import sync_playwright

        ensure_cdp_running(port=self.cdp_port)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(
            f"http://localhost:{self.cdp_port}"
        )
        self._ctx = (
            self._browser.contexts[0]
            if self._browser.contexts
            else self._browser.new_context()
        )
        self._page = self._ctx.new_page()

    def close(self) -> None:
        try:
            if self._page:
                self._page.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._page = None
        self._ctx = None
        self._browser = None
        self._pw = None
        self._warmed = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def post_json(
        self,
        path_or_url: str,
        payload: dict,
        *,
        expect_json: bool = True,
    ) -> MopsFetchResult:
        """POST a JSON payload to a MOPS endpoint. Returns the MopsFetchResult.

        ``path_or_url`` may be a path starting with '/' or a full URL on
        the mops.twse.com.tw origin.
        """
        url = path_or_url if path_or_url.startswith("http") else f"{_ORIGIN}{path_or_url}"
        return self._call("POST", url, data=payload, expect_json=expect_json)

    def get_json(
        self,
        path_or_url: str,
        params: Optional[dict] = None,
        *,
        expect_json: bool = True,
    ) -> MopsFetchResult:
        url = path_or_url if path_or_url.startswith("http") else f"{_ORIGIN}{path_or_url}"
        return self._call("GET", url, params=params, expect_json=expect_json)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _warm_origin(self) -> None:
        """Navigate once to prime cookies + any WAF state. Idempotent.

        Safe to call with no page attached (tests): it becomes a no-op.
        """
        if self._warmed:
            return
        if self._page is None:
            self._warmed = True  # nothing to warm; mark done so we don't loop
            return
        self._page.goto(_WARM_URL, wait_until="domcontentloaded", timeout=int(self.timeout * 1000))
        time.sleep(0.5)
        self._warmed = True

    def _sleep_until_min_interval(self) -> None:
        with self._lock:
            delta = time.perf_counter() - self._last_request_time
            wait = self.min_interval_seconds - delta
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.perf_counter()

    def _backoff(self, attempt: int) -> None:
        delay = (self.backoff_base ** attempt) + random.uniform(0, 1)
        time.sleep(delay)

    def _should_retry(self, status_code: int) -> bool:
        return status_code in (0, 429, 500, 502, 503, 504)

    def _call(
        self,
        method: str,
        url: str,
        *,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        expect_json: bool = True,
    ) -> MopsFetchResult:
        if self._ctx is None:
            self.open()
        self._warm_origin()

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": _ORIGIN,
            "Referer": f"{_ORIGIN}/mops/",
        }

        last_status = 0
        last_text = ""
        for attempt in range(self.max_retries + 1):
            self._sleep_until_min_interval()
            try:
                if method == "POST":
                    resp = self._ctx.request.post(
                        url, data=data, headers=headers, timeout=self.timeout * 1000
                    )
                else:
                    resp = self._ctx.request.get(
                        url, params=params, headers=headers, timeout=self.timeout * 1000
                    )
            except Exception as exc:
                logger.warning("MOPS request error attempt=%d url=%s err=%s", attempt, url, exc)
                last_status = 0
                last_text = str(exc)
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                break

            text = resp.text()
            last_status = resp.status
            last_text = text

            if 200 <= resp.status < 300:
                # Validate: if the response is the WAF bounce page it'll be
                # HTML even though status was 200. Catch that early.
                if expect_json and not _looks_like_json(text):
                    logger.warning(
                        "MOPS returned non-JSON body on 200 url=%s first200=%r",
                        url, text[:200],
                    )
                    if attempt < self.max_retries:
                        # Re-warm the origin in case the context lost its session.
                        self._warmed = False
                        self._warm_origin()
                        self._backoff(attempt)
                        continue
                return MopsFetchResult(
                    status_code=resp.status,
                    text=text,
                    raw_bytes=text.encode("utf-8"),
                    used_browser=True,
                )

            if self._should_retry(resp.status) and attempt < self.max_retries:
                self._backoff(attempt)
                continue
            break

        return MopsFetchResult(
            status_code=last_status,
            text=last_text,
            raw_bytes=last_text.encode("utf-8", errors="replace"),
            used_browser=True,
        )


def _looks_like_json(text: str) -> bool:
    """Fast pre-check: does the body look like JSON rather than HTML?"""
    s = (text or "").lstrip()
    return s.startswith("{") or s.startswith("[")
