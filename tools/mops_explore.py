"""
Discovery script: launch CDP Chrome against the new MOPS SPA, navigate to
the monthly-revenue form, click the 查詢 (Search) button, and capture the
XHR/fetch calls that fire. Also capture the final rendered DOM so we know
what selectors the scraper will need.

Run:
    python tools/mops_explore.py
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
        print(f"[info] CDP already running on :{CDP_PORT}")
        return
    CDP_PROFILE.mkdir(parents=True, exist_ok=True)
    print(f"[info] Launching Chrome port={CDP_PORT} profile={CDP_PROFILE}")
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
    raise RuntimeError(f"CDP Chrome did not come up on :{CDP_PORT}")


def explore() -> None:
    from playwright.sync_api import sync_playwright

    start_chrome()
    calls: list[dict] = []
    responses: dict[str, str] = {}  # url -> truncated response body

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        def on_request(req):
            if req.resource_type in ("xhr", "fetch"):
                calls.append({"method": req.method, "url": req.url,
                              "type": req.resource_type, "body": req.post_data})

        def on_response(resp):
            if resp.request.resource_type in ("xhr", "fetch"):
                try:
                    body = resp.text()
                    responses[resp.url] = body[:4000]
                except Exception:
                    responses[resp.url] = "<could not read body>"

        page.on("request", on_request)
        page.on("response", on_response)

        # Go directly to the 2330 monthly-revenue page (discovered via KeywordsQuery).
        target = "https://mops.twse.com.tw/mops/#/web/t146sb05?companyId=2330"
        print(f"[step] Navigating directly to: {target}")
        page.goto(target, wait_until="networkidle", timeout=30_000)
        time.sleep(5)

        # Some SPAs need a search click even on deep link
        print("[step] Clicking 查詢 to trigger data fetch")
        try:
            page.get_by_role("button", name="查詢").first.click(timeout=5000)
            time.sleep(7)
        except Exception as exc:
            print(f"  -> 查詢 click skipped: {type(exc).__name__}: {str(exc)[:120]}")

        try:
            print(f"\n[info] final url: {page.url}")
            print(f"[info] page title: {page.title()}")
        except Exception as exc:
            print(f"[warn] url/title: {exc}")

        # MOST IMPORTANT: the captured XHR calls (skip the analytics noise)
        print("\n[network calls captured — excluding analytics]")
        for c in calls:
            if "analytics.google" in c["url"] or "googletagmanager" in c["url"]:
                continue
            body = (c["body"] or "")[:300]
            print(f"  {c['method']} {c['url']}")
            if body:
                print(f"    body: {body}")

        # Peek at response bodies for MOPS API endpoints
        print("\n[response body snippets — MOPS JSON endpoints]")
        for url, body in responses.items():
            if "mops.twse.com.tw/mops/api" in url and body.strip().startswith(("{", "[")):
                print(f"\n--- {url}")
                print(body[:1500])

        # Table rendering?
        print(f"\n[info] table elements after search: {page.locator('table').count()}")
        print(f"[info] row elements after search: {page.locator('tr').count()}")

        # Dump first 2000 chars of the rendered body to confirm we got data
        print("\n[body text after search (first 2000 chars)]")
        try:
            print(page.locator("body").inner_text()[:2000])
        except Exception as exc:
            print(f"[warn] body text: {exc}")

        page.close()


if __name__ == "__main__":
    try:
        explore()
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
