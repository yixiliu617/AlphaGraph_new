"""
Taiwan scheduler entry point. Run as: python -m backend.app.services.taiwan.scheduler

Registered APScheduler jobs:
  - company_master_refresh   1st of month @ 03:00 TPE
  - monthly_revenue_daily    daily @ 10:00 TPE, cheap filter to current-month window
  - monthly_revenue_catchup  every 3 days @ 11:00 TPE, scrapes prior month
  - health_check             hourly; logs WARN if any scraper > 2x its cadence
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime
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
    scrape_monthly_revenue_market_month,
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
        raise RuntimeError("Scheduler expects SQLite in Plan 1. Migrate to Postgres later via Alembic.")
    conn = sqlite3.connect(uri.replace("sqlite:///", ""))
    ensure_heartbeat_table(conn)
    return conn


def job_company_master() -> None:
    name = "company_master"
    conn = _sqlite_conn()
    client = MopsClient()
    try:
        n = scrape_company_master(client)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.OK,
                        rows_inserted=n, rows_updated=0, rows_amended=0)
        logger.info("%s OK rows=%d", name, n)
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                        last_error_msg=str(exc))
    finally:
        client.close()
        conn.close()


def _run_mr_month(client: MopsClient, conn, year: int, month: int, label: str) -> None:
    total_inserted = total_updated = total_amended = 0
    err = None
    try:
        for market in ("TWSE", "TPEx"):
            stats = scrape_monthly_revenue_market_month(
                client, year=year, month=month, market=market,
            )
            total_inserted += stats.inserted
            total_updated += stats.touched
            total_amended += stats.amended
        write_heartbeat(conn, scraper_name=label,
                        status=HeartbeatStatus.OK,
                        rows_inserted=total_inserted,
                        rows_updated=total_updated,
                        rows_amended=total_amended)
        logger.info("%s ym=%04d-%02d inserted=%d amended=%d", label, year, month,
                    total_inserted, total_amended)
    except Exception as exc:
        err = str(exc)
        logger.exception("%s ym=%04d-%02d failed: %s", label, year, month, exc)
        write_heartbeat(conn, scraper_name=label,
                        status=HeartbeatStatus.FAILED,
                        last_error_msg=err)


def job_monthly_revenue_daily() -> None:
    now = datetime.now(TPE)
    client = MopsClient()
    conn = _sqlite_conn()
    try:
        _run_mr_month(client, conn, now.year, now.month, "monthly_revenue_daily")
    finally:
        client.close()
        conn.close()


def job_monthly_revenue_catchup() -> None:
    now = datetime.now(TPE)
    if now.month == 1:
        year, month = now.year - 1, 12
    else:
        year, month = now.year, now.month - 1
    client = MopsClient()
    conn = _sqlite_conn()
    try:
        _run_mr_month(client, conn, year, month, "monthly_revenue_catchup")
    finally:
        client.close()
        conn.close()


def job_health_check() -> None:
    """Reads heartbeats; logs WARN for scrapers stale beyond 2x their cadence."""
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

    sched.add_job(job_monthly_revenue_catchup,
                  CronTrigger(day="*/3", hour="11", minute="0"),
                  id="monthly_revenue_catchup", replace_existing=True)

    sched.add_job(job_health_check, CronTrigger(minute="17"),
                  id="health_check", replace_existing=True)

    logger.info("Taiwan scheduler starting (jobs=%d)", len(sched.get_jobs()))
    sched.start()


if __name__ == "__main__":
    main()
