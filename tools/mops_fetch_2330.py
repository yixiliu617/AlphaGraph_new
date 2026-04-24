"""
Use Playwright's page.request (which shares browser cookies + WAF clearance)
to hit the newly-discovered MOPS JSON API and dump the full response.

This is the replay pattern we'll productionise: one warm browser context
establishes cookies, then we use its request API to issue many efficient
JSON calls without re-rendering a page per ticker.
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
    raise RuntimeError(f"CDP Chrome did not come up on :{CDP_PORT}")


def main() -> None:
    from playwright.sync_api import sync_playwright

    start_chrome()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        # Warm the origin — ensures any cookie / token is established.
        print("[step] warming origin")
        page.goto("https://mops.twse.com.tw/mops/#/", wait_until="domcontentloaded", timeout=30_000)
        time.sleep(2)

        # Now fire the JSON call using the browser context's request API.
        url = "https://mops.twse.com.tw/mops/api/t146sb05"
        print(f"[step] POST {url}  body={{companyId: 2330}}")
        resp = ctx.request.post(
            url,
            data={"companyId": "2330"},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://mops.twse.com.tw",
                "Referer": "https://mops.twse.com.tw/mops/",
            },
        )
        print(f"[step] status={resp.status}")
        body = resp.text()
        print(f"[step] body length = {len(body)}")

        try:
            j = json.loads(body)
        except json.JSONDecodeError:
            print("[error] body is not JSON; first 500 chars:")
            print(body[:500])
            page.close()
            return

        # Dump full JSON to file for later reference
        out = Path("tools/_mops_2330_sample.json")
        out.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[step] full JSON saved to {out}")

        # Summarise structure
        print("\n[json structure]")
        print(f"  top-level keys: {list(j.keys())}")
        result = j.get("result", {})
        if isinstance(result, dict):
            print(f"  result keys: {list(result.keys())}")

        # Monthly revenue is nested somewhere — grep the JSON string for known revenue numbers
        # From our earlier rendered text, TSMC Mar 2026 = 415,191,699 (thousand TWD)
        hits = []
        def walk(node, path="$"):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
            else:
                s = str(node)
                if "415,191,699" in s or "415191699" in s:
                    hits.append((path, s[:200]))

        walk(j)
        print(f"\n[monthly revenue anchor 415,191,699 found at {len(hits)} locations]")
        for path, s in hits:
            print(f"  {path}: {s}")

        page.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
