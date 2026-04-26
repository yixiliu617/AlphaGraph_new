"""
TSMC quarterly-report archive crawler.

Three phases (each idempotent + skippable):
  A. Enumerate every (year, quarter) → list of 5 PDFs by walking the SPA
     year/quarter selectors at /chinese/quarterly-results. Saves an index
     JSON at backend/data/financials/raw/2330.TW/_index.json.
  B. Download each PDF via page.evaluate(fetch) (the only CF-bypassing
     method) and cache to backend/data/financials/raw/2330.TW/{year}/{Q}/
     {type}.pdf. Re-runs are no-ops when the cached file matches the
     enum'd sha256 (stored alongside on disk for verification).
  C. Run extract_pdf() on each cached management_report.pdf and write
     bronze JSON + silver Parquet rows.

Usage:
    python tools/tsmc_archive_crawler.py                       # all phases, all years
    python tools/tsmc_archive_crawler.py --years 2024,2025,2026
    python tools/tsmc_archive_crawler.py --phase A              # only enumerate
    python tools/tsmc_archive_crawler.py --phase B              # only download
    python tools/tsmc_archive_crawler.py --phase C              # only extract
    python tools/tsmc_archive_crawler.py --refresh-index        # re-walk SPA even if _index.json exists

See `.claude/skills/tsmc-quarterly-reports/SKILL.md` for the full design notes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

from playwright.sync_api import Page, sync_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from backend.scripts.extractors import tsmc_management_report as mgmt   # noqa: E402
from backend.scripts.extractors import tsmc_transcript as transcript     # noqa: E402

# Dedicated profile for the TSMC crawler so it doesn't fight other Playwright
# sessions or the user's day-to-day Chrome over the same user_data_dir.
PROFILE = Path("C:/Users/Sharo/.alphagraph_tsmc_profile")
INDEX_URL = "https://investor.tsmc.com/chinese/quarterly-results"
TICKER = "2330.TW"

DATA_ROOT = REPO_ROOT / "backend" / "data" / "financials"
RAW_TICKER_ROOT = DATA_ROOT / "raw" / TICKER
INDEX_FILE = RAW_TICKER_ROOT / "_index.json"


# ---------------------------------------------------------------------------
# Filename classification
# ---------------------------------------------------------------------------

# Chinese labels are TSMC's stable signal for what kind of doc this is —
# filename naming has drifted ("ManagementReport.pdf" vs "Management Report.pdf",
# "FS.pdf" vs "FS_audited.pdf"). Match on label first; fall back to filename.
LABEL_TYPE_MAP: dict[str, str] = {
    "營運績效報告":    "management_report",
    "法人說明會簡報":  "presentation",
    "法人說明會逐字稿": "transcript",
    "營收新聞稿":      "earnings_release",
    "財務報表":        "financial_statements",
}

PDF_TYPE_FILENAME_PATTERNS: list[tuple[str, str]] = [
    # Filename matching is normalised to lowercase + whitespace stripped.
    (r"management\s*report",        "management_report"),
    (r"presentation",               "presentation"),
    (r"earnings\s*release",         "earnings_release"),
    (r"transcript",                 "transcript"),
    (r"^fs($|[._])|financial",      "financial_statements"),
]


def classify_pdf(url: str, label: str = "") -> str:
    """Classify by Chinese label first (stable), then fall back to URL filename."""
    label_clean = (label or "").strip()
    if label_clean in LABEL_TYPE_MAP:
        return LABEL_TYPE_MAP[label_clean]
    fname = unquote(Path(urlparse(url).path).name).lower()
    for pat, kind in PDF_TYPE_FILENAME_PATTERNS:
        if re.search(pat, fname):
            return kind
    return "unknown"


# ---------------------------------------------------------------------------
# Phase A — Enumeration
# ---------------------------------------------------------------------------

def _click_year(page: Page, year: str) -> bool:
    """Click the <a> whose innerText is exactly the 4-digit year. Returns True
    on success. The year-selector strip collapses neighbors after a click —
    callers must re-navigate to /quarterly-results before jumping to a far
    year."""
    return bool(page.evaluate(
        """y => {
            const a = Array.from(document.querySelectorAll('a, button, [role="button"]'))
                .find(e => e.innerText && e.innerText.trim() === y);
            if (!a) return false;
            a.scrollIntoView({block: 'center'});
            a.click();
            return true;
        }""",
        year,
    ))


def _detect_quarter_tabs(page: Page) -> list[str]:
    """After a year is selected, return the list of available quarter-tab
    labels (e.g. ['Q1','Q2','Q3','Q4']). Tries several common shapes — the
    SPA has used both 'Q1'/'Q2' and '第一季'/'第二季' over time."""
    return page.evaluate(
        """() => {
            const seen = new Set();
            const candidates = Array.from(document.querySelectorAll(
                'a, button, [role="tab"], li, span, div'
            ));
            for (const e of candidates) {
                const t = (e.innerText || '').trim();
                if (/^Q[1-4]$/.test(t)) seen.add(t);
                if (/^第[一二三四]季$/.test(t)) {
                    const m = {'第一季':'Q1','第二季':'Q2','第三季':'Q3','第四季':'Q4'};
                    seen.add(m[t]);
                }
            }
            return Array.from(seen).sort();
        }"""
    )


def _click_quarter(page: Page, quarter_label: str) -> bool:
    """Click a Q tab. The TSMC SPA renders these as
        <li><a class="ga-tab-quaterly">Q1</a></li>
    (note the typo "quaterly"). Click the <a> directly so the SPA's
    own click handler fires — clicking the <li> wrapper or some other
    ancestor doesn't trigger the route change."""
    return bool(page.evaluate(
        """qen => {
            const tabs = Array.from(document.querySelectorAll('a.ga-tab-quaterly'));
            const target = tabs.find(a => (a.innerText || '').trim() === qen);
            if (!target) return false;
            target.scrollIntoView({block: 'center'});
            target.click();
            return true;
        }""",
        quarter_label,
    ))


