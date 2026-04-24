"""
Social-media scraper scheduler entry point.

Run as:  python -m backend.app.services.social.scheduler

Replaces the Windows-Task-Scheduler-based `scheduled_scrape.bat`
pipeline that silently broke when Task Scheduler spawned a shell
without Python on PATH. This scheduler:

  * runs cross-platform (works under systemd on the AWS EC2 box)
  * isolates job failures — each job has its own try/except so a
    news-API hiccup doesn't stall Reddit or GPU-price collection
  * writes per-scraper heartbeat rows to the shared SQLite table
  * staggers runs so all jobs don't stampede the network at :00

Jobs (all Asia/Taipei):

    news_scrape            every 2h @ :00  — 28 Google News RSS feeds
    reddit_scrape          every 2h @ :15  — 10 subreddits
    reddit_keyword_search  every 4h @ :30  — 16 keyword × 6-sub cross-product (heavy)
    gpu_price_snapshot     every 2h @ :45  — Vast.ai + RunPod + Tensordock
    social_health_check    hourly  @ :23  — reads heartbeats, logs stale scrapers

Zero frontend changes required. The scrapers write to the same
parquet paths the existing /api/v1/social/* endpoints read from.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.core.config import settings
from backend.app.services.social.health import (
    HeartbeatStatus,
    ensure_heartbeat_table,
    read_all_heartbeats,
    write_heartbeat,
)
from backend.app.services.social.sources import gpu_price, news, reddit

TPE = ZoneInfo("Asia/Taipei")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("social_scheduler")


def _sqlite_conn() -> sqlite3.Connection:
    uri = settings.POSTGRES_URI
    if not uri.startswith("sqlite:///"):
        raise RuntimeError(
            "social scheduler expects SQLite in dev; migrate to Postgres via "
            "Alembic for production."
        )
    conn = sqlite3.connect(uri.replace("sqlite:///", ""))
    ensure_heartbeat_table(conn)
    return conn


def _finish(
    conn: sqlite3.Connection,
    *,
    scraper_name: str,
    rows_before: int,
    rows_after: int,
    error: str | None,
) -> None:
    """Common heartbeat write path for all jobs."""
    if error:
        status = HeartbeatStatus.FAILED
    elif rows_after == rows_before:
        # No growth isn't necessarily a problem (feeds can be quiet), but it's
        # worth flagging DEGRADED if it's consistently zero. Scheduler can
        # decide to upgrade policy later.
        status = HeartbeatStatus.OK
    else:
        status = HeartbeatStatus.OK
    write_heartbeat(
        conn,
        scraper_name=scraper_name,
        status=status,
        rows_inserted=max(rows_after - rows_before, 0),
        rows_updated=0,
        rows_amended=0,
        last_error_msg=error,
    )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def job_news_scrape() -> None:
    name = "news_scrape"
    conn = _sqlite_conn()
    try:
        stats = news.scrape_all_feeds()
        _finish(conn, scraper_name=name,
                rows_before=stats.rows_before, rows_after=stats.rows_after,
                error=stats.error)
        logger.info(
            "%s  before=%d  after=%d  new=%d  err=%s",
            name, stats.rows_before, stats.rows_after, stats.new_rows, stats.error,
        )
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name,
                        status=HeartbeatStatus.FAILED, last_error_msg=str(exc))
    finally:
        conn.close()


def job_reddit_scrape() -> None:
    name = "reddit_scrape"
    conn = _sqlite_conn()
    try:
        stats = reddit.scrape_subreddits()
        _finish(conn, scraper_name=name,
                rows_before=stats.rows_before, rows_after=stats.rows_after,
                error=stats.error)
        logger.info(
            "%s  before=%d  after=%d  new=%d  err=%s",
            name, stats.rows_before, stats.rows_after, stats.new_rows, stats.error,
        )
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name,
                        status=HeartbeatStatus.FAILED, last_error_msg=str(exc))
    finally:
        conn.close()


def job_reddit_keyword_search() -> None:
    name = "reddit_keyword_search"
    conn = _sqlite_conn()
    try:
        stats = reddit.search_keywords()
        _finish(conn, scraper_name=name,
                rows_before=stats.rows_before, rows_after=stats.rows_after,
                error=stats.error)
        logger.info(
            "%s  before=%d  after=%d  new=%d  err=%s",
            name, stats.rows_before, stats.rows_after, stats.new_rows, stats.error,
        )
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name,
                        status=HeartbeatStatus.FAILED, last_error_msg=str(exc))
    finally:
        conn.close()


def job_gpu_price_snapshot() -> None:
    name = "gpu_price_snapshot"
    conn = _sqlite_conn()
    try:
        stats = gpu_price.snapshot_all_providers()
        _finish(conn, scraper_name=name,
                rows_before=stats.rows_before, rows_after=stats.rows_after,
                error=stats.error)
        logger.info(
            "%s  before=%d  after=%d  new=%d  err=%s",
            name, stats.rows_before, stats.rows_after, stats.new_rows, stats.error,
        )
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name,
                        status=HeartbeatStatus.FAILED, last_error_msg=str(exc))
    finally:
        conn.close()


def job_social_health_check() -> None:
    conn = _sqlite_conn()
    try:
        rows = read_all_heartbeats(conn)
    finally:
        conn.close()
    social_scraper_names = {
        "news_scrape", "reddit_scrape", "reddit_keyword_search",
        "gpu_price_snapshot",
    }
    for r in rows:
        if r["scraper_name"] not in social_scraper_names:
            continue
        if r["status"] != "ok":
            logger.warning(
                "Scraper %s status=%s last_error=%s",
                r["scraper_name"], r["status"], r.get("last_error_msg"),
            )
    logger.info("social_health_check seen=%d", len(rows))


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    sched = BlockingScheduler(timezone=TPE)

    sched.add_job(
        job_news_scrape,
        CronTrigger(hour="*/2", minute="0"),
        id="news_scrape", replace_existing=True,
    )
    sched.add_job(
        job_reddit_scrape,
        CronTrigger(hour="*/2", minute="15"),
        id="reddit_scrape", replace_existing=True,
    )
    sched.add_job(
        job_reddit_keyword_search,
        CronTrigger(hour="*/4", minute="30"),
        id="reddit_keyword_search", replace_existing=True,
    )
    sched.add_job(
        job_gpu_price_snapshot,
        CronTrigger(hour="*/2", minute="45"),
        id="gpu_price_snapshot", replace_existing=True,
    )
    sched.add_job(
        job_social_health_check,
        CronTrigger(minute="23"),
        id="social_health_check", replace_existing=True,
    )

    logger.info("Social scheduler starting (jobs=%d)", len(sched.get_jobs()))
    for j in sched.get_jobs():
        logger.info("  registered: %s  trigger=%s", j.id, j.trigger)
    sched.start()


if __name__ == "__main__":
    main()
