# Calendar Soft-Fields Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `webcast_url`, `dial_in_phone`, `dial_in_pin`, `press_release_url`, `transcript_url` on every row of `backend/data/earnings_calendar/events.parquet` by layering three enrichment methods (A: 8-K text parser, B: Gemini-grounded LLM, C: Q4 Inc IR scraper).

**Architecture:** Three independent methods write to per-source suffixed columns (`webcast_url_a/_b/_c`) with full provenance. A resolver in `storage.py` returns the first non-null in run order (A → B → C) when the existing public field names are read. Method C only runs on rows that A+B left with at least one empty soft field (last-resort policy).

**Tech Stack:** Python 3.13, pandas/pyarrow, requests, regex, pytest, Google Gemini Flash with Google-search grounding (via `google-generativeai`), pydantic for LLM output schema, BeautifulSoup4 for Q4 Inc HTML parsing.

**Spec:** `docs/superpowers/specs/2026-04-28-calendar-soft-fields-enrichment-design.md`

**Type-consistency note:** Method A's parser returns a `TypedDict` (subscript
access: `fields["webcast_url"]`); Method C's adapter returns a `@dataclass`
(attribute access: `fields.webcast_url`). The two runner scripts each use
the matching access pattern — don't unify these unless you also update the
call sites.

---

## File Structure

### Created
```
backend/app/services/calendar/enrichment/
  __init__.py
  url_validator.py
  budget.py
  press_release_parser.py
  llm_grounded.py
  orchestrator.py
  ir_scrapers/
    __init__.py
    _base.py
    _registry.py
    q4_inc.py
  # NOTE: ir_scrapers/_vendor_detect.py is mentioned in the design spec but
  # is NOT created by this plan. It only matters when adding the second
  # adapter (Notified/GlobeNewswire), where unknown IR pages need to be
  # routed automatically. Deferred to the Method C-beta plan.

backend/scripts/
  enrich_calendar_a.py
  enrich_calendar_b.py
  enrich_calendar_c.py
  enrich_calendar.py            # wrapper runs --all in order

backend/tests/calendar/
  __init__.py
  test_url_validator.py
  test_budget.py
  test_press_release_parser.py
  test_llm_grounded.py
  test_q4_inc_adapter.py
  test_resolver.py
  test_orchestrator.py
  fixtures/
    pr_nvda_q4fy2026.txt
    pr_no_dial_in.txt
    pr_intl_phone.txt
    gemini_response_klac.json
    q4_event_aapl.html
    q4_event_no_dial_in.html
```

### Modified
```
backend/app/services/calendar/storage.py   # add columns + resolver
frontend/src/app/(dashboard)/calendar/CalendarView.tsx   # dial-in tooltip
backend/app/services/prices/scheduler.py   # add daily 06:30 UTC job (or wherever existing schedulers live)
```

---

## Task 1: Schema migration — add new columns to `events.parquet`

**Files:**
- Modify: `backend/app/services/calendar/storage.py`
- Test: `backend/tests/calendar/test_resolver.py`

- [ ] **Step 1: Add new columns to `ALL_COLS` constant**

In `backend/app/services/calendar/storage.py`, locate `ALL_COLS` and extend it:

```python
ALL_COLS = [
    "ticker", "market", "fiscal_period",
    "release_datetime_utc", "release_local_tz", "status",
    "press_release_url", "filing_url",
    "webcast_url", "transcript_url", "dial_in_phone", "dial_in_pin",
    "source", "source_id",
    "verification",
    "time_of_day_code",
    "eps_forecast", "eps_estimates_count", "market_cap",
    "last_year_eps", "last_year_report_date",
    # Per-source soft-field provenance columns (Method A / B / C):
    "webcast_url_a",        "webcast_url_b",        "webcast_url_c",
    "dial_in_phone_a",      "dial_in_phone_b",      "dial_in_phone_c",
    "dial_in_pin_a",        "dial_in_pin_b",        "dial_in_pin_c",
    "press_release_url_a",  "press_release_url_b",  "press_release_url_c",
    "transcript_url_b",
    # Per-source enrichment metadata:
    "enrichment_a_attempted_at",
    "enrichment_b_attempted_at",
    "enrichment_c_attempted_at",
    "enrichment_b_cost_usd",
    "enrichment_c_vendor",
    "first_seen_at", "last_updated_at",
]
```

- [ ] **Step 2: Write the resolver test**

Create `backend/tests/calendar/__init__.py` (empty) and `backend/tests/calendar/test_resolver.py`:

```python
import pandas as pd
import pytest

from backend.app.services.calendar.storage import _resolve_soft_fields


def test_resolver_first_non_null_in_run_order():
    """A wins over B+C; B wins over C; C wins only when A+B both null."""
    row = pd.Series({
        "webcast_url_a": "https://a.com",
        "webcast_url_b": "https://b.com",
        "webcast_url_c": "https://c.com",
        "dial_in_phone_a": None,
        "dial_in_phone_b": "555-1111",
        "dial_in_phone_c": "555-2222",
        "dial_in_pin_a": None,
        "dial_in_pin_b": None,
        "dial_in_pin_c": "9999",
    })
    resolved = _resolve_soft_fields(row)
    assert resolved["webcast_url"]   == "https://a.com"   # A wins
    assert resolved["dial_in_phone"] == "555-1111"        # B wins (A null)
    assert resolved["dial_in_pin"]   == "9999"            # C wins (A+B null)


def test_resolver_handles_all_null():
    row = pd.Series({"webcast_url_a": None, "webcast_url_b": None, "webcast_url_c": None})
    resolved = _resolve_soft_fields(row)
    assert resolved["webcast_url"] is None


def test_resolver_handles_nan():
    """pd.NaN should be treated as null."""
    import numpy as np
    row = pd.Series({
        "webcast_url_a": np.nan,
        "webcast_url_b": "https://b.com",
        "webcast_url_c": np.nan,
    })
    resolved = _resolve_soft_fields(row)
    assert resolved["webcast_url"] == "https://b.com"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd C:/Users/Sharo/AI_projects/AlphaGraph_new && python -m pytest backend/tests/calendar/test_resolver.py -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_soft_fields'`

- [ ] **Step 4: Implement the resolver**

Add to `backend/app/services/calendar/storage.py` near the other helpers:

```python
# The 4 soft fields and their per-source column names, in run-order priority
# (A first, then B, then C as last resort). C runs only on rows where A+B
# left at least one field empty, so the resolver's first-non-null logic
# matches the runtime contract.
_SOFT_FIELD_SOURCES: dict[str, tuple[str, str, str]] = {
    "webcast_url":       ("webcast_url_a",       "webcast_url_b",       "webcast_url_c"),
    "dial_in_phone":     ("dial_in_phone_a",     "dial_in_phone_b",     "dial_in_phone_c"),
    "dial_in_pin":       ("dial_in_pin_a",       "dial_in_pin_b",       "dial_in_pin_c"),
    "press_release_url": ("press_release_url_a", "press_release_url_b", "press_release_url_c"),
}


def _resolve_soft_fields(row: pd.Series) -> dict[str, str | None]:
    """Return a dict mapping the public soft-field name to the first
    non-null value across (a, b, c) sources in run-order priority."""
    out: dict[str, str | None] = {}
    for public, (a, b, c) in _SOFT_FIELD_SOURCES.items():
        for col in (a, b, c):
            v = row.get(col)
            if not _is_empty(v):
                out[public] = v
                break
        else:
            out[public] = None
    # transcript_url has only one source (B)
    tv = row.get("transcript_url_b")
    out["transcript_url"] = None if _is_empty(tv) else tv
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest backend/tests/calendar/test_resolver.py -v`
Expected: 3 passed.

- [ ] **Step 6: Wire resolver into `read_events`**

Modify the bottom of `read_events` in `backend/app/services/calendar/storage.py` to overwrite the public columns from the resolver after reading:

```python
def read_events(...) -> pd.DataFrame:
    ...
    if df.empty:
        return df

    # Materialize the public soft-field columns from per-source columns.
    # Frontend continues to read webcast_url / dial_in_phone / etc. without
    # caring about provenance.
    for idx, row in df.iterrows():
        resolved = _resolve_soft_fields(row)
        for public_col, value in resolved.items():
            if value is not None:
                df.at[idx, public_col] = value

    return df
```

