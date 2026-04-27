---
name: pcpartpicker-scraper
description: Scraping PC component price trends from pcpartpicker.com — Cloudflare bypass, chart image download, LLM vision extraction
version: 1.0
last_validated_at: 2026-04-28
conditions: []
prerequisites: [web-scraping]
tags: [scraping, pricing, hardware, llm-vision, cloudflare]
---

# PCPartPicker Trends Scraper

## Overview

Scrapes price trend charts from `pcpartpicker.com/trends/` and extracts structured price data using LLM vision. Charts are pre-rendered PNGs on CDN — not interactive JS charts.

## Architecture

### Why This Approach
- pcpartpicker.com uses **Cloudflare Turnstile** (interactive challenge) on all pages
- Charts are **static PNG images** generated server-side daily, NOT Highcharts/D3/etc.
- Chart data is NOT in the DOM — it's baked into the PNG
- Solution: download PNGs from CDN (no Cloudflare), extract data via Gemini vision

### Two-Stage Pipeline

**Stage 1: Download** (`download` command)
1. Launch Chrome via subprocess with `--remote-debugging-port=9222` and dedicated scraper profile (`~/.alphagraph_scraper_profile`)
2. Connect Playwright via CDP (NOT `launch_persistent_context` — that injects `--enable-automation`)
3. Navigate to each category page, extract image URLs from jQuery gallery script (`var images = [...]`)
4. Download PNGs directly from CDN: `cdna.pcpartpicker.com/static/forever/images/trends/...`
5. Save manifest JSON with URLs, titles, local paths

**Stage 2: Extract** (`extract` command)
1. Read manifest JSON
2. For each chart PNG, call Gemini 2.5 Flash vision API
3. Prompt extracts monthly average prices from the chart image
4. Parse JSON response, save to parquet per category + combined

### Key Files
- `tools/web_scraper/pcpartpicker_trends.py` — main scraper script
- `backend/data/market_data/pcpartpicker_trends/` — output directory
  - `images/` — downloaded chart PNGs
  - `image_manifest.json` — metadata for all downloaded images
  - `<category>.parquet` — extracted data per category
  - `_combined.parquet` — all categories combined

## Categories

| Category | URL Path | Example Components |
|----------|----------|-------------------|
| cpu | /trends/price/cpu/ | Ryzen 5/7/9, Core i5/i7/i9 |
| cpu-cooler | /trends/price/cpu-cooler/ | Tower/AIO coolers |
| motherboard | /trends/price/motherboard/ | ATX/mATX by socket |
| memory | /trends/price/memory/ | DDR4/DDR5 by speed/capacity |
| storage | /trends/price/internal-hard-drive/ | SSD/HDD by capacity |
| video-card | /trends/price/video-card/ | RTX 4060-4090, RX 7600-7900 |
| power-supply | /trends/price/power-supply/ | By wattage |
| case | /trends/price/case/ | ATX/mATX cases |
| monitor | /trends/price/monitor/ | By size/resolution |

## CDN URL Pattern

```
https://cdna.pcpartpicker.com/static/forever/images/trends/
    {YYYY.MM.DD}.{currency}.{type}.{spec}.{hash}.png
```

Example: `2026.04.18.usd.ram.ddr4.3200.2x8192.<hash>.png`
- Date = daily generation date
- Currency = usd/cad/eur/gbp
- No Cloudflare on CDN — direct download works

## Chart Image Format

Each PNG shows:
- **Title**: "Average [Component] Price (USD) Over Last 18 Months ([Spec])"
- **Black line**: average price over time
- **Blue bands**: min/max price range
- **Light blue dots**: individual retailer prices
- **X-axis**: monthly labels (Nov 2024 - Mar 2026)
- **Y-axis**: price in USD

## Cloudflare Bypass Details

### What Works
- Launch Chrome via `subprocess.Popen()` with `--remote-debugging-port`
- Use a dedicated scraper profile dir (`~/.alphagraph_scraper_profile`)
- Connect Playwright via `connect_over_cdp()`
- First visit may require manual Turnstile click; subsequent pages auto-pass

### What Does NOT Work
- `launch_persistent_context()` — injects `--enable-automation` flag, always detected
- Any headless mode — always detected
- `playwright-stealth` — Turnstile still detects CDP connection
- Programmatic clicking the Turnstile checkbox — Cloudflare detects automation signals
- Direct HTTP requests (requests/curl) — instant 403

## Data Schema (Parquet Output)

| Column | Type | Description |
|--------|------|-------------|
| category | str | e.g., "memory", "video-card" |
| component | str | e.g., "DDR4-3200 2x8GB" |
| month | str | e.g., "Nov 2024" |
| date | datetime | Parsed from month |
| avg_price_usd | float | Average price read from chart |
| image_url | str | CDN URL for source image |
| extracted_at | str | ISO timestamp of extraction |

## Usage

```bash
# Download all categories (takes ~8 min with 60s crawl delay)
python tools/web_scraper/pcpartpicker_trends.py download

# Extract data from downloaded images (takes ~3 min, uses Gemini API)
python tools/web_scraper/pcpartpicker_trends.py extract

# Both steps
python tools/web_scraper/pcpartpicker_trends.py run

# Single category
python tools/web_scraper/pcpartpicker_trends.py download --category memory
```

## Known Issues & Edge Cases

1. **Gemini response truncation**: maxOutputTokens must be >= 8192; lower values truncate the JSON array for charts with many data points
2. **Markdown code fences**: Gemini sometimes wraps JSON in ``` blocks — the parser strips these
3. **Chrome must not be running**: If user's Chrome is open, the scraper profile may conflict. Script uses a separate profile to avoid this.
4. **Rate limits**: Gemini free tier ~10 req/min. 3-second delay between extraction calls.
5. **Price precision**: Vision extraction gives +-$5 precision for values under $200, +-$10-20 for higher values. Good enough for trend analysis, not for exact pricing.
6. **robots.txt**: 60-second crawl delay must be respected between page navigations

## Daily Automation

For daily runs, create a Windows Task Scheduler task:
```
python tools/web_scraper/pcpartpicker_trends.py run --delay 60
```
First run of the day may require manual Cloudflare click. Subsequent runs within ~30 min reuse the `cf_clearance` cookie automatically.
