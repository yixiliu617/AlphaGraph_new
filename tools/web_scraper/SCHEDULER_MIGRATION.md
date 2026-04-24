# Windows Task Scheduler → APScheduler migration

**Status:** The `scheduled_scrape.bat` / `scheduled_scrape.vbs` pair is
superseded by `backend/app/services/social/scheduler.py` (APScheduler,
cross-platform).

## Why

The Windows Task Scheduler-spawned shell doesn't have Python on
`PATH`, so every 2-hour firing was silently failing with
"Python was not found" and completing only the `echo [date] Scrape
complete.` line. The News / Reddit / GPU-price parquets stopped
growing on 2026-04-21 even though the task log suggested otherwise.

## Migrated: what runs each job now

| Old | New (APScheduler job id) |
|---|---|
| `python tools/web_scraper/news_tracker.py scrape` | `news_scrape` |
| `python tools/web_scraper/reddit_tracker.py scrape` | `reddit_scrape` |
| `python tools/web_scraper/reddit_tracker.py search` | `reddit_keyword_search` |
| `python tools/web_scraper/gpu_price_tracker.py snapshot` | `gpu_price_snapshot` |

Cron cadence: news/reddit-scrape/gpu every 2 h on staggered offsets
(0, 15, 45); reddit keyword-search every 4 h on :30 (heavier); hourly
`social_health_check` at :23.

## What to do on the dev box

1. **Disable the Windows scheduled task.**
   - Press Win + R, type `taskschd.msc`, Enter.
   - Find the task that runs `scheduled_scrape.vbs` (likely under
     "Task Scheduler Library" with a name like "AlphaGraph scraper" or
     similar).
   - Right-click → **Disable**. Do NOT delete — leave disabled as a
     record until you've confirmed the new scheduler works.

2. **Start the new scheduler:**
   ```
   python -m backend.app.services.social.scheduler
   ```
   Leave it running in its own terminal (or under NSSM to survive
   reboots on Windows dev machines).

3. **Verify after the next tick:**
   - News parquet modification time: `stat backend/data/market_data/news/google_news.parquet`
     should be within the last 2 hours.
   - Heartbeat row: `sqlite3 alphagraph.db "SELECT scraper_name, status,
     last_run_at FROM taiwan_scraper_heartbeat WHERE scraper_name LIKE '%_scrape';"`
   - Dashboard News tab now shows today's articles.

## On AWS EC2 (when we deploy)

Add a third systemd unit `alphagraph-social-scheduler.service` alongside
the Taiwan + web units. Example:

```ini
[Unit]
Description=AlphaGraph Social Scheduler
After=network.target alphagraph-web.service

[Service]
Type=simple
User=alphagraph
WorkingDirectory=/opt/alphagraph/app
Environment=PYTHONPATH=/opt/alphagraph/app
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/alphagraph/app/.venv/bin/python -m backend.app.services.social.scheduler
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Two scheduler processes (Taiwan + Social) instead of one because they
fail independently — a crash in MOPS CDP Chrome should not stop news
polling, and vice versa.

## Files left in place

- `scheduled_scrape.bat` — kept as a manual dev tool (after PATH is fixed).
- `scheduled_scrape.vbs` — kept for reference; not invoked by anything once the task is disabled.
- `scrape_log.txt` — will stop growing once the task is disabled; that's the signal the migration took effect.
