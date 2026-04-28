# Calendar Soft-Fields Enrichment — Design

**Status**: Draft (awaiting user review)
**Author**: Claude (with user review)
**Date**: 2026-04-28
**Scope**: One implementation plan — 3 layered enrichment methods (A + B + C) for the
earnings calendar `events.parquet`. Replaces the placeholder `webcast_url`,
`transcript_url`, `dial_in_phone`, `dial_in_pin`, `press_release_url` columns
that today are universally NULL on upcoming events and partially populated on
past events.

## 1. Goal

Populate the four "soft fields" on every row in `backend/data/earnings_calendar/events.parquet`:

| Field | Description |
|---|---|
| `webcast_url` | Live audio/video stream the company hosts on its IR site |
| `dial_in_phone` | Conference call dial-in number |
| `dial_in_pin` | Conference call access code / PIN |
| `press_release_url` | URL of the IR press release announcing the call |
| `transcript_url` | Post-event transcript (Seeking Alpha / Motley Fool / Bamsec / company) |

Three enrichment methods run in priority order; each only attempts fields that
prior layers left empty. The combined target is ≥95% population for past events
and ≥80% for upcoming events.

## 2. Architecture

```
backend/app/services/calendar/enrichment/
├── __init__.py
├── orchestrator.py             # runs A → B → C, merges results, idempotent
├── press_release_parser.py     # METHOD A
├── llm_grounded.py             # METHOD B
├── budget.py                   # daily $1 cap guard for B
├── url_validator.py            # HEAD then GET-with-Range fallback
└── ir_scrapers/                # METHOD C
    ├── _base.py                # IRAdapter protocol
    ├── _registry.py            # ticker -> adapter resolver
    ├── _vendor_detect.py       # HTML probe for unknown IR pages
    └── q4_inc.py               # first vendor (Phase C-alpha)
                                # additional vendors land in Phase C-beta+
```

Three CLI entry points:

```
backend/scripts/enrich_calendar_a.py    # one-shot: re-parse all earnings_releases
backend/scripts/enrich_calendar_b.py    # daily: LLM-grounded for upcoming events
backend/scripts/enrich_calendar_c.py    # daily: vendor adapters for remaining gaps
```

A wrapper script `backend/scripts/enrich_calendar.py --all` runs all three in
order, suitable for a single APScheduler job.

## 3. Schema changes to `events.parquet`

For full provenance and conflict detection, each method writes to its own
suffixed column. The user-facing API serves resolved values; provenance is
available when needed.

### New columns (per-source storage)

```
webcast_url_a, webcast_url_b, webcast_url_c                    : str | null
dial_in_phone_a, dial_in_phone_b, dial_in_phone_c              : str | null
dial_in_pin_a, dial_in_pin_b, dial_in_pin_c                    : str | null
press_release_url_a, press_release_url_b, press_release_url_c  : str | null
transcript_url_b                                                : str | null
                                                                  (only B sources transcripts)
```

### New metadata columns

```
enrichment_a_attempted_at      : timestamp     (last A run)
enrichment_b_attempted_at      : timestamp
enrichment_c_attempted_at      : timestamp
enrichment_b_cost_usd          : float         (cumulative LLM spend)
enrichment_c_vendor            : str | null    ("q4_inc" | "notified" | ...)
```

### Existing public columns become resolved views

`webcast_url`, `dial_in_phone`, `dial_in_pin`, `press_release_url`,
`transcript_url` continue to be read by the frontend and API exactly as today.
At read time, `storage.read_events()` resolves each from the suffixed columns
in run order:

```
webcast_url = first_non_null(webcast_url_a, webcast_url_b, webcast_url_c)
```

Run order matches priority: A first (free, deterministic), then B (LLM with
validation), then C (canonical vendor scrape, used as last resort for any
field still empty). Any layer that successfully fills a field "wins" — later
layers don't overwrite. C ONLY runs on rows where A+B left at least one field
empty, eliminating redundant scraping.

## 4. Method A — Press-release text parser

**Source**: `backend/data/earnings_releases/ticker=*.parquet` rows where
`items` contains `2.02` (Results of Operations). Each row's `text_raw` column
is the full press-release body, ~5–30 KB of plain text.

**Parser**: Regex passes in priority order, first match wins per field.
The patterns target the standard "conference call" disclosure paragraph that
nearly every US-listed company includes near the bottom of the release.

