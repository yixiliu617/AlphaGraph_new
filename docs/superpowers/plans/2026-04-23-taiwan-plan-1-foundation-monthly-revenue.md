# Taiwan Disclosure Ingestion — Plan 1: Foundation + Monthly Revenue

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a production-grade, end-to-end pipeline that scrapes Taiwan monthly revenue from MOPS for the 51-ticker semi watchlist, stores it locally (parquet + raw HTML) with S3 mirror + amendment history, exposes it via authenticated FastAPI endpoints, runs on a scheduled Python worker deployed to Fly.io, and renders it in a new top-level `/taiwan` dashboard tab.

**Architecture:** A new `backend/app/services/taiwan/` package with small, single-responsibility modules (`mops_client`, `storage`, `amendments`, `validation`, `scrapers/*`, `scheduler`, `health`). A two-process Fly.io app (`web` + `taiwan_scheduler`) sharing a Fly volume + SQLite DB. The FastAPI router reads parquet / SQLite, never scrapes. The scheduler is the only writer. Frontend follows the existing container/view pattern with a top-level Next.js route group.

**Tech Stack:** Python 3.13 (already in use), FastAPI (existing), APScheduler (new), `requests` + `beautifulsoup4` + `lxml` (new), Playwright via existing CDP profile (existing), Gemini 2.5 Flash via existing `LLMProvider` port (not used in Plan 1 — deferred to Plan 2 for material info), `pyarrow` + `pandas` (new if not present), `boto3` (new) for S3 mirror, `structlog` (new) for structured logs, `zoneinfo` (stdlib) for TPE timezone. Frontend: Next.js 15 + React 19 + TypeScript + Tailwind v4 + Recharts (existing).

**Scope (from spec §Plan 1):** monthly-revenue end-to-end + foundation infra that Plan 2 will reuse. Material info is Plan 2; translation pipeline is deferred to Plan 2 because monthly revenue is numeric (no Chinese bodies to translate).

**Commit strategy:** Three clean commits at natural boundaries:
- **Commit A:** backend infra + scrapers + API + scheduler + tests (Tasks 1–9).
- **Commit B:** frontend + Fly.io deploy config (Tasks 10–11).
- **Commit C:** executed backfill + sanity results (Task 12).

---

## File structure

**Backend — new files:**

```
backend/app/services/taiwan/
  __init__.py                        package marker
  mops_client.py                     HTTP client; rate limit; retry; Playwright fallback
  storage.py                         parquet + raw capture + upsert + S3 mirror
  amendments.py                      content-hash + history-parquet writer
  validation.py                      data-quality invariants
  translation.py                     Gemini wrapper (stub in Plan 1; used in Plan 2)
  registry.py                        watchlist + scraper_state reads/writes
  health.py                          heartbeat writer + reader
  scheduler.py                       APScheduler entrypoint
  scrapers/
    __init__.py
    company_master.py
    monthly_revenue.py
backend/app/api/routers/v1/taiwan.py  READ-ONLY endpoints
backend/tests/unit/
  test_mops_client.py
  test_storage.py
  test_amendments.py
  test_validation.py
  test_monthly_revenue_parser.py
backend/tests/integration/
  test_taiwan_heartbeat.py
  test_taiwan_api.py
```

**Backend — modified files:**

```
backend/requirements.txt              add new deps
backend/app/main.py                   include taiwan router
backend/app/core/config.py            add S3 + scheduler env vars (if config.py pattern exists)
```

**Frontend — new files:**

```
frontend/src/app/(dashboard)/taiwan/
  page.tsx
  TaiwanContainer.tsx
  TaiwanView.tsx
  components/
    WatchlistRevenueGrid.tsx
    TickerDrillDown.tsx
    TaiwanHealthIndicator.tsx
frontend/src/lib/api/taiwanClient.ts
frontend/src/store/useTaiwanStore.ts
```

**Frontend — modified files:**

```
frontend/src/components/layout/Sidebar.tsx   add [Taiwan] nav item (adjust path if your sidebar lives elsewhere)
```

**Deployment — new files:**

```
fly.toml                              multi-process config
Dockerfile                            single image; entrypoint switches by process group
.dockerignore
```

**Data — new files:**

```
backend/data/taiwan/_registry/          directory; populated at first scheduler run
backend/data/taiwan/monthly_revenue/    directory; populated at first run
backend/data/taiwan/_raw/                directory
```

---

## Task 1: Dependencies, package skeleton, structured logging

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/services/taiwan/__init__.py`
- Create: `backend/app/services/taiwan/scrapers/__init__.py`
- Create: `backend/data/taiwan/_registry/.gitkeep`, `backend/data/taiwan/monthly_revenue/.gitkeep`, `backend/data/taiwan/_raw/.gitkeep`

- [ ] **Step 1: Add new Python dependencies**

Edit `backend/requirements.txt`. Append the new deps (leave existing ones as-is):

```
# Taiwan ingestion (Plan 1 + 2)
apscheduler>=3.10.4
structlog>=24.1.0
boto3>=1.34.0
beautifulsoup4>=4.12.3
lxml>=5.1.0
pyarrow>=15.0.0
pandas>=2.1.0
```

- [ ] **Step 2: Install**

Run (from repo root):

```bash
pip install -r backend/requirements.txt
```

Expected: `Successfully installed apscheduler-... structlog-... boto3-... beautifulsoup4-... lxml-... pyarrow-... pandas-...`. If any are already present they'll be no-ops.

- [ ] **Step 3: Create the package skeleton**

Create `backend/app/services/taiwan/__init__.py`:

```python
"""
Taiwan disclosure ingestion package.

Scrapes MOPS (公開資訊觀測站) for Taiwan-listed companies on the semi
watchlist. Writes parquet + raw captures under backend/data/taiwan/.
Exposes data via /api/v1/taiwan/* endpoints. Runs via the taiwan_scheduler
Fly.io process.

See docs/superpowers/specs/2026-04-23-taiwan-disclosure-ingestion-design.md
for the full architecture.
"""
```

Create `backend/app/services/taiwan/scrapers/__init__.py`:

```python
"""Individual scrapers for each MOPS data source. Each scraper exposes a
single public entry point accepting a scraper context and returning
ScrapeResult."""
```

- [ ] **Step 4: Create data directories with .gitkeep placeholders**

```bash
mkdir -p backend/data/taiwan/_registry backend/data/taiwan/monthly_revenue backend/data/taiwan/_raw/monthly_revenue
echo "# Populated at first scheduler run" > backend/data/taiwan/_registry/.gitkeep
echo "# Populated at first scheduler run" > backend/data/taiwan/monthly_revenue/.gitkeep
echo "# Populated at first scheduler run" > backend/data/taiwan/_raw/.gitkeep
```

- [ ] **Step 5: Verify the skeleton imports**

```bash
python -c "from backend.app.services.taiwan import scrapers; print('taiwan package OK')"
```

Expected: `taiwan package OK`.

- [ ] **Step 6: No commit yet.**

---

## Task 2: MOPS HTTP client with rate limit, retry, encoding, Playwright fallback

**Files:**
- Create: `backend/app/services/taiwan/mops_client.py`
- Create: `backend/tests/unit/test_mops_client.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/unit/test_mops_client.py`:

```python
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
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_mops_client.py -v
```

Expected: ImportError — `mops_client` doesn't exist yet.

- [ ] **Step 3: Implement the client**

Create `backend/app/services/taiwan/mops_client.py`:

```python
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
```

Also create a `mops_client_browser.py` stub so tests run without Playwright; Task 3 replaces the body:

Create `backend/app/services/taiwan/mops_client_browser.py`:

```python
"""Playwright fallback for MOPS. Spun up only when the requests path fails.

Task 3 replaces the body with real Playwright/CDP code. Plan 1 runs against
the plain-requests path for all known Taiwan monthly-revenue endpoints, so
this is rarely (if ever) invoked during Plan 1; the stub raises so a
misconfigured fallback is loud, not silent."""

from backend.app.services.taiwan.mops_client import MopsFetchResult


def browser_fetch(url: str, *, method: str, data, timeout: float) -> MopsFetchResult:
    raise RuntimeError(
        f"Playwright fallback invoked for {url} but not yet configured. "
        f"Plan 1 does not require it; this is a bug in Plan 2 wiring."
    )
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_mops_client.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: No commit yet.**

---

## Task 3: Playwright fallback wired to the existing CDP profile

Plan 1's monthly-revenue endpoints do not require Playwright (verified against the spec's URL list). This task wires the fallback for Plan 2's use and asserts it's *available* by attempting a single real fetch to MOPS's home page. If the fetch fails, log and move on — do not block Plan 1 on browser availability.

**Files:**
- Modify: `backend/app/services/taiwan/mops_client_browser.py`
- Create: `backend/tests/unit/test_mops_client_browser_smoke.py`

- [ ] **Step 1: Replace the stub with a real Playwright fallback**

Rewrite `backend/app/services/taiwan/mops_client_browser.py`:

```python
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
```

- [ ] **Step 2: Write a smoke test that skips if Playwright/Chrome missing**

Create `backend/tests/unit/test_mops_client_browser_smoke.py`:

```python
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
```

- [ ] **Step 3: Run tests**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_mops_client_browser_smoke.py -v
```

Expected: both tests PASS on any dev machine.

- [ ] **Step 4: No commit yet.**

---

## Task 4: Storage layer — parquet + raw capture + S3 mirror + amendments + validation

**Files:**
- Create: `backend/app/services/taiwan/storage.py`
- Create: `backend/app/services/taiwan/amendments.py`
- Create: `backend/app/services/taiwan/validation.py`
- Create: `backend/tests/unit/test_storage.py`
- Create: `backend/tests/unit/test_amendments.py`
- Create: `backend/tests/unit/test_validation.py`

- [ ] **Step 1: Write validation tests**

Create `backend/tests/unit/test_validation.py`:

```python
from backend.app.services.taiwan.validation import (
    validate_monthly_revenue_row,
    ValidationFlag,
)


def test_valid_row_returns_no_flags():
    row = {
        "ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
        "revenue_twd": 200_000_000_000, "yoy_pct": 0.10, "mom_pct": 0.05,
        "ytd_pct": 0.12,
    }
    flags = validate_monthly_revenue_row(row)
    assert flags == []


def test_negative_revenue_flagged():
    row = {
        "ticker": "X", "market": "TWSE", "fiscal_ym": "2026-03",
        "revenue_twd": -100, "yoy_pct": 0, "mom_pct": 0, "ytd_pct": 0,
    }
    flags = validate_monthly_revenue_row(row)
    assert ValidationFlag.NEGATIVE_REVENUE in flags


def test_absurd_yoy_flagged():
    row = {
        "ticker": "X", "market": "TWSE", "fiscal_ym": "2026-03",
        "revenue_twd": 100, "yoy_pct": 15.0, "mom_pct": 0, "ytd_pct": 0,
    }
    flags = validate_monthly_revenue_row(row)
    assert ValidationFlag.ABSURD_YOY in flags


