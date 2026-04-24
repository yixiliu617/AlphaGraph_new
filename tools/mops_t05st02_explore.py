"""
Explore MOPS t05st02 (重大訊息 — material information) as a potential
live-tracking signal for monthly revenue disclosures.

Load the page, capture the JSON API it calls, fetch announcements for a
specific date range, filter for monthly-revenue keywords, and report
coverage.
"""
from __future__ import annotations

import json
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
    responses = {}

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        def on_request(req):
            if req.resource_type in ("xhr", "fetch") and "google" not in req.url:
                calls.append({"method": req.method, "url": req.url,
                              "type": req.resource_type, "body": req.post_data})

        def on_response(resp):
            if resp.request.resource_type in ("xhr", "fetch") and "google" not in resp.url:
                try:
                    responses[resp.url] = resp.text()[:5000]
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        print("[step] Load t05st02")
        page.goto("https://mops.twse.com.tw/mops/#/web/t05st02",
                  wait_until="networkidle", timeout=30_000)
        time.sleep(3)

        print(f"[info] title={page.title()}  inputs={page.locator('input').count()}  buttons={page.locator('button').count()}")

        # Body dump — what widgets are present?
        body = page.locator("body").inner_text()[:2500]
        print("\n[body first 2500]")
        print(body)

        print("\n[XHR calls on initial load]")
        for c in calls:
            b = (c.get("body") or "")[:250]
            print(f"  {c['method']} {c['url']}" + (f"  body={b}" if b else ""))

        # Fill the form: year=2026 month=4 day=10 — a date in the filing window
        print("\n[step] filling year=2026, month=4, day=10 and clicking 查詢")
        calls.clear()
        responses.clear()
        try:
            inputs = page.locator("input").all()
            print(f"  input count: {len(inputs)}")
            for i, inp in enumerate(inputs):
                placeholder = inp.get_attribute("placeholder") or ""
                name = inp.get_attribute("name") or ""
                id_ = inp.get_attribute("id") or ""
                print(f"    [{i}] name={name!r} id={id_!r} placeholder={placeholder!r}")

            # Try typing into the visible inputs — the SPA may use dropdowns, so
            # try direct text types first and fall back to role-based select.
            if len(inputs) >= 3:
                inputs[0].fill("2026")
                time.sleep(0.5)
                inputs[1].fill("4")
                time.sleep(0.5)
                inputs[2].fill("10")
                time.sleep(0.5)
                print(f"  filled values: {[inp.input_value() for inp in inputs]}")
        except Exception as exc:
            print(f"  fill exc: {exc}")

        try:
            page.get_by_role("button", name="查詢").first.click(timeout=5000)
            time.sleep(6)
        except Exception as exc:
            print(f"  click failed: {exc}")

        print("\n[XHR calls after 查詢]")
        for c in calls:
            b = (c.get("body") or "")[:250]
            print(f"  {c['method']} {c['url']}" + (f"  body={b}" if b else ""))

        print("\n[response body snippets of MOPS API calls]")
        for url, body_text in responses.items():
            if "mops.twse.com.tw/mops/api" in url:
                print(f"\n--- {url}")
                print(body_text[:2000])

        page.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)