```python
WEBCAST_RX = re.compile(
    r'(?:webcast|live\s+(?:audio\s+)?stream|listen\s+(?:to\s+the\s+call\s+)?)'
    r'.{0,80}?'
    r'(https?://[^\s)<\]"]+)',
    re.IGNORECASE | re.DOTALL,
)
PHONE_RX = re.compile(
    r'(?:dial-in|domestic|toll[-\s]?free|conference\s+number)'
    r'[^\d]{0,30}'
    r'(\+?[\d][\d\s\-\(\)]{8,18}\d)',
    re.IGNORECASE,
)
PIN_RX = re.compile(
    r'(?:conference\s+id|access\s+code|passcode|pin\s+number)'
    r'[^\d]{0,15}'
    r'(\d{4,12})',
    re.IGNORECASE,
)
```

**URL validation**: Every extracted URL is run through `url_validator.validate_url()`
before storing (see Section 7). Failed validations are NOT stored.

**Idempotence**: Re-running A on already-populated rows is a no-op. The
parser only writes to columns currently null. `enrichment_a_attempted_at` is
always bumped so we know we tried even if no fields matched.

**Coverage estimate**: ~95% of past events. ~30% of upcoming events (only
companies that file a pre-announcement 8-K with Item 7.01 referencing the
press release).

## 5. Method B — LLM-grounded search (Gemini Flash)

**Trigger**: events with `status` in {`upcoming`, `confirmed`} AND
`release_datetime_utc` within next 14 days AND any soft field still null
after Method A.

**Per-event prompt** (Pydantic-validated structured output):

```
SYSTEM: You are an investor-relations data extractor. Use Google Search
grounding to find the official URLs and conference dial-in details for a
specific upcoming earnings call. Return JSON matching the provided schema.
If a field cannot be confidently found, return null for that field. Do NOT
invent URLs or phone numbers.

USER: Find the {ticker} ({company_name}) {fiscal_period} earnings call
details. The call is scheduled for {release_date} ({time_of_day_code} ET).
Return: webcast_url, dial_in_phone, dial_in_pin, press_release_url,
transcript_url (if the call has already happened).
```

**Output schema**:
```python
class CallEnrichment(BaseModel):
    webcast_url:        Optional[HttpUrl]
    dial_in_phone:      Optional[str]
    dial_in_pin:        Optional[str]
    press_release_url:  Optional[HttpUrl]
    transcript_url:     Optional[HttpUrl]
```

**Validation pass after LLM**:
1. Every URL goes through `url_validator.validate_url()`. Failed validations
   are discarded (set to None).
2. Phone numbers are sanity-checked: must contain ≥10 digits, must not be
   an obvious placeholder (`123-456-7890`, `555-`).

**Caching**: One Gemini call per (ticker, fiscal_period) per 7 days, OR per
48 hours if the event is within 48 hours of release_datetime_utc (URLs change
close to call time).

**Cost tracking**: `enrichment_b_cost_usd` accumulates per event. The
`budget.remaining_budget_today()` helper (Section 8) is checked before each
call; the run exits cleanly when the daily $1.00 cap is reached.

**Coverage estimate**: 90%+ of upcoming events on the universe-list 99
tickers, 100% of post-event transcripts within ~48 hours.

## 6. Method C — Per-vendor IR scrapers

**Trigger**: events that still have any soft field null after Methods A + B.
This is the "last resort" path — only invoked for genuine gaps.

**Adapter protocol**:
```python
class IRAdapter(Protocol):
    vendor_name: str

    def detect(self, ir_url: str) -> bool:
        """Return True if this adapter can handle the given IR URL."""
        ...

    def fetch_event(self, ticker: str, fiscal_period: str) -> SoftFields:
        """Return the four soft-field URLs+strings, or None per field."""
        ...
```

**`SoftFields` shape** matches the per-source columns:
```python
@dataclass
class SoftFields:
    webcast_url:        Optional[str] = None
    dial_in_phone:      Optional[str] = None
    dial_in_pin:        Optional[str] = None
    press_release_url:  Optional[str] = None
```

**Phase rollout** (each phase is a separate sub-task in the implementation plan):

| Phase | Adapter | Universe coverage |
|---|---|---|
| C-alpha | Q4 Inc | ~40% (NVDA, AAPL, MSFT, AVGO, CDNS, AMD, others) |
| C-beta | Notified, GlobeNewswire IR | ~25% additional |
| C-tail | Per-ticker adapters for long tail | Approach 100% |

