"""
source_fetcher.py -- pull qualitative context for margin insights.

Targeted fetch: the caller passes "anchor dates" (peak periods, trough
periods, current period) and we fetch only the filings closest to those
dates. That keeps the prompt small and every source relevant.

Sources:
    1. 8-K item 2.02 (Results of Operations) -- earnings press releases,
       matched to anchor dates within a +/- window.
    2. Latest 10-Q / 10-K MD&A -- one excerpt, provides "current" color.

Future sources (hooks reserved, not yet implemented):
    - User notes / meeting notes -- pull from notes service, filter to
      ticker, treat as anchor="current" or matched to anchor date.
    - Earnings call transcripts -- once a transcript provider is wired in.

Each source can fail independently; full failure returns [] rather than
raising.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

log = logging.getLogger(__name__)


DocType = Literal["8-K", "10-Q", "10-K", "note"]

_MAX_CHARS_PER_DOC  = 3000     # truncate each excerpt
_FILING_SCAN_DEPTH  = 100      # raw 8-K filings to scan before filtering on 2.02
_ANCHOR_MATCH_DAYS  = 120      # an 8-K filing qualifies if filed within +/- N
                               # days of the anchor period_end


@dataclass
class SourceExcerpt:
    title:    str
    doc_type: DocType
    date:     str              # YYYY-MM-DD
    url:      str | None
    text:     str              # already truncated
    anchor:   str              # e.g. "peak-gross", "trough-net", "current"


@dataclass
class SourceTarget:
    """
    An anchor the service wants context for. `period_end` is the calendar
    date that period ended (e.g. NVDA FY2023-Q2 -> 2022-07-31).
    """
    anchor:     str            # label passed through to the excerpt
    period_end: str            # "YYYY-MM-DD"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_margin_sources(
    ticker: str,
    targets: list[SourceTarget],
) -> list[SourceExcerpt]:
    """
    Gather qualitative sources for the given anchor dates. Never raises.

    For each unique anchor date we try to find ONE earnings 8-K filed
    within +/- _ANCHOR_MATCH_DAYS days. In addition we always add the
    latest 10-Q/10-K MD&A and recent news for current-situation context.
    """
    ticker = ticker.upper().strip()
    out: list[SourceExcerpt] = []

    # Dedupe: several margin types may peak/trough in the same quarter.
    seen_dates: set[str] = set()
    deduped: list[SourceTarget] = []
    for t in targets:
        if not t.period_end or t.period_end in seen_dates:
            continue
        seen_dates.add(t.period_end)
        deduped.append(t)

    # ---- 1. Targeted 8-K earnings releases ----
    try:
        out.extend(_fetch_targeted_8ks(ticker, deduped))
    except Exception as exc:
        log.warning("Targeted 8-K fetch failed for %s: %s", ticker, exc)

    # ---- 2. Latest MD&A (one per form) -- current context ----
    try:
        out.extend(_fetch_latest_mdna(ticker))
    except Exception as exc:
        log.warning("MD&A fetch failed for %s: %s", ticker, exc)

    # ---- Future hook: user notes / meeting notes ----
    # try:
    #     out.extend(_fetch_user_notes(ticker))
    # except Exception as exc:
    #     log.warning("Notes fetch failed for %s: %s", ticker, exc)

    return out


# ---------------------------------------------------------------------------
# 1. Targeted 8-Ks -- match by filed-date proximity to each anchor
# ---------------------------------------------------------------------------

def _fetch_targeted_8ks(
    ticker: str,
    targets: list[SourceTarget],
) -> list[SourceExcerpt]:
    if not targets:
        return []

    from edgar import Company, set_identity
    set_identity("AlphaGraph Research alphagraph@research.com")

    company = Company(ticker)
    # Pull one batch of 8-Ks once, then match against all targets in memory.
    # This keeps us under the network/rate limits.
    filings = company.get_filings(form="8-K").head(_FILING_SCAN_DEPTH)

    # Pre-parse the item-2.02 filings we care about.
    earnings_filings: list[tuple[date, object]] = []
    for f in filings:
        items = getattr(f, "items", None) or []
        items_str = ",".join(str(i) for i in items)
        if "2.02" not in items_str:
            continue
        fdate = _filing_date(f)
        if fdate is None:
            continue
        earnings_filings.append((fdate, f))

    if not earnings_filings:
        return []

    out: list[SourceExcerpt] = []
    claimed_accessions: set[str] = set()

    for target in targets:
        target_date = _parse_date(target.period_end)
        if target_date is None:
            continue

        # Pick the earnings filing with the smallest |filed - target| that
        # hasn't already been claimed by another anchor.
        best: tuple[int, object] | None = None
        for fdate, f in earnings_filings:
            delta_days = abs((fdate - target_date).days)
            if delta_days > _ANCHOR_MATCH_DAYS:
                continue
            accession = _accession(f)
            if accession in claimed_accessions:
                continue
            if best is None or delta_days < best[0]:
                best = (delta_days, f)

        if best is None:
            continue

        filing = best[1]
        accession = _accession(filing)
        try:
            text = _extract_filing_text(filing)
        except Exception as exc:
            log.debug("Could not extract 8-K text for %s %s: %s", ticker, accession, exc)
            continue
        if not text:
            continue

        claimed_accessions.add(accession)
        fdate_str = str(_filing_date(filing) or "")
        out.append(SourceExcerpt(
            title   =f"8-K Earnings Release ({fdate_str}) -- anchor: {target.anchor}",
            doc_type="8-K",
            date    =fdate_str,
            url     =_safe_url(filing),
            text    =_truncate(text),
            anchor  =target.anchor,
        ))

    return out


# ---------------------------------------------------------------------------
# 2. Latest MD&A
# ---------------------------------------------------------------------------

_MDNA_HEADERS = [
    "management's discussion and analysis",
    "management's discussion & analysis",
]


def _fetch_latest_mdna(ticker: str) -> list[SourceExcerpt]:
    from edgar import Company, set_identity
    set_identity("AlphaGraph Research alphagraph@research.com")

    company = Company(ticker)
    out: list[SourceExcerpt] = []
    for form in ("10-Q", "10-K"):
        try:
            filings = company.get_filings(form=form).head(1)
        except Exception:
            continue
        for f in filings:
            try:
                text = _extract_filing_text(f)
                mdna = _slice_mdna(text)
            except Exception as exc:
                log.debug("MD&A extract failed for %s %s: %s", ticker, form, exc)
                continue
            if not mdna:
                continue
            fdate_str = str(_filing_date(f) or "")
            out.append(SourceExcerpt(
                title   =f"{form} MD&A ({fdate_str})",
                doc_type=form,  # type: ignore[arg-type]
                date    =fdate_str,
                url     =_safe_url(f),
                text    =_truncate(mdna),
                anchor  ="current",
            ))
    return out


def _slice_mdna(text: str) -> str:
    if not text:
        return ""
    lower = text.lower()
    idx = -1
    for header in _MDNA_HEADERS:
        j = lower.find(header)
        if j >= 0:
            idx = j
            break
    if idx < 0:
        return ""
    return text[idx : idx + _MAX_CHARS_PER_DOC * 3]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_filing_text(filing) -> str:
    for attr in ("markdown", "text"):
        try:
            fn = getattr(filing, attr, None)
            if callable(fn):
                out = fn()
                if out:
                    return _strip_html(str(out))
        except Exception:
            continue
    try:
        return _strip_html(filing.html() or "")
    except Exception:
        return ""


def _strip_html(raw: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", no_tags).strip()


def _truncate(text: str) -> str:
    if len(text) <= _MAX_CHARS_PER_DOC:
        return text
    return text[:_MAX_CHARS_PER_DOC] + " ..."


def _filing_date(filing) -> date | None:
    for attr in ("filing_date", "date_filed", "filed_date"):
        v = getattr(filing, attr, None)
        if v:
            return _parse_date(str(v))
    return None


def _parse_date(raw: str) -> date | None:
    if not raw:
        return None
    raw = str(raw)[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        return None


def _accession(filing) -> str:
    for attr in ("accession_number", "accession_no", "accession"):
        v = getattr(filing, attr, None)
        if v:
            return str(v)
    return str(id(filing))


def _safe_url(filing) -> str | None:
    for attr in ("filing_url", "url", "homepage_url"):
        v = getattr(filing, attr, None)
        if v:
            return str(v)
    return None


