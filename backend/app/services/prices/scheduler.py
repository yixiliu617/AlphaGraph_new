"""
Equity-prices scheduler entry point. Run as:

    python -m backend.app.services.prices.scheduler

Jobs (all Asia/Taipei time, declared in `backend/data/cron_jobs.json` and
also registered here for the live APScheduler runner):

  - prices.us_daily            07:00 daily
                               Daily OHLCV for US tickers (after Yahoo
                               finalises adj_close around 18:00 ET prior
                               session = 07:00 TPE).
  - prices.taiwan_daily        14:30 daily
                               Daily OHLCV for .TW tickers (1h after the
                               13:30 TWSE close).
  - prices.us_intraday_15m     :00 :15 :30 :45  during 21:00-04:59 TPE
                               (= 09:00-16:59 ET regular + buffer)
                               15-minute bars rolling 60-day window.
  - prices.taiwan_intraday_15m :00 :15 :30 :45  during 09:00-13:59 TPE
                               (= regular TWSE session)
                               15-minute bars rolling 60-day window.
  - prices.health_check        hourly @ :43

The actual download logic lives in
`backend/scripts/extractors/equity_prices.py`. This module is a thin
job runner + heartbeat shim, mirroring the Taiwan and Social scheduler
patterns. Tickers are sourced from the platform-universe registry so
the scheduler scales naturally as the universe grows toward 2000.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
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
from backend.app.services.universe_registry import read_universe
from backend.scripts.extractors import equity_prices as ep
from backend.scripts.extractors import twse_prices as twse_ep


TPE = ZoneInfo("Asia/Taipei")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("prices_scheduler")


# ---------------------------------------------------------------------------
# Universe selection
# ---------------------------------------------------------------------------


def _universe_tickers(market: str) -> list[str]:
    """Return tickers from `platform_universe.csv` for the given market.

    For Taiwan we suffix with `.TW` so yfinance accepts them (the registry
    stores raw co_id like '2330'). We exclude obviously non-equity rows by
    skipping blank tickers.
    """
    df = read_universe()
    if df.empty:
        # Fallback to the 18 default tickers so the scheduler still runs
        # against something on a fresh checkout. This also doubles as a
        # smoke-test path.
        if market == "US":
            return [t for t in ep.DEFAULT_TICKERS if not ep.is_taiwan(t)]
        return [t for t in ep.DEFAULT_TICKERS if ep.is_taiwan(t)]

    rows = df[df["market"] == market]
    rows = rows[rows["ticker"].astype(str).str.len() > 0]
    if market == "TW":
        return [f"{t}.TW" for t in rows["ticker"].astype(str).tolist()]
    return rows["ticker"].astype(str).tolist()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _sqlite_conn() -> sqlite3.Connection:
    uri = settings.POSTGRES_URI
    if not uri.startswith("sqlite:///"):
        raise RuntimeError("Scheduler expects SQLite in Plan 1.")
    conn = sqlite3.connect(uri.replace("sqlite:///", ""))
    ensure_heartbeat_table(conn)
    return conn


# ---------------------------------------------------------------------------
# Job runners
# ---------------------------------------------------------------------------


def _run_daily(name: str, tickers: list[str]) -> None:
    conn = _sqlite_conn()
    try:
        if not tickers:
            logger.warning("%s: no tickers in universe; skipping", name)
            write_heartbeat(conn, scraper_name=name,
                            status=HeartbeatStatus.DEGRADED,
                            last_error_msg="empty ticker universe")
            return
        # Resume mode: only fetch from the last stored date forward, so daily
        # ticks are cheap (~1 bar/ticker).
        from datetime import date, timedelta
        results = ep.extract_daily(
            tickers,
            start=date.today() - timedelta(days=7),  # safety overlap
            end=date.today(),
            max_workers=8,
            resume=True,
        )
        n_ok = sum(1 for r in results if r.error is None)
        n_err = len(results) - n_ok
        rows = sum(r.rows for r in results)
        status = HeartbeatStatus.OK if n_err == 0 else (
            HeartbeatStatus.DEGRADED if n_ok > 0 else HeartbeatStatus.FAILED
        )
        err_summary = None
        if n_err:
            sample = [r for r in results if r.error][:3]
            err_summary = "; ".join(f"{r.ticker}:{r.error}" for r in sample)
        write_heartbeat(conn, scraper_name=name, status=status,
                        rows_inserted=rows, rows_updated=0, rows_amended=0,
                        last_error_msg=err_summary)
        logger.info("%s ok=%d err=%d total_rows~=%d", name, n_ok, n_err, rows)
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                        last_error_msg=str(exc))
    finally:
        conn.close()


def _run_intraday(name: str, tickers: list[str]) -> None:
    conn = _sqlite_conn()
    try:
        if not tickers:
            logger.warning("%s: no tickers in universe; skipping", name)
            write_heartbeat(conn, scraper_name=name,
                            status=HeartbeatStatus.DEGRADED,
                            last_error_msg="empty ticker universe")
            return
        # Always re-pull a small window. The writer dedups + retains 60d.
        results = ep.extract_intraday(
            tickers,
            interval="15m",
            days=2,
            max_workers=8,
        )
        n_ok = sum(1 for r in results if r.error is None)
        n_err = len(results) - n_ok
        status = HeartbeatStatus.OK if n_err == 0 else (
            HeartbeatStatus.DEGRADED if n_ok > 0 else HeartbeatStatus.FAILED
        )
        err_summary = None
        if n_err:
            sample = [r for r in results if r.error][:3]
            err_summary = "; ".join(f"{r.ticker}:{r.error}" for r in sample)
        write_heartbeat(conn, scraper_name=name, status=status,
                        last_error_msg=err_summary)
        logger.info("%s ok=%d err=%d", name, n_ok, n_err)
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                        last_error_msg=str(exc))
    finally:
        conn.close()


def job_us_daily() -> None:
    _run_daily("prices.us_daily", _universe_tickers("US"))


def job_taiwan_daily() -> None:
    _run_daily("prices.taiwan_daily", _universe_tickers("TW"))


def job_us_intraday_15m() -> None:
    _run_intraday("prices.us_intraday_15m", _universe_tickers("US"))


def job_taiwan_intraday_15m() -> None:
    _run_intraday("prices.taiwan_intraday_15m", _universe_tickers("TW"))


def job_taiwan_twse_patch() -> None:
    """Nightly TWSE-direct overwrite of the prior 30 days for every .TW
    ticker. Heals Yahoo's historical gaps and provides authoritative
    same-day close after the 13:30 TPE close. Adjustment-factor logic
    refuses to write if Yahoo / TWSE disagree by >0.5% across the
    overlap (split / corp-action signal), so a stale split adjustment
    in Yahoo can't silently corrupt our silver."""
    name = "prices.taiwan_twse_patch"
    conn = _sqlite_conn()
    try:
        tickers = _universe_tickers("TW")
        if not tickers:
            write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.DEGRADED,
                            last_error_msg="empty TW universe")
            return
        results = twse_ep.patch_all_tw(days=30, tickers=tickers)
        n_ok = sum(1 for r in results if "error" not in r and not r.get("skipped_reason"))
        n_skipped = sum(1 for r in results if r.get("skipped_reason"))
        n_err = sum(1 for r in results if "error" in r)
        rows_total = sum((r.get("patched", 0) + r.get("filled_gap", 0)) for r in results)
        gaps_total = sum(r.get("filled_gap", 0) for r in results)
        status = (
            HeartbeatStatus.OK if n_err == 0 and n_skipped == 0
            else HeartbeatStatus.DEGRADED if n_ok > 0
            else HeartbeatStatus.FAILED
        )
        err_summary = None
        if n_err or n_skipped:
            samples = [r for r in results if r.get("error") or r.get("skipped_reason")][:3]
            err_summary = "; ".join(
                f"{r['ticker']}:{r.get('error') or r.get('skipped_reason')}"
                for r in samples
            )
        write_heartbeat(conn, scraper_name=name, status=status,
                        rows_inserted=gaps_total,
                        rows_updated=rows_total - gaps_total,
                        rows_amended=0,
                        last_error_msg=err_summary)
        logger.info("%s ok=%d skipped=%d err=%d rows_overwritten=%d gaps_filled=%d",
                    name, n_ok, n_skipped, n_err,
                    rows_total - gaps_total, gaps_total)
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s failed: %s", name, exc)
        write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                        last_error_msg=str(exc))
    finally:
        conn.close()