The MVP implementation plan covers **C-alpha only**. C-beta and C-tail are
deferred to follow-on work — they're independent adapters that don't touch
the orchestrator.

**Q4 Inc adapter specifics** (most companies use Q4):
```
Pattern: investor.<company>.com/events/event-details/<slug>
Webcast URL: in <script type="application/ld+json"> as schema.org Event
             with `recordingUrl` or `videoUrl`
Dial-in:     in the page body, in a panel like
             <div class="cnv-tabs__panel" data-section="dial-in">
Press release: linked from the event page as "Press Release" button
```

**Vendor detection**: For tickers without an explicit adapter mapping, the
`_vendor_detect.py` module fetches the company's IR root page once, sniffs
for vendor markers (`q4cdn.com`, `notified.com`, `globenewswire.com`), and
caches the mapping in `backend/data/_raw/calendar_enrichment/vendor_map.json`.

**URL validation**: same `url_validator.validate_url()` as A and B.

## 7. URL validation — `url_validator.py`

Per user direction. HEAD-first with GET-with-Range fallback, browser User-Agent.

```python
import requests

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

def validate_url(url: str, *, timeout: float = 5.0) -> bool:
    """Confirm a URL is reachable without downloading the body.

    Step 1: HEAD with browser User-Agent. 2xx -> True.
    Step 2 (HEAD failed/4xx/5xx): GET with Range: bytes=0-0. 200 or 206 -> True.

    Some CDNs (Cloudflare default, certain IR vendors) reject HEAD with 405.
    Range-aware servers respond 206 Partial Content (1 byte).
    Range-unaware servers respond 200 OK; we still abort after first chunk
    via stream=True.

    Returns False on connection errors, timeouts, or any non-2xx response.
    """
    headers = {"User-Agent": _BROWSER_UA, "Accept": "*/*"}
    try:
        r = requests.head(url, headers=headers, allow_redirects=True,
                          timeout=timeout)
        if 200 <= r.status_code < 300:
            return True
    except requests.RequestException:
        pass
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
            r.close()  # don't drain body
    except requests.RequestException:
        return False
```

Validation is unconditional: every URL from any layer passes through this
function before being stored. Validation failures are logged but don't
crash the layer.

## 8. Budget guard — `budget.py`

```python
DAILY_CAP_USD = 1.00
COST_PER_GEMINI_CALL_USD = 0.025  # Flash with grounding; refined after first day

def remaining_budget_today() -> float:
    today_start = pd.Timestamp.now(tz="UTC").normalize()
    df = read_events()
    spent_today = df.loc[
        (df["enrichment_b_attempted_at"] >= today_start),
        "enrichment_b_cost_usd",
    ].fillna(0.0).sum()
    return max(0.0, DAILY_CAP_USD - float(spent_today))
```

`enrich_calendar_b.py` calls `remaining_budget_today()` before each Gemini
invocation. Below `COST_PER_GEMINI_CALL_USD`, it logs `budget exhausted`
and exits cleanly — remaining events get picked up the next day.

## 9. Cache-first compliance

Per CLAUDE.md § "External-Data Cache-First Rule", every method persists
raw payloads before parsing.

```
backend/data/_raw/calendar_enrichment/
├── a/<accession_no>.txt              # raw text_raw snapshot used by A
├── b/<ticker>_<fiscal_period>.json   # raw Gemini response (full payload)
└── c/<vendor>/<ticker>_<fiscal_period>.html   # raw IR page HTML
```

`backend/data/_raw/` is already gitignored (commit 67f18847). The bronze
caches let us re-parse with updated regex/prompts/adapters without re-paying
Gemini or re-fetching IR pages.

## 10. Daily run sequence

`enrich_calendar.py --all` runs:

1. **A**: parse all rows in `earnings_releases/` whose `(ticker, accession_no)`
   maps to an `events.parquet` row with at least one null soft field. Re-run
   is cheap (text already cached).
2. **B**: for upcoming events (next 14 days) with any field still null after A,
   query Gemini-grounded until budget exhausted.
3. **C**: for events with any field still null after A + B, run the
   ticker's vendor adapter if one exists.