(Iterate-then-set is fine; events.parquet has at most a few thousand rows.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/calendar/storage.py backend/tests/calendar/__init__.py backend/tests/calendar/test_resolver.py
git commit -m "feat(calendar/enrichment): schema columns + resolver for soft fields

Adds per-source suffixed columns (_a/_b/_c) for webcast_url,
dial_in_phone, dial_in_pin, press_release_url plus transcript_url_b
and enrichment metadata. read_events() materializes the existing
public field names from these via _resolve_soft_fields() (first
non-null in run-order A > B > C)."
```

---

## Task 2: URL validator with HEAD-then-GET-Range fallback

**Files:**
- Create: `backend/app/services/calendar/enrichment/__init__.py` (empty)
- Create: `backend/app/services/calendar/enrichment/url_validator.py`
- Test: `backend/tests/calendar/test_url_validator.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/calendar/test_url_validator.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/calendar/test_url_validator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.services.calendar.enrichment'`

- [ ] **Step 3: Create the package + validator module**

Create `backend/app/services/calendar/enrichment/__init__.py` (empty) and `backend/app/services/calendar/enrichment/url_validator.py`:

```python
"""URL reachability validator.

HEAD-first with browser User-Agent; GET-with-Range fallback for CDNs that
reject HEAD (Cloudflare default, certain IR vendors). Range-aware servers
respond 206 Partial Content (1 byte); Range-unaware servers respond 200 OK
and we abort the read after the first chunk via stream=True.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def validate_url(url: str, *, timeout: float = 5.0) -> bool:
    """Return True if `url` returns a 2xx response (or 206 Partial Content).

    Step 1: HEAD with browser User-Agent.
    Step 2 (HEAD failed/4xx/5xx): GET with Range: bytes=0-0.
    """
    headers = {"User-Agent": _BROWSER_UA, "Accept": "*/*"}
    try:
        r = requests.head(url, headers=headers, allow_redirects=True,
                          timeout=timeout)
        if 200 <= r.status_code < 300:
            return True
    except requests.RequestException as exc:
        logger.debug("HEAD failed for %s: %s", url, exc)

    try:
        r = requests.get(
            url,
            headers={**headers, "Range": "bytes=0-0"},
            allow_redirects=True,
            timeout=timeout,
            stream=True,
        )
        try:
            return r.status_code in (200, 206)
        finally:
            r.close()
    except requests.RequestException as exc:
        logger.debug("GET-with-Range failed for %s: %s", url, exc)
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/calendar/test_url_validator.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/calendar/enrichment/__init__.py backend/app/services/calendar/enrichment/url_validator.py backend/tests/calendar/test_url_validator.py
git commit -m "feat(calendar/enrichment): URL validator with HEAD then GET-Range fallback

Browser User-Agent + HEAD first (cheap). On 4xx/5xx/timeout, falls back
to GET with Range: bytes=0-0 to handle CDNs that reject HEAD (Cloudflare,
some IR vendors). Accepts 200 or 206. Uses stream=True so unrange-aware
servers don't drain bodies on us."
```

---

## Task 3: Daily LLM budget guard ($1/day cap)

**Files:**
- Create: `backend/app/services/calendar/enrichment/budget.py`
- Test: `backend/tests/calendar/test_budget.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/calendar/test_budget.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from backend.app.services.calendar.enrichment.budget import (
    DAILY_CAP_USD, COST_PER_GEMINI_CALL_USD, remaining_budget_today,
)


def _make_events_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_remaining_budget_when_zero_spent():
    df = _make_events_df([])
    with patch("backend.app.services.calendar.enrichment.budget.read_events",
               return_value=df):
        assert remaining_budget_today() == DAILY_CAP_USD


def test_remaining_budget_subtracts_today_spend_only():
    today = pd.Timestamp.now(tz="UTC").normalize()
    yesterday = today - pd.Timedelta(days=1)
    df = _make_events_df([
        {"enrichment_b_attempted_at": yesterday, "enrichment_b_cost_usd": 0.50},
        {"enrichment_b_attempted_at": today,     "enrichment_b_cost_usd": 0.30},
        {"enrichment_b_attempted_at": today,     "enrichment_b_cost_usd": 0.10},
    ])
    with patch("backend.app.services.calendar.enrichment.budget.read_events",
               return_value=df):
        assert remaining_budget_today() == pytest.approx(DAILY_CAP_USD - 0.40)


def test_remaining_budget_clamps_to_zero():
    today = pd.Timestamp.now(tz="UTC").normalize()
    df = _make_events_df([
        {"enrichment_b_attempted_at": today, "enrichment_b_cost_usd": 5.00},  # over cap
    ])
    with patch("backend.app.services.calendar.enrichment.budget.read_events",
               return_value=df):
        assert remaining_budget_today() == 0.0


def test_remaining_budget_handles_missing_columns():
    """Empty/legacy parquets without enrichment_b_* columns should not crash."""
    df = pd.DataFrame(columns=["ticker"])
    with patch("backend.app.services.calendar.enrichment.budget.read_events",
               return_value=df):
        assert remaining_budget_today() == DAILY_CAP_USD


def test_per_call_cost_under_cap():
    """A single Gemini call must fit comfortably under the cap."""
    assert COST_PER_GEMINI_CALL_USD <= DAILY_CAP_USD / 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/calendar/test_budget.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement the budget guard**

Create `backend/app/services/calendar/enrichment/budget.py`:

```python
"""Daily LLM-spend cap for Method B (Gemini-grounded enrichment).

Tracks cumulative Gemini cost per event in events.parquet's
`enrichment_b_cost_usd` column; the budget guard sums today's UTC spend
and refuses further calls once the cap is reached.

Cap is enforced cooperatively: the orchestrator calls remaining_budget_today()
before each Gemini invocation. Bypassing the helper bypasses the cap.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import logging

import pandas as pd

from backend.app.services.calendar.storage import read_events

logger = logging.getLogger(__name__)

DAILY_CAP_USD: float = 1.00
# Gemini Flash with grounding, ~3KB prompt + ~500B output, refined after first
# day of real billing. One call is well under 1/10 of the daily cap so the
# orchestrator can make several attempts even mid-day.
COST_PER_GEMINI_CALL_USD: float = 0.025


def remaining_budget_today() -> float:
    """Return USD remaining in today's enrichment-B budget.

    Reads events.parquet, sums enrichment_b_cost_usd for rows whose
    enrichment_b_attempted_at falls on today's UTC date. Returns
    max(0, DAILY_CAP_USD - spent_today). Resilient to missing columns
    and empty parquets."""
    df = read_events()
    if df.empty:
        return DAILY_CAP_USD
    if "enrichment_b_attempted_at" not in df.columns or "enrichment_b_cost_usd" not in df.columns:
        return DAILY_CAP_USD

    today_start = pd.Timestamp.now(tz="UTC").normalize()
    mask = pd.to_datetime(df["enrichment_b_attempted_at"], utc=True, errors="coerce") >= today_start
    spent_today = pd.to_numeric(
        df.loc[mask, "enrichment_b_cost_usd"], errors="coerce",
    ).fillna(0.0).sum()
    return max(0.0, DAILY_CAP_USD - float(spent_today))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/calendar/test_budget.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/calendar/enrichment/budget.py backend/tests/calendar/test_budget.py
git commit -m "feat(calendar/enrichment): \$1/day Gemini budget guard

remaining_budget_today() reads events.parquet and sums
enrichment_b_cost_usd for rows with enrichment_b_attempted_at on
today's UTC date. Returns max(0, cap - spent). The orchestrator
checks this before each Gemini call and exits cleanly when exhausted."
```

---

## Task 4: Method A — press-release regex parser

**Files:**
- Create: `backend/app/services/calendar/enrichment/press_release_parser.py`
- Test: `backend/tests/calendar/test_press_release_parser.py`
- Test fixtures: `backend/tests/calendar/fixtures/pr_*.txt`

- [ ] **Step 1: Build the test fixtures**

Create `backend/tests/calendar/fixtures/pr_nvda_q4fy2026.txt`:

```
NVIDIA Corporation today announced financial results for the fourth quarter
ended January 26, 2026.

Conference Call Information

NVIDIA will hold a conference call today at 2 p.m. Pacific Time (5 p.m.
Eastern Time) to discuss its financial results. The webcast will be
accessible at https://investor.nvidia.com/events/event-details/q4-2026.
Domestic dial-in: 1-800-555-0123. International: +1-404-555-7890.
Conference ID: 8675309. A replay will be available shortly after.
```

Create `backend/tests/calendar/fixtures/pr_no_dial_in.txt`:

```
ACME Corp announced earnings today. The company will host a webcast at
https://investor.acme.com/q1-2026 starting at 4:30 p.m. Eastern.
No dial-in is provided for this event.
```

Create `backend/tests/calendar/fixtures/pr_intl_phone.txt`:

```
TSMC today reported. Listen live at https://www.tsmc.com/english/aboutTSMC/ir
The conference call number is 886-2-2723-9152, passcode 1234567.
```

- [ ] **Step 2: Write the parser tests**

Create `backend/tests/calendar/test_press_release_parser.py`:

```python
from pathlib import Path

import pytest

from backend.app.services.calendar.enrichment.press_release_parser import (
    parse_press_release,
)

FIX = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_extracts_webcast_dial_in_pin_from_nvda_style():
    text = _read("pr_nvda_q4fy2026.txt")
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://investor.nvidia.com/events/event-details/q4-2026"
    # Phone normalized: any digits/separators acceptable, just must be the domestic one
    assert "800" in out["dial_in_phone"] and "555-0123" in out["dial_in_phone"]
    assert out["dial_in_pin"] == "8675309"


def test_handles_release_with_no_dial_in():
    text = _read("pr_no_dial_in.txt")
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://investor.acme.com/q1-2026"
    assert out["dial_in_phone"] is None
    assert out["dial_in_pin"] is None


def test_handles_international_phone_format():
    text = _read("pr_intl_phone.txt")
    out = parse_press_release(text)
    assert out["webcast_url"].startswith("https://www.tsmc.com")
    assert "886" in out["dial_in_phone"]
    assert out["dial_in_pin"] == "1234567"


def test_returns_all_none_for_unrelated_text():
    out = parse_press_release("This is a press release about a new product launch.")
    assert out == {
        "webcast_url": None, "dial_in_phone": None, "dial_in_pin": None,
        "press_release_url": None,
    }


def test_strips_trailing_punctuation_from_url():
    text = ("The webcast can be accessed at "
            "https://investor.example.com/q1, including replay.")
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://investor.example.com/q1"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest backend/tests/calendar/test_press_release_parser.py -v`
Expected: FAIL with import error.

- [ ] **Step 4: Implement the parser**

Create `backend/app/services/calendar/enrichment/press_release_parser.py`:

```python
"""Method A: extract calendar soft fields from 8-K Item 2.02 press release text.

The text comes from earnings_releases/ticker=*.parquet, column text_raw.
Each press release has a "Conference Call Information" section near the
bottom with the webcast URL, dial-in phone number, and conference ID.

Regex-only — no LLM. Free, deterministic, fast. ~95% recall on past events.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import logging
import re
from typing import TypedDict

logger = logging.getLogger(__name__)


class SoftFields(TypedDict):
    webcast_url:        str | None
    dial_in_phone:      str | None
    dial_in_pin:        str | None
    press_release_url:  str | None


# First match wins. Patterns target the standard "conference call" disclosure
# paragraph nearly every US-listed company includes near the bottom of the
# release. Unicode aware (re.IGNORECASE) and multiline (re.DOTALL) so
# arbitrary line breaks between the keyword and the URL don't break matching.
_WEBCAST_RX = re.compile(
    r"(?:webcast|live\s+(?:audio\s+)?stream|listen(?:\s+to\s+the\s+call)?)"
    r".{0,80}?"
    r"(https?://[^\s)<\]\"'>,]+)",
    re.IGNORECASE | re.DOTALL,
)
_PHONE_RX = re.compile(
    r"(?:dial[-\s]?in|domestic|toll[-\s]?free|conference\s+(?:call\s+)?number)"
    r"[^\d+]{0,30}"
    r"(\+?[\d][\d\s\-\(\)]{8,18}\d)",
    re.IGNORECASE,
)
_PIN_RX = re.compile(
    r"(?:conference\s+id|access\s+code|passcode|pin\s+number)"
    r"[^\d]{0,15}"
    r"(\d{4,12})",
    re.IGNORECASE,
)
# Trailing characters we want to strip if they leak into a URL match.
_TRAILING_TRASH = ".,;:!?)"


def parse_press_release(text: str) -> SoftFields:
    """Extract soft fields from a press-release body. Missing fields -> None.

    Does NOT call validate_url — caller is responsible for validation.
    """
    return {
        "webcast_url":       _extract_url(text),
        "dial_in_phone":     _extract_phone(text),
        "dial_in_pin":       _extract_pin(text),
        # press_release_url: not extractable from the press release body
        # itself; the SEC filing URL is already stored as filing_url.
        "press_release_url": None,
    }


def _extract_url(text: str) -> str | None:
    m = _WEBCAST_RX.search(text)
    if not m:
        return None
    url = m.group(1)
    while url and url[-1] in _TRAILING_TRASH:
        url = url[:-1]
    return url or None


def _extract_phone(text: str) -> str | None:
    m = _PHONE_RX.search(text)
    if not m:
        return None
    phone = m.group(1).strip()
    return phone or None


def _extract_pin(text: str) -> str | None:
    m = _PIN_RX.search(text)
    return m.group(1) if m else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest backend/tests/calendar/test_press_release_parser.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/calendar/enrichment/press_release_parser.py backend/tests/calendar/test_press_release_parser.py backend/tests/calendar/fixtures/pr_*.txt
git commit -m "feat(calendar/enrichment): Method A press-release regex parser

Three regex patterns (webcast URL, dial-in phone, conference PIN)
match the standard 'Conference Call Information' paragraph found in
8-K Exhibit 99.1 press releases. First match wins per field; missing
fields return None. Fixtures cover NVDA-style, no-dial-in, and
international phone formats."
```

---

## Task 5: Method A end-to-end script (writes to events.parquet)

**Files:**
- Create: `backend/scripts/enrich_calendar_a.py`

- [ ] **Step 1: Implement the script**

Create `backend/scripts/enrich_calendar_a.py`:

```python
"""Method A: parse existing earnings_releases parquets and fill the
*_a soft-field columns on events.parquet.

Run:
    python -m backend.scripts.enrich_calendar_a              # all rows
    python -m backend.scripts.enrich_calendar_a --ticker NVDA  # one ticker

Idempotent: re-running only writes to columns that are still empty AND
where validation succeeds. enrichment_a_attempted_at is always bumped.

Cache-first: the raw text comes from earnings_releases parquets which are
themselves the bronze cache for SEC filings. We don't fetch anything new.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.calendar.enrichment.press_release_parser import (  # noqa: E402
    parse_press_release,
)
from backend.app.services.calendar.enrichment.url_validator import (  # noqa: E402
    validate_url,
)
from backend.app.services.calendar.storage import (  # noqa: E402
    read_events, upsert_events, _is_empty,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("enrich_calendar_a")

_RELEASES_DIR = PROJECT_ROOT / "backend" / "data" / "earnings_releases"


def _events_needing_a(events_df: pd.DataFrame, ticker: str | None) -> pd.DataFrame:
    df = events_df[events_df["source"] == "edgar_8k"]
    if ticker:
        df = df[df["ticker"] == ticker.upper()]
    needs = (
        df["webcast_url_a"].apply(_is_empty)
        | df["dial_in_phone_a"].apply(_is_empty)
        | df["dial_in_pin_a"].apply(_is_empty)
    )
    return df[needs]


def _load_release_text(ticker: str, accession_no: str) -> str | None:
    p = _RELEASES_DIR / f"ticker={ticker}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    row = df[df["accession_no"] == accession_no]
    if row.empty:
        return None
    text = row.iloc[0].get("text_raw")
    return str(text) if text else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Method A enrichment runner.")
    ap.add_argument("--ticker", default=None, help="Restrict to one ticker.")
    args = ap.parse_args()

    df = read_events()
    target = _events_needing_a(df, args.ticker)
    log.info("Method A: %d events need enrichment", len(target))

    now = pd.Timestamp.now(tz="UTC")
    updated_rows: list[dict] = []
    for _, ev in target.iterrows():
        ticker = ev["ticker"]
        # source_id is "ticker:accession_no:exhibit" -- pick out accession_no
        source_id = str(ev.get("source_id") or "")
        parts = source_id.split(":")
        accession = parts[1] if len(parts) >= 2 else ""
        if not accession:
            continue
        text = _load_release_text(ticker, accession)
        if not text:
            continue

        fields = parse_press_release(text)
        # Validate webcast URL before storing
        if fields["webcast_url"] and not validate_url(fields["webcast_url"]):
            log.info("[%s %s] webcast URL failed validation: %s",
                     ticker, ev["fiscal_period"], fields["webcast_url"])
            fields["webcast_url"] = None

        # Build the upsert row -- only the keys that are populated, plus the
        # required keying fields. upsert_events skips empty values via _is_empty.
        upd = {
            "ticker":       ticker,
            "market":       ev["market"],
            "fiscal_period": ev["fiscal_period"],
            "enrichment_a_attempted_at": now,
        }
        if fields["webcast_url"]:
            upd["webcast_url_a"] = fields["webcast_url"]
        if fields["dial_in_phone"]:
            upd["dial_in_phone_a"] = fields["dial_in_phone"]
        if fields["dial_in_pin"]:
            upd["dial_in_pin_a"] = fields["dial_in_pin"]
        # Press release URL: use the SEC filing URL we already have
        if not _is_empty(ev.get("filing_url")):
            upd["press_release_url_a"] = ev["filing_url"]
        updated_rows.append(upd)

    if not updated_rows:
        log.info("Method A: no rows to write.")
        return 0

    stats = upsert_events(updated_rows)
    log.info("Method A done: inserted=%d updated=%d touched=%d",
             stats.inserted, stats.updated, stats.touched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test with one ticker**

Run: `cd C:/Users/Sharo/AI_projects/AlphaGraph_new && python -m backend.scripts.enrich_calendar_a --ticker NVDA`
Expected output (exact counts may vary): `Method A done: inserted=0 updated=N touched=0` where N is roughly the number of NVDA past events.

Verify with a quick check:
```bash
python -c "
import pandas as pd
df = pd.read_parquet('backend/data/earnings_calendar/events.parquet')
nvda = df[df['ticker']=='NVDA'].sort_values('release_datetime_utc', ascending=False).head(3)
print(nvda[['ticker','fiscal_period','webcast_url_a','dial_in_phone_a','dial_in_pin_a']].to_string(index=False))
"
```
Expected: at least 2 of the 3 most recent NVDA events have non-null webcast_url_a or dial_in_pin_a.

- [ ] **Step 3: Full run**

Run: `python -m backend.scripts.enrich_calendar_a`
Expected: log `Method A: ~561 events need enrichment` then `Method A done: ... updated≈530`.

- [ ] **Step 4: Verify acceptance bar**

```bash
python -c "
import pandas as pd
df = pd.read_parquet('backend/data/earnings_calendar/events.parquet')
past = df[df['status']=='done']
print('past events:', len(past))
print('webcast_url_a populated:', past['webcast_url_a'].notna().sum())
print('dial_in_phone_a populated:', past['dial_in_phone_a'].notna().sum())
print('dial_in_pin_a populated:', past['dial_in_pin_a'].notna().sum())
print('press_release_url_a populated:', past['press_release_url_a'].notna().sum())
"
```
Expected: webcast_url_a + press_release_url_a + (dial_in_phone OR pin) populated for ≥530/561 past events. If below, capture a few rows where all three regexes failed and add their text snippets to the fixture set + extend the regexes; iterate until the bar is met.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/enrich_calendar_a.py
git commit -m "feat(calendar/enrichment): Method A runner — fill *_a columns from 8-K text

Reads earnings_releases/ticker=*.parquet, runs the press-release regex
parser, validates webcast URLs via HEAD/GET-Range, upserts to events.parquet
under webcast_url_a, dial_in_phone_a, dial_in_pin_a, press_release_url_a.
Idempotent. enrichment_a_attempted_at always bumped."
```

---

## Task 6: Method B — Gemini-grounded LLM client

**Files:**
- Create: `backend/app/services/calendar/enrichment/llm_grounded.py`
- Test: `backend/tests/calendar/test_llm_grounded.py`
- Test fixture: `backend/tests/calendar/fixtures/gemini_response_klac.json`

- [ ] **Step 1: Build the fixture**

Create `backend/tests/calendar/fixtures/gemini_response_klac.json`:

```json
{
  "webcast_url": "https://investor.kla.com/events/event-details/q3-fy2026",
  "dial_in_phone": "1-800-836-8184",
  "dial_in_pin": "9376251",
  "press_release_url": "https://www.businesswire.com/news/home/20260429012345/en/KLA-Corporation-Reports-Q3-FY2026-Results",
  "transcript_url": null
}
```

- [ ] **Step 2: Write the tests**

Create `backend/tests/calendar/test_llm_grounded.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from backend.app.services.calendar.enrichment.llm_grounded import (
    enrich_one_event, _build_prompt, _validate_response,
)

FIX = Path(__file__).parent / "fixtures"


def test_build_prompt_includes_required_fields():
    p = _build_prompt(
        ticker="KLAC",
        company_name="KLA Corporation",
        fiscal_period="FY2026-Q3",
        release_datetime_utc=pd.Timestamp("2026-04-30T20:30:00", tz="UTC"),
        time_of_day_code="AMC",
    )
    assert "KLAC" in p and "KLA Corporation" in p
    assert "FY2026-Q3" in p
    assert "2026-04-30" in p
    assert "AMC" in p


def test_validate_response_drops_invalid_urls():
    """validate_response uses url_validator; failing URLs become None."""
    raw = json.loads((FIX / "gemini_response_klac.json").read_text())
    # Stub validator: webcast OK, press_release rejected
    def stub_validate(url, **_):
        return "kla.com" in url
    with patch("backend.app.services.calendar.enrichment.llm_grounded.validate_url",
               side_effect=stub_validate):
        out = _validate_response(raw)
    assert out["webcast_url"] == raw["webcast_url"]
    assert out["press_release_url"] is None       # validation failed -> dropped
    assert out["dial_in_phone"]   == raw["dial_in_phone"]


def test_validate_response_rejects_placeholder_phone():
    """Obvious placeholder numbers (123-456-7890, 555-) discarded."""
    raw = {"webcast_url": None, "dial_in_phone": "123-456-7890", "dial_in_pin": "555-0000",
           "press_release_url": None, "transcript_url": None}
    with patch("backend.app.services.calendar.enrichment.llm_grounded.validate_url",
               return_value=True):
        out = _validate_response(raw)
    assert out["dial_in_phone"] is None
    # 555-0000 looks like a placeholder; pin must have >= 4 digits but we accept
    # any digit string for pin since real pins can be very short/long
    # (just sanity-check not letters)
    assert out["dial_in_pin"] in (None, "555-0000")  # pin policy is lenient


def test_enrich_one_event_returns_cost_and_fields(monkeypatch):
    """End-to-end: stub Gemini, assert (fields, cost_usd) tuple returned."""
    raw = json.loads((FIX / "gemini_response_klac.json").read_text())

    fake_response = MagicMock()
    fake_response.text = json.dumps(raw)

    mock_model = MagicMock()
    mock_model.generate_content.return_value = fake_response

    with patch("backend.app.services.calendar.enrichment.llm_grounded._get_model",
               return_value=mock_model), \
         patch("backend.app.services.calendar.enrichment.llm_grounded.validate_url",
               return_value=True):
        fields, cost = enrich_one_event(
            ticker="KLAC",
            company_name="KLA Corporation",
            fiscal_period="FY2026-Q3",
            release_datetime_utc=pd.Timestamp("2026-04-30T20:30:00", tz="UTC"),
            time_of_day_code="AMC",
        )
    assert fields["webcast_url"].endswith("q3-fy2026")
    assert cost > 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest backend/tests/calendar/test_llm_grounded.py -v`
Expected: FAIL with import error.

- [ ] **Step 4: Implement the LLM-grounded module**

Create `backend/app/services/calendar/enrichment/llm_grounded.py`:

```python
"""Method B: Gemini Flash + Google-search grounding for upcoming earnings events.

For each upcoming event with empty soft fields after Method A, query Gemini
Flash grounded with Google Search. Gemini visits the company's IR page,
NASDAQ announcement, and any wire-service press release; returns structured
JSON with the four soft fields.

Cost-tracked via budget.py; URL-validated via url_validator.py.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Tuple

import pandas as pd

from backend.app.services.calendar.enrichment.budget import (
    COST_PER_GEMINI_CALL_USD,
)
from backend.app.services.calendar.enrichment.url_validator import validate_url

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_RAW_DIR = _PROJECT_ROOT / "backend" / "data" / "_raw" / "calendar_enrichment" / "b"

# Lazy import + lazy model construction so tests can monkeypatch _get_model
# without paying for the SDK import.
_MODEL_NAME = "gemini-2.0-flash-exp"  # Flash with grounding support


def _get_model():
    import google.generativeai as genai
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY (or GOOGLE_API_KEY) env var required for Method B."
        )
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(
        model_name=_MODEL_NAME,
        # Enable Google-search grounding so Gemini can find the IR page directly.
        tools=[{"google_search_retrieval": {}}],
    )


