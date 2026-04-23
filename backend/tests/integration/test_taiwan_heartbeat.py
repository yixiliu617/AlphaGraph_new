"""Integration tests for the Taiwan scraper heartbeat table (SQLite)."""

import sqlite3
from datetime import datetime, timezone

import pytest

from backend.app.services.taiwan.health import (
    ensure_heartbeat_table,
    write_heartbeat,
    read_all_heartbeats,
    HeartbeatStatus,
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    ensure_heartbeat_table(conn)
    yield conn
    conn.close()


def test_write_and_read_heartbeat(db):
    write_heartbeat(db, scraper_name="monthly_revenue",
                    status=HeartbeatStatus.OK,
                    rows_inserted=12, rows_updated=0, rows_amended=1)
    rows = read_all_heartbeats(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["scraper_name"] == "monthly_revenue"
    assert r["status"] == "ok"
    assert r["rows_inserted"] == 12
    assert r["rows_amended"] == 1
    assert r["last_success_at"] is not None


def test_failed_heartbeat_sets_error_message(db):
    write_heartbeat(db, scraper_name="monthly_revenue",
                    status=HeartbeatStatus.FAILED,
                    last_error_msg="connection refused")
    r = read_all_heartbeats(db)[0]
    assert r["status"] == "failed"
    assert r["last_error_msg"] == "connection refused"
    assert r["last_error_at"] is not None


def test_multiple_scrapers_tracked_independently(db):
    write_heartbeat(db, scraper_name="monthly_revenue", status=HeartbeatStatus.OK)
    write_heartbeat(db, scraper_name="company_master", status=HeartbeatStatus.OK)
    names = sorted(r["scraper_name"] for r in read_all_heartbeats(db))
    assert names == ["company_master", "monthly_revenue"]