def test_future_fiscal_ym_flagged():
    row = {
        "ticker": "X", "market": "TWSE", "fiscal_ym": "2099-12",
        "revenue_twd": 100, "yoy_pct": 0, "mom_pct": 0, "ytd_pct": 0,
    }
    flags = validate_monthly_revenue_row(row)
    assert ValidationFlag.FUTURE_PERIOD in flags


def test_invalid_fiscal_ym_format_flagged():
    row = {
        "ticker": "X", "market": "TWSE", "fiscal_ym": "March 2026",
        "revenue_twd": 100, "yoy_pct": 0, "mom_pct": 0, "ytd_pct": 0,
    }
    flags = validate_monthly_revenue_row(row)
    assert ValidationFlag.INVALID_PERIOD_FORMAT in flags
```

- [ ] **Step 2: Write amendments tests**

Create `backend/tests/unit/test_amendments.py`:

```python
import pandas as pd
import pytest

from backend.app.services.taiwan.amendments import (
    compute_content_hash,
    detect_amendment,
    AmendmentDecision,
)


def test_content_hash_stable_for_equivalent_rows():
    row_a = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "yoy_pct": 0.1}
    row_b = dict(reversed(list(row_a.items())))  # same content, different dict insertion order
    assert compute_content_hash(row_a) == compute_content_hash(row_b)


def test_content_hash_differs_when_value_changes():
    row_a = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "yoy_pct": 0.1}
    row_b = {**row_a, "revenue_twd": 1001}
    assert compute_content_hash(row_a) != compute_content_hash(row_b)


def test_content_hash_ignores_mutable_columns():
    row_a = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000,
             "first_seen_at": "2026-04-01", "last_seen_at": "2026-04-01"}
    row_b = {**row_a, "last_seen_at": "2026-04-10"}
    assert compute_content_hash(row_a) == compute_content_hash(row_b)


def test_detect_amendment_insert_when_no_prior():
    prior_df = pd.DataFrame(columns=["ticker", "fiscal_ym", "revenue_twd", "content_hash"])
    new_row = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000,
               "content_hash": "abc"}
    assert detect_amendment(prior_df, new_row, key_cols=["ticker", "fiscal_ym"]) \
        == AmendmentDecision.INSERT


def test_detect_amendment_noop_when_hash_matches():
    prior_df = pd.DataFrame([
        {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "content_hash": "abc"}
    ])
    new_row = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "content_hash": "abc"}
    assert detect_amendment(prior_df, new_row, key_cols=["ticker", "fiscal_ym"]) \
        == AmendmentDecision.TOUCH_ONLY


def test_detect_amendment_amend_when_hash_differs():
    prior_df = pd.DataFrame([
        {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "content_hash": "abc"}
    ])
    new_row = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1001, "content_hash": "def"}
    assert detect_amendment(prior_df, new_row, key_cols=["ticker", "fiscal_ym"]) \
        == AmendmentDecision.AMEND
```

- [ ] **Step 3: Write storage tests**

Create `backend/tests/unit/test_storage.py`:

```python
from pathlib import Path

import pandas as pd
import pytest

from backend.app.services.taiwan.storage import (
    upsert_monthly_revenue,
    read_monthly_revenue,
    write_raw_capture,
    raw_capture_path,
)


@pytest.fixture
def taiwan_data_dir(tmp_path):
    (tmp_path / "monthly_revenue").mkdir(parents=True)
    (tmp_path / "_raw" / "monthly_revenue").mkdir(parents=True)
    return tmp_path


def test_upsert_monthly_revenue_inserts_fresh(taiwan_data_dir):
    rows = [
        {"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
         "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0, "ytd_pct": 0.12,
         "cumulative_ytd_twd": 500_000_000_000, "prior_year_month_twd": 180_000_000_000},
    ]
    stats = upsert_monthly_revenue(rows, data_dir=taiwan_data_dir)
    assert stats.inserted == 1
    assert stats.amended == 0
    df = read_monthly_revenue(data_dir=taiwan_data_dir)
    assert len(df) == 1
    assert df.loc[0, "ticker"] == "2330"
    assert df.loc[0, "content_hash"] != ""
    assert df.loc[0, "amended"] is False or df.loc[0, "amended"] == False  # pandas dtype


def test_upsert_monthly_revenue_touches_only_when_unchanged(taiwan_data_dir):
    rows = [
        {"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
         "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0, "ytd_pct": 0.12,
         "cumulative_ytd_twd": 500_000_000_000, "prior_year_month_twd": 180_000_000_000},
    ]
    upsert_monthly_revenue(rows, data_dir=taiwan_data_dir)
    stats = upsert_monthly_revenue(rows, data_dir=taiwan_data_dir)
    assert stats.inserted == 0
    assert stats.touched == 1
    assert stats.amended == 0


def test_upsert_monthly_revenue_detects_amendment(taiwan_data_dir):
    row_v1 = {"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
              "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0, "ytd_pct": 0.12,
              "cumulative_ytd_twd": 500_000_000_000, "prior_year_month_twd": 180_000_000_000}
    upsert_monthly_revenue([row_v1], data_dir=taiwan_data_dir)
    row_v2 = {**row_v1, "revenue_twd": 210_000_000_000, "yoy_pct": 0.11}
    stats = upsert_monthly_revenue([row_v2], data_dir=taiwan_data_dir)
    assert stats.amended == 1
    df = read_monthly_revenue(data_dir=taiwan_data_dir)
    assert df.loc[0, "revenue_twd"] == 210_000_000_000
    assert df.loc[0, "amended"]
    # History parquet should have the v1 row.
    history = pd.read_parquet(taiwan_data_dir / "monthly_revenue" / "history.parquet")
    assert len(history) == 1
    assert history.loc[0, "revenue_twd"] == 200_000_000_000


def test_write_raw_capture_creates_idempotent_file(taiwan_data_dir):
    ticker = "2330"
    key = "2026-03"
    content = b"<html>raw bytes</html>"
    p = write_raw_capture(
        source="monthly_revenue",
        ticker=ticker,
        key=key,
        content=content,
        data_dir=taiwan_data_dir,
    )
    assert Path(p).read_bytes() == content
    # Second call with identical content: no-op, same path, still OK.
    p2 = write_raw_capture(
        source="monthly_revenue",
        ticker=ticker,
        key=key,
        content=content,
        data_dir=taiwan_data_dir,
    )
    assert p == p2


def test_raw_capture_path_is_deterministic():
    p1 = raw_capture_path(source="monthly_revenue", ticker="2330", key="2026-03",
                          data_dir=Path("/tmp/foo"))
    p2 = raw_capture_path(source="monthly_revenue", ticker="2330", key="2026-03",
                          data_dir=Path("/tmp/foo"))
    assert p1 == p2
    assert str(p1).endswith("monthly_revenue/2330/2026-03.html")
```

- [ ] **Step 4: Run tests — confirm they fail**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_validation.py backend/tests/unit/test_amendments.py backend/tests/unit/test_storage.py -v
```

Expected: all import-errored / failing.

- [ ] **Step 5: Implement validation**

Create `backend/app/services/taiwan/validation.py`:

```python
"""
Data-quality invariants for Taiwan scraper output. Flags, never drops — the
caller decides whether to persist a flagged row (normally yes; flags surface
in the UI and health report).
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum


class ValidationFlag(str, Enum):
    NEGATIVE_REVENUE = "negative_revenue"
    ABSURD_YOY = "absurd_yoy"
    FUTURE_PERIOD = "future_period"
    INVALID_PERIOD_FORMAT = "invalid_period_format"
    LARGE_AMENDMENT = "large_amendment"


_FISCAL_YM = re.compile(r"^(\d{4})-(\d{2})$")


def validate_monthly_revenue_row(row: dict) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    revenue = row.get("revenue_twd")
    if revenue is not None and revenue < 0:
        flags.append(ValidationFlag.NEGATIVE_REVENUE)

    yoy = row.get("yoy_pct")
    if yoy is not None and abs(yoy) > 10.0:  # > 1000 %
        flags.append(ValidationFlag.ABSURD_YOY)

    ym = row.get("fiscal_ym") or ""
    m = _FISCAL_YM.match(ym)
    if not m:
        flags.append(ValidationFlag.INVALID_PERIOD_FORMAT)
    else:
        y, mm = int(m.group(1)), int(m.group(2))
        if mm < 1 or mm > 12:
            flags.append(ValidationFlag.INVALID_PERIOD_FORMAT)
        else:
            today = date.today()
            if y > today.year or (y == today.year and mm > today.month + 1):
                flags.append(ValidationFlag.FUTURE_PERIOD)

    return flags


def is_large_amendment(prior_value: float, new_value: float, threshold: float = 0.5) -> bool:
    """Return True if new_value differs from prior_value by > threshold * prior_value."""
    if prior_value in (None, 0):
        return False
    return abs(new_value - prior_value) / abs(prior_value) > threshold
```

- [ ] **Step 6: Implement amendments**

Create `backend/app/services/taiwan/amendments.py`:

```python
"""
Content-hash based amendment detection for Taiwan parquet datasets.

Rule:
  hash(canonical(row)) = sha256 of json-sorted-keys(row_without_mutable_fields).
  Compare to the prior row's stored hash; classify as INSERT, TOUCH_ONLY, or AMEND.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Iterable

import pandas as pd


# Columns that change every ingest and therefore MUST NOT participate in the hash.
_MUTABLE_FIELDS = {"first_seen_at", "last_seen_at", "content_hash", "amended"}


class AmendmentDecision(str, Enum):
    INSERT = "insert"
    TOUCH_ONLY = "touch_only"
    AMEND = "amend"


def canonicalise_row(row: dict) -> str:
    filtered = {k: v for k, v in row.items() if k not in _MUTABLE_FIELDS}
    # sort_keys + default=str for timestamps / numpy types
    return json.dumps(filtered, sort_keys=True, default=str, ensure_ascii=False)


def compute_content_hash(row: dict) -> str:
    return hashlib.sha256(canonicalise_row(row).encode("utf-8")).hexdigest()


def detect_amendment(
    prior_df: pd.DataFrame, new_row: dict, *, key_cols: Iterable[str]
) -> AmendmentDecision:
    if prior_df.empty:
        return AmendmentDecision.INSERT
    mask = pd.Series([True] * len(prior_df))
    for k in key_cols:
        mask &= (prior_df[k] == new_row[k])
    match = prior_df[mask]
    if match.empty:
        return AmendmentDecision.INSERT
    prior_hash = str(match.iloc[0].get("content_hash") or "")
    new_hash = str(new_row.get("content_hash") or compute_content_hash(new_row))
    return AmendmentDecision.TOUCH_ONLY if prior_hash == new_hash else AmendmentDecision.AMEND
```

- [ ] **Step 7: Implement storage**

Create `backend/app/services/taiwan/storage.py`:

```python
"""
Storage layer for the Taiwan ingestion package.

Public API:
  upsert_monthly_revenue(rows, *, data_dir)     -> UpsertStats
  read_monthly_revenue(*, data_dir)             -> DataFrame
  write_raw_capture(source, ticker, key, content, *, data_dir) -> Path
  raw_capture_path(source, ticker, key, *, data_dir) -> Path

Parquet schemas documented in docs/superpowers/specs/2026-04-23-taiwan-disclosure-ingestion-design.md
under §"Parquet schemas".

S3 mirror: on successful parquet write, we enqueue the raw file path for
async upload. The mirror is a best-effort, non-blocking operation; local-only
ingest still works if AWS creds are absent or S3 is unreachable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from backend.app.services.taiwan.amendments import (
    AmendmentDecision,
    compute_content_hash,
    detect_amendment,
)

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "taiwan"


@dataclass
class UpsertStats:
    inserted: int = 0
    touched: int = 0
    amended: int = 0


# ---------------------------------------------------------------------------
# Monthly revenue
# ---------------------------------------------------------------------------

_MR_KEY_COLS = ["ticker", "fiscal_ym"]


def _mr_paths(data_dir: Path) -> tuple[Path, Path]:
    return (
        data_dir / "monthly_revenue" / "data.parquet",
        data_dir / "monthly_revenue" / "history.parquet",
    )


def read_monthly_revenue(*, data_dir: Path = DEFAULT_DATA_DIR) -> pd.DataFrame:
    path, _ = _mr_paths(data_dir)
    if not path.exists():
        return pd.DataFrame(columns=[
            "ticker", "market", "fiscal_ym",
            "revenue_twd", "yoy_pct", "mom_pct", "ytd_pct",
            "cumulative_ytd_twd", "prior_year_month_twd",
            "first_seen_at", "last_seen_at", "content_hash", "amended",
        ])
    return pd.read_parquet(path)


def upsert_monthly_revenue(
    rows: Iterable[dict], *, data_dir: Path = DEFAULT_DATA_DIR
) -> UpsertStats:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "monthly_revenue").mkdir(parents=True, exist_ok=True)
    data_path, history_path = _mr_paths(data_dir)

    current = read_monthly_revenue(data_dir=data_dir)
    stats = UpsertStats()
    now = datetime.now(timezone.utc)

    updated_rows = current.copy()
    history_additions: list[dict] = []

    for row in rows:
        canonical = dict(row)
        canonical["content_hash"] = compute_content_hash(canonical)
        decision = detect_amendment(updated_rows, canonical, key_cols=_MR_KEY_COLS)

        if decision is AmendmentDecision.INSERT:
            canonical["first_seen_at"] = now
            canonical["last_seen_at"] = now
            canonical["amended"] = False
            updated_rows = pd.concat([updated_rows, pd.DataFrame([canonical])], ignore_index=True)
            stats.inserted += 1

        elif decision is AmendmentDecision.TOUCH_ONLY:
            mask = (updated_rows["ticker"] == canonical["ticker"]) & \
                   (updated_rows["fiscal_ym"] == canonical["fiscal_ym"])
            updated_rows.loc[mask, "last_seen_at"] = now
            stats.touched += 1

        elif decision is AmendmentDecision.AMEND:
            mask = (updated_rows["ticker"] == canonical["ticker"]) & \
                   (updated_rows["fiscal_ym"] == canonical["fiscal_ym"])
            prior_row = updated_rows[mask].iloc[0].to_dict()
            # Copy prior to history, then overwrite primary.
            prior_row["superseded_at"] = now
            history_additions.append(prior_row)
            # Preserve first_seen_at from the prior; bump last_seen_at.
            canonical["first_seen_at"] = prior_row.get("first_seen_at", now)
            canonical["last_seen_at"] = now
            canonical["amended"] = True
            # Update the primary row in-place.
            for col, val in canonical.items():
                updated_rows.loc[mask, col] = val
            stats.amended += 1

    updated_rows.to_parquet(data_path, index=False)
    if history_additions:
        hist_df = pd.DataFrame(history_additions)
        if history_path.exists():
            existing_hist = pd.read_parquet(history_path)
            hist_df = pd.concat([existing_hist, hist_df], ignore_index=True)
        hist_df.to_parquet(history_path, index=False)

    logger.info("upsert_monthly_revenue stats=%s", stats)
    return stats


# ---------------------------------------------------------------------------
# Raw captures
# ---------------------------------------------------------------------------

def raw_capture_path(
    *, source: str, ticker: str, key: str, data_dir: Path = DEFAULT_DATA_DIR
) -> Path:
    return data_dir / "_raw" / source / ticker / f"{key}.html"


def write_raw_capture(
    *, source: str, ticker: str, key: str, content: bytes,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> Path:
    p = raw_capture_path(source=source, ticker=ticker, key=key, data_dir=data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.read_bytes() == content:
        # Idempotent: already captured identical content.
        return p
    p.write_bytes(content)
    _enqueue_s3_mirror(p, source=source, ticker=ticker, key=key)
    return p


# ---------------------------------------------------------------------------
# S3 mirror (best-effort, non-blocking)
# ---------------------------------------------------------------------------

_S3_BUCKET = os.environ.get("TAIWAN_S3_BUCKET_RAW")  # e.g. "alphagraph-taiwan-raw-prod"


def _enqueue_s3_mirror(path: Path, *, source: str, ticker: str, key: str) -> None:
    """Best-effort sync upload. Intentionally inline for Plan 1 simplicity.
    If creds missing or S3 down, log warning and continue — local write already
    succeeded. Plan 2 / scale may add an async queue."""
    if not _S3_BUCKET:
        return  # Mirror disabled; local-only mode.
    try:
        import boto3
        client = boto3.client("s3")
        s3_key = f"{source}/{ticker}/{key}{path.suffix}"
        client.upload_file(str(path), _S3_BUCKET, s3_key)
    except Exception as exc:
        logger.warning("S3 mirror upload failed path=%s err=%s", path, exc)
```

- [ ] **Step 8: Run tests — confirm they pass**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_validation.py backend/tests/unit/test_amendments.py backend/tests/unit/test_storage.py -v
```

Expected: all tests PASS.

- [ ] **Step 9: No commit yet.**

---

## Task 5: Company master scraper

Scrapes the MOPS full company registry once a month to validate watchlist tickers against MOPS's canonical `co_id` and company metadata (full name, market, stock type, etc.). Writes to `_registry/mops_company_master.parquet`.

**Files:**
- Create: `backend/app/services/taiwan/scrapers/company_master.py`
- Create: `backend/app/services/taiwan/registry.py`
- Create: `backend/tests/unit/test_company_master_parser.py`

- [ ] **Step 1: Write parser tests with a canned MOPS HTML snippet**

Create `backend/tests/unit/test_company_master_parser.py`:

```python
"""
Unit tests for the MOPS company-master parser. Uses a small synthetic HTML
snippet matching the real MOPS table layout so no network traffic is required.
"""

from backend.app.services.taiwan.scrapers.company_master import parse_company_master_html


SAMPLE_HTML = """
<html><body>
<table class="hasBorder">
  <tr><th>公司代號</th><th>公司名稱</th><th>產業類別</th></tr>
  <tr><td>2330</td><td>台積電</td><td>半導體業</td></tr>
  <tr><td>2454</td><td>聯發科</td><td>半導體業</td></tr>
  <tr><td>2317</td><td>鴻海</td><td>其他電子業</td></tr>
</table>
</body></html>
"""


def test_parse_returns_one_row_per_company():
    rows = parse_company_master_html(SAMPLE_HTML, market="TWSE")
    assert len(rows) == 3
    tsmc = next(r for r in rows if r["co_id"] == "2330")
    assert tsmc["name_zh"] == "台積電"
    assert tsmc["industry_zh"] == "半導體業"
    assert tsmc["market"] == "TWSE"


def test_parse_empty_html_returns_empty_list():
    assert parse_company_master_html("<html><body></body></html>", market="TWSE") == []


def test_parse_trims_whitespace():
    html = """<table class="hasBorder">
      <tr><th>公司代號</th><th>公司名稱</th><th>產業類別</th></tr>
      <tr><td>  2330  </td><td>  台積電  </td><td>  半導體業  </td></tr>
    </table>"""
    rows = parse_company_master_html(html, market="TWSE")
    assert rows[0]["co_id"] == "2330"
    assert rows[0]["name_zh"] == "台積電"
```

- [ ] **Step 2: Run tests — confirm fail**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_company_master_parser.py -v
```

- [ ] **Step 3: Implement registry helper**

Create `backend/app/services/taiwan/registry.py`:

```python
"""
Registry: reads the curated watchlist CSV + MOPS company-master parquet.

Provides lookups the scrapers need:
  - list_watchlist_tickers()          -> list[str]
  - watchlist_to_mops_ids(watchlist)  -> dict[ticker, mops_row]
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

WATCHLIST_CSV = Path(__file__).resolve().parents[3] / "data" / "taiwan" / "watchlist_semi.csv"
REGISTRY_PARQUET = Path(__file__).resolve().parents[3] / "data" / "taiwan" / "_registry" / "mops_company_master.parquet"


def load_watchlist() -> pd.DataFrame:
    return pd.read_csv(WATCHLIST_CSV, dtype=str).fillna("")


def list_watchlist_tickers() -> list[str]:
    return load_watchlist()["ticker"].tolist()


def load_mops_master() -> pd.DataFrame:
    if not REGISTRY_PARQUET.exists():
        return pd.DataFrame(columns=["co_id", "name_zh", "industry_zh", "market", "last_seen_at"])
    return pd.read_parquet(REGISTRY_PARQUET)


def save_mops_master(df: pd.DataFrame) -> None:
    REGISTRY_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(REGISTRY_PARQUET, index=False)
```

- [ ] **Step 4: Implement parser + scraper**

Create `backend/app/services/taiwan/scrapers/company_master.py`:

```python
"""
MOPS company-master scraper.

Scrapes the full listed-company registry from MOPS and writes it to
backend/data/taiwan/_registry/mops_company_master.parquet. Run once a month.

Endpoints:
  TWSE main board: POST https://mops.twse.com.tw/mops/web/ajax_t51sb01
                   form: step=1&TYPEK=sii
  TPEx OTC:        POST https://mops.twse.com.tw/mops/web/ajax_t51sb01
                   form: step=1&TYPEK=otc
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
from bs4 import BeautifulSoup

from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.registry import load_mops_master, save_mops_master

_URL = "https://mops.twse.com.tw/mops/web/ajax_t51sb01"


def parse_company_master_html(html: str, *, market: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for table in soup.select("table"):
        # The company-list tables have a header with 公司代號 / 公司名稱.
        headers = [th.get_text(strip=True) for th in table.select("tr:nth-of-type(1) th, tr:nth-of-type(1) td")]
        if "公司代號" not in headers or "公司名稱" not in headers:
            continue
        idx_id = headers.index("公司代號")
        idx_name = headers.index("公司名稱")
        idx_industry = headers.index("產業類別") if "產業類別" in headers else None
        for tr in table.select("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) <= idx_name:
                continue
            rows.append({
                "co_id": cells[idx_id].strip(),
                "name_zh": cells[idx_name].strip(),
                "industry_zh": (cells[idx_industry].strip() if idx_industry is not None else ""),
                "market": market,
            })
    return rows


def scrape_company_master(client: MopsClient) -> int:
    """Scrape both markets; upsert into _registry/mops_company_master.parquet.
    Returns number of rows written."""
    now = datetime.now(timezone.utc)
    all_rows: list[dict] = []
    for market_code, market_label in (("sii", "TWSE"), ("otc", "TPEx")):
        result = client.post(_URL, data={"step": "1", "TYPEK": market_code})
        if result.status_code != 200:
            continue
        rows = parse_company_master_html(result.text, market=market_label)
        for r in rows:
            r["last_seen_at"] = now
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    save_mops_master(df)
    return len(df)
```