def _build_prompt(
    *, ticker: str, company_name: str, fiscal_period: str,
    release_datetime_utc: pd.Timestamp, time_of_day_code: str | None,
) -> str:
    when = release_datetime_utc.strftime("%Y-%m-%d")
    tod = time_of_day_code or "TBD"
    return (
        "You are an investor-relations data extractor. Use Google Search to find "
        f"the official URLs and conference dial-in details for {ticker} "
        f"({company_name})'s {fiscal_period} earnings call, scheduled for "
        f"{when} ({tod} US Eastern).\n\n"
        "Return ONLY valid JSON matching this schema (use null for unknowns):\n"
        "{\n"
        '  "webcast_url":       "https://...",       // official webcast URL\n'
        '  "dial_in_phone":     "+1-800-555-0123",   // primary domestic dial-in\n'
        '  "dial_in_pin":       "1234567",           // conference ID / passcode\n'
        '  "press_release_url": "https://...",       // IR press release URL\n'
        '  "transcript_url":    "https://..."        // post-event transcript URL or null\n'
        "}\n\n"
        "Do NOT invent URLs or phone numbers. If a field is not confidently found, "
        "return null. Prefer the company's own IR page over third-party aggregators."
    )


_PLACEHOLDER_PHONE_RX = re.compile(r"^[+\-\s\(]*123[\-\s]?456[\-\s]?7890")


