"""
Probe the `t146sb05_detail` API — the "full monthly revenue history"
endpoint hinted at by the overview response's moreInfoUrl.
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
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.goto("https://mops.twse.com.tw/mops/#/", wait_until="domcontentloaded", timeout=30_000)
        time.sleep(2)

        # Candidate payload keys to try based on the moreInfoUrl.parameters shape
        for payload in [
            {"company_id": "2330"},
            {"companyId": "2330"},
            {"company_id": "2330", "dataType": "all"},
            {"company_id": "2330", "years": 10},
        ]:
            url = "https://mops.twse.com.tw/mops/api/t146sb05_detail"
            print(f"\n[try] POST {url}  body={payload}")
            resp = ctx.request.post(
                url, data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json",
                         "Origin": "https://mops.twse.com.tw",
                         "Referer": "https://mops.twse.com.tw/mops/"})
            print(f"  status={resp.status}  len={len(resp.text())}")
            if resp.status == 200 and resp.text():
                body = resp.text()
                print(f"  first 400 chars: {body[:400]}")
                try:
                    j = json.loads(body)
                    if isinstance(j, dict) and j.get("code") == 200 and j.get("result"):
                        out = Path(f"tools/_mops_2330_detail_{hash(frozenset(payload.items()))}.json")
                        out.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
                        print(f"  -> saved to {out}")
                        res = j["result"]
                        print(f"  result keys: {list(res.keys()) if isinstance(res, dict) else type(res)}")
                        break
                except json.JSONDecodeError:
                    pass

        page.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)