def job_prices_health_check() -> None:
    """Read all `prices.*` heartbeats and log WARN on stale rows."""
    conn = _sqlite_conn()
    try:
        rows = read_all_heartbeats(conn)
    finally:
        conn.close()
    for r in rows:
        if not r["scraper_name"].startswith("prices."):
            continue
        if r["status"] != "ok":
            logger.warning("Prices scraper %s status=%s last_error=%s",
                           r["scraper_name"], r["status"], r.get("last_error_msg"))
    logger.info("prices.health_check scrapers=%d",
                sum(1 for r in rows if r["scraper_name"].startswith("prices.")))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    sched = BlockingScheduler(timezone=TPE)

    sched.add_job(
        job_us_daily,
        CronTrigger(hour="7", minute="0"),
        id="prices.us_daily", replace_existing=True,
    )
    sched.add_job(
        job_taiwan_daily,
        CronTrigger(hour="14", minute="30"),
        id="prices.taiwan_daily", replace_existing=True,
    )
    sched.add_job(
        job_us_intraday_15m,
        CronTrigger(hour="21-23,0-4", minute="0,15,30,45"),
        id="prices.us_intraday_15m", replace_existing=True,
    )
    sched.add_job(
        job_taiwan_intraday_15m,
        CronTrigger(hour="9-13", minute="0,15,30,45"),
        id="prices.taiwan_intraday_15m", replace_existing=True,
    )
    sched.add_job(
        job_taiwan_twse_patch,
        CronTrigger(hour="15", minute="0"),
        id="prices.taiwan_twse_patch", replace_existing=True,
    )
    sched.add_job(
        job_prices_health_check,
        CronTrigger(minute="43"),
        id="prices.health_check", replace_existing=True,
    )

    logger.info("Prices scheduler starting (jobs=%d)", len(sched.get_jobs()))
    for j in sched.get_jobs():
        logger.info("  registered: %s  trigger=%s", j.id, j.trigger)
    sched.start()


if __name__ == "__main__":
    main()