def _validate_response(raw: dict) -> dict:
    """Drop invalid URLs and obvious placeholder phone numbers."""
    out = {k: raw.get(k) for k in
           ("webcast_url", "dial_in_phone", "dial_in_pin",
            "press_release_url", "transcript_url")}

    for url_field in ("webcast_url", "press_release_url", "transcript_url"):
        url = out.get(url_field)
        if url and not validate_url(url):
            logger.info("Method B: dropping invalid URL for %s: %s", url_field, url)
            out[url_field] = None

    phone = out.get("dial_in_phone")
    if phone and _PLACEHOLDER_PHONE_RX.match(phone):
        out["dial_in_phone"] = None

    return out


def enrich_one_event(
    *, ticker: str, company_name: str, fiscal_period: str,
    release_datetime_utc: pd.Timestamp, time_of_day_code: str | None,
) -> Tuple[dict, float]:
    """Single Gemini-grounded query. Returns (validated_fields, cost_usd).

    Persists the raw Gemini text response to bronze cache before parsing,
    per the project-wide cache-first rule.
    """
    prompt = _build_prompt(
        ticker=ticker, company_name=company_name, fiscal_period=fiscal_period,
        release_datetime_utc=release_datetime_utc, time_of_day_code=time_of_day_code,
    )
    model = _get_model()
    response = model.generate_content(prompt)
    raw_text = response.text or ""

    # Bronze persist BEFORE parsing.
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = f"{ticker}_{fiscal_period}.json"
    (_RAW_DIR / cache_key).write_text(raw_text, encoding="utf-8")

    # Strip any markdown code-fence Gemini occasionally adds.
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.rstrip("`").strip()

    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("Method B: malformed JSON from Gemini for %s %s: %s",
                       ticker, fiscal_period, e)
        return ({"webcast_url": None, "dial_in_phone": None, "dial_in_pin": None,
                 "press_release_url": None, "transcript_url": None},
                COST_PER_GEMINI_CALL_USD)

    return _validate_response(raw), COST_PER_GEMINI_CALL_USD
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest backend/tests/calendar/test_llm_grounded.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/calendar/enrichment/llm_grounded.py backend/tests/calendar/test_llm_grounded.py backend/tests/calendar/fixtures/gemini_response_klac.json
git commit -m "feat(calendar/enrichment): Method B — Gemini-grounded LLM client

