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

  === High-frequency (every 2-4 hours) ===
    news_scrape            every 2h @ :00  — 28 Google News RSS feeds
    reddit_scrape          every 2h @ :15  — 10 subreddits
    reddit_keyword_search  every 4h @ :30  — 16 keyword × 6-sub cross-product (heavy)
    x_ingest               every 4h @ :50  — twitterapi.io last-24h for validated accounts

  === Daily (prices move slowly) ===
    pcpartpicker_daily     daily @ 04:00 — CPU / GPU / memory / SSD / monitor / PSU price trends
                                           (Cloudflare + Playwright + Gemini vision)
    camel_daily            daily @ 04:30 — CamelCamelCamel Amazon price history (4 DDR4 ASINs;
                                           full re-scrape per run, replace-per-ASIN upsert)
    gpu_price_snapshot     daily @ 04:45 — Vast.ai + RunPod + Tensordock rental prices

  === Infra / observability ===
    social_health_check    hourly @ :23   — reads heartbeats, logs stale scrapers

Zero frontend changes required. The scrapers write to the same
parquet paths the existing /api/v1/social/* and /api/v1/pricing/*
endpoints read from.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sqlite3
import subprocess
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


def _run_subprocess_job(
    *,
    name: str,
    cmd: list[str],
    cwd: pathlib.Path,
    timeout_sec: int,
) -> None:
    """Run a CLI scraper as a subprocess and record a heartbeat.

    Used for scrapers whose entry points are CLI scripts rather than
    importable functions (pcpartpicker, x_backfill). Captures combined
    stdout/stderr for the log; a non-zero exit code → FAILED heartbeat.
    """
    conn = _sqlite_conn()
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONPATH", str(cwd))
    try:
        logger.info("%s starting: %s", name, " ".join(cmd))
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            err = (result.stderr or "")[-400:] or f"exit code {result.returncode}"
            logger.warning("%s failed rc=%s err=%s", name, result.returncode, err)
            write_heartbeat(conn, scraper_name=name,
                            status=HeartbeatStatus.FAILED, last_error_msg=err)
        else:
            logger.info("%s OK rc=0", name)
            write_heartbeat(conn, scraper_name=name,
                            status=HeartbeatStatus.OK,
                            rows_inserted=0, rows_updated=0, rows_amended=0)
    except subprocess.TimeoutExpired:
        logger.exception("%s timed out after %ds", name, timeout_sec)
        write_heartbeat(conn, scraper_name=name,
                        status=HeartbeatStatus.FAILED,
                        last_error_msg=f"timed out after {timeout_sec}s")
    except Exception as exc:
        logger.exception("%s errored: %s", name, exc)
        write_heartbeat(conn, scraper_name=name,
                        status=HeartbeatStatus.FAILED, last_error_msg=str(exc))
    finally:
        conn.close()


# Project root: parents[4] from this file (services/social/scheduler.py).
#   parents[0] = social   parents[1] = services   parents[2] = app
#   parents[3] = backend  parents[4] = project root
_ROOT = pathlib.Path(__file__).resolve().parents[4]


def job_pcpartpicker_daily() -> None:
    """PCPartPicker price trends: download chart PNGs + Gemini-vision extract.
    Heavy (~10-15 min) — Playwright + Cloudflare bypass + LLM calls. Daily cadence."""
    _run_subprocess_job(
        name="pcpartpicker_daily",
        cmd=[sys.executable, "tools/web_scraper/pcpartpicker_trends.py", "run"],
        cwd=_ROOT,
        timeout_sec=30 * 60,   # 30 min hard cap
    )


def job_camel_daily() -> None:
    """CamelCamelCamel Amazon price history: download chart PNGs + Gemini-vision
    extract. ~4 ASINs per run, ~$0.02/day in Gemini calls. Cloudflare-protected
    CDN — uses the same Playwright CDP profile as pcpartpicker (port 9223)."""
    _run_subprocess_job(
        name="camel_daily",
        cmd=[sys.executable, "tools/web_scraper/camel_tracker.py", "run"],
        cwd=_ROOT,
        timeout_sec=10 * 60,   # 10 min hard cap — few products, fast
    )


def job_x_ingest() -> None:
    """X/Twitter incremental ingest: last 24h of tweets for validated accounts."""
    _run_subprocess_job(
        name="x_ingest",
        cmd=[sys.executable, "tools/x_backfill.py", "--days", "1"],
        cwd=_ROOT,
        timeout_sec=15 * 60,   # 15 min hard cap
    )


def job_social_health_check() -> None:
    conn = _sqlite_conn()
    try:
        rows = read_all_heartbeats(conn)
    finally:
        conn.close()
    social_scraper_names = {
        "news_scrape", "reddit_scrape", "reddit_keyword_search",
        "gpu_price_snapshot", "pcpartpicker_daily", "camel_daily", "x_ingest",
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
        CronTrigger(hour="4", minute="45"),
        id="gpu_price_snapshot", replace_existing=True,
    )
    sched.add_job(
        job_x_ingest,
        CronTrigger(hour="*/4", minute="50"),
        id="x_ingest", replace_existing=True,
    )
    sched.add_job(
        job_pcpartpicker_daily,
        CronTrigger(hour="4", minute="0"),
        id="pcpartpicker_daily", replace_existing=True,
    )
    sched.add_job(
        job_camel_daily,
        CronTrigger(hour="4", minute="30"),
        id="camel_daily", replace_existing=True,
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
