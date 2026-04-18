"""
PCPartPicker Trends Scraper
===========================
Downloads price trend chart images from pcpartpicker.com/trends/ and extracts
structured price data using LLM vision.

Usage:
    # Step 1: Download chart images from all categories
    python tools/web_scraper/pcpartpicker_trends.py download

    # Step 1b: Download a single category
    python tools/web_scraper/pcpartpicker_trends.py download --category memory

    # Step 2: Extract data from downloaded images using LLM vision
    python tools/web_scraper/pcpartpicker_trends.py extract

    # Both steps in one go
    python tools/web_scraper/pcpartpicker_trends.py run

    # List available categories
    python tools/web_scraper/pcpartpicker_trends.py list

Architecture:
    1. Launch Chrome via subprocess (real profile, no automation flags)
    2. Connect Playwright via CDP to scrape chart image URLs from page JS
    3. Download PNGs directly from CDN (no Cloudflare on CDN)
    4. Use Gemini vision to extract price data from chart images
    5. Save structured data as parquet
"""

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
from dotenv import load_dotenv

load_dotenv()

CATEGORIES = {
    "cpu": "/trends/price/cpu/",
    "cpu-cooler": "/trends/price/cpu-cooler/",
    "motherboard": "/trends/price/motherboard/",
    "memory": "/trends/price/memory/",
    "storage": "/trends/price/internal-hard-drive/",
    "video-card": "/trends/price/video-card/",
    "power-supply": "/trends/price/power-supply/",
    "case": "/trends/price/case/",
    "monitor": "/trends/price/monitor/",
}

BASE_URL = "https://pcpartpicker.com"
DATA_DIR = Path("backend/data/market_data/pcpartpicker_trends")
IMAGE_DIR = DATA_DIR / "images"
SCRAPER_PROFILE = Path(os.path.expanduser("~/.alphagraph_scraper_profile"))
DEBUG_PORT = 9222
CRAWL_DELAY = 60


# ---------------------------------------------------------------------------
# Chrome management
# ---------------------------------------------------------------------------