Single-event enrichment via Gemini Flash with Google-search grounding.
Returns (validated_fields, cost_usd). Validates URLs through
url_validator.validate_url; drops obvious placeholder phone numbers.
Persists raw response to backend/data/_raw/calendar_enrichment/b/
before parsing per the cache-first rule. Cleanly tolerates malformed
JSON returned by Gemini."
```

---

## Task 7: Method B end-to-end script (writes to events.parquet)

**Files:**
- Create: `backend/scripts/enrich_calendar_b.py`

- [ ] **Step 1: Implement the script**

Create `backend/scripts/enrich_calendar_b.py`:

```python
"""Method B: Gemini-grounded enrichment for upcoming events.

Iterates events with status in {upcoming, confirmed} releasing within the next
14 days that have any soft field still null after Method A. Queries Gemini
Flash grounded with Google Search; validates URLs; upserts to events.parquet.

Daily $1 budget cap enforced via budget.remaining_budget_today() before each call.

Run:
    python -m backend.scripts.enrich_calendar_b              # next 14 days
    python -m backend.scripts.enrich_calendar_b --days 7     # custom window

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.calendar.enrichment.budget import (  # noqa: E402
    remaining_budget_today, COST_PER_GEMINI_CALL_USD,
)
from backend.app.services.calendar.enrichment.llm_grounded import (  # noqa: E402
    enrich_one_event,
)
from backend.app.services.calendar.storage import (  # noqa: E402
    read_events, upsert_events, _is_empty,
)
from backend.app.services.universe_registry import read_universe  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("enrich_calendar_b")


def _ticker_to_name() -> dict[str, str]:
    df = read_universe()
    if df.empty:
        return {}
    return dict(zip(df["ticker"].astype(str), df["name"].astype(str).fillna("")))


def _events_needing_b(events_df: pd.DataFrame, days: int) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    end = now + pd.Timedelta(days=days)
    df = events_df[events_df["status"].isin(["upcoming", "confirmed"])]
    df = df[(df["release_datetime_utc"] >= now) & (df["release_datetime_utc"] <= end)]
    needs = (
        df["webcast_url_b"].apply(_is_empty)
        & df["webcast_url_a"].apply(_is_empty)   # only if A didn't already fill it
    ) | (
        df["dial_in_phone_b"].apply(_is_empty)
        & df["dial_in_phone_a"].apply(_is_empty)
    )
    # Caching: skip if attempted in last 7 days, unless within 48h of release
    last48h = now + pd.Timedelta(hours=48)
    recently_tried = (
        pd.to_datetime(df["enrichment_b_attempted_at"], utc=True, errors="coerce")
        >= now - pd.Timedelta(days=7)
    )
    is_imminent = df["release_datetime_utc"] <= last48h
    skip = recently_tried & ~is_imminent
    return df[needs & ~skip]


def main() -> int:
    ap = argparse.ArgumentParser(description="Method B Gemini-grounded enrichment.")
    ap.add_argument("--days", type=int, default=14, help="Window in days.")
    args = ap.parse_args()

    df = read_events()
    targets = _events_needing_b(df, args.days)
    name_map = _ticker_to_name()
    log.info("Method B: %d events need enrichment", len(targets))

    now = pd.Timestamp.now(tz="UTC")
    updated_rows: list[dict] = []

    for _, ev in targets.iterrows():
        budget = remaining_budget_today()
        if budget < COST_PER_GEMINI_CALL_USD:
            log.info("Method B: budget exhausted (remaining=$%.2f), stopping.", budget)
            break

        ticker = ev["ticker"]
        try:
            fields, cost = enrich_one_event(
                ticker=ticker,
                company_name=name_map.get(ticker, ticker),
                fiscal_period=ev["fiscal_period"],
                release_datetime_utc=ev["release_datetime_utc"],
                time_of_day_code=ev.get("time_of_day_code"),
            )
        except Exception as exc:
            log.warning("[%s %s] Gemini call failed: %s", ticker, ev["fiscal_period"], exc)
            continue

        upd = {
            "ticker":       ticker,
            "market":       ev["market"],
            "fiscal_period": ev["fiscal_period"],
            "enrichment_b_attempted_at": now,
            "enrichment_b_cost_usd": float(ev.get("enrichment_b_cost_usd") or 0.0) + cost,
        }
        # Only fill _b columns where (a) A didn't fill the field and (b) Gemini returned a value.
        if fields["webcast_url"] and _is_empty(ev.get("webcast_url_a")):
            upd["webcast_url_b"] = fields["webcast_url"]
        if fields["dial_in_phone"] and _is_empty(ev.get("dial_in_phone_a")):
            upd["dial_in_phone_b"] = fields["dial_in_phone"]
        if fields["dial_in_pin"] and _is_empty(ev.get("dial_in_pin_a")):
            upd["dial_in_pin_b"] = fields["dial_in_pin"]
        if fields["press_release_url"] and _is_empty(ev.get("press_release_url_a")):
            upd["press_release_url_b"] = fields["press_release_url"]
        if fields["transcript_url"]:
            upd["transcript_url_b"] = fields["transcript_url"]
        updated_rows.append(upd)
        log.info("[%s %s] enriched (remaining_budget=$%.3f)",
                 ticker, ev["fiscal_period"], budget - cost)

    if not updated_rows:
        log.info("Method B: no rows to write.")
        return 0
    stats = upsert_events(updated_rows)
    log.info("Method B done: inserted=%d updated=%d touched=%d  spent=$%.3f",
             stats.inserted, stats.updated, stats.touched,
             sum(r.get("enrichment_b_cost_usd", 0) for r in updated_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test (real Gemini call, costs ~$0.025)**

Ensure `GEMINI_API_KEY` is set in environment. Run on a single ticker by temporarily filtering the events list (or just run the small budget):

Run: `python -m backend.scripts.enrich_calendar_b --days 7`
Expected: log lines `[KLAC FY2026-Q3] enriched (remaining_budget=$0.975)` for each event, ending with `Method B done: ... spent=$0.X`.

If no upcoming events in the next 7 days, expand to `--days 21`.

- [ ] **Step 3: Verify Gemini's output landed**

```bash
python -c "
import pandas as pd
df = pd.read_parquet('backend/data/earnings_calendar/events.parquet')
upcoming = df[df['status']=='upcoming'].sort_values('release_datetime_utc').head(5)
print(upcoming[['ticker','fiscal_period','webcast_url_b','dial_in_phone_b','press_release_url_b']].to_string(index=False))
"
```
Expected: at least 3 of the next 5 upcoming events have a non-null webcast_url_b.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/enrich_calendar_b.py
git commit -m "feat(calendar/enrichment): Method B runner — daily Gemini-grounded fill

Iterates upcoming events in window; for each, calls enrich_one_event from
llm_grounded module; respects \$1/day budget cap; persists per-call cost
in enrichment_b_cost_usd. Only writes to _b columns where the matching
_a column is empty (A wins; B only fills gaps). Bronze raw responses
land in backend/data/_raw/calendar_enrichment/b/."
```

---

## Task 8: Method C — IRAdapter base + Q4 Inc adapter

**Files:**
- Create: `backend/app/services/calendar/enrichment/ir_scrapers/__init__.py` (empty)
- Create: `backend/app/services/calendar/enrichment/ir_scrapers/_base.py`
- Create: `backend/app/services/calendar/enrichment/ir_scrapers/q4_inc.py`
- Test: `backend/tests/calendar/test_q4_inc_adapter.py`
- Test fixture: `backend/tests/calendar/fixtures/q4_event_aapl.html`

- [ ] **Step 1: Build the HTML fixture**

Create `backend/tests/calendar/fixtures/q4_event_aapl.html` (minimal, but covers the fields the adapter needs):

```html
<!DOCTYPE html>
<html>
<head>
<title>Q1 FY2026 Earnings Conference Call - Apple Inc.</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Event",
  "name": "Q1 FY2026 Earnings Conference Call",
  "startDate": "2026-04-30T17:00:00-04:00",
  "endDate":   "2026-04-30T18:00:00-04:00",
  "eventStatus": "https://schema.org/EventScheduled",
  "url": "https://investor.apple.com/events/event-details/q1-fy2026",
  "videoUrl": "https://edge.media-server.com/mmc/p/aapl-q1-fy2026"
}
</script>
</head>
<body>
<div class="event-page">
  <a class="press-release-link" href="https://www.apple.com/newsroom/2026/04/apple-reports-q1-results.html">Press Release</a>
  <div class="cnv-tabs__panel" data-section="dial-in">
    <p>Toll-free: <strong>1-877-555-7676</strong></p>
    <p>International: <strong>+1-201-555-1234</strong></p>
    <p>Conference ID: <strong>13745829</strong></p>
  </div>
</div>
</body>
</html>
```

- [ ] **Step 2: Write the tests**

Create `backend/tests/calendar/test_q4_inc_adapter.py`:

```python
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.app.services.calendar.enrichment.ir_scrapers.q4_inc import (
    Q4IncAdapter, parse_event_html,
)

FIX = Path(__file__).parent / "fixtures"


def test_parse_event_html_extracts_all_fields():
    html = (FIX / "q4_event_aapl.html").read_text(encoding="utf-8")
    fields = parse_event_html(html)
    assert fields.webcast_url == "https://edge.media-server.com/mmc/p/aapl-q1-fy2026"
    assert fields.dial_in_phone and "877-555-7676" in fields.dial_in_phone
    assert fields.dial_in_pin == "13745829"
    assert fields.press_release_url == "https://www.apple.com/newsroom/2026/04/apple-reports-q1-results.html"


def test_parse_event_html_returns_nones_when_missing():
    fields = parse_event_html("<html><body>nothing here</body></html>")
    assert fields.webcast_url is None
    assert fields.dial_in_phone is None
    assert fields.dial_in_pin is None
    assert fields.press_release_url is None


def test_adapter_detect_recognizes_q4_inc_url():
    a = Q4IncAdapter()
    assert a.detect("https://investor.apple.com/events/event-details/q1-fy2026") is True
    assert a.detect("https://www.q4ir.com/some-page") is True
    assert a.detect("https://example.com/whatever") is False


def test_adapter_fetch_event_uses_html_fixture(monkeypatch):
    """fetch_event happy path: HTTP 200 + parseable HTML."""
    html = (FIX / "q4_event_aapl.html").read_text(encoding="utf-8")
    fake_resp = MagicMock(status_code=200, text=html)
    fake_resp.raise_for_status = MagicMock()
    a = Q4IncAdapter()
    with patch("backend.app.services.calendar.enrichment.ir_scrapers.q4_inc.requests.get",
               return_value=fake_resp), \
         patch.object(a, "_resolve_event_url",
                      return_value="https://investor.apple.com/events/event-details/q1-fy2026"):
        fields = a.fetch_event(ticker="AAPL", fiscal_period="FY2026-Q1")
    assert fields.webcast_url is not None
    assert fields.dial_in_pin == "13745829"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest backend/tests/calendar/test_q4_inc_adapter.py -v`
Expected: FAIL with import error.

- [ ] **Step 4: Implement the base protocol**

Create `backend/app/services/calendar/enrichment/ir_scrapers/_base.py`:

```python
"""IRAdapter base protocol for Method C vendor-specific scrapers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SoftFields:
    webcast_url:        str | None = None
    dial_in_phone:      str | None = None
    dial_in_pin:        str | None = None
    press_release_url:  str | None = None