The wrapper script logs:
- per-method counts: `A: filled X webcasts, Y dial-ins...`
- per-method validation rejections (URL failed HEAD + GET-Range)
- B's spend and remaining budget at exit
- C's per-vendor counts

Recommended schedule (APScheduler, fits existing pattern):
```
"enrich_calendar"   daily 06:30 UTC   (after refresh_calendar_us at 06:00)
```

## 11. Test plan

### Method A
- Unit tests on a corpus of 50 hand-picked text snippets from
  `earnings_releases` (5 per top-coverage ticker). Asserts each regex
  extracts the expected URL/phone/PIN.
- Negative tests: malformed text, no conference-call section,
  HTML-encoded URLs, internationally formatted numbers.

### Method B
- Mock-Gemini integration test: stub the SDK, return canned JSON, verify
  the orchestrator stores correctly.
- Live smoke test (run nightly, max 1 call): query for one specific upcoming
  event, assert at least 2 of 4 soft fields populated.
- Budget guard test: simulate `enrichment_b_cost_usd` near cap, assert
  the next call is rejected.

### Method C
- Per-adapter unit tests with HTML fixtures committed under
  `backend/tests/fixtures/ir_scrapers/q4_inc/`. Covers happy path + 4
  edge cases per vendor (event not found, JSON-LD malformed, dial-in
  panel rendered server-side vs client-side, redirect to legacy URL).
- Vendor detection test: feed unknown HTML, assert correct adapter
  routing or "no adapter".

### URL validator
- Mock `requests` with various HTTP responses (200, 206, 301→200, 405,
  403, 500, timeout). Assert each path returns the documented bool.
- Live smoke test: 5 known-good URLs from real IR pages.

### Resolver
- Synthesize an event row with all 12 suffixed columns. Assert the served
  fields match the documented priority (A > B > C run order; first non-null
  wins).

## 12. Frontend changes

Minimal — the existing CalendarView already renders 8-K, Live, Transcript
links when the corresponding URL is non-null. After this work, those links
will populate for many more rows.

Two additions:

1. **Dial-in tooltip**: when `dial_in_phone` is non-null, render a small
   phone icon next to the Live link. Hover reveals phone + PIN as
   click-to-copy strings.

2. **Source badge** (optional, debug-mode only): a tiny letter badge after
   the source pill — `A` (cyan) / `B` (purple) / `C` (emerald) — showing
   which layer filled the soft fields. Hidden by default; shown when
   `?debug=1` query param is present.

## 13. Out of scope

- Method C-beta and C-tail adapters (Notified, GlobeNewswire IR, long-tail
  per-ticker). These ship as follow-on PRs after the orchestrator and
  C-alpha land.
- Foreign-exchange tickers (TSM, ASML, etc.) which file 20-F not 10-K and
  whose IR pages run on different vendors. Deferred per existing policy.
- Taiwan/JP/KR/CN markets. Soft-fields enrichment for those will reuse
  the orchestrator pattern but with market-specific Methods A and C.
- Phone-number formatting (E.164 normalization). Stored as-extracted; the
  frontend does best-effort display formatting.

## 14. Acceptance criteria

A run of `python -m backend.scripts.enrich_calendar --all` against the
current `events.parquet` produces:

- Method A: ≥530 of the 561 past events have webcast_url + press_release_url
  + (dial_in_phone OR dial_in_pin). Validation pass rate ≥95%.
- Method B: at least 50 of the 69 upcoming events have webcast_url
  populated, ≥40 have press_release_url, ≥30 have dial_in_phone.
  Daily spend ≤$1.00.
- Method C-alpha (Q4 Inc): for the ~40 universe tickers on Q4 Inc, all
  events with any A+B gap get the gap closed where the IR page has the
  data.

Frontend: when navigating to `/calendar` after the run, ≥80% of upcoming
event rows show at least the Live link, and a representative spot-check of
10 past events shows all four URL/phone fields populated.

## 15. Open follow-ups (post-merge)

- Adapter health monitoring: weekly run that asserts each Q4 Inc adapter
  selector still resolves, alerts on regressions.
- LLM cost calibration: after first week, recompute
  `COST_PER_GEMINI_CALL_USD` from actual billing data.
- Long-tail per-ticker IR adapters for tickers not covered by C-alpha.
- Soft-field freshness policy: when does a previously-validated webcast URL
  expire? (suggest: when release_datetime_utc + 90 days, archive the URL
  but stop re-validating).
