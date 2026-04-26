"""
Investigate the TSMC IR site structure properly using the deep-link
URL pattern the user gave: /chinese/quarterly-results/{YYYY}/q{N}.

Goals:
  1. Confirm /chinese/quarterly-results/2026/q1 actually works.
  2. Probe whether older quarters (e.g. 2010/q1) and pre-2021 quarters
     are reachable this way (the SPA-click crawl said 2020-Q1 had no PDFs;
     was that because of how I navigated, or because PDFs are truly absent?).
  3. Dump every visible piece of content on the 2026/Q1 page, especially
     "業績展望" (Performance Outlook / Guidance), so we can spot anything
     we've been missing.
  4. Walk the IR site's other sections (annual reports, financial reports,
     events) and list what doc types live there.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROFILE = Path("C:/Users/Sharo/.alphagraph_tsmc_profile")
OUT = Path("C:/tmp/tsmc_explore_v2"); OUT.mkdir(parents=True, exist_ok=True)


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False, channel="chrome",
            args=["--start-maximized"], no_viewport=True,
        )
        page = ctx.new_page()

        # 1. Try the deep-link pattern
        targets = [
            ("2026/q1", "https://investor.tsmc.com/chinese/quarterly-results/2026/q1"),
            ("2024/q3", "https://investor.tsmc.com/chinese/quarterly-results/2024/q3"),
            ("2020/q1", "https://investor.tsmc.com/chinese/quarterly-results/2020/q1"),  # pre-cutoff
            ("2010/q1", "https://investor.tsmc.com/chinese/quarterly-results/2010/q1"),  # very old
            ("2000/q4", "https://investor.tsmc.com/chinese/quarterly-results/2000/q4"),  # ancient
        ]
        for label, url in targets:
            print(f"\n=== {label}  →  {url} ===")
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
                print(f"  status={resp.status if resp else '?'}  title={page.title()!r}")
            except Exception as e:
                print(f"  goto exception: {e}")
                continue

            # Capture all PDF anchors
            pdfs = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => /\\.pdf(\\?|$)/i.test(a.href))
                    .map(a => ({label: a.innerText.trim().slice(0,40), href: a.href}))"""
            )
            print(f"  PDF anchors: {len(pdfs)}")
            for p in pdfs:
                print(f"    [{p['label']:25}] {p['href'][:130]}")

            # Look for the 業績展望 (guidance) section text
            outlook = page.evaluate(
                """() => {
                    const out = [];
                    for (const el of document.querySelectorAll('*')) {
                        const txt = (el.innerText || '').trim();
                        if (txt.length < 4 || txt.length > 500) continue;
                        if (txt.includes('業績展望') || txt.includes('展望')
                          || txt.toLowerCase().includes('guidance')
                          || txt.toLowerCase().includes('outlook')) {
                            const tag = el.tagName.toLowerCase();
                            // only emit leaf-text-ish nodes
                            if (el.children.length <= 2) {
                                out.push(`<${tag}> ${txt}`);
                            }
                        }
                    }
                    return Array.from(new Set(out)).slice(0, 30);
                }"""
            )
            if outlook:
                print(f"  'guidance/展望' hits ({len(outlook)}):")
                for line in outlook[:10]:
                    print(f"    {line[:200]}")

            # Save the rendered page HTML for offline mining
            html_path = OUT / f"{label.replace('/','_')}.html"
            html_path.write_text(page.content(), encoding="utf-8")
            print(f"  HTML saved -> {html_path}")

        # 2. Walk the other IR landing pages we have not explored yet
        other_landings = [
            "https://investor.tsmc.com/chinese/annual-reports",
            "https://investor.tsmc.com/chinese/financial-reports",
            "https://investor.tsmc.com/chinese/events/investor-meetings",
            "https://investor.tsmc.com/chinese/dividends/tsmc-dividend-policy",
        ]
        for url in other_landings:
            print(f"\n=== Landing: {url} ===")
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                print(f"  status={resp.status if resp else '?'}  title={page.title()!r}")
                pdfs = page.evaluate(
                    """() => Array.from(document.querySelectorAll('a[href]'))
                        .filter(a => /\\.pdf(\\?|$)/i.test(a.href))
                        .length"""
                )
                print(f"  PDF anchors on this page: {pdfs}")
            except Exception as e:
                print(f"  goto exception: {e}")

        ctx.close()


if __name__ == "__main__":
    main()
