from pathlib import Path

import pandas as pd
import pytest

from backend.app.services.taiwan.storage import (
    upsert_monthly_revenue,
    read_monthly_revenue,
    write_raw_capture,
    raw_capture_path,
)


@pytest.fixture
def taiwan_data_dir(tmp_path):
    (tmp_path / "monthly_revenue").mkdir(parents=True)
    (tmp_path / "_raw" / "monthly_revenue").mkdir(parents=True)
    return tmp_path


def test_upsert_monthly_revenue_inserts_fresh(taiwan_data_dir):
    rows = [
        {"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
         "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0, "ytd_pct": 0.12,
         "cumulative_ytd_twd": 500_000_000_000, "prior_year_month_twd": 180_000_000_000},
    ]
    stats = upsert_monthly_revenue(rows, data_dir=taiwan_data_dir)
    assert stats.inserted == 1
    assert stats.amended == 0
    df = read_monthly_revenue(data_dir=taiwan_data_dir)
    assert len(df) == 1
    assert df.loc[0, "ticker"] == "2330"
    assert df.loc[0, "content_hash"] != ""
    assert df.loc[0, "amended"] is False or df.loc[0, "amended"] == False  # pandas dtype


def test_upsert_monthly_revenue_touches_only_when_unchanged(taiwan_data_dir):
    rows = [
        {"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
         "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0, "ytd_pct": 0.12,
         "cumulative_ytd_twd": 500_000_000_000, "prior_year_month_twd": 180_000_000_000},
    ]
    upsert_monthly_revenue(rows, data_dir=taiwan_data_dir)
    stats = upsert_monthly_revenue(rows, data_dir=taiwan_data_dir)
    assert stats.inserted == 0
    assert stats.touched == 1
    assert stats.amended == 0


def test_upsert_monthly_revenue_detects_amendment(taiwan_data_dir):
    row_v1 = {"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
              "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0, "ytd_pct": 0.12,
              "cumulative_ytd_twd": 500_000_000_000, "prior_year_month_twd": 180_000_000_000}
    upsert_monthly_revenue([row_v1], data_dir=taiwan_data_dir)
    row_v2 = {**row_v1, "revenue_twd": 210_000_000_000, "yoy_pct": 0.11}
    stats = upsert_monthly_revenue([row_v2], data_dir=taiwan_data_dir)
    assert stats.amended == 1
    df = read_monthly_revenue(data_dir=taiwan_data_dir)
    assert df.loc[0, "revenue_twd"] == 210_000_000_000
    assert df.loc[0, "amended"]
    # History parquet should have the v1 row.
    history = pd.read_parquet(taiwan_data_dir / "monthly_revenue" / "history.parquet")
    assert len(history) == 1
    assert history.loc[0, "revenue_twd"] == 200_000_000_000


def test_write_raw_capture_creates_idempotent_file(taiwan_data_dir):
    ticker = "2330"
    key = "2026-03"
    content = b"<html>raw bytes</html>"
    p = write_raw_capture(
        source="monthly_revenue",
        ticker=ticker,
        key=key,
        content=content,
        data_dir=taiwan_data_dir,
    )
    assert Path(p).read_bytes() == content
    # Second call with identical content: no-op, same path, still OK.
    p2 = write_raw_capture(
        source="monthly_revenue",
        ticker=ticker,
        key=key,
        content=content,
        data_dir=taiwan_data_dir,
    )
    assert p == p2


def test_raw_capture_path_is_deterministic():
    p1 = raw_capture_path(source="monthly_revenue", ticker="2330", key="2026-03",
                          data_dir=Path("/tmp/foo"))
    p2 = raw_capture_path(source="monthly_revenue", ticker="2330", key="2026-03",
                          data_dir=Path("/tmp/foo"))
    assert p1 == p2
    assert str(p1).replace("\\", "/").endswith("monthly_revenue/2330/2026-03.html")
