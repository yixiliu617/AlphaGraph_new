"""
Taiwan scheduler entry point. Run as: python -m backend.app.services.taiwan.scheduler

Jobs (all Asia/Taipei time):
  - company_master_refresh   1st of month @ 03:00
                             Resolves the watchlist via KeywordsQuery so we
                             know each ticker's (market, sector).
  - monthly_revenue_daily    daily @ 10:00
                             Per-ticker call to t146sb05_detail — MOPS
                             publishes by the 10th of each month.
  - health_check             hourly @ :17
                             Logs status of scraper heartbeats.

One MopsClient is kept open for the duration of each job so all tickers
reuse the same warmed browser context (WAF-cleared, cookie-stable).
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.core.config import settings
from backend.app.services.taiwan.health import (
    HeartbeatStatus,
    ensure_heartbeat_table,
    read_all_heartbeats,
    write_heartbeat,
)
from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.scrapers.company_master import scrape_company_master
from backend.app.services.taiwan.scrapers.monthly_revenue import (
    scrape_monthly_revenue_watchlist,
)

TPE = ZoneInfo("Asia/Taipei")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("taiwan_scheduler")


def _sqlite_conn() -> sqlite3.Connection:
    uri = settings.POSTGRES_URI
    if not uri.startswith("sqlite:///"):
        raise RuntimeError("Scheduler expects SQLite in Plan 1. Migrate to Postgres via Alembic for prod.")
    conn = sqlite3.connect(uri.replace("sqlite:///", ""))
    ensure_heartbeat_table(conn)
    return conn


def job_company_master() -> None:
    name = "company_master"
    conn = _sqlite_conn()
    try:
        with MopsClient() as client:
            n = scrape_company_master(client)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.OK,
                        rows_inserted=n, rows_updated=0, rows_amended=0)
        logger.info("%s OK rows=%d", name, n)
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                        last_error_msg=str(exc))
    finally:
        conn.close()


def job_monthly_revenue_daily() -> None:
    name = "monthly_revenue_daily"
    conn = _sqlite_conn()
    try:
        with MopsClient() as client:
            stats = scrape_monthly_revenue_watchlist(client)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.OK,
                        rows_inserted=stats.inserted,
                        rows_updated=stats.touched,
                        rows_amended=stats.amended)
        logger.info("%s OK stats=%s", name, stats)
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                        last_error_msg=str(exc))
    finally:
        conn.close()


def job_health_check() -> None:
    conn = _sqlite_conn()
    try:
        rows = read_all_heartbeats(conn)
    finally:
        conn.close()
    for r in rows:
        if r["status"] != "ok":
            logger.warning("Scraper %s status=%s last_error=%s",
                           r["scraper_name"], r["status"], r.get("last_error_msg"))
    logger.info("health_check scrapers=%d", len(rows))


def main() -> None:
    sched = BlockingScheduler(timezone=TPE)

    sched.add_job(job_company_master, CronTrigger(day="1", hour="3", minute="0"),
                  id="company_master_refresh", replace_existing=True)

    sched.add_job(job_monthly_revenue_daily, CronTrigger(hour="10", minute="0"),
                  id="monthly_revenue_daily", replace_existing=True)

    sched.add_job(job_health_check, CronTrigger(minute="17"),
                  id="health_check", replace_existing=True)

    logger.info("Taiwan scheduler starting (jobs=%d)", len(sched.get_jobs()))
    sched.start()


if __name__ == "__main__":
    main()