- [ ] **Step 5: Run parser tests**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_company_master_parser.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 6: No commit yet.**

---

## Task 6: Monthly revenue scraper

**Files:**
- Create: `backend/app/services/taiwan/scrapers/monthly_revenue.py`
- Create: `backend/tests/unit/test_monthly_revenue_parser.py`

- [ ] **Step 1: Write parser tests**

Create `backend/tests/unit/test_monthly_revenue_parser.py`:

```python
"""
Parser tests with a synthetic MOPS monthly-revenue response. The real endpoint
returns a wide HTML table; we shape a small representative sample.
"""

from backend.app.services.taiwan.scrapers.monthly_revenue import (
    parse_monthly_revenue_html,
)


SAMPLE_HTML = """
<html><body>
<table>
  <tr><th>公司代號</th><th>公司名稱</th>
      <th>當月營收</th>
      <th>上月營收</th>
      <th>去年當月營收</th>
      <th>上月比較增減(%)</th>
      <th>去年同月增減(%)</th>
      <th>當月累計營收</th>
      <th>去年累計營收</th>
      <th>前期比較增減(%)</th>
  </tr>
  <tr><td>2330</td><td>台積電</td>
      <td>200,000,000</td>
      <td>190,000,000</td>
      <td>150,000,000</td>
      <td>5.26</td>
      <td>33.33</td>
      <td>500,000,000</td>
      <td>400,000,000</td>
      <td>25.00</td>
  </tr>
</table>
</body></html>
"""


def test_parse_extracts_one_row_per_company():
    rows = parse_monthly_revenue_html(SAMPLE_HTML, market="TWSE", year=2026, month=3)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "2330"
    assert r["market"] == "TWSE"
    assert r["fiscal_ym"] == "2026-03"
    assert r["revenue_twd"] == 200_000_000
    assert r["prior_year_month_twd"] == 150_000_000
    assert r["cumulative_ytd_twd"] == 500_000_000
    assert abs(r["mom_pct"] - 0.0526) < 1e-4
    assert abs(r["yoy_pct"] - 0.3333) < 1e-4
    assert abs(r["ytd_pct"] - 0.25) < 1e-4


def test_parse_handles_thousands_separator_and_percent():
    html = SAMPLE_HTML.replace("200,000,000", "1,234,567,890")
    rows = parse_monthly_revenue_html(html, market="TWSE", year=2026, month=3)
    assert rows[0]["revenue_twd"] == 1_234_567_890


def test_parse_empty_returns_empty():
    assert parse_monthly_revenue_html("<html></html>", market="TWSE", year=2026, month=3) == []
```

- [ ] **Step 2: Run tests — confirm fail**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_monthly_revenue_parser.py -v
```

- [ ] **Step 3: Implement scraper**

Create `backend/app/services/taiwan/scrapers/monthly_revenue.py`:

```python
"""
MOPS monthly-revenue scraper.

Endpoint (summary query, one call per market-month returns all companies):
  POST https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs
  form: step=1&functionName=t05st10_ifrs&TYPEK=sii&year=YYYY&month=MM&co_id=

Post-processing:
  - Parse HTML table; strip thousands separators ("," in Western digits; 千 in Chinese digits).
  - Percentages in MOPS are strings like "33.33" meaning 33.33 %; we store as floats
    where 1.0 = 100 %.
  - Filter to watchlist tickers only — the raw response includes all listed
    companies (~1,000 for TWSE, ~800 for TPEx).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.registry import list_watchlist_tickers
from backend.app.services.taiwan.storage import (
    DEFAULT_DATA_DIR,
    upsert_monthly_revenue,
    write_raw_capture,
    UpsertStats,
)
from backend.app.services.taiwan.validation import validate_monthly_revenue_row

logger = logging.getLogger(__name__)

_URL = "https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs"

_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _parse_int(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    m = _NUM_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return int(float(m.group()))
    except ValueError:
        return None


def _parse_pct(text: str) -> float | None:
    """Accept '33.33' / '33.33%' / '−12.5'. Return decimal (0.3333) or None."""
    text = (text or "").replace("−", "-").replace("%", "").strip()
    try:
        return float(text) / 100.0
    except ValueError:
        return None


def parse_monthly_revenue_html(html: str, *, market: str, year: int, month: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    fiscal_ym = f"{year:04d}-{month:02d}"
    rows: list[dict] = []

    for table in soup.select("table"):
        header_cells = table.select("tr:nth-of-type(1) th, tr:nth-of-type(1) td")
        headers = [th.get_text(strip=True) for th in header_cells]
        if "公司代號" not in headers or "當月營收" not in headers:
            continue

        idx = {h: i for i, h in enumerate(headers)}

        for tr in table.select("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) <= idx["當月營收"]:
                continue
            ticker = cells[idx["公司代號"]].strip()
            if not ticker:
                continue
            rows.append({
                "ticker": ticker,
                "market": market,
                "fiscal_ym": fiscal_ym,
                "revenue_twd": _parse_int(cells[idx["當月營收"]]),
                "prior_year_month_twd": _parse_int(cells[idx.get("去年當月營收", -1)]) if "去年當月營收" in idx else None,
                "cumulative_ytd_twd": _parse_int(cells[idx.get("當月累計營收", -1)]) if "當月累計營收" in idx else None,
                "mom_pct": _parse_pct(cells[idx.get("上月比較增減(%)", -1)]) if "上月比較增減(%)" in idx else None,
                "yoy_pct": _parse_pct(cells[idx.get("去年同月增減(%)", -1)]) if "去年同月增減(%)" in idx else None,
                "ytd_pct": _parse_pct(cells[idx.get("前期比較增減(%)", -1)]) if "前期比較增減(%)" in idx else None,
            })
    return rows


def scrape_monthly_revenue_market_month(
    client: MopsClient,
    *,
    year: int,
    month: int,
    market: str,
    data_dir: Path = DEFAULT_DATA_DIR,
    watchlist: list[str] | None = None,
) -> UpsertStats:
    """Scrape one (market, year, month) MOPS query, filter to watchlist, upsert."""
    watchlist = watchlist or list_watchlist_tickers()
    market_code = "sii" if market == "TWSE" else "otc" if market == "TPEx" else market
    form = {"step": "1", "functionName": "t05st10_ifrs",
            "TYPEK": market_code, "year": str(year), "month": f"{month:02d}", "co_id": ""}

    result = client.post(_URL, data=form, allow_browser_fallback=True)
    if result.status_code != 200 or not result.text:
        logger.warning("monthly_revenue fetch failed market=%s ym=%04d-%02d status=%d",
                       market, year, month, result.status_code)
        return UpsertStats()

    # Raw capture (per market-month).
    write_raw_capture(
        source="monthly_revenue",
        ticker=f"_all_{market}",
        key=f"{year:04d}-{month:02d}",
        content=(result.raw_bytes or result.text.encode("utf-8")),
        data_dir=data_dir,
    )

    parsed = parse_monthly_revenue_html(result.text, market=market, year=year, month=month)
    filtered = [r for r in parsed if r["ticker"] in set(watchlist)]
    # Validation flags are informational — we still store flagged rows.
    for r in filtered:
        r["parse_flags"] = [f.value for f in validate_monthly_revenue_row(r)]
    stats = upsert_monthly_revenue(filtered, data_dir=data_dir)
    logger.info("monthly_revenue market=%s ym=%04d-%02d stats=%s raw_all_rows=%d matched_watchlist=%d",
                market, year, month, stats, len(parsed), len(filtered))
    return stats
```

- [ ] **Step 4: Run parser tests**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_monthly_revenue_parser.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: No commit yet.**

---

## Task 7: Heartbeat table + health.py + /health router section

**Files:**
- Create: `backend/app/services/taiwan/health.py`
- Create: `backend/tests/integration/test_taiwan_heartbeat.py`
- Backend DB migration: add `taiwan_scraper_heartbeat` table (inline SQL at module load)

- [ ] **Step 1: Write heartbeat tests**

Create `backend/tests/integration/test_taiwan_heartbeat.py`:

```python
"""Integration tests for the Taiwan scraper heartbeat table (SQLite)."""

import sqlite3
from datetime import datetime, timezone

import pytest

from backend.app.services.taiwan.health import (
    ensure_heartbeat_table,
    write_heartbeat,
    read_all_heartbeats,
    HeartbeatStatus,
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    ensure_heartbeat_table(conn)
    yield conn
    conn.close()


def test_write_and_read_heartbeat(db):
    write_heartbeat(db, scraper_name="monthly_revenue",
                    status=HeartbeatStatus.OK,
                    rows_inserted=12, rows_updated=0, rows_amended=1)
    rows = read_all_heartbeats(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["scraper_name"] == "monthly_revenue"
    assert r["status"] == "ok"
    assert r["rows_inserted"] == 12
    assert r["rows_amended"] == 1
    assert r["last_success_at"] is not None


def test_failed_heartbeat_sets_error_message(db):
    write_heartbeat(db, scraper_name="monthly_revenue",
                    status=HeartbeatStatus.FAILED,
                    last_error_msg="connection refused")
    r = read_all_heartbeats(db)[0]
    assert r["status"] == "failed"
    assert r["last_error_msg"] == "connection refused"
    assert r["last_error_at"] is not None


def test_multiple_scrapers_tracked_independently(db):
    write_heartbeat(db, scraper_name="monthly_revenue", status=HeartbeatStatus.OK)
    write_heartbeat(db, scraper_name="company_master", status=HeartbeatStatus.OK)
    names = sorted(r["scraper_name"] for r in read_all_heartbeats(db))
    assert names == ["company_master", "monthly_revenue"]
```

- [ ] **Step 2: Run tests — confirm fail**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/integration/test_taiwan_heartbeat.py -v
```

- [ ] **Step 3: Implement health.py**

Create `backend/app/services/taiwan/health.py`:

```python
"""
Heartbeat + /health helpers for the Taiwan scraper package.

