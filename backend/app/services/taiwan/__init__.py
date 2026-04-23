"""
Taiwan disclosure ingestion package.

Scrapes MOPS (公開資訊觀測站) for Taiwan-listed companies on the semi
watchlist. Writes parquet + raw captures under backend/data/taiwan/.
Exposes data via /api/v1/taiwan/* endpoints. Runs via the taiwan_scheduler
Fly.io process.

See docs/superpowers/specs/2026-04-23-taiwan-disclosure-ingestion-design.md
for the full architecture.
"""