def _debug_dump_q_tabs(page: Page) -> str:
    """One-off diagnostic: dump everything that looks like a Q tab to stdout."""
    return page.evaluate(
        """() => {
            const out = [];
            for (const e of document.querySelectorAll('a, button, li, span, div, [role="tab"]')) {
                const t = (e.innerText || '').trim();
                if (/^Q[1-4]$/.test(t) || /^第[一二三四]季$/.test(t)) {
                    const cls = (e.className || '').toString().slice(0, 60);
                    const id = (e.id || '').slice(0, 30);
                    const role = (e.getAttribute && e.getAttribute('role')) || '';
                    out.push(`<${e.tagName.toLowerCase()} id=${id!r ?? ''} role=${role} class=${cls}>: ${t}`);
                }
            }
            return out.join('\\n');
        }"""
    )


def _extract_pdf_anchors(page: Page) -> list[dict]:
    """All anchors whose href ends in `.pdf` and lives under /reports/."""
    return page.evaluate(
        """() => {
            const out = [];
            for (const a of document.querySelectorAll('a[href]')) {
                const href = a.href;
                if (!/\\.pdf(\\?|$)/i.test(href)) continue;
                if (!/reports\\//.test(href)) continue;
                out.push({label: (a.innerText || '').trim(), href: href});
            }
            return out;
        }"""
    )


def _safe_title(page: Page) -> str:
    """page.title() can fire mid-navigation; retry on the navigation race."""
    for _ in range(4):
        try:
            return page.title()
        except Exception:
            page.wait_for_timeout(800)
    return ""


