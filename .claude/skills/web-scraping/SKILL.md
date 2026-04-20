---
name: web-scraping
description: Building web scrapers for AlphaGraph — Cloudflare bypass, chart extraction, RSS feeds, Reddit, translation. Patterns, pitfalls, and architecture.
---

# Web Scraping Skill

## Architecture Pattern

```
tools/web_scraper/<scraper>.py     → scrape data → parquet
backend/data/market_data/<source>/ → parquet storage + config JSON
backend/app/api/routers/v1/<api>.py → FastAPI endpoint
frontend/src/lib/api/<client>.ts   → API client
frontend/src/app/(dashboard)/<page>/ → UI component
```

## Cloudflare Bypass (Playwright CDP)

When a site has Cloudflare protection:

### What Works
```python
import subprocess
proc = subprocess.Popen([
    chrome_exe,
    '--remote-debugging-port=9222',
    f'--user-data-dir={SCRAPER_PROFILE}',  # ~/.alphagraph_scraper_profile
    '--no-first-run',
    '--disable-blink-features=AutomationControlled',
    'about:blank'
])
# Then connect Playwright:
browser = p.chromium.connect_over_cdp('http://localhost:9222')
```

### What Does NOT Work
- `launch_persistent_context()` — injects `--enable-automation`, always detected
- Headless mode — always detected
- `playwright-stealth` — Turnstile still detects CDP
- Clicking Turnstile checkbox programmatically — Cloudflare detects automation signals
- Direct HTTP requests — instant 403

### Cloudflare Severity by Site
| Site | Severity | Workaround |
|------|----------|------------|
| PCPartPicker (pages) | Medium — shows Turnstile checkbox | CDP + scraper profile, auto-passes after first manual solve |
| PCPartPicker (CDN) | None | Direct HTTP download works |
| CamelCamelCamel (pages + CDN) | High — blocks CDP too sometimes | CDP, may need manual Cloudflare click |
| Truth Social | Very High — blocks everything | Could not bypass, even with CDP |
| Reddit | Full API block | Use Arctic Shift API instead |

## Chart Image Data Extraction

When chart data is in static PNG images (not interactive JS):

```python
# 1. Download the image (from CDN or via browser)
# 2. Send to Gemini vision with extraction prompt
# 3. Parse JSON response

PROMPT = '''Extract prices at WEEKLY intervals from this chart.
Return ONLY a JSON array: [{"date": "2024-10-07", "price": 50}, ...]'''

payload = {
    'contents': [{'parts': [
        {'text': prompt},
        {'inline_data': {'mime_type': 'image/png', 'data': img_b64}},
    ]}],
    'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 16384},
}
```

### Pitfalls
- `maxOutputTokens` must be ≥ 8192 for weekly data (16384 safer) — truncation causes missing `]`
- Gemini sometimes wraps JSON in markdown `` ```json `` fences — strip them
- Use `text.find('[')` / `text.rfind(']')` not regex for JSON extraction
- Batch vs one-at-a-time: batch translation (~50% failure), one-at-a-time (reliable but slower)
- Monthly ≈ 17 points, bi-weekly ≈ 40, weekly ≈ 78 per 18-month chart
- Cost: ~$0.004 per chart (weekly extraction)

## Google News RSS

### How It Works
```
GET https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en
→ Returns RSS 2.0 XML, up to 100 items
→ No auth, no API key, no rate limit, no Cloudflare
→ NO LLM involved — standard Google News search index
```

### Search Operators
| Operator | Example | Effect |
|----------|---------|--------|
| `"..."` | `"DRAM price"` | Exact phrase |
| `OR` | `NVIDIA OR AMD` | Boolean OR |
| `-` | `tariff -sports` | Exclude |
| `site:` | `site:reuters.com` | Source filter |
| `when:` | `when:1h`, `when:7d` | Time filter |
| `intitle:` | `intitle:tariff` | Title-only |

### Keyword Pitfalls
**Generic company names match unrelated content:**
- `"SCREEN"` → matches ScreenRant (entertainment) → use `"SCREEN Holdings"`
- `"Disco"` → matches disco music → use `"Disco Corporation"`
- `"KLA"` → relatively unique, OK as-is
- Always test new queries manually before adding to config

### Config-Driven Architecture
Feed definitions in `news_config.json` — add new feeds without code changes:
```json
{
  "feeds": {
    "my_new_feed": {
      "label": "Display Name",
      "query": "keyword1 OR \"exact phrase\" OR keyword2",
      "region": "US"
    }
  }
}
```
Regions: `US`, `UK`, `JP`, `KR`, `CN`

### Translation Pipeline
Non-English feeds (Korean/Japanese/Chinese) auto-translated via Gemini:
- One-at-a-time for reliability (batch has ~50% JSON parse failure rate)
- Stored as `title_en` column alongside original `title`
- Frontend shows English as main title, original below in italics
- Cost: ~$0.0002 per headline

## Reddit (Arctic Shift API)

### Why Not Reddit's Own API
- Reddit blocked all unauthenticated `.json` endpoints (403) since 2023
- OAuth requires app registration at reddit.com/prefs/apps — reCAPTCHA loop bug
- PRAW library works but needs credentials

### Arctic Shift API
```
GET https://arctic-shift.photon-reddit.com/api/posts/search
  ?query=DDR5&subreddit=hardware&limit=100&sort=desc&sort_type=created_utc
```
- Free, no auth, archives all Reddit posts
- `query` REQUIRES `subreddit` parameter — can't search all of Reddit
- `sort_type` only supports `default` or `created_utc` — NOT `score`
- Returns full post metadata: score, upvote_ratio, num_comments, author, flair

## Windows Console Pitfalls

**Unicode crashes:** Windows cp1252 encoding can't handle Korean/Japanese/Chinese/emoji characters in `print()`. Always encode to ASCII for console output:
```python
title_clean = title.encode('ascii', 'replace').decode()[:70]
print(f'{title_clean}')
```

**File encoding:** Always use `encoding="utf-8"` when reading/writing config files with non-ASCII content:
```python
with open(config_path, encoding="utf-8") as f:
    config = json.load(f)
```

## Adding a New Data Source — Checklist

1. Create scraper: `tools/web_scraper/<name>_tracker.py`
2. Create config: `backend/data/market_data/<name>/<name>_config.json`
3. Test scraper, verify data in parquet
4. Add API endpoint: `backend/app/api/routers/v1/<router>.py`
5. Register router in `backend/main.py`
6. Add frontend client: `frontend/src/lib/api/<name>Client.ts`
7. Add UI component (new tab or section in existing page)
8. Update nav if new page: `TopNav.tsx` + `GlobalSidebar.tsx`
9. Update memory: `memory/project_web_scraping.md`
