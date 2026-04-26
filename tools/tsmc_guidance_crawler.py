"""
TSMC quarterly guidance (業績展望) crawler.

Walks the deep-link URL pattern /chinese/quarterly-results/{year}/q{n}
discovered during 2026-04-26 exploration. Each page renders an HTML
guidance table that's NOT in any of the 5 quarterly PDFs — pure
forward-looking data.

Two phases:
  A. Visit each (year, q) deep-link, save the page HTML to bronze, and
     also append PDF URLs found on the page to _index.json (the deep-link
     URL is more reliable than the SPA-click crawler in tsmc_archive_crawler.py).
  B. Parse saved HTML through tsmc_guidance.extract_guidance_from_html
     into the silver guidance parquet.

Usage:
    python tools/tsmc_guidance_crawler.py                       # all phases, 2010-2026
    python tools/tsmc_guidance_crawler.py --years 2024,2025,2026
    python tools/tsmc_guidance_crawler.py --quarters 1,2,3,4
    python tools/tsmc_guidance_crawler.py --phase A             # crawl only
    python tools/tsmc_guidance_crawler.py --phase B             # extract only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from backend.scripts.extractors import tsmc_guidance as guidance   # noqa: E402

PROFILE = Path("C:/Users/Sharo/.alphagraph_tsmc_profile")
TICKER = "2330.TW"
DATA_ROOT = REPO_ROOT / "backend" / "data" / "financials"
BRONZE_ROOT = DATA_ROOT / "raw" / TICKER

INDEX_URL = "https://investor.tsmc.com/chinese/quarterly-results"


def _page_url(year: int, q: int) -> str:
    return f"{INDEX_URL}/{year}/q{q}"


def _html_cache_path(year: int, q: int) -> Path:
    return BRONZE_ROOT / str(year) / f"Q{q}" / "page.html"


# ---------------------------------------------------------------------------
# Phase A — crawl (visit each deep-link, cache HTML + PDF urls)
# ---------------------------------------------------------------------------

def crawl_pages(page: Page, years: list[int], quarters: list[int]) -> dict:
    """Visit each (year, q) deep-link, save HTML to bronze. Also collect
    PDF anchors so we can update the master _index.json later."""
    visited: dict[str, dict] = {}
    for year in years:
        for q in quarters:
            url = _page_url(year, q)
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
            except Exception as e:
                print(f"  {year}/q{q}: goto failed: {e}")
                continue

            status = resp.status if resp else 0
            title = page.title()
            # Heuristic: a real quarterly-results page has both 業績展望 AND
            # 5 PDF anchors. Pages for periods that don't exist 404 OR
            # silently land on the latest period (which we'd duplicate).
            html = page.content()
            has_outlook = "業績展望" in html
            pdfs = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => /\\.pdf(\\?|$)/i.test(a.href) && /reports\\/|encrypt_file\\//.test(a.href))
                    .map(a => ({label: a.innerText.trim(), href: a.href}))"""
            )
            # Validate: title must say "{year} Q{q}"
            expected_title_token = f"{year} Q{q}"
            ok = (status == 200 and has_outlook and expected_title_token in title)
            print(f"  {year}/q{q}: status={status}  has_outlook={has_outlook}  pdfs={len(pdfs)}  title={title!r}")
            if not ok:
                continue
            # Save the HTML
            out = _html_cache_path(year, q)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            visited[f"{year}/Q{q}"] = {
                "title": title,
                "url": url,
                "pdfs": pdfs,
                "saved_html": str(out),
            }
            # Polite throttle
            time.sleep(0.6)
    return visited


# ---------------------------------------------------------------------------
# Phase B — extract guidance from cached HTML
# ---------------------------------------------------------------------------

def extract_pages(years: list[int], quarters: list[int]) -> dict:
    """For every saved page.html, run the guidance extractor."""
    summary = {"total": 0, "extracted": 0, "errors": 0, "facts": 0}
    for year in years:
        for q in quarters:
            html_path = _html_cache_path(year, q)
            if not html_path.exists():
                continue
            summary["total"] += 1
            try:
                bronze, facts = guidance.extract_guidance_from_html(
                    html_path.read_text(encoding="utf-8", errors="replace"),
                    ticker=TICKER,
                    page_period_label=f"{q}Q{year % 100:02d}",
                    source_url=_page_url(year, q),
                )
            except Exception as e:
                print(f"  {year}/q{q}: extract FAILED: {e}")
                summary["errors"] += 1
                continue
            guidance.write_bronze(bronze)
            guidance.upsert_silver(facts, ticker=TICKER)
            print(f"  {year}/q{q}: extracted {len(facts)} guidance facts")
            summary["extracted"] += 1
            summary["facts"] += len(facts)
    return summary


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--years", default=",".join(str(y) for y in range(2010, 2027)),
                   help="Comma-separated years (default 2010..2026)")
    p.add_argument("--quarters", default="1,2,3,4")
    p.add_argument("--phase", choices=["A", "B"], help="Only one phase")
    args = p.parse_args()

    years = [int(y) for y in args.years.split(",")]
    quarters = [int(q) for q in args.quarters.split(",")]
    do_A = args.phase in (None, "A")
    do_B = args.phase in (None, "B")

    if do_A:
        print(f"[A] Crawling {len(years)} years × {len(quarters)} quarters via deep-links")
        with sync_playwright() as plw:
            ctx = plw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE), headless=False, channel="chrome",
                args=["--start-maximized"], no_viewport=True,
            )
            page = ctx.new_page()
            visited = crawl_pages(page, years, quarters)
            ctx.close()
        print(f"[A] HTML saved for {len(visited)} pages")

    if do_B:
        print("[B] Extracting guidance from cached HTML")
        summary = extract_pages(years, quarters)
        print(f"[B] {summary}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
