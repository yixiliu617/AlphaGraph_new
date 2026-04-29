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
    check_url, log_validation,
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
        | df["press_release_url_a"].apply(_is_empty)
    )
    return df[needs]


def _accession_from_source_id(source_id: str) -> str:
    """Extract the accession number from a source_id.

    Older rows store the bare accession_no (e.g. "0001045810-18-000052").
    Future rows may use a colon-separated form (e.g.
    "ticker:accession_no:exhibit"). Be liberal in what we accept: if a
    colon is present, take the second segment if it looks like an
    accession; otherwise return the source_id unchanged.
    """
    if not source_id:
        return ""
    if ":" in source_id:
        parts = source_id.split(":")
        # ticker:accession:exhibit -- accession in slot 1
        if len(parts) >= 2 and "-" in parts[1]:
            return parts[1]
        # accession:exhibit -- accession in slot 0
        if "-" in parts[0]:
            return parts[0]
        return parts[0]
    return source_id


def _load_release_text(ticker: str, accession_no: str) -> str | None:
    """Return the press-release body text for (ticker, accession).

    A single accession often has multiple exhibits (EX-99.1 press release,
    EX-99.2 CFO commentary, etc.). We prefer the one whose description
    contains "PRESS RELEASE" or whose exhibit is EX-99.1; otherwise we
    fall back to the row with the most text.
    """
    p = _RELEASES_DIR / f"ticker={ticker}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    sub = df[df["accession_no"] == accession_no]
    if sub.empty:
        return None

    # Prefer a row whose description mentions "PRESS RELEASE".
    desc_match = sub[sub["description"].fillna("").str.upper().str.contains(
        "PRESS RELEASE", na=False
    )]
    if not desc_match.empty:
        chosen = desc_match.iloc[0]
    else:
        # Prefer EX-99.1 (the canonical press release exhibit).
        ex991 = sub[sub["exhibit"].fillna("") == "EX-99.1"]
        if not ex991.empty:
            chosen = ex991.iloc[0]
        else:
            # Fall back to the longest text row.
            chosen = sub.sort_values("text_chars", ascending=False).iloc[0]

    text = chosen.get("text_raw")
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
    skipped_no_text = 0
    skipped_no_accession = 0
    webcast_extracted = 0
    webcast_validated = 0
    webcast_failed_validation = 0
    phone_extracted = 0
    pin_extracted = 0
    counters: dict[str, int] = {}
    counters_state: dict[str, int] = {}

    for _, ev in target.iterrows():
        try:
            ticker = ev["ticker"]
            source_id = str(ev.get("source_id") or "")
            accession = _accession_from_source_id(source_id)
            if not accession:
                skipped_no_accession += 1
                continue
            text = _load_release_text(ticker, accession)
            if not text:
                skipped_no_text += 1
                continue

            fields = parse_press_release(text)

            if fields["webcast_url"]:
                webcast_extracted += 1
                result = check_url(fields["webcast_url"])
                log_validation(
                    result, url=fields["webcast_url"],
                    ticker=ticker, fiscal_period=ev["fiscal_period"], layer="a",
                )
                counters_state[result.state] = counters_state.get(result.state, 0) + 1
                if not result.valid:
                    log.info("[%s %s] webcast URL failed validation (state=%s status=%s): %s",
                             ticker, ev["fiscal_period"], result.state,
                             result.status_code, fields["webcast_url"])
                    fields["webcast_url"] = None
                    webcast_failed_validation += 1
                else:
                    webcast_validated += 1

            if fields["dial_in_phone"]:
                phone_extracted += 1
            if fields["dial_in_pin"]:
                pin_extracted += 1

            # Build the upsert row -- only the keys that are populated, plus the
            # required keying fields. upsert_events skips empty values via _is_empty.
            upd: dict = {
                "ticker":        ticker,
                "market":        ev["market"],
                "fiscal_period": ev["fiscal_period"],
                "enrichment_a_attempted_at": now,
            }
            if fields["webcast_url"]:
                upd["webcast_url_a"] = fields["webcast_url"]
            if fields["dial_in_phone"]:
                upd["dial_in_phone_a"] = fields["dial_in_phone"]
            if fields["dial_in_pin"]:
                upd["dial_in_pin_a"] = fields["dial_in_pin"]
            # Press release URL: use the SEC filing URL we already have.
            if not _is_empty(ev.get("filing_url")):
                upd["press_release_url_a"] = ev["filing_url"]
            updated_rows.append(upd)
        except Exception as exc:
            log.warning("[%s %s] event failed: %s",
                        ev.get("ticker"), ev.get("fiscal_period"), exc)
            counters["failed_event"] = counters.get("failed_event", 0) + 1
            continue

    state_summary = " ".join(
        f"{k}={v}" for k, v in sorted(counters_state.items(), key=lambda x: -x[1])
    ) or "(none)"
    log.info(
        "Method A extraction stats: webcast extracted=%d validated=%d failed=%d "
        "phone=%d pin=%d skipped_no_text=%d skipped_no_accession=%d failed_events=%d",
        webcast_extracted, webcast_validated, webcast_failed_validation,
        phone_extracted, pin_extracted, skipped_no_text, skipped_no_accession,
        counters.get("failed_event", 0),
    )
    log.info("webcast states: %s", state_summary)

    if not updated_rows:
        log.info("Method A: no rows to write. | webcast states: %s", state_summary)
        return 0

    stats = upsert_events(updated_rows)
    log.info(
        "Method A done: inserted=%d updated=%d touched=%d | webcast states: %s",
        stats.inserted, stats.updated, stats.touched, state_summary,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
