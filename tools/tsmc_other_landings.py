"""
Quick enumerator for the two un-touched IR landings discovered while
exploring the deep-link URL pattern: /chinese/annual-reports and
/chinese/financial-reports. Just lists the PDFs (year, label, type, url)
into a JSON catalog so we can decide what's worth downloading.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROFILE = Path("C:/Users/Sharo/.alphagraph_tsmc_profile")
OUT = Path("C:/tmp/tsmc_other_landings"); OUT.mkdir(parents=True, exist_ok=True)

LANDINGS = {
    "annual-reports":   "https://investor.tsmc.com/chinese/annual-reports",
    "financial-reports":"https://investor.tsmc.com/chinese/financial-reports",
}


def _classify_year(label: str, url: str) -> int | None:
    """Try to extract a 4-digit year from the link label or URL."""
    for s in (label, url):
        m = re.search(r"\b(19|20)\d{2}\b", s)
        if m:
            return int(m.group(0))
    return None


def main() -> int:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False, channel="chrome",
            args=["--start-maximized"], no_viewport=True,
        )
        page = ctx.new_page()

        catalog = {}
        for name, url in LANDINGS.items():
            print(f"\n=== {name} : {url} ===")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"  goto failed: {e}")
                continue
            print(f"  title: {page.title()!r}")

            # All PDF anchors under encrypt_file or reports paths
            entries = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => /\\.pdf(\\?|$)/i.test(a.href)
                              && /encrypt_file|reports\\//i.test(a.href))
                    .map(a => ({
                        label: (a.innerText || '').trim().slice(0, 60),
                        href: a.href,
                    }))"""
            )
            print(f"  PDFs: {len(entries)}")
            # Group by year
            by_year: dict[int, list[dict]] = {}
            unknown: list[dict] = []
            for e in entries:
                y = _classify_year(e["label"], e["href"])
                if y is None:
                    unknown.append(e)
                else:
                    by_year.setdefault(y, []).append({**e, "year": y})

            # Print summary by year (descending)
            for y in sorted(by_year, reverse=True):
                labels = [x["label"][:24] for x in by_year[y]]
                print(f"    {y}: {len(by_year[y])} files  e.g. {labels[:3]}")
            if unknown:
                print(f"    (no year detected: {len(unknown)})")

            catalog[name] = {
                "url": url,
                "title": page.title(),
                "total_pdfs": len(entries),
                "by_year": {str(y): by_year[y] for y in sorted(by_year, reverse=True)},
                "unknown_year": unknown,
            }

            # Save HTML for offline mining
            html = page.content()
            (OUT / f"{name}.html").write_text(html, encoding="utf-8")

        out_path = OUT / "catalog.json"
        out_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nCatalog -> {out_path}")
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
