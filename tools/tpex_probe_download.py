"""
Load the TPEx 歷年上櫃股票統計 page, capture every XHR + the real XLS
download URLs (from the 下載XLS anchor elements), then grab one
file and inspect its contents.
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

CDP_PORT = 9222
CDP_PROFILE = Path.home() / ".alphagraph_scraper_profile"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def cdp_running() -> bool:
    try:
        return requests.get(f"http://localhost:{CDP_PORT}/json/version", timeout=1).status_code == 200
    except requests.RequestException:
        return False


def start_chrome() -> None:
    if cdp_running():
        return
    subprocess.Popen([CHROME, f"--remote-debugging-port={CDP_PORT}",
                      f"--user-data-dir={CDP_PROFILE}", "--no-first-run",
                      "--disable-blink-features=AutomationControlled", "about:blank"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 15
    while time.time() < deadline:
        if cdp_running():
            return
        time.sleep(0.3)
    raise RuntimeError("CDP did not come up")


def main() -> None:
    from playwright.sync_api import sync_playwright
    start_chrome()
    calls = []

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        def on_request(req):
            if req.resource_type in ("xhr", "fetch", "document") and "google" not in req.url:
                calls.append({"method": req.method, "url": req.url,
                              "type": req.resource_type, "body": req.post_data})

        page.on("request", on_request)

        url = sys.argv[1] if len(sys.argv) > 1 else "https://www.tpex.org.tw/zh-tw/mainboard/listed/month/revenue.html"
        print(f"[step] Loading {url}")
        page.goto(url, wait_until="networkidle", timeout=30_000)
        time.sleep(3)

        print(f"[info] title={page.title()}")

        # Find all download-looking anchors
        print("\n[download anchors]")
        for a in page.locator("a").all():
            try:
                t = a.inner_text().strip()[:80]
                h = a.get_attribute("href") or ""
                if any(k in t.lower() for k in ("download", "xls", "csv", "ods", "xlsx")) or \
                   h.lower().endswith((".xls", ".csv", ".ods", ".xlsx")) or \
                   "download" in h.lower() or "StaticFile" in h or "staticfile" in h.lower():
                    print(f"  '{t}'  ->  {h}")
            except Exception:
                pass

        # Also dump body text to see if there are more hints
        body = page.locator("body").inner_text()
        # find lines mentioning XLS, CSV, download
        print("\n[lines mentioning XLS/ODS/download/營收/revenue]:")
        for line in body.splitlines():
            if any(k in line for k in ("XLS", "ODS", "CSV", "download", "Download", "營收", "Revenue", "Monthly")):
                if len(line.strip()) > 2:
                    print(f"  {line.strip()[:150]}")

        # Triggered XHRs
        print("\n[XHR / fetch calls (non-google)]:")
        for c in calls:
            body_preview = (c.get("body") or "")[:200]
            print(f"  {c['method']} {c['url']}" + (f"  body={body_preview}" if body_preview else ""))

        page.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)
