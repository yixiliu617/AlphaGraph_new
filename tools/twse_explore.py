"""
Discover the TWSE 上市公司月報 (listed company monthly report) data
endpoints by intercepting XHRs on www.twse.com.tw/zh/trading/statistics/index04.html.

Run:
    python tools/twse_explore.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CDP_PORT = int(os.environ.get("ALPHAGRAPH_SCRAPER_CDP_PORT", "9222"))
CDP_PROFILE = Path(os.environ.get("ALPHAGRAPH_SCRAPER_PROFILE",
                                  str(Path.home() / ".alphagraph_scraper_profile")))
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def cdp_running() -> bool:
    try:
        return requests.get(f"http://localhost:{CDP_PORT}/json/version", timeout=1).status_code == 200
    except requests.RequestException:
        return False


def start_chrome() -> None:
    if cdp_running():
        return
    CDP_PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [CHROME, f"--remote-debugging-port={CDP_PORT}",
         f"--user-data-dir={CDP_PROFILE}", "--no-first-run",
         "--disable-blink-features=AutomationControlled", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        if cdp_running():
            return
        time.sleep(0.3)
    raise RuntimeError("CDP Chrome did not come up")


def explore() -> None:
    from playwright.sync_api import sync_playwright

    start_chrome()
    calls: list[dict] = []
    responses: dict[str, str] = {}

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        def on_request(req):
            if req.resource_type in ("xhr", "fetch", "document"):
                if "google" not in req.url and "doubleclick" not in req.url:
                    calls.append({"method": req.method, "url": req.url,
                                  "type": req.resource_type, "body": req.post_data})

        def on_response(resp):
            if resp.request.resource_type in ("xhr", "fetch"):
                if "google" in resp.url or "doubleclick" in resp.url:
                    return
                try:
                    responses[resp.url] = resp.text()[:2500]
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        print("[step] Loading TWSE index04")
        page.goto("https://www.twse.com.tw/zh/trading/statistics/index04.html",
                  wait_until="networkidle", timeout=30_000)
        time.sleep(3)

        # Print what links exist on the page.
        print(f"\n[info] link count: {page.locator('a').count()}")
        print("[info] first 15 link labels on the page:")
        links = page.locator("a").all()
        for a in links[:15]:
            try:
                text = a.inner_text().strip()[:80]
                href = a.get_attribute("href")
                if text and href and href != "#":
                    print(f"  {text}  ->  {href}")
            except Exception:
                pass

        # Peek at the dumped body — maybe the 4 CSV download links are visible
        print("\n[body text first 2000]")
        try:
            print(page.locator("body").inner_text()[:2000])
        except Exception as exc:
            print(f"[warn] {exc}")

        print("\n[network: non-google XHR/fetch/document calls]")
        for c in calls:
            body = (c.get("body") or "")[:200]
            print(f"  {c['method']} {c['url']}  type={c['type']}" + (f"  body={body}" if body else ""))

        # Dump JSON-ish response snippets
        print("\n[json-ish response snippets]")
        for url, body in list(responses.items())[:20]:
            if body and (body.lstrip().startswith("{") or body.lstrip().startswith("[")):
                print(f"--- {url}")
                print(body[:500])

        page.close()


if __name__ == "__main__":
    try:
        explore()
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)
