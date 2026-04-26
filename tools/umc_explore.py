"""
UMC (2303.TW) IR site reconnaissance — same probe pattern we used for TSMC.

Outputs:
  C:/tmp/umc_explore/landing.html — rendered HTML of the IR landing
  C:/tmp/umc_explore/quarterly.html — quarterly-results page if discoverable
  Console: every PDF anchor + nearby anchor structure (year/quarter selectors)
"""

from __future__ import annotations
import sys, json, re
from pathlib import Path
from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROFILE = Path("C:/Users/Sharo/.alphagraph_tsmc_profile")  # reuse — same domain trust footprint
OUT = Path("C:/tmp/umc_explore"); OUT.mkdir(parents=True, exist_ok=True)


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False, channel="chrome",
            args=["--start-maximized"], no_viewport=True,
        )
        page = ctx.new_page()

        # 1. Hit the IR landing
        landing = "https://www.umc.com/en/Investors"
        print(f"\n[1] {landing}")
        page.goto(landing, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
        print(f"  title: {page.title()!r}")
        (OUT / "landing.html").write_text(page.content(), encoding="utf-8")

        # 2. Find IR sub-section anchors
        sections = page.evaluate(
            """() => {
              const out = [];
              for (const a of document.querySelectorAll('a[href]')) {
                const text = (a.innerText || '').trim();
                const href = a.href;
                if (!text || text.length > 60) continue;
                // Anything that looks like a quarterly/financial/earnings/transcript link
                if (/quarter|financial|earnings|transcript|presentation|conference|annual|report|reports|filing|webcast|disclosure|investor/i.test(text + ' ' + href)) {
                  out.push({text, href});
                }
              }
              // Dedupe by href
              const seen = new Set();
              return out.filter(o => seen.has(o.href) ? false : (seen.add(o.href), true));
            }"""
        )
        print(f"  IR-relevant anchors: {len(sections)}")
        for s in sections[:30]:
            print(f"    [{s['text'][:35]:35}]  {s['href']}")

        # 3. Look for any PDF links on the landing
        pdfs = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .filter(a => /\\.pdf(\\?|$)/i.test(a.href))
                .map(a => ({label: (a.innerText||'').trim().slice(0,40), href: a.href}))"""
        )
        print(f"\n  PDFs visible on landing: {len(pdfs)}")
        for p in pdfs[:10]:
            print(f"    [{p['label']:25}]  {p['href']}")

        # 4. Try to navigate to a likely quarterly-results page
        candidate = next(
            (s for s in sections
             if "quarter" in (s["text"] + s["href"]).lower()
             and "umc.com" in s["href"]), None,
        )
        if candidate:
            print(f"\n[2] Quarterly link found: {candidate['href']}")
            page.goto(candidate["href"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
            print(f"  title: {page.title()!r}")
            (OUT / "quarterly.html").write_text(page.content(), encoding="utf-8")
            qpdfs = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => /\\.pdf(\\?|$)/i.test(a.href))
                    .map(a => ({label: (a.innerText||'').trim().slice(0,40), href: a.href}))"""
            )
            print(f"  PDFs on quarterly page: {len(qpdfs)}")
            for p in qpdfs[:20]:
                print(f"    [{p['label']:35}]  {p['href']}")

        ctx.close()


if __name__ == "__main__":
    main()
