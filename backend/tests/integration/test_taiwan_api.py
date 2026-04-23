"""Integration tests for /api/v1/taiwan/* using an isolated data_dir."""

import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.app.services.taiwan import storage


@pytest.fixture
def taiwan_tmp(monkeypatch, tmp_path):
    """Point the taiwan package at a scratch data dir so tests don't pollute real data."""
    monkeypatch.setattr(storage, "DEFAULT_DATA_DIR", tmp_path)
    (tmp_path / "monthly_revenue").mkdir(parents=True)
    (tmp_path / "_registry").mkdir(parents=True)
    (tmp_path / "_raw" / "monthly_revenue").mkdir(parents=True)
    # Minimal watchlist
    wl = pd.DataFrame([
        {"ticker": "2330", "name": "TSMC", "market": "TWSE",
         "sector": "Semiconductors", "subsector": "Foundry", "notes": ""}
    ])
    # Override registry path
    from backend.app.services.taiwan import registry
    monkeypatch.setattr(registry, "WATCHLIST_CSV", tmp_path / "watchlist_semi.csv")
    wl.to_csv(tmp_path / "watchlist_semi.csv", index=False)
    monkeypatch.setattr(registry, "REGISTRY_PARQUET",
                        tmp_path / "_registry" / "mops_company_master.parquet")
    return tmp_path


def test_watchlist_endpoint_returns_our_watchlist(taiwan_tmp):
    client = TestClient(app)
    resp = client.get("/api/v1/taiwan/watchlist")
    assert resp.status_code == 200
    data = resp.json()["data"]
    tickers = {r["ticker"] for r in data}
    assert "2330" in tickers


def test_monthly_revenue_endpoint_returns_saved_rows(taiwan_tmp):
    rows = [{"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
             "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0,
             "ytd_pct": 0.12, "cumulative_ytd_twd": 500_000_000_000,
             "prior_year_month_twd": 180_000_000_000}]
    storage.upsert_monthly_revenue(rows, data_dir=taiwan_tmp)

    client = TestClient(app)
    resp = client.get("/api/v1/taiwan/monthly-revenue?tickers=2330&months=12")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["ticker"] == "2330"
    assert data[0]["revenue_twd"] == 200_000_000_000
    assert abs(data[0]["yoy_pct"] - 0.1) < 1e-6


def test_ticker_endpoint_returns_note_metadata(taiwan_tmp):
    rows = [{"ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
             "revenue_twd": 200_000_000_000, "yoy_pct": 0.1, "mom_pct": 0.0,
             "ytd_pct": 0.12, "cumulative_ytd_twd": 500_000_000_000,
             "prior_year_month_twd": 180_000_000_000}]
    storage.upsert_monthly_revenue(rows, data_dir=taiwan_tmp)
    client = TestClient(app)
    resp = client.get("/api/v1/taiwan/ticker/2330")
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["ticker"] == "2330"
    assert d["name"] == "TSMC"
    assert d["subsector"] == "Foundry"
    assert d["latest_revenue"]["fiscal_ym"] == "2026-03"


def test_health_endpoint_returns_scrapers_list(taiwan_tmp):
    client = TestClient(app)
    resp = client.get("/api/v1/taiwan/health")
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert "scrapers" in d
    assert isinstance(d["scrapers"], list)