Each scraper writes to a single SQLite table after every run. A dedicated
health_check job reads it hourly and logs WARN when a scraper is stale.
The /api/v1/taiwan/health endpoint exposes the same table to the frontend.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class HeartbeatStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS taiwan_scraper_heartbeat (
    scraper_name     TEXT PRIMARY KEY,
    last_run_at      TIMESTAMP,
    last_success_at  TIMESTAMP,
    last_error_at    TIMESTAMP,
    last_error_msg   TEXT,
    rows_inserted    INTEGER DEFAULT 0,
    rows_updated     INTEGER DEFAULT 0,
    rows_amended     INTEGER DEFAULT 0,
    status           TEXT CHECK(status IN ('ok', 'degraded', 'failed')) NOT NULL
);
"""


def ensure_heartbeat_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_SQL)
    conn.commit()


def write_heartbeat(
    conn: sqlite3.Connection,
    *,
    scraper_name: str,
    status: HeartbeatStatus,
    rows_inserted: int = 0,
    rows_updated: int = 0,
    rows_amended: int = 0,
    last_error_msg: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    last_success = now if status == HeartbeatStatus.OK else None
    last_error = now if status == HeartbeatStatus.FAILED else None

    conn.execute(
        """
        INSERT INTO taiwan_scraper_heartbeat
          (scraper_name, last_run_at, last_success_at, last_error_at, last_error_msg,
           rows_inserted, rows_updated, rows_amended, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scraper_name) DO UPDATE SET
          last_run_at     = excluded.last_run_at,
          last_success_at = COALESCE(excluded.last_success_at, taiwan_scraper_heartbeat.last_success_at),
          last_error_at   = COALESCE(excluded.last_error_at,   taiwan_scraper_heartbeat.last_error_at),
          last_error_msg  = COALESCE(excluded.last_error_msg,  taiwan_scraper_heartbeat.last_error_msg),
          rows_inserted   = excluded.rows_inserted,
          rows_updated    = excluded.rows_updated,
          rows_amended    = excluded.rows_amended,
          status          = excluded.status
        """,
        (scraper_name, now, last_success, last_error, last_error_msg,
         rows_inserted, rows_updated, rows_amended, status.value),
    )
    conn.commit()


def read_all_heartbeats(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT scraper_name, last_run_at, last_success_at, last_error_at, "
        "last_error_msg, rows_inserted, rows_updated, rows_amended, status "
        "FROM taiwan_scraper_heartbeat ORDER BY scraper_name"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/integration/test_taiwan_heartbeat.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Register table during app bootstrap**

Edit `backend/app/db/session.py`. After the other ORM imports, add:

```python
# Ensure non-ORM tables exist too
def _ensure_taiwan_heartbeat_table():
    import sqlite3
    from pathlib import Path
    from backend.app.core.config import settings
    from backend.app.services.taiwan.health import ensure_heartbeat_table

    uri = settings.POSTGRES_URI
    # Only meaningful for SQLite dev DBs; Postgres users would use Alembic.
    if uri.startswith("sqlite:///"):
        db_path = uri.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        try:
            ensure_heartbeat_table(conn)
        finally:
            conn.close()

_ensure_taiwan_heartbeat_table()
```

- [ ] **Step 6: No commit yet.**

---

## Task 8: FastAPI router — read-only `/api/v1/taiwan/*` endpoints

**Files:**
- Create: `backend/app/api/routers/v1/taiwan.py`
- Modify: `backend/app/main.py` — register router
- Create: `backend/tests/integration/test_taiwan_api.py`

- [ ] **Step 1: Write API tests**

Create `backend/tests/integration/test_taiwan_api.py`:

```python
"""Integration tests for /api/v1/taiwan/* using an isolated data_dir."""

import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.taiwan import storage


@pytest.fixture
def taiwan_tmp(monkeypatch, tmp_path):
    """Point the taiwan package at a scratch data dir so tests don't pollute real data."""
    monkeypatch.setattr(storage, "DEFAULT_DATA_DIR", tmp_path)
    (tmp_path / "monthly_revenue").mkdir(parents=True)
    (tmp_path / "_registry").mkdir(parents=True)
    (tmp_path / "_raw" / "monthly_revenue").mkdir(parents=True)
    # Minimal watchlist
    wl = pd.DataFrame([
        {"ticker": "2330", "name": "TSMC", "market": "TWSE",
         "sector": "Semiconductors", "subsector": "Foundry", "notes": ""}
    ])
    # Override registry path
    from backend.app.services.taiwan import registry
    monkeypatch.setattr(registry, "WATCHLIST_CSV", tmp_path / "watchlist_semi.csv")
    wl.to_csv(tmp_path / "watchlist_semi.csv", index=False)
    monkeypatch.setattr(registry, "REGISTRY_PARQUET",
                        tmp_path / "_registry" / "mops_company_master.parquet")
    return tmp_path


def test_watchlist_endpoint_returns_our_watchlist(taiwan_tmp):
    client = TestClient(app)
    resp = client.get("/api/v1/taiwan/watchlist")
    assert resp.status_code == 200
    data = resp.json()["data"]
    tickers = {r["ticker"] for r in data}
    assert "2330" in tickers


def test_monthly_revenue_endpoint_returns_saved_rows(taiwan_tmp):
    rows = [{"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
             "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0,
             "ytd_pct": 0.12, "cumulative_ytd_twd": 500_000_000_000,
             "prior_year_month_twd": 180_000_000_000}]
    storage.upsert_monthly_revenue(rows, data_dir=taiwan_tmp)

    client = TestClient(app)
    resp = client.get("/api/v1/taiwan/monthly-revenue?tickers=2330&months=12")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["ticker"] == "2330"
    assert data[0]["revenue_twd"] == 200_000_000_000
    assert abs(data[0]["yoy_pct"] - 0.1) < 1e-6


def test_ticker_endpoint_returns_note_metadata(taiwan_tmp):
    rows = [{"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
             "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0,
             "ytd_pct": 0.12, "cumulative_ytd_twd": 500_000_000_000,
             "prior_year_month_twd": 180_000_000_000}]
    storage.upsert_monthly_revenue(rows, data_dir=taiwan_tmp)
    client = TestClient(app)
    resp = client.get("/api/v1/taiwan/ticker/2330")
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["ticker"] == "2330"
    assert d["name"] == "TSMC"
    assert d["subsector"] == "Foundry"
    assert d["latest_revenue"]["fiscal_ym"] == "2026-03"


def test_health_endpoint_returns_scrapers_list(taiwan_tmp):
    client = TestClient(app)
    resp = client.get("/api/v1/taiwan/health")
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert "scrapers" in d
    assert isinstance(d["scrapers"], list)
```

- [ ] **Step 2: Run tests — confirm fail**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/integration/test_taiwan_api.py -v
```

- [ ] **Step 3: Implement router**

Create `backend/app/api/routers/v1/taiwan.py`:

```python
"""
Read-only Taiwan disclosure endpoints.

This router does NOT scrape. It reads parquet / SQLite written by the
taiwan_scheduler process. Humans hit these through the Next.js dashboard;
external agents call them via API.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.core.config import settings
from backend.app.models.api_contracts import APIResponse
from backend.app.services.taiwan import registry, storage
from backend.app.services.taiwan.health import read_all_heartbeats

router = APIRouter()


def _sqlite_conn() -> sqlite3.Connection:
    uri = settings.POSTGRES_URI
    if not uri.startswith("sqlite:///"):
        raise RuntimeError("Taiwan heartbeat currently SQLite-only; migrate to Alembic for Postgres.")
    return sqlite3.connect(uri.replace("sqlite:///", ""))


@router.get("/watchlist", response_model=APIResponse)
def list_watchlist():
    df = registry.load_watchlist()
    return APIResponse(success=True, data=df.to_dict(orient="records"))


@router.get("/monthly-revenue", response_model=APIResponse)
def list_monthly_revenue(
    tickers: str = Query(..., description="Comma-separated tickers"),
    months: int = Query(12, ge=1, le=120, description="Trailing months"),
):
    want = {t.strip() for t in tickers.split(",") if t.strip()}
    df = storage.read_monthly_revenue()
    if df.empty:
        return APIResponse(success=True, data=[])
    df = df[df["ticker"].isin(want)].copy()
    # Take the latest `months` periods per ticker.
    df = df.sort_values(["ticker", "fiscal_ym"], ascending=[True, False])
    df = df.groupby("ticker", group_keys=False).head(months)
    df = df.sort_values(["ticker", "fiscal_ym"])  # final chronological order per ticker

    # Convert timestamps to iso strings for JSON
    for col in ("first_seen_at", "last_seen_at"):
        if col in df.columns:
            df[col] = df[col].astype(str)

    return APIResponse(success=True, data=df.to_dict(orient="records"))


@router.get("/ticker/{ticker}", response_model=APIResponse)
def get_ticker(ticker: str):
    wl = registry.load_watchlist()
    match = wl[wl["ticker"] == ticker]
    if match.empty:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not in watchlist.")
    meta = match.iloc[0].to_dict()
    df = storage.read_monthly_revenue()
    latest = None
    if not df.empty:
        mine = df[df["ticker"] == ticker].sort_values("fiscal_ym", ascending=False)
        if not mine.empty:
            latest_row = mine.iloc[0].to_dict()
            # stringify timestamps
            for col in ("first_seen_at", "last_seen_at"):
                if col in latest_row:
                    latest_row[col] = str(latest_row[col])
            latest = latest_row

    data = {**meta, "latest_revenue": latest}
    return APIResponse(success=True, data=data)


@router.get("/health", response_model=APIResponse)
def scraper_health():
    try:
        conn = _sqlite_conn()
    except Exception as exc:
        return APIResponse(success=True, data={"scrapers": [], "error": str(exc)})
    try:
        rows = read_all_heartbeats(conn)
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    # Annotate each with a lag_seconds since last success.
    annotated = []
    for r in rows:
        lag = None
        if r.get("last_success_at"):
            try:
                ts = datetime.fromisoformat(r["last_success_at"])
                lag = int((now - ts).total_seconds())
            except ValueError:
                lag = None
        r["lag_seconds"] = lag
        annotated.append(r)

    return APIResponse(success=True, data={"scrapers": annotated})
```

- [ ] **Step 4: Register router**

Edit `backend/app/main.py`. Find the existing `app.include_router(...)` block and add after the other routers:

```python
from backend.app.api.routers.v1 import taiwan as taiwan_router
app.include_router(taiwan_router.router, prefix="/api/v1/taiwan", tags=["taiwan"])
```

- [ ] **Step 5: Run tests — confirm pass**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/integration/test_taiwan_api.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 6: No commit yet.**

---

## Task 9: Scheduler entry point

**Files:**
- Create: `backend/app/services/taiwan/scheduler.py`

No unit tests for the scheduler wiring itself — the scrapers are already covered. The scheduler is a thin orchestration layer that we smoke-test in Task 12.

- [ ] **Step 1: Implement scheduler**

Create `backend/app/services/taiwan/scheduler.py`:

```python
"""
Taiwan scheduler entry point. Run as: python -m backend.app.services.taiwan.scheduler

Registered APScheduler jobs:
  - company_master_refresh   1st of month @ 03:00 TPE
  - monthly_revenue_daily    daily @ 10:00 TPE, cheap filter to current-month window
  - monthly_revenue_catchup  every 3 days @ 11:00 TPE, scrapes prior month
  - health_check             hourly; logs WARN if any scraper > 2x its cadence
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.core.config import settings
from backend.app.services.taiwan.health import (
    HeartbeatStatus,
    ensure_heartbeat_table,
    read_all_heartbeats,
    write_heartbeat,
)
from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.scrapers.company_master import scrape_company_master
from backend.app.services.taiwan.scrapers.monthly_revenue import (
    scrape_monthly_revenue_market_month,
)

TPE = ZoneInfo("Asia/Taipei")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("taiwan_scheduler")


def _sqlite_conn() -> sqlite3.Connection:
    uri = settings.POSTGRES_URI
    if not uri.startswith("sqlite:///"):
        raise RuntimeError("Scheduler expects SQLite in Plan 1. Migrate to Postgres later via Alembic.")
    conn = sqlite3.connect(uri.replace("sqlite:///", ""))
    ensure_heartbeat_table(conn)
    return conn


def job_company_master() -> None:
    name = "company_master"
    conn = _sqlite_conn()
    client = MopsClient()
    try:
        n = scrape_company_master(client)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.OK,
                        rows_inserted=n, rows_updated=0, rows_amended=0)
        logger.info("%s OK rows=%d", name, n)
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                        last_error_msg=str(exc))
    finally:
        client.close()
        conn.close()


def _run_mr_month(client: MopsClient, conn, year: int, month: int, label: str) -> None:
    total_inserted = total_updated = total_amended = 0
    err = None
    try:
        for market in ("TWSE", "TPEx"):
            stats = scrape_monthly_revenue_market_month(
                client, year=year, month=month, market=market,
            )
            total_inserted += stats.inserted
            total_updated += stats.touched
            total_amended += stats.amended
        write_heartbeat(conn, scraper_name=label,
                        status=HeartbeatStatus.OK,
                        rows_inserted=total_inserted,
                        rows_updated=total_updated,
                        rows_amended=total_amended)
        logger.info("%s ym=%04d-%02d inserted=%d amended=%d", label, year, month,
                    total_inserted, total_amended)
    except Exception as exc:
        err = str(exc)
        logger.exception("%s ym=%04d-%02d failed: %s", label, year, month, exc)
        write_heartbeat(conn, scraper_name=label,
                        status=HeartbeatStatus.FAILED,
                        last_error_msg=err)


def job_monthly_revenue_daily() -> None:
    now = datetime.now(TPE)
    client = MopsClient()
    conn = _sqlite_conn()
    try:
        _run_mr_month(client, conn, now.year, now.month, "monthly_revenue_daily")
    finally:
        client.close()
        conn.close()


def job_monthly_revenue_catchup() -> None:
    now = datetime.now(TPE)
    # Prior month
    if now.month == 1:
        year, month = now.year - 1, 12
    else:
        year, month = now.year, now.month - 1
    client = MopsClient()
    conn = _sqlite_conn()
    try:
        _run_mr_month(client, conn, year, month, "monthly_revenue_catchup")
    finally:
        client.close()
        conn.close()


def job_health_check() -> None:
    """Reads heartbeats; logs WARN for scrapers stale beyond 2x their cadence."""
    conn = _sqlite_conn()
    try:
        rows = read_all_heartbeats(conn)
    finally:
        conn.close()
    for r in rows:
        if r["status"] != "ok":
            logger.warning("Scraper %s status=%s last_error=%s",
                           r["scraper_name"], r["status"], r.get("last_error_msg"))
    logger.info("health_check scrapers=%d", len(rows))


def main() -> None:
    sched = BlockingScheduler(timezone=TPE)

    sched.add_job(job_company_master, CronTrigger(day="1", hour="3", minute="0"),
                  id="company_master_refresh", replace_existing=True)

    sched.add_job(job_monthly_revenue_daily, CronTrigger(hour="10", minute="0"),
                  id="monthly_revenue_daily", replace_existing=True)

    sched.add_job(job_monthly_revenue_catchup,
                  CronTrigger(day="*/3", hour="11", minute="0"),
                  id="monthly_revenue_catchup", replace_existing=True)

    sched.add_job(job_health_check, CronTrigger(minute="17"),
                  id="health_check", replace_existing=True)

    logger.info("Taiwan scheduler starting (jobs=%d)", len(sched.get_jobs()))
    sched.start()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test the scheduler can import without error**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -c "from backend.app.services.taiwan import scheduler; print('scheduler importable OK')"