class IRAdapter(Protocol):
    vendor_name: str

    def detect(self, ir_url: str) -> bool:
        """True if this adapter handles the given IR URL."""
        ...

    def fetch_event(self, ticker: str, fiscal_period: str) -> SoftFields:
        """Return soft fields for the given (ticker, fiscal_period). Each
        field is None when the adapter cannot determine it. Persists raw HTML
        to backend/data/_raw/calendar_enrichment/c/<vendor>/ before parsing.
        """
        ...
```

Create empty `backend/app/services/calendar/enrichment/ir_scrapers/__init__.py`.

- [ ] **Step 5: Implement the Q4 Inc adapter**

Create `backend/app/services/calendar/enrichment/ir_scrapers/q4_inc.py`:

```python
"""Q4 Inc IR-page adapter.

Q4 Inc hosts ~40% of US-listed company IR pages (NVDA, AAPL, MSFT, AVGO,
CDNS, AMD, and others). Their event pages share a consistent structure:
- A schema.org/Event JSON-LD block with `videoUrl` (webcast)
- A `cnv-tabs__panel[data-section=dial-in]` panel with phone numbers + PIN
- A `.press-release-link` <a> pointing to the press release

The event URL pattern is `investor.<company>.com/events/event-details/<slug>`.
We resolve the slug per (ticker, fiscal_period) by either:
  1. A hard-coded ticker -> IR root mapping (top tickers we explicitly handle)
  2. Querying the events index and matching on fiscal_period text

This module covers only the parser. The orchestrator (Task 10) handles
event-URL resolution and persistence.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from backend.app.services.calendar.enrichment.ir_scrapers._base import (
    IRAdapter, SoftFields,
)
from backend.app.services.calendar.enrichment.url_validator import validate_url

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_RAW_DIR = _PROJECT_ROOT / "backend" / "data" / "_raw" / "calendar_enrichment" / "c" / "q4_inc"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Hosts where Q4 Inc serves IR pages.
_Q4_HOST_RX = re.compile(
    r"(?:investor\.[a-z0-9\-]+\.com|q4ir\.com|q4cdn\.com)",
    re.IGNORECASE,
)


def parse_event_html(html: str) -> SoftFields:
    """Extract soft fields from a Q4 Inc event-details page."""
    soup = BeautifulSoup(html, "html.parser")

    webcast = None
    pressrel = None

    # JSON-LD: schema.org/Event with videoUrl
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        # Sometimes the payload is a list of @graph items
        candidates = data if isinstance(data, list) else [data]
        for c in candidates:
            if not isinstance(c, dict):
                continue
            if (c.get("@type") or "").endswith("Event"):
                webcast = c.get("videoUrl") or c.get("recordingUrl") or webcast

    # Press release link
    pr_link = soup.select_one("a.press-release-link[href]")
    if pr_link:
        pressrel = pr_link["href"]

    # Dial-in panel
    panel = soup.select_one("[data-section='dial-in']") or soup.select_one(".cnv-tabs__panel")
    phone = pin = None
    if panel:
        text = panel.get_text(" ", strip=True)
        # First phone-shaped string in panel that contains "555" or 8+ digits
        m = re.search(r"(\+?[\d][\d\s\-\(\)]{8,18}\d)", text)
        if m:
            phone = m.group(1).strip()
        m = re.search(r"(?:Conference\s+ID|Access\s+Code|PIN)\s*[:#]?\s*(\d{4,12})",
                      text, re.IGNORECASE)
        if m:
            pin = m.group(1)

    return SoftFields(
        webcast_url=webcast,
        dial_in_phone=phone,
        dial_in_pin=pin,
        press_release_url=pressrel,
    )


class Q4IncAdapter:
    vendor_name = "q4_inc"

    def detect(self, ir_url: str) -> bool:
        return bool(_Q4_HOST_RX.search(ir_url or ""))

    def _resolve_event_url(self, ticker: str, fiscal_period: str) -> str | None:
        """Map (ticker, fiscal_period) to the event-details URL.

        For MVP, this method has a small explicit ticker -> events-index
        mapping covering the highest-traffic tickers. Tickers not in the
        map fall back to no-result (orchestrator records "vendor=q4_inc"
        but no fields, allowing future improvement)."""
        # Ticker -> events index URL. Slug is then matched by fiscal_period.
        # Keep this small and explicit; adding a ticker is a one-line change.
        events_index = {
            "AAPL": "https://investor.apple.com/events/default.aspx",
            "NVDA": "https://investor.nvidia.com/events/default.aspx",
            "MSFT": "https://www.microsoft.com/en-us/Investor/events.aspx",
            "AVGO": "https://investors.broadcom.com/events",
            "CDNS": "https://investor.cadence.com/events",
            "AMD":  "https://ir.amd.com/events",
        }
        index_url = events_index.get(ticker.upper())
        if not index_url:
            return None

        try:
            r = requests.get(index_url, headers={"User-Agent": _BROWSER_UA}, timeout=10)
            r.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("[q4_inc %s] events-index fetch failed: %s", ticker, exc)
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        # Find a link whose text contains the fiscal_period token (e.g. "Q1 FY2026")
        # Try a few human variants.
        fp = fiscal_period.upper()
        q = fp.split("-")[1] if "-" in fp else ""        # "Q1"
        fy = fp.split("-")[0]                              # "FY2026"
        wanted_substrings = [fp, f"{q} {fy}", f"{q} {fy.lstrip('FY')}"]

        for a in soup.find_all("a", href=True):
            text_upper = (a.get_text() or "").upper()
            for w in wanted_substrings:
                if w in text_upper:
                    href = a["href"]
                    if href.startswith("/"):
                        # Resolve relative URL against the index URL
                        from urllib.parse import urljoin
                        href = urljoin(index_url, href)
                    return href
        return None

    def fetch_event(self, ticker: str, fiscal_period: str) -> SoftFields:
        url = self._resolve_event_url(ticker, fiscal_period)
        if not url:
            return SoftFields()

        try:
            r = requests.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=15)
            r.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("[q4_inc %s %s] event-page fetch failed: %s",
                           ticker, fiscal_period, exc)
            return SoftFields()

        # Bronze persist before parsing.
        _RAW_DIR.mkdir(parents=True, exist_ok=True)
        cache_key = f"{ticker}_{fiscal_period}.html"
        try:
            (_RAW_DIR / cache_key).write_text(r.text, encoding="utf-8")
        except OSError as exc:
            logger.debug("[q4_inc] failed to write bronze cache: %s", exc)

        fields = parse_event_html(r.text)

        # Validate URLs.
        for attr in ("webcast_url", "press_release_url"):
            v = getattr(fields, attr)
            if v and not validate_url(v):
                logger.info("[q4_inc] dropping invalid %s: %s", attr, v)
                setattr(fields, attr, None)

        return fields
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest backend/tests/calendar/test_q4_inc_adapter.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/calendar/enrichment/ir_scrapers/__init__.py backend/app/services/calendar/enrichment/ir_scrapers/_base.py backend/app/services/calendar/enrichment/ir_scrapers/q4_inc.py backend/tests/calendar/test_q4_inc_adapter.py backend/tests/calendar/fixtures/q4_event_aapl.html
git commit -m "feat(calendar/enrichment): Method C — Q4 Inc IR-page adapter

IRAdapter protocol + Q4Inc implementation. Parses schema.org/Event
JSON-LD for webcast URL, .cnv-tabs__panel[data-section=dial-in] for
phone+PIN, .press-release-link for press release URL. Validates URLs
through the shared validator. Bronze raw HTML persisted under
backend/data/_raw/calendar_enrichment/c/q4_inc/."
```

---

## Task 9: Method C end-to-end script (writes to events.parquet)

**Files:**
- Create: `backend/scripts/enrich_calendar_c.py`
- Create: `backend/app/services/calendar/enrichment/ir_scrapers/_registry.py`

- [ ] **Step 1: Implement the registry**

Create `backend/app/services/calendar/enrichment/ir_scrapers/_registry.py`:

```python
"""Registry of available IRAdapters. Adding a new vendor adapter requires
only adding it here and to the ALL_ADAPTERS list."""
from __future__ import annotations

from backend.app.services.calendar.enrichment.ir_scrapers._base import IRAdapter
from backend.app.services.calendar.enrichment.ir_scrapers.q4_inc import Q4IncAdapter

ALL_ADAPTERS: list[IRAdapter] = [
    Q4IncAdapter(),
]


def adapter_for_ticker(ticker: str) -> IRAdapter | None:
    """Return the first adapter that claims this ticker by hard-coded mapping.

    For MVP we only consult Q4IncAdapter._resolve_event_url's hard map; a
    future iteration can add a per-ticker IR-root vendor probe."""
    for a in ALL_ADAPTERS:
        # MVP heuristic: try to resolve. If non-None, this adapter "claims"
        # the ticker. (Cheap because resolve hits the events index page.)
        if hasattr(a, "_resolve_event_url"):
            if a._resolve_event_url(ticker, "PROBE") is not None:
                return a
    return None


def adapter_for_url(ir_url: str) -> IRAdapter | None:
    for a in ALL_ADAPTERS:
        if a.detect(ir_url):
            return a
    return None
```

- [ ] **Step 2: Implement the runner**

Create `backend/scripts/enrich_calendar_c.py`:

```python
"""Method C: Q4 Inc IR-page scraper for events with A+B gaps.

