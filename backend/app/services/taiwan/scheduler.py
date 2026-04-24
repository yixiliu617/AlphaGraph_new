"""
Taiwan scheduler entry point. Run as: python -m backend.app.services.taiwan.scheduler

Jobs (all Asia/Taipei time):

  === Live freshness layer (MOPS t146sb05_detail) ===
  - monthly_revenue_daily    daily @ 10:00
                             Per-ticker call to t146sb05_detail for all
                             51 watchlist tickers. Base cadence for the
                             16th-31st of each month when few filings drop.
  - monthly_revenue_window   every 30 min @ :00,:30, day=1-15
                             Same endpoint, higher frequency during the
                             statutory filing window. Captures new files
                             within 30 min.

  === Material-info early-warning (t05st02) ===
  - material_info_window     every 15 min @ :07,:22,:37,:52, day=1-15
                             Polls t05st02 for watchlist revenue-flavored
                             announcements. When a match arrives, triggers
                             an immediate monthly_revenue poll for that
                             ticker so we don't wait for the next window tick.

  === Historical backfill + corrections ===
  - twse_weekly_patch        Sunday @ 03:00
  - tpex_weekly_patch        Sunday @ 03:30
                             Pull prior-month bulk archive and upsert —
                             catches amendments that might slip past
                             per-ticker polling.

  === Infra / observability ===
  - company_master_refresh   1st of month @ 03:00
                             Watchlist → market/sector resolver via
                             KeywordsQuery.
  - health_check             hourly @ :17
                             Logs WARN on any stale scraper.

One MopsClient is kept open per job invocation so all 51 tickers reuse
the same warmed browser context (WAF-cleared, cookie-stable).
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
from backend.app.services.taiwan.scrapers.material_info import (
    scrape_material_info_window,
)
from backend.app.services.taiwan.scrapers.monthly_revenue import (
    scrape_monthly_revenue_ticker,
    scrape_monthly_revenue_watchlist,
)
from backend.app.services.taiwan.scrapers.tpex_historical import (
    backfill_range as tpex_backfill_range,
)
from backend.app.services.taiwan.scrapers.twse_historical import (
    backfill_range as twse_backfill_range,
)
from backend.app.services.taiwan.registry import (
    list_watchlist_tickers,
    load_mops_master,
)
from backend.app.services.taiwan.storage import (
    DEFAULT_DATA_DIR,
    upsert_monthly_revenue,
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


def _prior_month(now_tpe: datetime) -> tuple[int, int]:
    if now_tpe.month == 1:
        return now_tpe.year - 1, 12
    return now_tpe.year, now_tpe.month - 1


# ---------------------------------------------------------------------------
# Company master — monthly
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Monthly revenue — daily + window
# ---------------------------------------------------------------------------

def _run_full_watchlist_revenue(name: str) -> None:
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


def job_monthly_revenue_daily() -> None:
    _run_full_watchlist_revenue("monthly_revenue_daily")


def job_monthly_revenue_window() -> None:
    _run_full_watchlist_revenue("monthly_revenue_window")


# ---------------------------------------------------------------------------
# Material info (t05st02) — every 15 min during the window
# ---------------------------------------------------------------------------

def job_material_info_window() -> None:
    """Poll t05st02, upsert matches, and trigger a per-ticker
    monthly_revenue poll for any new (ticker, fiscal_ym) we haven't
    stored yet."""
    name = "material_info_window"
    conn = _sqlite_conn()
    try:
        with MopsClient() as client:
            stats, triggers = scrape_material_info_window(client)

            # For each triggered (ticker, fiscal_ym) that doesn't already
            # have monthly_revenue data, immediately poll t146sb05_detail
            # for that ticker. We reuse the same warm MopsClient.
            if triggers:
                market_by_ticker = _load_market_by_ticker()
                trigger_stats = {"inserted": 0, "touched": 0, "amended": 0}
                triggered_tickers = {t for t, _ym in triggers}
                for ticker in triggered_tickers:
                    market = market_by_ticker.get(ticker, "Unknown")
                    s = scrape_monthly_revenue_ticker(
                        client, ticker, market=market,
                    )
                    trigger_stats["inserted"] += s.inserted
                    trigger_stats["touched"] += s.touched
                    trigger_stats["amended"] += s.amended
                logger.info("material_info_window trigger-polled %d ticker(s): %s",
                            len(triggered_tickers), trigger_stats)

        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.OK,
                        rows_inserted=stats.inserted,
                        rows_updated=stats.touched,
                        rows_amended=stats.amended)
        logger.info("%s OK stats=%s triggers=%d", name, stats, len(triggers))
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                        last_error_msg=str(exc))
    finally:
        conn.close()


def _load_market_by_ticker() -> dict[str, str]:
    master = load_mops_master()
    if master.empty or "co_id" not in master.columns or "market" not in master.columns:
        return {}
    return dict(zip(master["co_id"].astype(str), master["market"].astype(str)))


# ---------------------------------------------------------------------------
# Weekly bulk patches (TWSE C04003 + TPEx O_YYYYMM)
# ---------------------------------------------------------------------------

def _run_bulk_patch(
    name: str,
    *,
    fn,  # twse_backfill_range or tpex_backfill_range
    cache_sub: str,
) -> None:
    """Run one-month bulk-archive patch for the prior month."""
    conn = _sqlite_conn()
    try:
        year, month = _prior_month(datetime.now(TPE))
        watchlist = set(list_watchlist_tickers())
        cache_dir = DEFAULT_DATA_DIR / "_raw" / cache_sub
        rows = fn(
            start=(year, month), end=(year, month),
            watchlist=watchlist, cache_dir=cache_dir,
        )
        if not rows:
            write_heartbeat(conn, scraper_name=name,
                            status=HeartbeatStatus.DEGRADED,
                            rows_inserted=0, rows_updated=0, rows_amended=0,
                            last_error_msg=f"no rows returned for {year:04d}-{month:02d}")
            logger.warning("%s: 0 rows for %04d-%02d", name, year, month)
            return

        stats = upsert_monthly_revenue(rows)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.OK,
                        rows_inserted=stats.inserted,
                        rows_updated=stats.touched,
                        rows_amended=stats.amended)
        logger.info("%s OK ym=%04d-%02d stats=%s", name, year, month, stats)
    except Exception as exc:
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                        last_error_msg=str(exc))
    finally:
        conn.close()


def job_twse_weekly_patch() -> None:
    _run_bulk_patch("twse_weekly_patch",
                    fn=twse_backfill_range,
                    cache_sub="twse_zip")


def job_tpex_weekly_patch() -> None:
    _run_bulk_patch("tpex_weekly_patch",
                    fn=tpex_backfill_range,
                    cache_sub="tpex_xls")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    sched = BlockingScheduler(timezone=TPE)

    # Live freshness ---------------------------------------------------
    sched.add_job(
        job_monthly_revenue_daily,
        CronTrigger(hour="10", minute="0"),
        id="monthly_revenue_daily", replace_existing=True,
    )
    sched.add_job(
        job_monthly_revenue_window,
        CronTrigger(day="1-15", minute="0,30"),
        id="monthly_revenue_window", replace_existing=True,
    )

    # Material-info early-warning ---------------------------------------
    sched.add_job(
        job_material_info_window,
        CronTrigger(day="1-15", minute="7,22,37,52"),
        id="material_info_window", replace_existing=True,
    )

    # Weekly bulk patches -----------------------------------------------
    sched.add_job(
        job_twse_weekly_patch,
        CronTrigger(day_of_week="sun", hour="3", minute="0"),
        id="twse_weekly_patch", replace_existing=True,
    )
    sched.add_job(
        job_tpex_weekly_patch,
        CronTrigger(day_of_week="sun", hour="3", minute="30"),
        id="tpex_weekly_patch", replace_existing=True,
    )

    # Infra --------------------------------------------------------------
    sched.add_job(
        job_company_master,
        CronTrigger(day="1", hour="3", minute="0"),
        id="company_master_refresh", replace_existing=True,
    )
    sched.add_job(
        job_health_check,
        CronTrigger(minute="17"),
        id="health_check", replace_existing=True,
    )

    logger.info("Taiwan scheduler starting (jobs=%d)", len(sched.get_jobs()))
    for j in sched.get_jobs():
        logger.info("  registered: %s  trigger=%s", j.id, j.trigger)
    sched.start()


if __name__ == "__main__":
    main()
