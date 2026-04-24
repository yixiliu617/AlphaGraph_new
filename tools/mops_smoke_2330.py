"""
Path A smoke test: fetch TSMC (2330) monthly revenue via the new MOPS
JSON API and print the top 5 rows. Uses Playwright's CDP-attached
browser context to bypass the WAF that blocks direct HTTP clients.
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


def _parse_int(s: str) -> int | None:
    s = (s or "").replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_pct(s: str) -> float | None:
    s = (s or "").replace("%", "").strip()
    if not s or s == "-":
        return None
    try:
        v = float(s)
        # MOPS sentinel for divide-by-zero / overflow
        if abs(v) >= 999_999.99:
            return None
        return v / 100.0
    except ValueError:
        return None


def parse_rows(raw_rows: list[list[str]]) -> list[dict]:
    """Normalise MOPS rows to our canonical monthly revenue schema.

    Row shape: [roc_year, month, revenue_ktwd, prior_yr_month_ktwd,
                yoy_pct, ytd_ktwd, prior_yr_ytd_ktwd, ytd_yoy_pct]
    Units in response are thousand TWD — we store full TWD.
    """
    out: list[dict] = []
    for row in raw_rows:
        if len(row) < 8:
            continue
        roc_year = row[0].strip()
        month = row[1].strip()
        try:
            ad_year = int(roc_year) + 1911
            m = int(month)
        except ValueError:
            continue
        rev_k = _parse_int(row[2])
        prior_k = _parse_int(row[3])
        ytd_k = _parse_int(row[5])
        prior_ytd_k = _parse_int(row[6])
        out.append({
            "fiscal_ym": f"{ad_year:04d}-{m:02d}",
            "revenue_twd": rev_k * 1000 if rev_k is not None else None,
            "prior_year_month_twd": prior_k * 1000 if prior_k is not None else None,
            "yoy_pct": _parse_pct(row[4]),
            "cumulative_ytd_twd": ytd_k * 1000 if ytd_k is not None else None,
            "prior_year_ytd_twd": prior_ytd_k * 1000 if prior_ytd_k is not None else None,
            "ytd_pct": _parse_pct(row[7]),
        })
    return out


def main(ticker: str = "2330") -> None:
    from playwright.sync_api import sync_playwright
    start_chrome()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        # Warm the origin so the browser context has whatever headers MOPS expects.
        page.goto("https://mops.twse.com.tw/mops/#/", wait_until="domcontentloaded", timeout=30_000)
        time.sleep(1.5)

        url = "https://mops.twse.com.tw/mops/api/t146sb05_detail"
        print(f"[fetch] POST {url}  body={{company_id: {ticker!r}}}")
        resp = ctx.request.post(
            url, data={"company_id": ticker},
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Origin": "https://mops.twse.com.tw",
                     "Referer": "https://mops.twse.com.tw/mops/"})
        print(f"[fetch] status={resp.status} size={len(resp.text())}")
        if resp.status != 200:
            print("[fetch] non-200 — bail"); page.close(); return

        j = resp.json()
        page.close()

    if j.get("code") != 200:
        print(f"[fetch] api code={j.get('code')} message={j.get('message')}"); return

    raw = j["result"]["data"]
    rows = parse_rows(raw)
    print(f"\n[parse] {len(rows)} monthly rows parsed  (title: {j['result'].get('title')})")

    print("\n=== Top 5 rows (most recent first) ===")
    print(f"{'fiscal_ym':<10}  {'revenue_twd':>18}  {'prior_yr_month':>18}  {'yoy_pct':>8}  "
          f"{'ytd_twd':>18}  {'ytd_pct':>8}")
    print("-" * 92)
    for r in rows[:5]:
        rev = f"{r['revenue_twd']:,}" if r['revenue_twd'] is not None else "—"
        prior = f"{r['prior_year_month_twd']:,}" if r['prior_year_month_twd'] is not None else "—"
        ytd = f"{r['cumulative_ytd_twd']:,}" if r['cumulative_ytd_twd'] is not None else "—"
        yoy = f"{r['yoy_pct']*100:+.2f}%" if r['yoy_pct'] is not None else "—"
        ytd_p = f"{r['ytd_pct']*100:+.2f}%" if r['ytd_pct'] is not None else "—"
        print(f"{r['fiscal_ym']:<10}  {rev:>18}  {prior:>18}  {yoy:>8}  {ytd:>18}  {ytd_p:>8}")


if __name__ == "__main__":
    try:
        ticker = sys.argv[1] if len(sys.argv) > 1 else "2330"
        main(ticker)
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)
