"""
CamelCamelCamel Amazon price history scraper.

Reads manifest.json (list of tracked Amazon ASINs + chart URLs), downloads
each product's full-history price chart PNG via Playwright CDP (Cloudflare-
protected CDN), runs Gemini 2.5 Flash vision to extract quarterly prices
plus the lowest/highest/current summary, and upserts the result to
`camelcamelcamel_prices.parquet`.

Architecture (cloned from pcpartpicker_trends.py):
    1. Launch Chrome via subprocess with --remote-debugging-port=9223
       (port 9223 picked so it can't collide with pcpartpicker on 9222).
    2. Warm camelcamelcamel.com origin so Cloudflare cookies/clearance
       attach to the browser context.
    3. Use page.request.get(chart_url) inside the warmed context —
       Playwright inherits the cookies, bypassing the 403 that plain
       HTTP hits (confirmed behavior per the web-scraping skill).
    4. Gemini vision prompt asks for quarterly prices + lowest/highest/
       current marker text rendered in the chart legend.
    5. Replace-per-ASIN upsert: each run produces a fresh full history,
       so we drop this ASIN's rows and re-insert. Preserves rows for
       ASINs not in the current manifest (manual history stays intact).

Usage:
    # Full run: download + extract for every ASIN in manifest
    python tools/web_scraper/camel_tracker.py run

    # Just re-download images (e.g. if Gemini flaked)
    python tools/web_scraper/camel_tracker.py download

    # Just re-extract from locally cached images (no browser, no Cloudflare)
    python tools/web_scraper/camel_tracker.py extract

    # Single ASIN debug
    python tools/web_scraper/camel_tracker.py run --asin B08176KLZT

Cost: ~$0.004 per chart (Gemini 2.5 Flash, 1 image), ~4 ASINs = ~$0.02/run.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

if sys.platform == "win32":
    # Avoid cp1252 crashes on non-ASCII product names in print output.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load .env for GEMINI_API_KEY without requiring python-dotenv at import time.
_ROOT = Path(__file__).resolve().parents[2]
_ENV = _ROOT / ".env"
if _ENV.exists():
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DATA_DIR      = Path("backend/data/market_data/camelcamelcamel")
IMAGE_DIR     = DATA_DIR / "images"
MANIFEST_PATH = DATA_DIR / "manifest.json"
PARQUET_PATH  = DATA_DIR / "camelcamelcamel_prices.parquet"

SCRAPER_PROFILE = Path(os.path.expanduser("~/.alphagraph_scraper_profile"))
DEBUG_PORT      = 9223   # distinct from pcpartpicker's 9222
ORIGIN_URL      = "https://camelcamelcamel.com/"

# Chart URL in manifest has tp=all (full history). Keep it.

# ---------------------------------------------------------------------------
# Chrome lifecycle
# ---------------------------------------------------------------------------

def _launch_chrome() -> subprocess.Popen:
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome_exe = next((p for p in chrome_paths if os.path.exists(p)), None)
    if not chrome_exe:
        print("ERROR: Chrome not found at standard paths", file=sys.stderr)
        sys.exit(1)

    SCRAPER_PROFILE.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={SCRAPER_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1280,900",
        "about:blank",
    ]
    print(f">> Launching Chrome on port {DEBUG_PORT}...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return proc


def _kill_chrome(proc: subprocess.Popen) -> None:
    print(">> Closing Chrome...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait_for_cloudflare(page, timeout_s: int = 120) -> bool:
    """Block until the Turnstile / 'Just a moment' interstitial is gone.
    If stalled longer than timeout_s, return False so the caller can skip."""
    try:
        title = page.title()
    except Exception:
        title = ""
    if "Just a moment" not in title and "Cloudflare" not in title:
        return True
    print()
    print("=" * 60)
    print("  CLOUDFLARE VERIFICATION REQUIRED on camelcamelcamel.com")
    print("  If running interactively, click 'Verify you are human'.")
    print("  The scraper profile caches the solve for future runs.")
    print("=" * 60)
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            t = page.title()
            if "Just a moment" not in t and "Cloudflare" not in t:
                print(">> Cloudflare passed.")
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


# ---------------------------------------------------------------------------
# Step 1: Download chart PNGs
# ---------------------------------------------------------------------------

def download_charts(manifest: list[dict]) -> list[dict]:
    """Download each product's chart PNG via a Cloudflare-warmed browser context."""
    from playwright.sync_api import sync_playwright

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    chrome_proc = _launch_chrome()
    ok_manifest: list[dict] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            print(f">> Warming {ORIGIN_URL}")
            page.goto(ORIGIN_URL, timeout=60000, wait_until="domcontentloaded")
            if not _wait_for_cloudflare(page, timeout_s=120):
                print("   Cloudflare stalled -- skipping this run.")
                return ok_manifest

            for i, entry in enumerate(manifest):
                asin = entry["asin"]
                url  = entry["chart_url"]
                local_path = IMAGE_DIR / f"{asin}.png"
                print(f"   [{i+1}/{len(manifest)}] {asin} ...", end=" ", flush=True)
                try:
                    resp = page.request.get(url, timeout=30000)
                    if not resp.ok:
                        print(f"HTTP {resp.status}")
                        continue
                    body = resp.body()
                    if len(body) < 2000:
                        print(f"suspiciously small ({len(body)}B) -- skip")
                        continue
                    local_path.write_bytes(body)
                    ok = dict(entry)
                    ok["local_path"]    = str(local_path)
                    ok["downloaded_at"] = today
                    ok["file_size"]     = len(body)
                    ok_manifest.append(ok)
                    print(f"OK {len(body)//1024}KB")
                except Exception as exc:
                    print(f"ERR {exc}")
            browser.close()
    finally:
        _kill_chrome(chrome_proc)

    return ok_manifest


