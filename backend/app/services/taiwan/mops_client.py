"""
MopsClient — a rate-limited, retrying HTTP client for the MOPS portal with an
opt-in Playwright fallback for endpoints that refuse plain requests.

Defaults are production-safe: 1 req/sec sustained, 3 retries with exponential
backoff, Big5/UTF-8 encoding auto-detection, realistic Chrome User-Agent.

Playwright fallback uses the existing `web-scraping` skill's CDP Chrome
profile (~/.alphagraph_scraper_profile). Fallback is triggered on HTTP 403,
503, or when the response body contains a known CAPTCHA marker.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from threading import Lock
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Markers that, if present in response body, indicate we need a browser.
_CAPTCHA_MARKERS = (
    "kaptcha.jpg",
    "captcha",
    "Please enable JavaScript",
    "驗證碼",
)


@dataclass
class MopsFetchResult:
    status_code: int
    text: str
    used_browser: bool = False
    raw_bytes: Optional[bytes] = None
    encoding: Optional[str] = None


class MopsClient:
    """Thread-safe single-host HTTP client for MOPS.

    Construct once per scraper; pass into scrape functions. Keeps a
    `requests.Session` alive across calls for cookie persistence.
    """

    def __init__(
        self,
        *,
        min_interval_seconds: float = 1.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._last_request_time: float = 0.0
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        allow_browser_fallback: bool = False,
    ) -> MopsFetchResult:
        return self._fetch("GET", url, params=params, data=None,
                           allow_browser_fallback=allow_browser_fallback)

    def post(
        self,
        url: str,
        *,
        data: Optional[dict] = None,
        allow_browser_fallback: bool = False,
    ) -> MopsFetchResult:
        return self._fetch("POST", url, params=None, data=data,
                           allow_browser_fallback=allow_browser_fallback)

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _sleep_until_min_interval(self) -> None:
        with self._lock:
            delta = time.perf_counter() - self._last_request_time
            wait = self.min_interval_seconds - delta
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.perf_counter()

    def _should_retry(self, status_code: int) -> bool:
        return status_code in (429, 500, 502, 503, 504)

    def _should_try_browser(self, status_code: int, text: str) -> bool:
        if status_code in (403, 503):
            return True
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in _CAPTCHA_MARKERS)

    def _decode_body(self, resp: requests.Response) -> tuple[str, str]:
        """Return (decoded_text, encoding_used). Falls back to big5 if utf-8 fails."""
        for encoding in ("utf-8", "big5", "latin-1"):
            try:
                return resp.content.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        # Last-ditch: replace undecodable bytes so we always have *some* text.
        return resp.content.decode("utf-8", errors="replace"), "utf-8-replaced"

    def _fetch(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict],
        data: Optional[dict],
        allow_browser_fallback: bool,
    ) -> MopsFetchResult:
        last_status = 0
        last_text = ""
        last_raw = b""
        for attempt in range(self.max_retries + 1):
            self._sleep_until_min_interval()
            try:
                if method == "POST":
                    resp = self._session.post(url, data=data, timeout=self.timeout)
                else:
                    resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                logger.warning("MOPS request error attempt=%d url=%s err=%s", attempt, url, exc)
                last_status = 0
                last_text = str(exc)
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                break

            text, encoding = self._decode_body(resp)
            last_status = resp.status_code
            last_text = text
            last_raw = resp.content

            if 200 <= resp.status_code < 300:
                return MopsFetchResult(
                    status_code=resp.status_code,
                    text=text,
                    used_browser=False,
                    raw_bytes=resp.content,
                    encoding=encoding,
                )

            if self._should_retry(resp.status_code) and attempt < self.max_retries:
                self._backoff(attempt)
                continue

            # Non-retriable or retries exhausted — maybe try browser.
            if allow_browser_fallback and self._should_try_browser(resp.status_code, text):
                return self._browser_fetch(url, method, data)

            break

        # If we never got a 2xx, return the last observation.
        return MopsFetchResult(
            status_code=last_status,
            text=last_text,
            used_browser=False,
            raw_bytes=last_raw,
            encoding=None,
        )

    def _backoff(self, attempt: int) -> None:
        # 2^attempt + jitter: 2, 4, 8 ... + up to 1s jitter
        delay = (self.backoff_base ** attempt) + random.uniform(0, 1)
        time.sleep(delay)

    # ------------------------------------------------------------------
    # Playwright fallback — wired separately in Task 3
    # ------------------------------------------------------------------

    def _browser_fetch(self, url: str, method: str, data: Optional[dict]) -> MopsFetchResult:
        """Default stub — Task 3 wires the real Playwright path. Overridable in tests."""
        from backend.app.services.taiwan.mops_client_browser import browser_fetch
        return browser_fetch(url, method=method, data=data, timeout=self.timeout)
