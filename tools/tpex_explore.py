"""
Discover TPEx monthly revenue endpoints.

TPEx = 櫃買中心 = Taipei Exchange. Our 8 TPEx watchlist tickers need
historical revenue data beyond the 12 months that MOPS provides.

Run:
    python tools/tpex_explore.py
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


def explore(path: str) -> None:
    from playwright.sync_api import sync_playwright
    start_chrome()
    calls: list[dict] = []
    responses: dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        def on_request(req):
            if req.resource_type in ("xhr", "fetch"):
                if "google" not in req.url and "doubleclick" not in req.url:
                    calls.append({"method": req.method, "url": req.url,
                                  "type": req.resource_type, "body": req.post_data})

        def on_response(resp):
            if resp.request.resource_type in ("xhr", "fetch"):
                if "google" in resp.url or "doubleclick" in resp.url:
                    return
                try:
                    responses[resp.url] = resp.text()[:3000]
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        target = f"https://www.tpex.org.tw{path}"
        print(f"[step] Loading {target}")
        try:
            page.goto(target, wait_until="networkidle", timeout=30_000)
        except Exception as exc:
            print(f"[warn] goto: {exc}")
        time.sleep(3)

        print(f"[info] url={page.url}")
        print(f"[info] title={page.title()}")

        # Body dump to see layout
        try:
            print("\n[body first 2000]")
            print(page.locator("body").inner_text()[:2000])
        except Exception as exc:
            print(f"[warn] body: {exc}")

        # Links that look revenue-related
        print("\n[links mentioning 營收/revenue/月報]:")
        for a in page.locator("a").all()[:120]:
            try:
                t = a.inner_text().strip()[:80]
                h = a.get_attribute("href") or ""
                if any(k in t for k in ("營收","營業收入","月報","monthly")) or \
                   any(k in h.lower() for k in ("monthly","revenue","report","stat")):
                    if t and h:
                        print(f"  {t}  ->  {h}")
            except Exception:
                pass

        print("\n[XHR calls (non-analytics)]:")
        for c in calls:
            body = (c.get("body") or "")[:200]
            print(f"  {c['method']} {c['url']}" + (f"  body={body}" if body else ""))

        page.close()


if __name__ == "__main__":
    try:
        path = sys.argv[1] if len(sys.argv) > 1 else "/zh-tw/mainboard/trading/statistics/month/listed.html"
        explore(path)
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)