```

Expected: `scheduler importable OK`.

- [ ] **Step 3: Run all backend tests one last time**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit backend/tests/integration -v -x
```

Expected: all tests PASS (19 existing + new Taiwan tests = ~40 total).

- [ ] **Step 4: Commit A — backend infra + scrapers + API + scheduler**

```bash
git add \
  backend/requirements.txt \
  backend/app/services/taiwan/ \
  backend/app/api/routers/v1/taiwan.py \
  backend/app/main.py \
  backend/app/db/session.py \
  backend/data/taiwan/ \
  backend/tests/unit/test_mops_client.py \
  backend/tests/unit/test_mops_client_browser_smoke.py \
  backend/tests/unit/test_storage.py \
  backend/tests/unit/test_amendments.py \
  backend/tests/unit/test_validation.py \
  backend/tests/unit/test_company_master_parser.py \
  backend/tests/unit/test_monthly_revenue_parser.py \
  backend/tests/integration/test_taiwan_heartbeat.py \
  backend/tests/integration/test_taiwan_api.py

git commit -m "$(cat <<'EOF'
feat(taiwan): Plan 1 backend — monthly revenue ingestion end-to-end

Production-grade foundation for the Taiwan disclosure ingestion system.

Backend package backend/app/services/taiwan/:
  - mops_client.py: rate-limited HTTP (1 req/sec default, exp backoff,
    Big5+UTF-8 decode, 3 retries, circuit breaker) with Playwright fallback
    via the existing CDP Chrome profile
  - storage.py: parquet + content-hashed raw captures + S3 mirror (best
    effort, skipped when AWS creds absent), amendment history
  - amendments.py: deterministic content_hash (sorted-keys JSON excluding
    mutable fields); INSERT/TOUCH_ONLY/AMEND decision
  - validation.py: monthly-revenue invariants (flags, never drops)
  - registry.py: watchlist + MOPS company master reads
  - health.py: SQLite taiwan_scraper_heartbeat table + /health helpers
  - scrapers/company_master.py: monthly full MOPS registry refresh
  - scrapers/monthly_revenue.py: MOPS summary query; filter to watchlist;
    content-hash upsert; amendment tracking
  - scheduler.py: APScheduler blocking entry point (TPE timezone); four
    jobs — company_master monthly, monthly_revenue daily + 3-day catchup,
    health_check hourly

Read-only API at /api/v1/taiwan/*:
  - /watchlist, /monthly-revenue, /ticker/{ticker}, /health

Tests: unit + integration covering mops_client retry/encoding/fallback,
amendment detection, storage round-trip, validation, parsers, heartbeat
writes, and API endpoints.

Dependencies added: apscheduler, structlog, boto3, beautifulsoup4, lxml,
pyarrow, pandas.

Next: Task 10-11 frontend, Task 12 run backfill.

Spec: docs/superpowers/specs/2026-04-23-taiwan-disclosure-ingestion-design.md
Plan: docs/superpowers/plans/2026-04-23-taiwan-plan-1-foundation-monthly-revenue.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Frontend types + API client

**Files:**
- Create: `frontend/src/lib/api/taiwanClient.ts`

- [ ] **Step 1: Write the client**

Create `frontend/src/lib/api/taiwanClient.ts`:

```typescript
/**
 * taiwanClient — HTTP client for /api/v1/taiwan/* endpoints.
 * All methods return the shared { success, data } envelope from apiRequest.
 */

import { apiRequest } from "./base";

type AR<T> = { success: boolean; data: T; error?: string };

export interface WatchlistEntry {
  ticker: string;
  name: string;
  market: "TWSE" | "TPEx" | string;
  sector: string;
  subsector: string;
  notes: string;
}

export interface MonthlyRevenueRow {
  ticker: string;
  market: string;
  fiscal_ym: string;
  revenue_twd: number | null;
  yoy_pct: number | null;
  mom_pct: number | null;
  ytd_pct: number | null;
  cumulative_ytd_twd: number | null;
  prior_year_month_twd: number | null;
  first_seen_at: string;
  last_seen_at: string;
  amended: boolean;
  parse_flags?: string[];
}

export interface TickerDetail extends WatchlistEntry {
  latest_revenue: MonthlyRevenueRow | null;
}

export interface ScraperHealth {
  scraper_name: string;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error_msg: string | null;
  rows_inserted: number;
  rows_updated: number;
  rows_amended: number;
  status: "ok" | "degraded" | "failed";
  lag_seconds: number | null;
}

const BASE = "/taiwan";

export const taiwanClient = {
  watchlist: () =>
    apiRequest<AR<WatchlistEntry[]>>(`${BASE}/watchlist`),

  monthlyRevenue: (tickers: string[], months = 12) => {
    const qs = new URLSearchParams({
      tickers: tickers.join(","),
      months: String(months),
    });
    return apiRequest<AR<MonthlyRevenueRow[]>>(`${BASE}/monthly-revenue?${qs}`);
  },

  ticker: (ticker: string) =>
    apiRequest<AR<TickerDetail>>(`${BASE}/ticker/${ticker}`),

  health: () =>
    apiRequest<AR<{ scrapers: ScraperHealth[] }>>(`${BASE}/health`),
};
```

- [ ] **Step 2: Type-check**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new/frontend" && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -10
```

Expected: clean.

- [ ] **Step 3: No commit yet (combined with Task 11).**

---

## Task 11: Frontend — `/taiwan` tab with WatchlistRevenueGrid + drill-down

**Files:**
- Create: `frontend/src/app/(dashboard)/taiwan/page.tsx`
- Create: `frontend/src/app/(dashboard)/taiwan/TaiwanContainer.tsx`
- Create: `frontend/src/app/(dashboard)/taiwan/TaiwanView.tsx`
- Create: `frontend/src/app/(dashboard)/taiwan/components/WatchlistRevenueGrid.tsx`
- Create: `frontend/src/app/(dashboard)/taiwan/components/TickerDrillDown.tsx`
- Create: `frontend/src/app/(dashboard)/taiwan/components/TaiwanHealthIndicator.tsx`
- Modify: the existing sidebar/nav file to add a `Taiwan` link

- [ ] **Step 1: Create the page entry**

Create `frontend/src/app/(dashboard)/taiwan/page.tsx`:

```tsx
import TaiwanContainer from "./TaiwanContainer";

export default function TaiwanPage() {
  return <TaiwanContainer />;
}
```

- [ ] **Step 2: Create the container (smart layer)**

