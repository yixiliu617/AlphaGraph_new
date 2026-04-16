"""
ingest_earnings_releases.py -- persistently store 8-K Item 2.02 earnings
press releases for the ticker universe.

Why: source_fetcher.py fetches these on every margin-insights request and
truncates to 3000 chars -- which drops the detailed margin commentary that
lives later in each release. This script pulls the FULL text once and
stores it locally so downstream analysis (margin-commentary extraction,
Pinecone embedding, semantic search) can work from complete documents.

Output: backend/data/earnings_releases/ticker={TICKER}.parquet
  columns: accession_no, filing_date, period_of_report, form, items,
           title, url, text_raw, text_chars, fetched_at
  compression: zstd level 9 (text compresses ~10x)

Idempotent: if a ticker parquet already exists, existing accession_nos
are skipped and only new filings are fetched.

Usage:
    python backend/scripts/ingest_earnings_releases.py              # whole universe
    python backend/scripts/ingest_earnings_releases.py --tickers NVDA
    python backend/scripts/ingest_earnings_releases.py --lookback-years 10
"""
from __future__ import annotations

import argparse
import html
import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger("ingest_earnings_releases")

_REPO_ROOT  = Path(__file__).resolve().parents[2]
_OUT_DIR    = _REPO_ROOT / "backend" / "data" / "earnings_releases"
_TOPLINE_CF = _REPO_ROOT / "backend" / "data" / "filing_data" / "topline" / "cash_flow"

_DEFAULT_LOOKBACK_YEARS = 8
# 8-K filings are dense — scan depth must cover ~lookback_years * ~30 filings/yr
# plus headroom for event-driven filings (executive departures, acquisitions).
_FILING_SCAN_DEPTH = 500


# ---------------------------------------------------------------------------
# Text extraction (shared shape with source_fetcher.py)
# ---------------------------------------------------------------------------

