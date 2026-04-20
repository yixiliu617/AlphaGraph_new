@echo off
REM AlphaGraph Scheduled Scraper — runs every 2 hours via Task Scheduler
REM Scrapes Google News (27 feeds) + Reddit (10 subs + 16 keywords)

cd /d C:\Users\Sharo\AI_projects\AlphaGraph_new

echo [%date% %time%] Starting scheduled scrape... >> tools\web_scraper\scrape_log.txt

REM Google News (27 feeds, ~1 min, ~$0.05 for translations)
python tools\web_scraper\news_tracker.py scrape >> tools\web_scraper\scrape_log.txt 2>&1

REM Reddit subreddit posts (10 subs, ~30 sec)
python tools\web_scraper\reddit_tracker.py scrape >> tools\web_scraper\scrape_log.txt 2>&1

REM Reddit keyword search (16 keywords × 6 subs, ~3 min)
python tools\web_scraper\reddit_tracker.py search >> tools\web_scraper\scrape_log.txt 2>&1

REM Cloud GPU prices (Vast.ai + RunPod + Tensordock, ~15 sec)
python tools\web_scraper\gpu_price_tracker.py snapshot >> tools\web_scraper\scrape_log.txt 2>&1

echo [%date% %time%] Scrape complete. >> tools\web_scraper\scrape_log.txt
echo. >> tools\web_scraper\scrape_log.txt
