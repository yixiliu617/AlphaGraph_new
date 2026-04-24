"""
Storage-layer test for upsert_material_info — verifies dedup behaviour
on the (ticker, announcement_datetime, subject) key.
"""

from __future__ import annotations

from backend.app.services.taiwan.storage import (
    read_material_info,
    upsert_material_info,
)


def _row(ticker="2330", dt="2026-04-10T13:50:36",
         subject="台積公司2026年3月營收報告", fiscal="2026-03"):
    return {
        "ticker": ticker,
        "name_zh": "台積電",
        "announcement_date": "115/04/10",
        "announcement_time": "13:50:36",
        "announcement_datetime": dt,
        "subject": subject,
        "filing_type": "monthly_revenue",
        "fiscal_ym_guess": fiscal,
        "parameters_json": "{}",
    }


def test_material_info_insert_then_touch(tmp_path):
    stats1 = upsert_material_info([_row()], data_dir=tmp_path)
    assert stats1.inserted == 1
    assert stats1.touched == 0

    # Re-upsert same row → touch, not insert.
    stats2 = upsert_material_info([_row()], data_dir=tmp_path)
    assert stats2.inserted == 0
    assert stats2.touched == 1

    df = read_material_info(data_dir=tmp_path)
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "2330"
    assert df.iloc[0]["fiscal_ym_guess"] == "2026-03"


def test_material_info_two_distinct_announcements_same_ticker(tmp_path):
    stats = upsert_material_info([
        _row(subject="台積公司2026年3月營收報告"),
        _row(dt="2026-04-10T16:55:44", subject="台積公司2026年3月合併營收"),
    ], data_dir=tmp_path)
    assert stats.inserted == 2

    df = read_material_info(data_dir=tmp_path)
    assert len(df) == 2


def test_material_info_read_empty_returns_dataframe(tmp_path):
    df = read_material_info(data_dir=tmp_path)
    assert list(df.columns)[:3] == ["ticker", "name_zh", "announcement_date"]
    assert len(df) == 0
