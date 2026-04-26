"""
One-off explorer: probe TSMC investor.tsmc.com for the management report
PDFs. Output: (1) download the 1Q26 PDF locally, (2) discover the index
page that lists all historical management reports.

The site is Cloudflare-protected; direct curl returns the JS challenge.
We use Playwright with the persistent profile (already CF-cleared from
prior MOPS work) to fetch via a real browser context.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROFILE = Path("C:/Users/Sharo/.alphagraph_scraper_profile")
OUT_DIR = Path("/tmp/tsmc_test")
OUT_DIR.mkdir(parents=True, exist_ok=True)

PDF_URL = (
    "https://investor.tsmc.com/chinese/encrypt/files/encrypt_file/reports/"
    "2026-04/5508a9df8981f587c73dbfaf9f577f142e22bbb1/1Q26ManagementReport.pdf"
)
# Likely-candidate index pages — guess from the URL hierarchy + TSMC IR layout.
# The Chinese investor page tree typically has a /quarterly-results or
# /financial-information subsection.
INDEX_CANDIDATES = [
    "https://investor.tsmc.com/chinese/quarterly-results",      # latest quarter
    "https://investor.tsmc.com/chinese/quarterly-results/2025",
    "https://investor.tsmc.com/chinese/quarterly-results/2020",
    "https://investor.tsmc.com/chinese/quarterly-results/2010",
    "https://investor.tsmc.com/chinese/quarterly-results/2000",
    "https://investor.tsmc.com/chinese/quarterly-results/1997", # earliest
]


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

        # 1) Navigate to the index FIRST so the page JS clears the Cloudflare
        #    challenge for this browser context. After that, ctx.request can
        #    re-use the cleared cookies + JA3 fingerprint to fetch the PDF.
        print("[1] Warming Cloudflare via /quarterly-results page navigation")
        page.goto(
            "https://investor.tsmc.com/chinese/quarterly-results",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        page.wait_for_timeout(4000)
        print(f"    title={page.title()!r}")

        # Now retry the PDF fetch via the warmed context.
        print(f"[1b] Fetching PDF (post-warm): {PDF_URL}")
        try:
            r = ctx.request.get(PDF_URL, timeout=45000)
            print(f"    status={r.status}  ct={r.headers.get('content-type','?')}  len={len(r.body())}")
            if r.ok and r.headers.get("content-type", "").startswith("application/pdf"):
                out = OUT_DIR / "1Q26ManagementReport.pdf"
                out.write_bytes(r.body())
                print(f"    saved -> {out} ({out.stat().st_size:,} bytes)")
            else:
                print("    NOT a PDF — first 200 bytes:")
                print("    ", r.body()[:200])
        except Exception as e:
            print(f"    fetch failed: {e}")

        # 1b2) Plan B: run fetch() inside the page JS context. The page is
        # already on investor.tsmc.com after [1] above, so this is a same-
        # origin request that carries CF cookies and JA3 fingerprint. Read
        # the body as a base64 string so we can shuttle it over the bridge.
        print("[1b2] page.evaluate(fetch PDF as base64)")
        try:
            b64 = page.evaluate(
                """async (url) => {
                    const r = await fetch(url, {credentials: 'include'});
                    if (!r.ok) return {err: 'http '+r.status};
                    const buf = await r.arrayBuffer();
                    let s = '';
                    const bytes = new Uint8Array(buf);
                    for (let i=0; i<bytes.byteLength; i++) s += String.fromCharCode(bytes[i]);
                    return {b64: btoa(s), ct: r.headers.get('content-type'), len: bytes.byteLength};
                }""",
                PDF_URL,
            )
            if isinstance(b64, dict) and b64.get("b64"):
                import base64
                body = base64.b64decode(b64["b64"])
                out = Path("C:/tmp/tsmc_test/1Q26ManagementReport.pdf")
                out.write_bytes(body)
                print(f"    saved: ct={b64.get('ct')}  size={len(body):,}  -> {out}")
                head = body[:8]
                print(f"    head bytes: {head!r}  (PDF magic = b'%PDF')")
            else:
                print(f"    fetch failed: {b64}")
        except Exception as e:
            print(f"    evaluate exception: {e}")

        # 1c) Look for year/quarter selector controls on the page so we
        #     understand how to navigate to historical quarters.
        print("[1c] Inspecting page UI for year/quarter selector")
        try:
            selects = page.eval_on_selector_all(
                "select",
                "els => els.map(s => ({name: s.name, id: s.id, options: Array.from(s.options).map(o => o.value+'|'+o.text).slice(0,20)}))",
            )
            print(f"    <select> elements: {len(selects)}")
            for s in selects:
                print(f"      id={s['id']!r}  name={s['name']!r}  options(first 20):")
                for opt in s["options"]:
                    print(f"        {opt}")
        except Exception as e:
            print(f"    select probe failed: {e}")

        # Buttons / links that might be year/quarter pickers
        try:
            buttons = page.eval_on_selector_all(
                "button, [role='button'], [role='tab']",
                "els => els.map(b => ({text: b.innerText.trim().slice(0,40), role: b.getAttribute('role'), cls: (b.className||'').slice(0,40)})).filter(x => x.text)",
            )
            print(f"    button-like elements: {len(buttons)} (showing those that look like Y/Q pickers):")
            for b in buttons:
                t = b["text"]
                if any(k in t for k in ["202", "Q1", "Q2", "Q3", "Q4", "年", "季"]):
                    print(f"      {t!r:50} role={b['role']!r}  cls={b['cls']!r}")
        except Exception as e:
            print(f"    button probe failed: {e}")

        # Save the rendered DOM for offline analysis
        html_dump = OUT_DIR / "quarterly-results.html"
        html_dump.write_text(page.content(), encoding="utf-8")
        print(f"    HTML dump -> {html_dump}")

        # 2) Walk index candidates — find which page lists management reports.
        print()
        for url in INDEX_CANDIDATES:
            print(f"[2] Probing index: {url}")
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                title = page.title()
                # Look for any anchors mentioning "Management Report" or "ManagementReport"
                links = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(a => ({href: a.href, text: a.innerText.trim()}))",
                )
                report_links = [
                    l for l in links
                    if "ManagementReport" in l["href"]
                    or "管理階層" in l.get("text", "")
                    or "管理階層" in l["href"]
                    or "Management" in l.get("text", "")
                ]
                print(f"    status={resp.status if resp else '?'}  title={title!r}")
                print(f"    total_links={len(links)}  report_links={len(report_links)}")
                for rl in report_links[:8]:
                    print(f"      {rl['text'][:50]:50} -> {rl['href']}")
                if report_links:
                    # Save full link dump from the most-promising index for later mining
                    dump = OUT_DIR / f"links_{url.split('/')[-1] or 'root'}.txt"
                    dump.write_text(
                        "\n".join(f"{l['text']}\t{l['href']}" for l in links),
                        encoding="utf-8",
                    )
                    print(f"    full link dump -> {dump}")
            except Exception as e:
                print(f"    failed: {e}")

        ctx.close()


if __name__ == "__main__":
    main()