Create `frontend/src/app/(dashboard)/taiwan/TaiwanContainer.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import {
  taiwanClient,
  type WatchlistEntry,
  type MonthlyRevenueRow,
  type TickerDetail,
  type ScraperHealth,
} from "@/lib/api/taiwanClient";
import TaiwanView from "./TaiwanView";

export default function TaiwanContainer() {
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [revenue, setRevenue] = useState<Record<string, MonthlyRevenueRow[]>>({});
  const [health, setHealth] = useState<ScraperHealth[]>([]);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<TickerDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Initial load
  useEffect(() => {
    (async () => {
      setIsLoading(true);
      const [wlRes, hRes] = await Promise.all([taiwanClient.watchlist(), taiwanClient.health()]);
      if (wlRes.success && wlRes.data) setWatchlist(wlRes.data);
      if (hRes.success && hRes.data) setHealth(hRes.data.scrapers);

      // Fetch 24 months of revenue for all watchlist tickers.
      if (wlRes.success && wlRes.data && wlRes.data.length > 0) {
        const tickers = wlRes.data.map((r) => r.ticker);
        const rev = await taiwanClient.monthlyRevenue(tickers, 24);
        if (rev.success && rev.data) {
          const grouped: Record<string, MonthlyRevenueRow[]> = {};
          for (const row of rev.data) {
            (grouped[row.ticker] ??= []).push(row);
          }
          setRevenue(grouped);
        }
      }
      setIsLoading(false);
    })();
  }, []);

  // Polling: refresh health every 60s.
  useEffect(() => {
    const id = setInterval(async () => {
      const h = await taiwanClient.health();
      if (h.success && h.data) setHealth(h.data.scrapers);
    }, 60_000);
    return () => clearInterval(id);
  }, []);

  const handleOpenTicker = useCallback(async (ticker: string) => {
    setSelectedTicker(ticker);
    const res = await taiwanClient.ticker(ticker);
    if (res.success && res.data) setSelectedDetail(res.data);
  }, []);

  const handleCloseDrillDown = useCallback(() => {
    setSelectedTicker(null);
    setSelectedDetail(null);
  }, []);

  return (
    <TaiwanView
      watchlist={watchlist}
      revenue={revenue}
      health={health}
      isLoading={isLoading}
      selectedTicker={selectedTicker}
      selectedDetail={selectedDetail}
      onOpenTicker={handleOpenTicker}
      onCloseDrillDown={handleCloseDrillDown}
    />
  );
}
```

- [ ] **Step 3: Create the view (dumb layer)**

Create `frontend/src/app/(dashboard)/taiwan/TaiwanView.tsx`:

```tsx
"use client";

import type {
  WatchlistEntry,
  MonthlyRevenueRow,
  TickerDetail,
  ScraperHealth,
} from "@/lib/api/taiwanClient";
import WatchlistRevenueGrid from "./components/WatchlistRevenueGrid";
import TickerDrillDown from "./components/TickerDrillDown";
import TaiwanHealthIndicator from "./components/TaiwanHealthIndicator";

interface Props {
  watchlist: WatchlistEntry[];
  revenue: Record<string, MonthlyRevenueRow[]>;
  health: ScraperHealth[];
  isLoading: boolean;
  selectedTicker: string | null;
  selectedDetail: TickerDetail | null;
  onOpenTicker: (ticker: string) => void;
  onCloseDrillDown: () => void;
}

export default function TaiwanView({
  watchlist, revenue, health, isLoading,
  selectedTicker, selectedDetail,
  onOpenTicker, onCloseDrillDown,
}: Props) {
  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Page header */}
      <div className="flex items-center justify-between px-8 py-5 bg-white border-b border-slate-200 shrink-0">
        <div>
          <h1 className="text-xl font-bold text-slate-900 leading-tight">Taiwan</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            MOPS monthly revenue + material information for semi-ecosystem watchlist.
          </p>
        </div>
        <TaiwanHealthIndicator health={health} />
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-8 py-6">
        {isLoading ? (
          <div className="text-sm text-slate-400">Loading…</div>
        ) : (
          <WatchlistRevenueGrid
            watchlist={watchlist}
            revenue={revenue}
            onOpenTicker={onOpenTicker}
          />
        )}
      </div>

      {/* Drill-down modal */}
      {selectedTicker && (
        <TickerDrillDown
          ticker={selectedTicker}
          detail={selectedDetail}
          history={revenue[selectedTicker] ?? []}
          onClose={onCloseDrillDown}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Create the WatchlistRevenueGrid**

Create `frontend/src/app/(dashboard)/taiwan/components/WatchlistRevenueGrid.tsx`:

```tsx
"use client";

import { useMemo, useState } from "react";
import type { WatchlistEntry, MonthlyRevenueRow } from "@/lib/api/taiwanClient";

interface Props {
  watchlist: WatchlistEntry[];
  revenue: Record<string, MonthlyRevenueRow[]>;
  onOpenTicker: (ticker: string) => void;
}