def _strip_html(raw: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", raw)
    # Decode HTML entities (&#160; -> nbsp, &#8226; -> bullet, etc.)
    decoded = html.unescape(no_tags)
    return re.sub(r"\s+", " ", decoded).strip()


def _extract_attachment_text(attachment) -> str:
    """Download the raw HTML of one filing attachment and return clean text."""
    try:
        raw = attachment.download()
    except Exception:
        return ""
    if not raw:
        return ""
    return _strip_html(str(raw))


# ---------------------------------------------------------------------------
# Filing metadata helpers
# ---------------------------------------------------------------------------

def _parse_date(raw) -> date | None:
    if raw is None:
        return None
    s = str(raw)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
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


def _items_str(filing) -> str:
    # edgartools returns .items as a comma-joined string already (e.g.
    # "2.02,9.01"). Older versions returned a list. Handle both.
    items = getattr(filing, "items", None)
    if items is None:
        return ""
    if isinstance(items, str):
        return items
    try:
        return ",".join(str(i) for i in items)
    except TypeError:
        return str(items)


def _period_of_report(filing) -> date | None:
    for attr in ("period_of_report", "period_report", "report_period"):
        v = getattr(filing, attr, None)
        if v:
            d = _parse_date(str(v))
            if d:
                return d
    return None


def _title(filing) -> str:
    for attr in ("description", "title", "form_description"):
        v = getattr(filing, attr, None)
        if v:
            return str(v)
    return ""


# ---------------------------------------------------------------------------
# Universe discovery
# ---------------------------------------------------------------------------

def discover_universe() -> list[str]:
    if not _TOPLINE_CF.exists():
        return []
    return sorted(p.stem.replace("ticker=", "") for p in _TOPLINE_CF.glob("ticker=*.parquet"))


# ---------------------------------------------------------------------------
# Per-ticker ingest
# ---------------------------------------------------------------------------

def ingest_ticker(ticker: str, lookback_years: int = _DEFAULT_LOOKBACK_YEARS) -> dict:
    from edgar import Company, set_identity
    set_identity("AlphaGraph Research alphagraph@research.com")

    out_path = _OUT_DIR / f"ticker={ticker}.parquet"
    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    existing: pd.DataFrame | None = None
    # Primary key is (accession_no, exhibit) — one filing can have multiple
    # EX-99.* exhibits (e.g. NVDA files EX-99.1 press release + EX-99.2 CFO
    # commentary for each earnings release).
    existing_keys: set[tuple[str, str]] = set()
    if out_path.exists():
        try:
            existing = pd.read_parquet(out_path)
            existing_keys = set(
                zip(existing["accession_no"].astype(str), existing["exhibit"].astype(str))
            )
        except Exception as e:
            log.warning("  %s: could not read existing parquet (%s); rebuilding", ticker, e)
            existing = None

    today = date.today()
    cutoff = today.replace(year=today.year - lookback_years)

    company = Company(ticker)
    filings = company.get_filings(form="8-K").head(_FILING_SCAN_DEPTH)

    new_rows: list[dict] = []
    scanned = 0
    earnings_matched = 0
    skipped_existing = 0
    exhibits_extracted = 0

    for f in filings:
        scanned += 1
        items = _items_str(f)
        if "2.02" not in items:
            continue

        fdate = _parse_date(getattr(f, "filing_date", None))
        if fdate is None:
            continue
        if fdate < cutoff:
            continue

        earnings_matched += 1
        accession = _accession(f)
        filing_url = _safe_url(f)
        period = _period_of_report(f) or fdate
        filing_title = _title(f)

        # Pull every EX-99.* attachment from this filing. 99.1 is the press
        # release, 99.2 is often CFO commentary (gold for margin analysis),
        # 99.3+ may be supplementary tables. We skip XBRL/images/binaries.
        try:
            attachments = list(f.attachments)
        except Exception as e:
            log.debug("  %s: cannot list attachments for %s: %s", ticker, accession, e)
            continue

        for att in attachments:
            doc_type = str(getattr(att, "document_type", "") or "")
            if not doc_type.startswith("EX-99."):
                continue
            key = (accession, doc_type)
            if key in existing_keys:
                skipped_existing += 1
                continue

            text = _extract_attachment_text(att)
            if not text:
                continue
            exhibits_extracted += 1

            new_rows.append({
                "accession_no":     accession,
                "exhibit":          doc_type,
                "document":         str(getattr(att, "document", "") or ""),
                "description":      str(getattr(att, "description", "") or ""),
                "filing_date":      pd.Timestamp(fdate),
                "period_of_report": pd.Timestamp(period),
                "form":             "8-K",
                "items":            items,
                "title":            filing_title,
                "url":              filing_url,
                "text_raw":         text,
                "text_chars":       len(text),
                "fetched_at":       pd.Timestamp(datetime.now(timezone.utc)),
            })

    report: dict = {
        "ticker":             ticker,
        "scanned":            scanned,
        "earnings_matched":   earnings_matched,
        "skipped_existing":   skipped_existing,
        "exhibits_extracted": exhibits_extracted,
        "added":              len(new_rows),
    }

    if not new_rows:
        if existing is not None:
            report["total"]      = len(existing)
            report["parquet_kb"] = round(out_path.stat().st_size / 1024, 1)
        else:
            report["total"]      = 0
            report["parquet_kb"] = 0
        return report

    new_df = pd.DataFrame(new_rows)
    if existing is not None and not existing.empty:
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=["accession_no", "exhibit"], keep="last")
    combined = combined.sort_values(["filing_date", "exhibit"]).reset_index(drop=True)

    combined.to_parquet(out_path, compression="zstd", compression_level=9)

    report["total"]      = len(combined)
    report["parquet_kb"] = round(out_path.stat().st_size / 1024, 1)
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest 8-K earnings press releases to local parquet store.")
    ap.add_argument("--tickers", nargs="*", help="Tickers to ingest (default: whole universe)")
    ap.add_argument("--lookback-years", type=int, default=_DEFAULT_LOOKBACK_YEARS,
                    help=f"Years of history to fetch (default {_DEFAULT_LOOKBACK_YEARS})")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    tickers = [t.upper() for t in (args.tickers or discover_universe())]
    if not tickers:
        log.error("No tickers provided and universe is empty.")
        return 1

    log.info("Ingesting 8-K earnings press releases for %d tickers (lookback %d years)",
             len(tickers), args.lookback_years)
    log.info("Output dir: %s", _OUT_DIR)
    log.info("")

    reports: list[dict] = []
    for ticker in tickers:
        try:
            r = ingest_ticker(ticker, lookback_years=args.lookback_years)
            reports.append(r)
            log.info("  %-6s  +%3d new  %3d total  %6s kB  (scanned=%d, matched=%d)",
                     r["ticker"], r.get("added", 0), r.get("total", 0),
                     r.get("parquet_kb", "?"),
                     r.get("scanned", 0), r.get("earnings_matched", 0))
        except Exception as e:
            log.error("  %-6s  FAILED: %s", ticker, e, exc_info=args.verbose)
            reports.append({"ticker": ticker, "error": str(e)})

    total_added = sum(r.get("added", 0) for r in reports)
    total_rows  = sum(r.get("total", 0) for r in reports)
    total_kb    = sum(r.get("parquet_kb", 0) for r in reports if isinstance(r.get("parquet_kb"), (int, float)))
    log.info("")
    log.info("Done. +%d filings; %d total rows across %d tickers; %.1f kB on disk",
             total_added, total_rows, len(tickers), total_kb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