# ---------------------------------------------------------------------------
# Step 2: Extract structured data via Gemini
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """You are reading a CamelCamelCamel Amazon price-history chart.
The chart shows the green "Amazon" price line over time. The legend at the top-right
displays three summary values:
  - "Lowest":  the lowest historical Amazon price
  - "Highest": the highest historical Amazon price
  - "Current": the current Amazon price (or "-" if unavailable; use null)

Extract two things:

1. Quarterly sample prices. Pick one reading per fiscal quarter the chart covers —
   use the chart value closest to the middle of each quarter. Skip quarters where
   the chart has no data. Quarter labels: "Q1 2020", "Q2 2020", ..., up to and
   including any partial current quarter.

2. The three summary values from the legend box.

Return ONLY a JSON object with this exact shape, no prose, no markdown:

{
  "quarters": [
    {"quarter": "Q1 2020", "approx_price_usd": 155.0},
    {"quarter": "Q2 2020", "approx_price_usd": 155.0},
    ...
  ],
  "lowest":  58.00,
  "highest": 319.99,
  "current": 299.98
}

Round all prices to two decimals. Use null for any summary value that isn't displayed.
"""


def _gemini_extract(local_path: str, product_name: str) -> dict | None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    with open(local_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent?key=" + api_key
    )
    payload = {
        "contents": [{
            "parts": [
                {"text": f"Product: {product_name}\n\n{_EXTRACTION_PROMPT}"},
                {"inline_data": {"mime_type": "image/png", "data": img_b64}},
            ],
        }],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
    }

    resp = requests.post(url, json=payload, timeout=90)
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    # Strip markdown fences if Gemini adds them.
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        print(f"   WARN: no JSON in response: {text[:200]}")
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        print(f"   WARN: JSON parse error ({exc}): {text[start:start+200]}")
        return None


