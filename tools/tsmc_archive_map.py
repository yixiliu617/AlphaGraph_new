"""
Map the full TSMC quarterly-results archive: every Year x Quarter page,
the 5 PDF links each one publishes.

Strategy: navigate to /chinese/quarterly-results, then click through each
Year and each Q tab. Capture the PDF anchors after each click. Output one
JSON file per year + a master CSV.

Heads-up: the page is a SPA, so we have to click + wait, not URL-navigate.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROFILE = Path("C:/Users/Sharo/.alphagraph_scraper_profile")
OUT_DIR = Path("C:/tmp/tsmc_test/archive")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _extract_pdf_links(page) -> list[dict]:
    return page.eval_on_selector_all(
        "a[href*='reports/'][href$='.pdf'], a[href*='ManagementReport'], a[href*='Presentation'], a[href*='EarningsRelease'], a[href*='FS.pdf'], a[href*='Transcript']",
        "els => els.map(a => ({text: a.innerText.trim(), href: a.href}))",
    )


def main() -> None:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            channel="chrome",
            args=["--start-maximized"],
            no_viewport=True,
        )
        page = ctx.new_page()

        print("[setup] loading /chinese/quarterly-results")
        page.goto(
            "https://investor.tsmc.com/chinese/quarterly-results",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        page.wait_for_timeout(4000)
        print(f"[setup] title={page.title()!r}")

        # Year tabs: <a> elements whose innerText is a 4-digit year. Sample
        # 5 years across the range as a smoke test before crawling all 30.
        year_anchors = page.eval_on_selector_all(
            "a",
            "els => els.filter(a => /^(19|20)\\d{2}$/.test(a.innerText.trim())).map(a => a.innerText.trim())",
        )
        print(f"[setup] found {len(year_anchors)} year tabs: {year_anchors[:5]}...{year_anchors[-3:]}")

        sample_years = ["2026", "2024", "2020", "2010", "1997"]
        archive: dict[str, dict] = {}

        for year in sample_years:
            print(f"\n[year {year}]")
            try:
                # Click the year link
                clicked = page.evaluate(
                    """y => {
                        const anchors = Array.from(document.querySelectorAll('a'));
                        const target = anchors.find(a => a.innerText.trim() === y);
                        if (target) { target.click(); return true; }
                        return false;
                    }""",
                    year,
                )
                if not clicked:
                    print(f"  no anchor found for {year}")
                    continue
                page.wait_for_timeout(2500)
                print(f"  title after click: {page.title()!r}")

                # Now look for Q1-Q4 tabs and click each
                year_data: dict[str, list[dict]] = {}
                quarters_seen = page.eval_on_selector_all(
                    "a, button, [role='tab']",
                    "els => els.map(e => e.innerText.trim()).filter(t => /^Q[1-4]$/.test(t))",
                )
                print(f"  Q tabs visible: {quarters_seen}")

                for q in ["Q1", "Q2", "Q3", "Q4"]:
                    if q not in quarters_seen:
                        continue
                    page.evaluate(
                        """q => {
                            const els = Array.from(document.querySelectorAll('a, button, [role=\"tab\"]'));
                            const t = els.find(e => e.innerText.trim() === q);
                            if (t) t.click();
                        }""",
                        q,
                    )
                    page.wait_for_timeout(1800)
                    pdfs = _extract_pdf_links(page)
                    year_data[q] = pdfs
                    print(f"    {q}: {len(pdfs)} PDF links")
                    for pdf in pdfs:
                        print(f"      [{pdf['text'][:18]:18}] {pdf['href'][:140]}")
                archive[year] = year_data
            except Exception as e:
                print(f"  failed: {e}")

        out = OUT_DIR / "archive_map.json"
        out.write_text(json.dumps(archive, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[done] saved -> {out}")
        print(f"  years sampled: {list(archive.keys())}")
        ctx.close()


if __name__ == "__main__":
    main()
