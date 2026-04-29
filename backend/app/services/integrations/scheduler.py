"""
APScheduler entry point for integration syncs.

  python -m backend.app.services.integrations.scheduler

Runs `sync_runner.sync_all_due()` every 15 minutes. The runner itself
respects each service's per-credential cadence (see
`oauth_scopes.SERVICES[*].sync_minutes`) — calendar = 30 min, mail =
60 min, OneNote = 240 min, etc. — so the 15-minute scheduler tick is
just a "check who's due" pass; actual API calls only happen for due
credentials.

Heartbeat goes to the same `taiwan_scraper_heartbeat` SQLite table the
other AlphaGraph schedulers use, with scraper_name='integrations.sync'.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.core.config import settings
from backend.app.services.integrations.sync_runner import sync_all_due
from backend.app.services.taiwan.health import (
    HeartbeatStatus, ensure_heartbeat_table, write_heartbeat,
)


TPE = ZoneInfo("Asia/Taipei")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("integrations_scheduler")


def _sqlite_conn() -> sqlite3.Connection:
    uri = settings.POSTGRES_URI
    if not uri.startswith("sqlite:///"):
        # Heartbeat table is on SQLite for now (cross-domain); see roadmap.
        # On Postgres-only deploy, this scheduler still runs but the
        # heartbeat write below is a no-op.
        return None  # type: ignore
    conn = sqlite3.connect(uri.replace("sqlite:///", ""))
    ensure_heartbeat_table(conn)
    return conn


def job_integrations_sync() -> None:
    name = "integrations.sync"
    conn = _sqlite_conn()
    try:
        results = sync_all_due()
        n_ran = sum(1 for r in results if not r.get("skipped"))
        n_ok  = sum(1 for r in results if r.get("ok"))
        n_err = sum(1 for r in results if r.get("error"))
        rows  = sum(r.get("inserted", 0) + r.get("updated", 0) for r in results)
        status = (
            HeartbeatStatus.OK if n_err == 0
            else HeartbeatStatus.DEGRADED if n_ok > 0
            else HeartbeatStatus.FAILED
        )
        err = None
        if n_err:
            err_samples = [r for r in results if r.get("error")][:3]
            err = "; ".join(f"{r['service']}:{r['error']}" for r in err_samples)
        if conn is not None:
            write_heartbeat(conn, scraper_name=name, status=status,
                            rows_inserted=rows, last_error_msg=err)
        logger.info("%s ran=%d ok=%d err=%d rows=%d",
                    name, n_ran, n_ok, n_err, rows)
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s failed: %s", name, exc)
        if conn is not None:
            write_heartbeat(conn, scraper_name=name, status=HeartbeatStatus.FAILED,
                            last_error_msg=str(exc))
    finally:
        if conn is not None:
            conn.close()


def main() -> None:
    sched = BlockingScheduler(timezone=TPE)
    sched.add_job(
        job_integrations_sync,
        CronTrigger(minute="*/15"),
        id="integrations.sync", replace_existing=True,
    )
    logger.info("Integrations scheduler starting")
    for j in sched.get_jobs():
        logger.info("  registered: %s  trigger=%s", j.id, j.trigger)
    sched.start()


if __name__ == "__main__":
    main()