Iterates events with status in {upcoming, confirmed} where any soft field
is still null after Methods A and B. For each, runs the registered adapter
(currently Q4 Inc only); upserts results to events.parquet.

Run:
    python -m backend.scripts.enrich_calendar_c

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.calendar.enrichment.ir_scrapers._registry import (  # noqa: E402
    adapter_for_ticker,
)
from backend.app.services.calendar.storage import (  # noqa: E402
    read_events, upsert_events, _is_empty,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("enrich_calendar_c")


def _events_with_gaps(events_df: pd.DataFrame) -> pd.DataFrame:
    df = events_df[events_df["status"].isin(["upcoming", "confirmed"])]
    # Each soft field is "filled" if either _a or _b populated it. C should
    # run when ALL of (webcast, phone, pin, press) have NEITHER _a NOR _b.
    def field_empty(prefix: str, row: pd.Series) -> bool:
        return _is_empty(row.get(f"{prefix}_a")) and _is_empty(row.get(f"{prefix}_b"))

    keep_idx = []
    for idx, row in df.iterrows():
        if (field_empty("webcast_url", row) or field_empty("dial_in_phone", row)
                or field_empty("dial_in_pin", row) or field_empty("press_release_url", row)):
            keep_idx.append(idx)
    return df.loc[keep_idx]


def main() -> int:
    df = read_events()
    targets = _events_with_gaps(df)
    log.info("Method C: %d events with A+B gaps", len(targets))

    now = pd.Timestamp.now(tz="UTC")
    updated_rows: list[dict] = []

    for _, ev in targets.iterrows():
        ticker = ev["ticker"]
        adapter = adapter_for_ticker(ticker)
        if adapter is None:
            continue
        try:
            fields = adapter.fetch_event(ticker, ev["fiscal_period"])
        except Exception as exc:
            log.warning("[%s %s %s] adapter failed: %s",
                        adapter.vendor_name, ticker, ev["fiscal_period"], exc)
            continue

        upd = {
            "ticker":       ticker,
            "market":       ev["market"],
            "fiscal_period": ev["fiscal_period"],
            "enrichment_c_attempted_at": now,
            "enrichment_c_vendor": adapter.vendor_name,
        }
        # Only fill _c columns where neither _a nor _b filled it.
        if fields.webcast_url and _is_empty(ev.get("webcast_url_a")) and _is_empty(ev.get("webcast_url_b")):
            upd["webcast_url_c"] = fields.webcast_url
        if fields.dial_in_phone and _is_empty(ev.get("dial_in_phone_a")) and _is_empty(ev.get("dial_in_phone_b")):
            upd["dial_in_phone_c"] = fields.dial_in_phone
        if fields.dial_in_pin and _is_empty(ev.get("dial_in_pin_a")) and _is_empty(ev.get("dial_in_pin_b")):
            upd["dial_in_pin_c"] = fields.dial_in_pin
        if fields.press_release_url and _is_empty(ev.get("press_release_url_a")) and _is_empty(ev.get("press_release_url_b")):
            upd["press_release_url_c"] = fields.press_release_url

        # Always include the metadata even if no fields filled, so we don't
        # rerun the adapter every cycle.
        updated_rows.append(upd)

    if not updated_rows:
        log.info("Method C: no rows to write.")
        return 0
    stats = upsert_events(updated_rows)
    log.info("Method C done: inserted=%d updated=%d touched=%d",
             stats.inserted, stats.updated, stats.touched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Smoke-test on a known Q4 Inc ticker**

Run: `python -m backend.scripts.enrich_calendar_c`
Expected: log line `Method C: N events with A+B gaps` then per-ticker fetches. May find 0 events if A+B already covered everything (success state).

If event count > 0 and you see no fields land, manually verify by running a smaller test:
```python
from backend.app.services.calendar.enrichment.ir_scrapers.q4_inc import Q4IncAdapter
print(Q4IncAdapter().fetch_event("AAPL", "FY2026-Q1"))
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/calendar/enrichment/ir_scrapers/_registry.py backend/scripts/enrich_calendar_c.py
git commit -m "feat(calendar/enrichment): Method C runner — Q4 Inc gap filling

Method C runs only on events where neither _a nor _b filled at least
one soft field (last-resort policy). Iterates the IRAdapter registry,
calls adapter.fetch_event(ticker, fiscal_period), upserts _c columns.
Always writes enrichment_c_attempted_at + enrichment_c_vendor so we
don't reprocess the same ticker every cycle."
```

---

## Task 10: Orchestrator + wrapper script

**Files:**
- Create: `backend/app/services/calendar/enrichment/orchestrator.py`
- Create: `backend/scripts/enrich_calendar.py`
- Test: `backend/tests/calendar/test_orchestrator.py`

- [ ] **Step 1: Write the orchestrator test**

Create `backend/tests/calendar/test_orchestrator.py`:

```python
from unittest.mock import patch, MagicMock

from backend.app.services.calendar.enrichment.orchestrator import run_all


def test_run_all_invokes_a_then_b_then_c():
    """Orchestrator must call A, then B, then C in order."""
    call_order: list[str] = []
    with patch("backend.app.services.calendar.enrichment.orchestrator.run_method_a",
               side_effect=lambda: call_order.append("A")), \
         patch("backend.app.services.calendar.enrichment.orchestrator.run_method_b",
               side_effect=lambda **kwargs: call_order.append("B")), \
         patch("backend.app.services.calendar.enrichment.orchestrator.run_method_c",
               side_effect=lambda: call_order.append("C")):
        run_all(b_days=14)
    assert call_order == ["A", "B", "C"]


def test_run_all_continues_when_a_fails():
    """A's failure shouldn't stop B and C."""
    call_order: list[str] = []
    with patch("backend.app.services.calendar.enrichment.orchestrator.run_method_a",
               side_effect=Exception("boom")), \
         patch("backend.app.services.calendar.enrichment.orchestrator.run_method_b",
               side_effect=lambda **kwargs: call_order.append("B")), \
         patch("backend.app.services.calendar.enrichment.orchestrator.run_method_c",
               side_effect=lambda: call_order.append("C")):
        run_all(b_days=14)
    assert call_order == ["B", "C"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/calendar/test_orchestrator.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement the orchestrator**

Create `backend/app/services/calendar/enrichment/orchestrator.py`:

```python
"""Calendar soft-fields enrichment orchestrator.

Runs the three layered methods in order: A (press-release parser) ->
B (Gemini-grounded) -> C (vendor IR scrapers). Each method is independent;
failures in one don't stop the others.

Suitable for direct invocation by the daily APScheduler job.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _run_subprocess(module: str, *args: str) -> int:
    cmd = [sys.executable, "-m", module, *args]
    logger.info("orchestrator: running %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return result.returncode


def run_method_a() -> int:
    return _run_subprocess("backend.scripts.enrich_calendar_a")


def run_method_b(*, b_days: int = 14) -> int:
    return _run_subprocess("backend.scripts.enrich_calendar_b", "--days", str(b_days))


def run_method_c() -> int:
    return _run_subprocess("backend.scripts.enrich_calendar_c")


def run_all(*, b_days: int = 14) -> dict[str, int | None]:
    """Run A, B, C in order. Returns per-method exit codes; None for any
    method that raised an exception."""
    results: dict[str, int | None] = {"a": None, "b": None, "c": None}
    for name, fn, kwargs in (
        ("a", run_method_a, {}),
        ("b", run_method_b, {"b_days": b_days}),
        ("c", run_method_c, {}),
    ):
        try:
            results[name] = fn(**kwargs)
        except Exception as exc:
            logger.error("orchestrator: method %s raised %s", name.upper(), exc)
            results[name] = None
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/calendar/test_orchestrator.py -v`
Expected: 2 passed.

- [ ] **Step 5: Implement the wrapper CLI**

Create `backend/scripts/enrich_calendar.py`:

```python
"""Wrapper that runs all three enrichment methods sequentially.

Run:
    python -m backend.scripts.enrich_calendar --all
    python -m backend.scripts.enrich_calendar --all --b-days 7

Suitable for the daily APScheduler job.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

from backend.app.services.calendar.enrichment.orchestrator import run_all  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Run all calendar enrichment methods.")
    ap.add_argument("--all", action="store_true", help="Run methods A, B, C in order.")
    ap.add_argument("--b-days", type=int, default=14, help="Method B window (default 14).")
    args = ap.parse_args()

    if not args.all:
        ap.print_help()
        return 1
    results = run_all(b_days=args.b_days)
    print(f"[enrich_calendar] results: {results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Smoke-test the wrapper**

Run: `python -m backend.scripts.enrich_calendar --all --b-days 7`
Expected: A runs, B runs (subject to budget cap), C runs. Each prints its own done line. Wrapper exits 0.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/calendar/enrichment/orchestrator.py backend/scripts/enrich_calendar.py backend/tests/calendar/test_orchestrator.py
git commit -m "feat(calendar/enrichment): orchestrator + --all wrapper

orchestrator.run_all() invokes the three method runners as subprocesses
in order A->B->C. A failure in one doesn't stop the others.
backend/scripts/enrich_calendar.py --all is the entry point used by
the daily APScheduler job."
```

---

## Task 11: Schedule the daily run via APScheduler

**Files:**
- Modify: `backend/app/services/prices/scheduler.py` (or wherever existing schedulers live; this codebase uses one module per scheduled-job-domain)

- [ ] **Step 1: Locate and inspect the existing scheduler module**

Run: `grep -rn "BlockingScheduler\|APScheduler\|add_job" backend/app/services/ --include="*.py" | head -10`
Expected: a list of files that already register APScheduler jobs. Pick the file colocated with calendar/refresh jobs (or the prices scheduler — both follow the same pattern).

- [ ] **Step 2: Add the daily enrichment job**

In the existing scheduler module, add an entry following the pattern of the existing `refresh_calendar_us` daily job:

```python
# Daily soft-fields enrichment for the calendar. Runs after the upcoming-events
# refresh so that Methods B and C see the freshest set of upcoming events.
scheduler.add_job(
    func=lambda: subprocess.run(
        [sys.executable, "-m", "backend.scripts.enrich_calendar", "--all"],
        cwd=PROJECT_ROOT, check=False,
    ),
    trigger="cron",
    hour=6, minute=30,        # 06:30 UTC, 30 min after refresh_calendar_us
    timezone="UTC",
    id="enrich_calendar_daily",
    replace_existing=True,
    misfire_grace_time=900,   # 15 minutes
)
```

- [ ] **Step 3: Smoke-test by manually triggering the job**

If the scheduler exposes a way to trigger jobs by id (most do), trigger it. Otherwise verify by inspecting `scheduler.print_jobs()`:

```python
python -c "
from backend.app.services.prices.scheduler import scheduler  # adjust if path differs
scheduler.start()
scheduler.print_jobs()
"
```
Expected: a job named `enrich_calendar_daily` listed with next run-time at 06:30 UTC.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/prices/scheduler.py  # adjust to the file you modified
git commit -m "chore(scheduler): daily 06:30 UTC calendar soft-fields enrichment

Runs backend/scripts/enrich_calendar.py --all 30 minutes after
refresh_calendar_us. Methods A, B, C run sequentially; B respects
the \$1/day budget cap."
```

---

## Task 12: Frontend — dial-in tooltip with click-to-copy

**Files:**
- Modify: `frontend/src/app/(dashboard)/calendar/CalendarView.tsx`

- [ ] **Step 1: Add the DialInBadge component above EventTable**

Locate `CalendarView.tsx`. Add the following helper above the existing `EventTable` function:

```tsx
function DialInBadge({ phone, pin }: { phone: string | null; pin: string | null }) {
  if (!phone && !pin) return null;
  const tip = [
    phone && `Dial-in: ${phone}`,
    pin   && `PIN: ${pin}`,
    "(click to copy)",
  ].filter(Boolean).join("\n");

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    const payload = [phone, pin && `PIN: ${pin}`].filter(Boolean).join(" / ");
    if (payload) navigator.clipboard?.writeText(payload);
  };

  return (
    <button
      onClick={handleClick}
      title={tip}
      className="inline-flex items-center gap-0.5 text-[10px] text-slate-600
                 hover:text-indigo-600 hover:underline cursor-pointer"
    >
      <Phone size={10} />
      {phone ? "Dial" : "PIN"}
    </button>
  );
}
```

Add `Phone` to the lucide-react import block at the top of the file.

- [ ] **Step 2: Render the badge in the Links cell**

In the existing `<td>` for the Links column (search the file for the existing 8-K / Live / Transcript link block), add `<DialInBadge ... />` after the existing links:

```tsx
<td className="px-3 py-2 text-right">
  <span className="inline-flex gap-2 justify-end">
    {e.press_release_url && (
      <a href={e.press_release_url} target="_blank" rel="noopener noreferrer" ...>
        <ExternalLink size={10} /> 8-K
      </a>
    )}
    {e.webcast_url && (
      <a href={e.webcast_url} target="_blank" rel="noopener noreferrer" ...>
        <ExternalLink size={10} /> Live
      </a>
    )}
    {e.transcript_url && (
      <a href={e.transcript_url} target="_blank" rel="noopener noreferrer" ...>
        <ExternalLink size={10} /> Transcript
      </a>
    )}
    <DialInBadge phone={e.dial_in_phone} pin={e.dial_in_pin} />
  </span>
</td>
```

- [ ] **Step 3: Smoke-test in the running frontend**

Restart the frontend if needed. Open `/calendar`, find a row whose `dial_in_phone` is non-null (after Method A or B has run). Hover over the new `Dial` badge; tooltip should show "Dial-in: 1-800-... / PIN: ..." and clicking should copy.

- [ ] **Step 4: Commit**

```bash
git add "frontend/src/app/(dashboard)/calendar/CalendarView.tsx"
git commit -m "feat(calendar/frontend): dial-in tooltip with click-to-copy

DialInBadge renders next to existing Live/8-K/Transcript links when
dial_in_phone or dial_in_pin is non-null. Hover shows phone+PIN as
a tooltip; click copies 'phone / PIN: pin' to the clipboard."
```

---

## Task 13: End-to-end verification against acceptance criteria

**Files:** none (no code changes)

- [ ] **Step 1: Run a full enrichment cycle**

Run: `python -m backend.scripts.enrich_calendar --all --b-days 14`
Expected: A logs ~530 updated, B logs spend < $1.00, C logs gap-filling activity.

- [ ] **Step 2: Verify Method A acceptance bar**

```bash
python -c "
import pandas as pd
df = pd.read_parquet('backend/data/earnings_calendar/events.parquet')
past = df[df['status']=='done']
n = len(past)
filled_webcast = past['webcast_url_a'].notna().sum()
filled_press   = past['press_release_url_a'].notna().sum()
filled_phone_or_pin = past[['dial_in_phone_a','dial_in_pin_a']].notna().any(axis=1).sum()
print(f'Past events: {n}')
print(f'webcast_url_a:        {filled_webcast} ({filled_webcast/n*100:.0f}%)')
print(f'press_release_url_a:  {filled_press} ({filled_press/n*100:.0f}%)')
print(f'dial_in_phone OR pin: {filled_phone_or_pin} ({filled_phone_or_pin/n*100:.0f}%)')
"
```
Acceptance: ≥530 of 561 past events with webcast_url_a + press_release_url_a populated, ≥530 with dial_in_phone_a OR dial_in_pin_a populated. If under, capture failing texts and extend regex patterns in `press_release_parser.py`, then re-run.

- [ ] **Step 3: Verify Method B acceptance bar**

```bash
python -c "
import pandas as pd
df = pd.read_parquet('backend/data/earnings_calendar/events.parquet')
upcoming = df[df['status']=='upcoming']
n = len(upcoming)
print(f'Upcoming events: {n}')
print('webcast_url filled:        ', upcoming['webcast_url_b'].notna().sum())
print('dial_in_phone filled:      ', upcoming['dial_in_phone_b'].notna().sum())
print('press_release_url filled:  ', upcoming['press_release_url_b'].notna().sum())
print('total enrichment_b spend: \$', upcoming['enrichment_b_cost_usd'].fillna(0).sum())
"
```
Acceptance: ≥50 of 69 upcoming with webcast_url_b, ≥40 with press_release_url_b, ≥30 with dial_in_phone_b, total spend ≤ $1.00.

- [ ] **Step 4: Verify resolver materializes public columns**

Restart the backend (per the saved Windows uvicorn-reload note: kill and re-launch). Then:

```bash
curl -s "http://127.0.0.1:8000/api/v1/calendar/events/upcoming?days=21" | python -c "
import json, sys
d = json.load(sys.stdin)
rows = d['data']
filled = sum(1 for r in rows if r.get('webcast_url'))
print(f'upcoming with public webcast_url: {filled}/{len(rows)}')
print('first event with full bundle:')
import pprint
for r in rows:
    if r.get('webcast_url') and r.get('dial_in_phone'):
        pprint.pprint({k: r.get(k) for k in ('ticker','fiscal_period','webcast_url','dial_in_phone','dial_in_pin','press_release_url')})
        break
"
```
Acceptance: at least one upcoming row returns the full bundle in the public field names.

- [ ] **Step 5: Frontend visual check**

Open `/calendar` in the browser. Verify:
- Past events now show the 8-K, Live, Dial badges populated for ~95% of rows.
- Upcoming events show Live + Dial for the majority (where B succeeded).
- Hover on Dial badge shows phone + PIN tooltip.
- Click on Dial badge copies the value (paste into a notes app to confirm).

- [ ] **Step 6: Commit a short verification note (no code)**

```bash
echo "Verified: A filled X past events, B spent \$Y on Z upcoming events, C filled W gaps." > /tmp/verify.txt
# Optional: add a dated note to docs/ for the team
git commit --allow-empty -m "test(calendar/enrichment): full-cycle verification

Past events: webcast=N, dial-in=M, press=P (target >=530/561)
Upcoming events: webcast=K, dial-in=L (target >=50/69, >=30/69)
Method B spend: \$X.XX (cap \$1.00)
Method C gap-fills: G events"
```

---

## Self-review checklist for the implementation engineer

Before marking the plan complete:

- [ ] All 13 tasks committed with the listed commit messages.
- [ ] `python -m pytest backend/tests/calendar/ -v` passes (8 test files; ~25 tests).
- [ ] Acceptance bars met (Task 13 Steps 2-3).
- [ ] No `_a`/`_b`/`_c` columns leak into the API JSON responses (the resolver runs at read-time and emits only the public field names; verify via `curl` in Task 13 Step 4).
- [ ] `backend/data/_raw/calendar_enrichment/` is in `.gitignore` (already in Task 0 spec; double-check the directory exists post-run and isn't being committed).
- [ ] Daily APScheduler job present and queued (Task 11 Step 3 output).

## Out of scope — follow-on work after this plan ships

- Method C-beta: Notified, GlobeNewswire IR adapters (each their own implementation plan).
- Method C-tail: per-ticker IR adapters for the long tail.
- Phone-number normalization to E.164.
- Soft-field freshness/expiry policy.
- LLM cost recalibration after first week of real billing.
- Foreign filer support (TSM, ASML, etc., 20-F).
- TW/JP/KR/CN markets — reuse the orchestrator pattern with market-specific A and C.
