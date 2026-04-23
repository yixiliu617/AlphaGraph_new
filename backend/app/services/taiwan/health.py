"""
Heartbeat + /health helpers for the Taiwan scraper package.

Each scraper writes to a single SQLite table after every run. A dedicated
health_check job reads it hourly and logs WARN when a scraper is stale.
The /api/v1/taiwan/health endpoint exposes the same table to the frontend.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class HeartbeatStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS taiwan_scraper_heartbeat (
    scraper_name     TEXT PRIMARY KEY,
    last_run_at      TIMESTAMP,
    last_success_at  TIMESTAMP,
    last_error_at    TIMESTAMP,
    last_error_msg   TEXT,
    rows_inserted    INTEGER DEFAULT 0,
    rows_updated     INTEGER DEFAULT 0,
    rows_amended     INTEGER DEFAULT 0,
    status           TEXT CHECK(status IN ('ok', 'degraded', 'failed')) NOT NULL
);
"""


def ensure_heartbeat_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_SQL)
    conn.commit()


def write_heartbeat(
    conn: sqlite3.Connection,
    *,
    scraper_name: str,
    status: HeartbeatStatus,
    rows_inserted: int = 0,
    rows_updated: int = 0,
    rows_amended: int = 0,
    last_error_msg: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    last_success = now if status == HeartbeatStatus.OK else None
    last_error = now if status == HeartbeatStatus.FAILED else None

    conn.execute(
        """
        INSERT INTO taiwan_scraper_heartbeat
          (scraper_name, last_run_at, last_success_at, last_error_at, last_error_msg,
           rows_inserted, rows_updated, rows_amended, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scraper_name) DO UPDATE SET
          last_run_at     = excluded.last_run_at,
          last_success_at = COALESCE(excluded.last_success_at, taiwan_scraper_heartbeat.last_success_at),
          last_error_at   = COALESCE(excluded.last_error_at,   taiwan_scraper_heartbeat.last_error_at),
          last_error_msg  = COALESCE(excluded.last_error_msg,  taiwan_scraper_heartbeat.last_error_msg),
          rows_inserted   = excluded.rows_inserted,
          rows_updated    = excluded.rows_updated,
          rows_amended    = excluded.rows_amended,
          status          = excluded.status
        """,
        (scraper_name, now, last_success, last_error, last_error_msg,
         rows_inserted, rows_updated, rows_amended, status.value),
    )
    conn.commit()


def read_all_heartbeats(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT scraper_name, last_run_at, last_success_at, last_error_at, "
        "last_error_msg, rows_inserted, rows_updated, rows_amended, status "
        "FROM taiwan_scraper_heartbeat ORDER BY scraper_name"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