def launch_chrome():
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome_exe = next((p for p in chrome_paths if os.path.exists(p)), None)
    if not chrome_exe:
        print("ERROR: Chrome not found")
        sys.exit(1)

    SCRAPER_PROFILE.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={SCRAPER_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1920,1080",
        "about:blank",
    ]
    print(f"Launching Chrome (port {DEBUG_PORT})...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return proc


def kill_chrome(proc):
    print("Closing Chrome...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Step 1: Download chart images
# ---------------------------------------------------------------------------

def wait_for_cloudflare(page, timeout_s=180):
    title = page.title()
    if "Just a moment" not in title:
        return True
    print()
    print("=" * 60)
    print("  CLOUDFLARE VERIFICATION REQUIRED")
    print("  Click 'Verify you are human' in the Chrome window")
    print("=" * 60)
    print()
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            if "Just a moment" not in page.title():
                print(">> Verification passed!")
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def scrape_image_urls(page, category, path):
    """Extract chart image URLs and titles from a category page."""
    url = BASE_URL + path
    print(f"\n>> {category}: {url}")

    page.goto(url, timeout=60000, wait_until="networkidle")
    time.sleep(2)

    if not wait_for_cloudflare(page):
        print(f"   SKIPPED (Cloudflare)")
        return []

    script = page.evaluate("""() => {
        const scripts = document.querySelectorAll('script:not([src])');
        for (const s of scripts) {
            const t = s.textContent || '';
            if (t.includes('var images')) return t;
        }
        return null;
    }""")

    if not script:
        print("   WARNING: No gallery script found")
        return []

    entries = re.findall(
        r'src:\s*"([^"]+)".*?title:\s*"([^"]+)"',
        script,
        re.DOTALL,
    )

    results = []
    for src, title in entries:
        title_clean = title.replace("\\u002D", "-")
        img_url = f"https:{src}" if src.startswith("//") else src
        results.append({"title": title_clean, "url": img_url, "category": category})

    print(f"   Found {len(results)} charts")
    return results


def download_images(categories_to_scrape, delay):
    """Scrape image URLs from pages and download PNGs from CDN."""
    from playwright.sync_api import sync_playwright

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    chrome_proc = launch_chrome()

    all_entries = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            for i, (cat, path) in enumerate(categories_to_scrape.items()):
                if i > 0:
                    print(f"\n   Waiting {delay}s (crawl delay)...")
                    time.sleep(delay)
                try:
                    entries = scrape_image_urls(page, cat, path)
                    all_entries.extend(entries)
                except Exception as e:
                    print(f"   ERROR: {e}")

            browser.close()
    finally:
        kill_chrome(chrome_proc)

    # Download images from CDN (no Cloudflare)
    print(f"\n>> Downloading {len(all_entries)} chart images from CDN...")
    today = datetime.now().strftime("%Y-%m-%d")
    manifest = []

    for entry in all_entries:
        safe_name = re.sub(r'[^\w\-]', '_', entry["title"])
        filename = f"{entry['category']}__{safe_name}.png"
        filepath = IMAGE_DIR / filename

        try:
            resp = requests.get(entry["url"], timeout=30)
            resp.raise_for_status()
            filepath.write_bytes(resp.content)
            entry["local_path"] = str(filepath)
            entry["downloaded_at"] = today
            entry["file_size"] = len(resp.content)
            manifest.append(entry)
            print(f"   OK {filename} ({len(resp.content)//1024}KB)")
        except Exception as e:
            print(f"   FAIL {filename}: {e}")

    # Save manifest
    manifest_path = DATA_DIR / "image_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\n>> Manifest: {len(manifest)} images -> {manifest_path}")

    return manifest


# ---------------------------------------------------------------------------
# Step 2: Extract data from images using LLM vision
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are analyzing a PCPartPicker price trend chart image.

The chart shows "Average Price (USD) Over Last 18 Months" for a specific PC component.
- The thick black line is the average price
- Blue bands show min/max price range
- Light blue dots are individual data points
- X-axis: dates (monthly labels)
- Y-axis: price in USD

Extract the average price (black line) at each month shown on the X-axis.
Read the values as precisely as you can from the chart.

Return ONLY a JSON array of objects with this format, no other text:
[
  {"month": "Nov 2024", "avg_price_usd": 50},
  {"month": "Dec 2024", "avg_price_usd": 52},
  ...
]

Include ALL months visible on the X-axis. Round prices to the nearest dollar.
If the price is hard to read for a month, give your best estimate.
"""


def extract_with_gemini(image_path, title):
    """Extract price data from chart image using Gemini vision."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set in .env")

    with open(image_path, "rb") as f:
        img_bytes = f.read()
    img_b64 = base64.b64encode(img_bytes).decode()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    payload = {
        "contents": [{
            "parts": [
                {"text": f"Component: {title}\n\n{EXTRACTION_PROMPT}"},
                {"inline_data": {"mime_type": "image/png", "data": img_b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 8192,
        },
    }

    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    result = resp.json()

    text = result["candidates"][0]["content"]["parts"][0]["text"]

    # Strip markdown code fences if present
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)

    # Find the JSON array — match from first [ to last ]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        print(f"   WARNING: Could not parse JSON from response for {title}")
        print(f"   Response: {text[:200]}")
        return []

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"   WARNING: JSON parse error for {title}: {e}")
        print(f"   Response: {text[:200]}")
        return []


def extract_data(manifest_path=None):
    """Extract price data from all downloaded chart images."""
    if manifest_path is None:
        manifest_path = DATA_DIR / "image_manifest.json"

    with open(manifest_path) as f:
        manifest = json.load(f)

    print(f">> Extracting data from {len(manifest)} chart images...")
    all_rows = []
    errors = []

    for i, entry in enumerate(manifest):
        title = entry["title"]
        category = entry["category"]
        local_path = entry["local_path"]

        if not os.path.exists(local_path):
            print(f"   SKIP {title} (file missing)")
            continue

        print(f"   [{i+1}/{len(manifest)}] {category}/{title}...", end=" ", flush=True)

        try:
            data_points = extract_with_gemini(local_path, f"{category} - {title}")
            for dp in data_points:
                all_rows.append({
                    "category": category,
                    "component": title,
                    "month": dp["month"],
                    "avg_price_usd": dp["avg_price_usd"],
                    "image_url": entry["url"],
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                })
            print(f"{len(data_points)} months")

            # Rate limit: ~10 requests/min for free Gemini
            if i < len(manifest) - 1:
                time.sleep(3)

        except Exception as e:
            print(f"ERROR: {e}")
            errors.append({"title": title, "error": str(e)})

    if not all_rows:
        print(">> No data extracted!")
        return

    df = pd.DataFrame(all_rows)

    # Parse month strings to dates for sorting
    df["date"] = pd.to_datetime(df["month"], format="%b %Y")
    df = df.sort_values(["category", "component", "date"])

    # Save per-category parquet
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for cat in df["category"].unique():
        cat_df = df[df["category"] == cat]
        out_path = DATA_DIR / f"{cat}.parquet"
        cat_df.to_parquet(out_path, index=False, compression="zstd")
        print(f"   {cat}: {len(cat_df)} rows -> {out_path}")

    # Save combined
    combined_path = DATA_DIR / "_combined.parquet"
    df.to_parquet(combined_path, index=False, compression="zstd")
    print(f"\n>> Combined: {len(df)} rows -> {combined_path}")

    # Summary
    print("\n>> Summary:")
    summary = df.groupby(["category", "component"]).agg(
        months=("month", "count"),
        min_price=("avg_price_usd", "min"),
        max_price=("avg_price_usd", "max"),
        latest_price=("avg_price_usd", "last"),
    )
    print(summary.to_string())

    if errors:
        print(f"\n>> {len(errors)} errors:")
        for e in errors:
            print(f"   {e['title']}: {e['error']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PCPartPicker Trends Scraper")
    parser.add_argument("command", choices=["download", "extract", "run", "list"],
                        help="download=get images, extract=LLM vision, run=both, list=show categories")
    parser.add_argument("--category", "-c", help="Single category to scrape")
    parser.add_argument("--delay", type=int, default=CRAWL_DELAY,
                        help=f"Crawl delay seconds (default: {CRAWL_DELAY})")
    args = parser.parse_args()

    if args.command == "list":
        print("Available categories:")
        for name, path in CATEGORIES.items():
            print(f"  {name:15s} {BASE_URL}{path}")
        return

    cats = {args.category: CATEGORIES[args.category]} if args.category else CATEGORIES

    if args.command in ("download", "run"):
        download_images(cats, args.delay)

    if args.command in ("extract", "run"):
        extract_data()


if __name__ == "__main__":
    main()
