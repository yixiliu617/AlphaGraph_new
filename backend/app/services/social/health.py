"""
Heartbeat hooks for social-media scrapers.

Re-exports the taiwan.health primitives so the social package has its
own import path while sharing the single `taiwan_scraper_heartbeat`
SQLite table (which is effectively a cross-domain scraper registry —
the `taiwan_` prefix is historical; scraper_name is the discriminator).
"""

from __future__ import annotations

from backend.app.services.taiwan.health import (
    HeartbeatStatus,
    ensure_heartbeat_table,
    read_all_heartbeats,
    write_heartbeat,
)

__all__ = [
    "HeartbeatStatus",
    "ensure_heartbeat_table",
    "read_all_heartbeats",
    "write_heartbeat",
]
