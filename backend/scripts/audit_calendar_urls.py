"""Generate a human-auditable calendar URL audit file.

Joins events.parquet's _a soft-field columns with the latest validation
log entry per URL, producing two artifacts:

  backend/data/earnings_calendar/url_audit_report.csv   -- one row per event,
      sortable in Excel
  backend/data/earnings_calendar/url_audit_summary.md   -- counts by state +
      top failed hosts + a guide to acting on each state

Per-event columns in the CSV:
  ticker, market, fiscal_period, release_date, status,
  webcast_url, webcast_state, webcast_status_code, webcast_method,
  press_release_url,
  dial_in_phone, dial_in_pin,
  enrichment_a_attempted_at

Validation states from the JSONL log:
  ok                   -- 2xx/206 response. URL works for browsers + Python.
  cdn_block            -- 401/403/406/429/451/501. Bot-blocked but URL exists.
                          A real human can probably open it. Spot-check a few.
  server_error         -- 5xx. Server flaky; real users get retries.
  not_found            -- 404/410. URL definitely does not exist.
  other_4xx            -- 400/etc. Likely broken.
  connection_failure   -- DNS / connection refused / timeout. Domain dead?
  ssl_failure          -- Cert validation failed.
  request_error        -- Other errors (malformed URL, etc.).

Run:
    python -m backend.scripts.audit_calendar_urls                  # uses existing log
    python -m backend.scripts.audit_calendar_urls --revalidate     # validate every URL freshly
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.calendar.storage import read_events  # noqa: E402
from backend.app.services.calendar.enrichment.url_validator import (  # noqa: E402
    check_url, log_validation,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("audit_calendar_urls")

_VALIDATION_LOG = PROJECT_ROOT / "backend" / "data" / "_raw" / "calendar_enrichment" / "url_validation_log.jsonl"
_OUT_CSV        = PROJECT_ROOT / "backend" / "data" / "earnings_calendar" / "url_audit_report.csv"
_OUT_MD         = PROJECT_ROOT / "backend" / "data" / "earnings_calendar" / "url_audit_summary.md"

# Ordering for the markdown summary: most-actionable states first.
_STATE_ORDER = [
    "ok", "cdn_block", "server_error",
    "not_found", "other_4xx", "ssl_failure",
    "connection_failure", "request_error",
]
_STATE_GUIDE = {
    "ok":                  "URL works. No action needed.",
    "cdn_block":           "URL exists but the IR vendor blocked our HEAD/GET. Real browsers can open it. Spot-check a few.",
    "server_error":        "Server returned 5xx. Probably transient. Spot-check; if persistent, the URL may be retired.",
    "not_found":           "URL is gone (404/410). Investigate -- the company likely retired the page.",
    "other_4xx":           "Other client error (e.g. 400). Probably broken.",
    "connection_failure":  "DNS / connection failure. The domain may be dead or the URL malformed.",
    "ssl_failure":         "Cert validation failed. Probably an internal/staging URL that leaked into the press release.",
    "request_error":       "Malformed URL or other request error. Investigate the source text.",
}


def _load_latest_validation_per_url() -> dict[str, dict]:
    """Read the JSONL log and return the most recent record per URL."""
    if not _VALIDATION_LOG.exists():
        log.warning("Validation log not found at %s -- audit will lack state data", _VALIDATION_LOG)
        return {}
    by_url: dict[str, dict] = {}
    for line in _VALIDATION_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = rec.get("url")
        if not url:
            continue
        # Newer record wins (lines appended chronologically).
        by_url[url] = rec
    return by_url


def _revalidate_all_webcasts(df: pd.DataFrame) -> None:
    """Run check_url on every event's webcast_url_a so the log has a
    fresh state record per URL. De-duplicates by URL so each unique
    URL is validated only once even if many events share it."""
    seen: set[str] = set()
    targets: list[tuple[str, str, str]] = []  # (url, ticker, fiscal_period)
    for _, ev in df.iterrows():
        url = ev.get("webcast_url_a")
        if pd.isna(url) or not url:
            continue
        url = str(url)
        if url in seen:
            continue
        seen.add(url)
        targets.append((url, str(ev.get("ticker", "")), str(ev.get("fiscal_period", ""))))
    log.info("revalidating %d unique webcast URLs", len(targets))
    for i, (url, tk, fp) in enumerate(targets, 1):
        try:
            res = check_url(url)
            log_validation(res, url=url, ticker=tk, fiscal_period=fp, layer="a")
            if i % 25 == 0:
                log.info("  validated %d / %d", i, len(targets))
        except Exception as exc:
            log.warning("  [%s %s] validate failed: %s", tk, fp, exc)
    log.info("revalidation complete")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate calendar URL audit.")
    ap.add_argument("--revalidate", action="store_true",
                    help="Re-validate every webcast_url_a freshly before the audit "
                         "(useful when the log is incomplete from older runs).")
    args = ap.parse_args()

    df = read_events()
    log.info("read %d events from events.parquet", len(df))

    if args.revalidate:
        _revalidate_all_webcasts(df)

    val = _load_latest_validation_per_url()
    log.info("loaded %d unique-URL validation records", len(val))

    rows: list[dict] = []
    for _, ev in df.iterrows():
        webcast = ev.get("webcast_url_a")
        webcast_state = ""
        webcast_status_code = ""
        webcast_method = ""
        if pd.notna(webcast) and webcast:
            rec = val.get(str(webcast))
            if rec:
                webcast_state       = str(rec.get("state") or "")
                webcast_status_code = str(rec.get("status_code") or "")
                webcast_method      = str(rec.get("method") or "")

        # release_date as YYYY-MM-DD (pandas Timestamp -> isoformat slice)
        rel = ev.get("release_datetime_utc")
        rel_date = ""
        if pd.notna(rel):
            try:
                rel_date = pd.Timestamp(rel).strftime("%Y-%m-%d")
            except Exception:
                rel_date = str(rel)[:10]

        rows.append({
            "ticker":                     ev.get("ticker", ""),
            "market":                     ev.get("market", ""),
            "fiscal_period":              ev.get("fiscal_period", ""),
            "release_date":               rel_date,
            "status":                     ev.get("status", ""),
            "webcast_url":                webcast if pd.notna(webcast) else "",
            "webcast_state":              webcast_state,
            "webcast_status_code":        webcast_status_code,
            "webcast_method":             webcast_method,
            "press_release_url":          ev.get("press_release_url_a") if pd.notna(ev.get("press_release_url_a")) else "",
            "dial_in_phone":              ev.get("dial_in_phone_a") if pd.notna(ev.get("dial_in_phone_a")) else "",
            "dial_in_pin":                ev.get("dial_in_pin_a") if pd.notna(ev.get("dial_in_pin_a")) else "",
            "enrichment_a_attempted_at":  pd.Timestamp(ev.get("enrichment_a_attempted_at")).isoformat() if pd.notna(ev.get("enrichment_a_attempted_at")) else "",
        })

    out_df = pd.DataFrame(rows)
    out_df = out_df.sort_values(["release_date", "ticker"], ascending=[False, True])
    out_df.to_csv(_OUT_CSV, index=False, encoding="utf-8")
    log.info("wrote %s (%d rows)", _OUT_CSV, len(out_df))

    # ---- Markdown summary ----
    state_counts = Counter()
    no_url_count = 0
    for r in rows:
        if r["webcast_url"]:
            state_counts[r["webcast_state"] or "no_validation_record"] += 1
        else:
            no_url_count += 1

    # Top failed hosts (states other than ok/cdn_block/server_error)
    failing_states = {"not_found", "other_4xx", "connection_failure", "ssl_failure", "request_error"}
    failed_host_counts: Counter = Counter()
    for r in rows:
        if r["webcast_state"] in failing_states and r["webcast_url"]:
            host = urlparse(r["webcast_url"]).netloc.lower()
            if host:
                failed_host_counts[host] += 1

    md_lines: list[str] = []
    md_lines.append("# Calendar URL Audit Report")
    md_lines.append("")
    md_lines.append(f"Generated by `python -m backend.scripts.audit_calendar_urls`.")
    md_lines.append("Full per-event detail in `url_audit_report.csv` (alongside this file).")
    md_lines.append("")
    md_lines.append(f"**Total events**: {len(rows)}")
    md_lines.append(f"**Events with a webcast_url**: {len(rows) - no_url_count} of {len(rows)} ({(len(rows) - no_url_count) / len(rows) * 100:.0f}%)")
    md_lines.append(f"**Events with NO webcast_url**: {no_url_count}")
    md_lines.append("")
    md_lines.append("## Webcast URL state breakdown")
    md_lines.append("")
    md_lines.append("| State | Count | % of populated | What it means / what to do |")
    md_lines.append("|---|---:|---:|---|")
    state_total = sum(state_counts.values()) or 1
    for s in _STATE_ORDER:
        if s in state_counts:
            pct = state_counts[s] / state_total * 100
            md_lines.append(f"| `{s}` | {state_counts[s]} | {pct:.0f}% | {_STATE_GUIDE.get(s, '')} |")
    # Catch any extras
    for s, n in state_counts.items():
        if s not in _STATE_ORDER:
            md_lines.append(f"| `{s}` | {n} | — | (state not in standard guide) |")
    md_lines.append("")

    if failed_host_counts:
        md_lines.append("## Top hosts with failing webcast URLs")
        md_lines.append("")
        md_lines.append("These are URLs that returned 404/connection-failure/etc. — most actionable.")
        md_lines.append("")
        md_lines.append("| Host | Failures |")
        md_lines.append("|---|---:|")
        for host, n in failed_host_counts.most_common(20):
            md_lines.append(f"| {host} | {n} |")
        md_lines.append("")

    # Per-ticker summary, useful for spot-checking
    md_lines.append("## Per-ticker webcast coverage")
    md_lines.append("")
    md_lines.append("| Ticker | Events | URLs populated | ok | cdn_block | failures | None |")
    md_lines.append("|---|---:|---:|---:|---:|---:|---:|")
    by_ticker: dict[str, dict[str, int]] = defaultdict(lambda: {"events": 0, "ok": 0, "cdn_block": 0, "fail": 0, "none": 0})
    for r in rows:
        t = r["ticker"]
        by_ticker[t]["events"] += 1
        if not r["webcast_url"]:
            by_ticker[t]["none"] += 1
        elif r["webcast_state"] == "ok":
            by_ticker[t]["ok"] += 1
        elif r["webcast_state"] == "cdn_block":
            by_ticker[t]["cdn_block"] += 1
        else:
            by_ticker[t]["fail"] += 1
    for t in sorted(by_ticker):
        s = by_ticker[t]
        ticker_populated = s["ok"] + s["cdn_block"] + s["fail"]
        md_lines.append(f"| {t} | {s['events']} | {ticker_populated} | {s['ok']} | {s['cdn_block']} | {s['fail']} | {s['none']} |")
    md_lines.append("")

    md_lines.append("## How to use this report")
    md_lines.append("")
    md_lines.append("1. **Open `url_audit_report.csv` in Excel / Google Sheets**.")
    md_lines.append("2. Filter the `webcast_state` column:")
    md_lines.append("   - `ok` rows: nothing to check.")
    md_lines.append("   - `cdn_block` rows: spot-check 2-3. The IR vendor blocked our automated check, but the URL is real. If the URL opens in your browser, leave it.")
    md_lines.append("   - `not_found` / `connection_failure` / etc.: investigate. The URL is genuinely broken — likely retired by the company. Consider removing or flagging.")
    md_lines.append("3. Press releases (`press_release_url`) are the SEC 8-K filing URL — always 100% populated for past events. No action needed.")
    md_lines.append("4. Empty `dial_in_phone` / `dial_in_pin` cells are expected: most companies (Apple, Microsoft, etc.) don't disclose dial-in info in the press release. Method B (Gemini-grounded, future task) will fill these where the IR page exposes them.")
    md_lines.append("")

    _OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")
    log.info("wrote %s", _OUT_MD)

    # Print a short stdout digest too.
    print()
    print(f"=== Audit complete ===")
    print(f"  CSV: {_OUT_CSV}")
    print(f"  MD:  {_OUT_MD}")
    print()
    coverage = len(rows) - no_url_count
    print(f"Webcast URL coverage: {coverage} / {len(rows)} ({coverage / len(rows) * 100:.0f}%)")
    print(f"State breakdown:")
    for s in _STATE_ORDER:
        if s in state_counts:
            print(f"  {s:<22} {state_counts[s]}")
    no_state = state_counts.get("no_validation_record", 0) + state_counts.get("", 0)
    if no_state:
        print(f"  (no state record)     {no_state} -- run with --revalidate to populate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