def _wait_settled(page: Page, ms: int = 2000) -> None:
    """Wait for the SPA to settle after a click. Tries domcontentloaded first
    (cheap), then falls back to a fixed timeout — TSMC's page is single-route
    and clicks don't always trigger a real document navigation."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=4000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def enumerate_archive(page: Page, years: list[str] | None = None) -> dict:
    """Phase A: walk year/quarter selectors and capture every PDF URL.
    Re-navigates to INDEX_URL between years — the year-selector strip
    collapses neighbors after a click, so non-adjacent year jumps fail
    without a fresh load. Validates each (year,Q) tab by checking the
    page title actually advertises that quarter; that lets us skip
    'fake' Q tabs (e.g. Q2/Q3/Q4 of the current year before they're
    published)."""
    page.goto(INDEX_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3000)

    all_years = page.evaluate(
        """() => Array.from(document.querySelectorAll('a'))
            .map(a => (a.innerText || '').trim())
            .filter(t => /^(19|20)\\d{2}$/.test(t))"""
    )
    all_years = sorted(set(all_years), reverse=True)
    target_years = [y for y in all_years if (years is None or y in years)]
    print(f"[enum] year selector: {len(all_years)} years available; will crawl {len(target_years)}")
    if target_years:
        print(f"[enum] target years: {target_years[0]}..{target_years[-1]}")

    quarters: dict[str, dict] = {}
    for i, year in enumerate(target_years, 1):
        # Try up to 2 attempts per year — the SPA has occasional transient
        # races where the click lands but the PDF list isn't repainted yet.
        for attempt in (1, 2):
            try:
                page.goto(INDEX_URL, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                print(f"  [{i}/{len(target_years)}] {year} (try {attempt}) goto failed: {e}")
                page.wait_for_timeout(2000)
                continue
            page.wait_for_timeout(1800 if attempt == 1 else 3500)

            if not _click_year(page, year):
                if attempt == 1:
                    continue
                print(f"  [{i}/{len(target_years)}] {year}  SKIP — anchor not in DOM (year picker collapsed)")
                break
            _wait_settled(page, 2000 if attempt == 1 else 4000)
            break  # year click succeeded; proceed to quarter loop
        else:
            continue

        year_title = _safe_title(page)
        q_tabs = _detect_quarter_tabs(page)
        # Always also capture whatever is currently visible — gives us 1
        # quarter even if the Q-tab clicker turns out to be unreliable.
        m = re.search(r"\bQ([1-4])\b", year_title)
        captured_any = False

        if q_tabs:
            for q in q_tabs:
                if not _click_quarter(page, q):
                    continue
                _wait_settled(page, 1500)
                q_title = _safe_title(page)
                # Validate: the displayed title must actually advertise this Q,
                # otherwise the tab is decorative-only (e.g. Q3 tab in a year
                # whose 3Q hasn't been published yet just shows latest).
                if f"Q{q[-1]}" not in q_title:
                    continue
                pdfs = _extract_pdf_anchors(page)
                if not pdfs:
                    continue
                quarters[f"{year}/{q}"] = {
                    "title": q_title,
                    "pdfs": [
                        {"label": p["label"], "type": classify_pdf(p["href"], p["label"]), "url": p["href"]}
                        for p in pdfs
                    ],
                }
                print(f"  [{i}/{len(target_years)}] {year} {q}: {len(pdfs)} PDFs  ({q_title})")
                captured_any = True
        elif m:
            # Only a single Q on display; capture it.
            q = f"Q{m.group(1)}"
            pdfs = _extract_pdf_anchors(page)
            if pdfs:
                quarters[f"{year}/{q}"] = {
                    "title": year_title,
                    "pdfs": [
                        {"label": p["label"], "type": classify_pdf(p["href"], p["label"]), "url": p["href"]}
                        for p in pdfs
                    ],
                }
                print(f"  [{i}/{len(target_years)}] {year} {q} (no tabs, single quarter): {len(pdfs)} PDFs")
                captured_any = True

        if not captured_any:
            print(f"  [{i}/{len(target_years)}] {year}  SKIP — nothing captured  (title={year_title!r})")

    return {
        "ticker": TICKER,
        "enumerated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "index_url": INDEX_URL,
        "quarters": quarters,
    }


def save_index(index: dict) -> Path:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return INDEX_FILE


def load_index() -> dict | None:
    if not INDEX_FILE.exists():
        return None
    return json.loads(INDEX_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Phase B — Download
# ---------------------------------------------------------------------------

def _pdf_cache_path(year: str, quarter: str, pdf_type: str) -> Path:
    return RAW_TICKER_ROOT / year / quarter / f"{pdf_type}.pdf"


def fetch_pdf_via_page(page: Page, url: str) -> bytes | None:
    """Run fetch(url) inside the page JS context — only CF-bypassing path.
    Caller must have done page.goto(INDEX_URL) once first to warm cookies."""
    result = page.evaluate(
        """async (u) => {
            try {
                const r = await fetch(u, {credentials: 'include'});
                if (!r.ok) return {err: 'http ' + r.status};
                const buf = await r.arrayBuffer();
                let s = '';
                const bytes = new Uint8Array(buf);
                for (let i=0; i<bytes.byteLength; i++) s += String.fromCharCode(bytes[i]);
                const ct = r.headers.get('content-type') || '';
                return {b64: btoa(s), ct: ct, len: bytes.byteLength};
            } catch (e) {
                return {err: 'exc ' + e.message};
            }
        }""",
        url,
    )
    if "err" in result:
        print(f"      fetch err: {result['err']}")
        return None
    if "pdf" not in (result.get("ct") or "").lower():
        print(f"      not pdf: ct={result.get('ct')!r} len={result.get('len')}")
        return None
    return base64.b64decode(result["b64"])


def download_archive(page: Page, index: dict, only_types: set[str] | None = None) -> dict:
    """Phase B: download every PDF in the index, caching by content hash."""
    page.goto(INDEX_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3000)

    summary = {"total": 0, "cached": 0, "downloaded": 0, "failed": 0}
    for yq, info in index["quarters"].items():
        year, quarter = yq.split("/")
        for pdf in info["pdfs"]:
            kind = pdf["type"]
            if kind == "unknown":
                continue
            if only_types and kind not in only_types:
                continue
            summary["total"] += 1
            out_path = _pdf_cache_path(year, quarter, kind)
            if out_path.exists() and out_path.stat().st_size > 1024:
                # Trust the cached file (size > 1KB rules out HTML challenge dumps).
                summary["cached"] += 1
                continue
            print(f"  [{yq} / {kind}]  fetching {pdf['url'][:90]}…")
            body = fetch_pdf_via_page(page, pdf["url"])
            if body is None or not body.startswith(b"%PDF"):
                print(f"      FAILED — got {len(body) if body else 0} bytes, head={body[:8] if body else None!r}")
                summary["failed"] += 1
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(body)
            sha = hashlib.sha256(body).hexdigest()
            (out_path.with_suffix(".sha256")).write_text(sha, encoding="utf-8")
            print(f"      saved {len(body):,} bytes  sha256={sha[:12]}…")
            summary["downloaded"] += 1
            time.sleep(1.0)  # polite throttle to TSMC's CDN
    return summary


# ---------------------------------------------------------------------------
# Phase C — Extract
# ---------------------------------------------------------------------------

def extract_archive(index: dict, only_types: set[str] | None = None) -> dict:
    """Phase C: run extractors over cached PDFs.

    Currently dispatches by document type:
      management_report -> tsmc_management_report.extract_pdf -> long-format facts
      transcript        -> tsmc_transcript.extract_pdf -> speaker-turn rows
    """
    summary = {"mgmt_total": 0, "mgmt_extracted": 0, "mgmt_errors": 0,
               "txn_total": 0, "txn_extracted": 0, "txn_errors": 0}
    for yq, info in index["quarters"].items():
        year, quarter = yq.split("/")
        q_num = quarter[1]
        yy = year[-2:]
        period_label = f"{q_num}Q{yy}"

        # ---- Management report ----
        if only_types is None or "management_report" in only_types:
            pdf_path = _pdf_cache_path(year, quarter, "management_report")
            if pdf_path.exists():
                summary["mgmt_total"] += 1
                url = next((p["url"] for p in info["pdfs"] if p["type"] == "management_report"), None)
                try:
                    bronze, facts = mgmt.extract_pdf(
                        pdf_path, ticker=TICKER,
                        report_period_label=period_label, source_url=url,
                    )
                    mgmt.write_bronze(bronze)
                    mgmt.upsert_silver(facts, ticker=TICKER)
                    print(f"  [{yq}] mgmt: {len(facts)} facts")
                    summary["mgmt_extracted"] += 1
                except Exception as e:
                    print(f"  [{yq}] mgmt FAIL: {e}")
                    summary["mgmt_errors"] += 1

        # ---- Transcript ----
        if only_types is None or "transcript" in only_types:
            pdf_path = _pdf_cache_path(year, quarter, "transcript")
            if pdf_path.exists():
                summary["txn_total"] += 1
                url = next((p["url"] for p in info["pdfs"] if p["type"] == "transcript"), None)
                try:
                    bronze, turns = transcript.extract_pdf(
                        pdf_path, ticker=TICKER, source_url=url,
                    )
                    transcript.write_bronze(bronze)
                    transcript.upsert_silver(turns, ticker=TICKER)
                    print(f"  [{yq}] transcript: {len(turns)} turns")
                    summary["txn_extracted"] += 1
                except Exception as e:
                    print(f"  [{yq}] transcript FAIL: {e}")
                    summary["txn_errors"] += 1

    return summary


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--years", help="Comma-separated subset, e.g. 2024,2025,2026")
    p.add_argument("--phase", choices=["A", "B", "C"], help="Run only one phase")
    p.add_argument("--refresh-index", action="store_true", help="Force re-enumerate even if _index.json exists")
    p.add_argument("--types", help="Comma-separated PDF types to download (default: all known)")
    args = p.parse_args()

    target_years = [y.strip() for y in args.years.split(",")] if args.years else None
    only_types = set(t.strip() for t in args.types.split(",")) if args.types else None

    do_A = args.phase in (None, "A")
    do_B = args.phase in (None, "B")
    do_C = args.phase in (None, "C")

    needs_browser = do_A or do_B
    if needs_browser:
        with sync_playwright() as plw:
            ctx = plw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                headless=False,
                channel="chrome",
                args=["--start-maximized"],
                no_viewport=True,
            )
            page = ctx.new_page()

            # Phase A
            if do_A:
                if INDEX_FILE.exists() and not args.refresh_index:
                    print(f"[A] Using cached _index.json (pass --refresh-index to re-walk)")
                    index = load_index()
                else:
                    print("[A] Enumerating archive…")
                    index = enumerate_archive(page, years=target_years)
                    save_index(index)
                    print(f"[A] Saved -> {INDEX_FILE} ({len(index['quarters'])} quarters)")
            else:
                index = load_index()
                if index is None:
                    print("ERROR: no _index.json — run Phase A first.")
                    return 1

            # Phase B
            if do_B:
                print("[B] Downloading PDFs…")
                summary = download_archive(page, index, only_types=only_types)
                print(f"[B] {summary}")

            ctx.close()
    else:
        index = load_index()
        if index is None:
            print("ERROR: no _index.json — run Phase A first.")
            return 1

    # Phase C — pure CPU, no browser needed
    if do_C:
        print("[C] Extracting cached PDFs → bronze + silver…")
        summary = extract_archive(index, only_types=only_types)
        print(f"[C] {summary}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