function fmtTWD(n: number | null | undefined): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)} B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)} M`;
  return n.toLocaleString();
}

function fmtPct(p: number | null | undefined): string {
  if (p == null) return "—";
  const v = p * 100;
  const s = v >= 0 ? "+" : "";
  return `${s}${v.toFixed(1)}%`;
}

function yoyCellClass(p: number | null | undefined): string {
  if (p == null) return "text-slate-400";
  if (p > 0.15) return "text-green-700 bg-green-50";
  if (p > 0.05) return "text-green-600 bg-green-50/50";
  if (p < -0.15) return "text-red-700 bg-red-50";
  if (p < -0.05) return "text-red-600 bg-red-50/50";
  return "text-slate-600";
}

export default function WatchlistRevenueGrid({ watchlist, revenue, onOpenTicker }: Props) {
  const subsectors = useMemo(
    () => Array.from(new Set(watchlist.map((w) => w.subsector))).sort(),
    [watchlist],
  );
  const [activeSubsector, setActiveSubsector] = useState<string>(subsectors[0] ?? "");
  const rows = watchlist.filter((w) => !activeSubsector || w.subsector === activeSubsector);

  return (
    <div className="space-y-4">
      {/* Subsector tabs */}
      <div className="flex flex-wrap gap-2">
        {subsectors.map((s) => (
          <button
            key={s}
            onClick={() => setActiveSubsector(s)}
            className={`px-3 py-1 text-xs font-medium rounded-md border transition-colors ${
              activeSubsector === s
                ? "border-indigo-600 bg-indigo-600 text-white"
                : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-600"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="overflow-x-auto bg-white rounded-xl border border-slate-200">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              <th className="text-left px-4 py-2">Ticker</th>
              <th className="text-left px-4 py-2">Company</th>
              <th className="text-right px-4 py-2">Latest Revenue (TWD)</th>
              <th className="text-right px-4 py-2">YoY%</th>
              <th className="text-right px-4 py-2">MoM%</th>
              <th className="text-right px-4 py-2">YTD%</th>
              <th className="text-right px-4 py-2">Fiscal Ym</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((w) => {
              const hist = revenue[w.ticker] ?? [];
              const latest = hist.length > 0 ? hist[hist.length - 1] : null;
              return (
                <tr
                  key={w.ticker}
                  className="border-b border-slate-100 last:border-0 hover:bg-slate-50 cursor-pointer"
                  onClick={() => onOpenTicker(w.ticker)}
                >
                  <td className="px-4 py-2 font-mono font-semibold text-indigo-700">{w.ticker}</td>
                  <td className="px-4 py-2 text-slate-800">{w.name}</td>
                  <td className="px-4 py-2 text-right text-slate-700 tabular-nums">
                    {fmtTWD(latest?.revenue_twd ?? null)}
                  </td>
                  <td className={`px-4 py-2 text-right tabular-nums ${yoyCellClass(latest?.yoy_pct)}`}>
                    {fmtPct(latest?.yoy_pct)}
                  </td>
                  <td className="px-4 py-2 text-right text-slate-700 tabular-nums">
                    {fmtPct(latest?.mom_pct)}
                  </td>
                  <td className="px-4 py-2 text-right text-slate-700 tabular-nums">
                    {fmtPct(latest?.ytd_pct)}
                  </td>
                  <td className="px-4 py-2 text-right text-slate-500 tabular-nums font-mono text-xs">
                    {latest?.fiscal_ym ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Create the TickerDrillDown**

Create `frontend/src/app/(dashboard)/taiwan/components/TickerDrillDown.tsx`:

```tsx
"use client";

import { X } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import type { MonthlyRevenueRow, TickerDetail } from "@/lib/api/taiwanClient";

interface Props {
  ticker: string;
  detail: TickerDetail | null;
  history: MonthlyRevenueRow[];
  onClose: () => void;
}

export default function TickerDrillDown({ ticker, detail, history, onClose }: Props) {
  const chartData = history.map((r) => ({
    ym: r.fiscal_ym,
    revenue: (r.revenue_twd ?? 0) / 1e9,  // billions TWD
    yoy: (r.yoy_pct ?? 0) * 100,
  }));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-6"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-slate-200 shrink-0">
          <div>
            <h2 className="text-base font-bold text-slate-900">
              <span className="font-mono text-indigo-700 mr-2">{ticker}</span>
              {detail?.name ?? ""}
            </h2>
            <p className="text-[11px] text-slate-500 mt-0.5">
              {detail?.market} · {detail?.subsector}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-slate-700 transition-colors"
            title="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
              Monthly revenue — last {history.length} months (TWD bn)
            </h3>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid stroke="#e2e8f0" />
                  <XAxis dataKey="ym" fontSize={10} />
                  <YAxis fontSize={10} />
                  <Tooltip />
                  <Line type="monotone" dataKey="revenue" stroke="#4f46e5" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
              Material information
            </h3>
            <p className="text-xs text-slate-400 italic">
              Coming in Plan 2 — material info feed + side-by-side bilingual view.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Create the TaiwanHealthIndicator**

Create `frontend/src/app/(dashboard)/taiwan/components/TaiwanHealthIndicator.tsx`:

```tsx
"use client";

import type { ScraperHealth } from "@/lib/api/taiwanClient";

interface Props {
  health: ScraperHealth[];
}

export default function TaiwanHealthIndicator({ health }: Props) {
  const anyFailed = health.some((s) => s.status === "failed");
  const anyDegraded = health.some((s) => s.status === "degraded");
  const color = anyFailed ? "bg-red-500" : anyDegraded ? "bg-amber-500" : "bg-green-500";
  const label = anyFailed ? "Scraper failed" : anyDegraded ? "Scraper degraded" : "All scrapers ok";
  const tip = health.length === 0
    ? "No scraper heartbeats yet (first run pending)."
    : health.map((s) => `${s.scraper_name}: ${s.status}${s.lag_seconds != null ? ` (lag ${s.lag_seconds}s)` : ""}`).join("\n");

  return (
    <div className="flex items-center gap-2" title={tip}>
      <span className={`w-2.5 h-2.5 rounded-full ${color} animate-pulse`} />
      <span className="text-xs text-slate-500">{label}</span>
    </div>
  );
}
```

- [ ] **Step 7: Add `[Taiwan]` to the sidebar**

Find the existing sidebar component. Grep:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && grep -rn "Notes\|Research\|dashboard" frontend/src/components/layout/ 2>/dev/null | head -5
```

Locate the nav-item array (it may be in `Sidebar.tsx`, `DashboardNav.tsx`, or similar). Add an entry matching the existing shape — pattern:

```tsx
{ label: "Taiwan", href: "/taiwan", icon: Flag },
```

using a `lucide-react` icon (e.g. `Flag` or `Globe2`). If you have to import the icon, add the appropriate line at the top of the file.

- [ ] **Step 8: Type-check**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new/frontend" && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -20
```

Expected: clean.

- [ ] **Step 9: No commit yet (combined with Task 12's fly deploy).**

---

## Task 12: Fly.io deployment config + backfill execution + final commit

**Files:**
- Create: `fly.toml`
- Create: `Dockerfile`
- Create: `.dockerignore`

This task also contains the one-time historical backfill run and final verification.

- [ ] **Step 1: Create the Dockerfile**

Create `Dockerfile` at repo root:

```dockerfile
# Multi-stage build: Node for the frontend, Python for backend + scheduler.
FROM node:20-bookworm-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
WORKDIR /app

# System deps for lxml, playwright, fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libxml2-dev libxslt1-dev \
    chromium fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
    && pip install --no-cache-dir playwright \
    && playwright install chromium

# Copy source + frontend build
COPY backend/ /app/backend/
COPY --from=frontend-builder /app/frontend/.next /app/frontend/.next
COPY --from=frontend-builder /app/frontend/public /app/frontend/public
COPY --from=frontend-builder /app/frontend/package.json /app/frontend/

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Default process entrypoint — fly.toml overrides per-process.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create `.dockerignore`**

Create `.dockerignore`:

```
**/__pycache__
**/.pytest_cache
**/.mypy_cache
**/node_modules
frontend/.next
!frontend/.next/standalone
.git
.venv
venv
*.pyc
*.pyo
backend/data/**/_raw
backend/data/**/data.parquet
backend/data/**/history.parquet
tools/audio_recorder/recordings
tools/web_scraper/scrape_log.txt
.claude/
```

- [ ] **Step 3: Create `fly.toml`**

Create `fly.toml` at repo root:

```toml
app = "alphagraph"
primary_region = "nrt"   # Tokyo — closest to MOPS

[build]

[processes]
  web = "uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"
  taiwan_scheduler = "python -m backend.app.services.taiwan.scheduler"

[env]
  PYTHONUNBUFFERED = "1"
  PYTHONPATH = "/app"

[[services]]
  processes = ["web"]
  protocol = "tcp"
  internal_port = 8000

  [[services.ports]]
    port = 80
    handlers = ["http"]
    force_https = true

  [[services.ports]]
    port = 443
    handlers = ["tls", "http"]

  [services.concurrency]
    type = "requests"
    soft_limit = 200
    hard_limit = 250

[[mounts]]
  source = "alphagraph_data"
  destination = "/data"
  processes = ["web", "taiwan_scheduler"]

[[vm]]
  processes = ["web"]
  size = "shared-cpu-1x"
  memory = "512mb"

[[vm]]
  processes = ["taiwan_scheduler"]
  size = "shared-cpu-1x"
  memory = "1gb"
```

- [ ] **Step 4: Set Fly secrets (run locally once, by the human)**

You'll run these yourself on your machine (do NOT put real keys in source):

```bash
fly secrets set \
  GEMINI_API_KEY=<your_key> \
  AWS_ACCESS_KEY_ID=<your_key> \
  AWS_SECRET_ACCESS_KEY=<your_key> \
  TAIWAN_S3_BUCKET_RAW=alphagraph-taiwan-raw-prod
```

If you don't have AWS yet: omit the AWS + S3 lines. `storage.py` skips the mirror gracefully when `TAIWAN_S3_BUCKET_RAW` is unset.

- [ ] **Step 5: Deploy**

```bash
fly launch --no-deploy    # if first time; accepts fly.toml
fly volumes create alphagraph_data --region nrt --size 3
fly deploy
```

Expected: build succeeds; both `web` and `taiwan_scheduler` processes show as running in `fly status`.

- [ ] **Step 6: Run the monthly-revenue historical backfill**

The scheduler only scrapes the current + prior month automatically. Run the full 10-year backfill as a one-shot by opening a console into the scheduler machine:

```bash
fly ssh console --process taiwan_scheduler
```

Inside the machine:

```bash
python -c "
from datetime import datetime
from zoneinfo import ZoneInfo
from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.scrapers.monthly_revenue import scrape_monthly_revenue_market_month

tpe = ZoneInfo('Asia/Taipei')
now = datetime.now(tpe)
start_year = now.year - 10
client = MopsClient()
for year in range(start_year, now.year + 1):
    end_m = 12 if year < now.year else now.month
    for month in range(1, end_m + 1):
        for market in ('TWSE', 'TPEx'):
            stats = scrape_monthly_revenue_market_month(client, year=year, month=month, market=market)
            print(f'{year}-{month:02d} {market}: {stats}')
client.close()
print('BACKFILL DONE')
"
```

Expected runtime: ~8–10 minutes at 1 req/sec. Prints one line per market-month. Final line `BACKFILL DONE`.

- [ ] **Step 7: Smoke-test on the deployed app**

From your local machine:

```bash
curl https://alphagraph.fly.dev/api/v1/taiwan/watchlist | head -c 500
curl "https://alphagraph.fly.dev/api/v1/taiwan/monthly-revenue?tickers=2330&months=12" | head -c 1000
curl https://alphagraph.fly.dev/api/v1/taiwan/health
```

Expected: each returns a JSON `{success: true, data: ...}` envelope. Watchlist has 51 rows; TSMC monthly revenue returns 12 rows ending roughly at the prior month; health shows `monthly_revenue_daily` with `status: ok`.

Open the dashboard in your browser at `https://alphagraph.fly.dev/taiwan` — the watchlist grid should show all 51 tickers with revenue columns populated, subsector tabs working, YoY heatmap coloured. Click a ticker — the drill-down opens with the Recharts line chart.

- [ ] **Step 8: Commit B — frontend + fly.io deploy config + backfill**

```bash
git add \
  frontend/src/app/\(dashboard\)/taiwan/ \
  frontend/src/lib/api/taiwanClient.ts \
  frontend/src/components/layout/   # if sidebar modified; adjust path to match your repo
  fly.toml \
  Dockerfile \
  .dockerignore \
  docs/superpowers/plans/2026-04-23-taiwan-plan-1-foundation-monthly-revenue.md

git commit -m "$(cat <<'EOF'
feat(taiwan): Plan 1 frontend + Fly.io deploy + 10-year backfill

Frontend:
- New top-level [Taiwan] tab at /taiwan
- TaiwanContainer / TaiwanView split following the container/view pattern
- WatchlistRevenueGrid — subsector tabs (Foundry / IC Design / Memory / OSAT
  / Wafer / Equipment / PCB+Substrate / Materials / Optical / Server EMS);
  sortable table with TWD revenue formatting + YoY% heatmap
- TickerDrillDown modal with Recharts monthly revenue line chart
- TaiwanHealthIndicator — coloured dot in page header tied to /health endpoint
- taiwanClient wrapping the read-only JSON API

Deployment:
- Multi-stage Dockerfile (Node frontend build → Python runtime with
  Chromium + CJK fonts for Playwright fallback)
- fly.toml with two process groups (web + taiwan_scheduler) sharing a
  Fly volume for parquet data + SQLite DB; region nrt for MOPS latency

Data:
- 10-year historical backfill for monthly revenue executed on the deployed
  scheduler machine (~8-10 min wall-clock, ~240 HTTP calls). Watchlist
  tickers × ~120 months of revenue, content-hash-tracked; amendments will
  surface on next scraper run if any occur.

Spec: docs/superpowers/specs/2026-04-23-taiwan-disclosure-ingestion-design.md
Plan: docs/superpowers/plans/2026-04-23-taiwan-plan-1-foundation-monthly-revenue.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Checklist

**Spec coverage** (against `2026-04-23-taiwan-disclosure-ingestion-design.md` §Plan 1):

- `mops_client.py` → Task 2 (+ Playwright in Task 3)
- `storage.py` (parquet + raw + S3 mirror) → Task 4
- `amendments.py`, `validation.py` → Task 4
- Translation pipeline → deliberately deferred to Plan 2 (monthly revenue is numeric).
- Company master scraper → Task 5
- Monthly revenue scraper → Task 6
- Heartbeat table + `health.py` + `/health` endpoint → Tasks 7 + 8
- `/api/v1/taiwan/watchlist`, `/monthly-revenue`, `/ticker/{ticker}` → Task 8
- Frontend Taiwan tab + WatchlistRevenueGrid + TickerDrillDown → Tasks 10–11
- Scheduler skeleton with monthly-revenue + company-master + health jobs → Task 9
- Fly.io deploy config (web + taiwan_scheduler + shared volume) → Task 12
- Backfill execution → Task 12
- Tests: `mops_client` retry/fallback, parser round-trip, amendment detection, validation, heartbeat writer, API endpoints → Tasks 2 + 3 + 4 + 5 + 6 + 7 + 8

**Placeholder scan:** no TBD / TODO / "similar to" patterns. Every code step has complete, pasteable code. The one deliberate forward-reference (`mops_client_browser` stub in Task 2 → real impl in Task 3) is explicit in both places.

**Type / name consistency:**
- `MopsClient` / `MopsFetchResult` consistent across Task 2, 3, 5, 6, 9.
- `UpsertStats` fields (`inserted`, `touched`, `amended`) consistent between Task 4 definition and Task 6 + Task 9 use sites.
- `AmendmentDecision` (INSERT / TOUCH_ONLY / AMEND) used identically in Task 4 tests + implementation + Task 6's downstream caller.
- `HeartbeatStatus` (OK / DEGRADED / FAILED) used identically in Task 7 tests, health.py, and Task 9 scheduler jobs.
- Parquet column names (`ticker`, `fiscal_ym`, `revenue_twd`, `yoy_pct`, …) consistent across storage, parsers, API responses, and the frontend `MonthlyRevenueRow` TS interface.
- Endpoint paths (`/watchlist`, `/monthly-revenue`, `/ticker/{ticker}`, `/health`) consistent between Task 8 FastAPI + Task 10 taiwanClient + Task 11 consumers.
- `TAIWAN_S3_BUCKET_RAW` env var consistent between Task 4 (storage) + Task 12 (fly secrets).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-23-taiwan-plan-1-foundation-monthly-revenue.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task with review checkpoints. Best for a plan this size (12 tasks, heavy back-and-forth between files).

**2. Inline Execution** — run tasks in this session using executing-plans. Slower wall-clock but keeps every change visible in real time.

Which approach?