def extract_all(manifest: list[dict]) -> pd.DataFrame:
    """Run Gemini vision over every manifest entry; return an upsert-ready DataFrame."""
    all_rows: list[dict] = []
    for i, entry in enumerate(manifest):
        asin          = entry["asin"]
        product_name  = entry["product_name"]
        local_path    = entry.get("local_path") or str(IMAGE_DIR / f"{asin}.png")
        if not os.path.exists(local_path):
            print(f"   [{i+1}/{len(manifest)}] {asin}: SKIP (no local PNG)")
            continue
        print(f"   [{i+1}/{len(manifest)}] {asin} {product_name[:40]!s} ...",
              end=" ", flush=True)
        try:
            parsed = _gemini_extract(local_path, product_name)
            if parsed is None:
                continue
            for q in parsed.get("quarters", []):
                if "quarter" not in q or "approx_price_usd" not in q:
                    continue
                if q["approx_price_usd"] is None:
                    continue
                all_rows.append({
                    "asin":             asin,
                    "product_name":     product_name,
                    "quarter":          q["quarter"],
                    "approx_price_usd": float(q["approx_price_usd"]),
                    "source":           "camelcamelcamel",
                })
            # Summary markers — one row each, sentinel quarter strings.
            for label, key in [
                ("__lowest__",  "lowest"),
                ("__highest__", "highest"),
                ("__current__", "current"),
            ]:
                v = parsed.get(key)
                if v is None:
                    continue
                all_rows.append({
                    "asin":             asin,
                    "product_name":     product_name,
                    "quarter":          label,
                    "approx_price_usd": float(v),
                    "source":           "camelcamelcamel",
                })
            qn = len(parsed.get("quarters", []))
            print(f"{qn} quarters + summary")
            # Gentle pacing so we don't trip Gemini free-tier limits.
            if i < len(manifest) - 1:
                time.sleep(3)
        except Exception as exc:
            print(f"ERROR: {exc}")

    return pd.DataFrame(all_rows)


# ---------------------------------------------------------------------------
# Persistence — replace-per-ASIN upsert
# ---------------------------------------------------------------------------

def upsert_parquet(new_rows: pd.DataFrame) -> None:
    if new_rows.empty:
        print(">> No rows to upsert.")
        return
    refreshed_asins = set(new_rows["asin"].unique())
    if PARQUET_PATH.exists():
        existing = pd.read_parquet(PARQUET_PATH)
        keep = existing[~existing["asin"].isin(refreshed_asins)]
        merged = pd.concat([keep, new_rows], ignore_index=True)
    else:
        merged = new_rows.copy()

    PARQUET_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(PARQUET_PATH, index=False, compression="zstd")
    print(f">> Wrote {len(merged)} rows ({len(new_rows)} fresh, "
          f"{len(merged) - len(new_rows)} preserved) -> {PARQUET_PATH}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="CamelCamelCamel Amazon price history scraper")
    ap.add_argument("command", choices=["download", "extract", "run", "list"],
                    help="download=PNGs only, extract=Gemini only, run=both, list=dump manifest")
    ap.add_argument("--asin", default=None, help="Restrict to a single ASIN (debug)")
    args = ap.parse_args()

    if not MANIFEST_PATH.exists():
        print(f"ERROR: manifest missing at {MANIFEST_PATH}", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if args.asin:
        manifest = [m for m in manifest if m["asin"] == args.asin]
        if not manifest:
            print(f"No matching ASIN: {args.asin}", file=sys.stderr)
            return 1

    if args.command == "list":
        for m in manifest:
            print(f"  {m['asin']}: {m['product_name'][:70]}")
        return 0

    if args.command in ("download", "run"):
        downloaded = download_charts(manifest)
        if not downloaded:
            print("!! No charts downloaded; aborting extract.")
            return 1 if args.command == "download" else 1
        # Use the fresh manifest for the extract step so local_path is current.
        manifest = downloaded

    if args.command in ("extract", "run"):
        df = extract_all(manifest)
        upsert_parquet(df)

    return 0


if __name__ == "__main__":
    sys.exit(main())
