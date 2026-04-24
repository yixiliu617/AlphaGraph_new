"""
Tests for news-article clustering (primary selection + cross-scrape matching).

Imports the recluster helper from tools/web_scraper/recluster_news.py,
which shares the canonical clustering behaviour with news_tracker.py's
scrape-time clustering.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]  # repo root
RECLUSTER_PY = ROOT / "tools" / "web_scraper" / "recluster_news.py"


@pytest.fixture(scope="module")
def recluster_mod():
    spec = importlib.util.spec_from_file_location("recluster_news", RECLUSTER_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["recluster_news"] = mod
    spec.loader.exec_module(mod)
    return mod


def _row(title, tier=2, guid=None, pub="2026-04-24T10:00:00+00:00"):
    return {
        "title": title,
        "guid": guid or title,
        "source_tier": tier,
        "pub_iso": pub,
        "source_name": f"src-{tier}",
    }


def test_cluster_id_is_deterministic(recluster_mod):
    assert recluster_mod._cluster_id("foo bar") == recluster_mod._cluster_id("foo bar")
    assert recluster_mod._cluster_id("foo bar") != recluster_mod._cluster_id("baz")


def test_similar_titles_share_cluster(recluster_mod):
    df = pd.DataFrame([
        _row("Intel Q1 earnings beat estimates", tier=2, guid="a"),
        _row("Intel Q1 earnings beat analyst estimates", tier=2, guid="b"),
        _row("Rubin launches new NVIDIA AI chip"           , tier=2, guid="c"),
    ])
    out = recluster_mod.recluster(df)
    ids = out.set_index("guid")["cluster_id"]
    # a and b should cluster together
    assert ids["a"] == ids["b"]
    # c should be a separate cluster
    assert ids["c"] != ids["a"]


def test_primary_selection_prefers_lower_source_tier(recluster_mod):
    df = pd.DataFrame([
        _row("Intel Q1 earnings beat", tier=2, guid="a", pub="2026-04-24T09:00:00+00:00"),
        _row("Intel Q1 earnings beat", tier=1, guid="b", pub="2026-04-24T10:00:00+00:00"),  # tier 1 = better
        _row("Intel Q1 earnings beat", tier=3, guid="c", pub="2026-04-24T08:00:00+00:00"),
    ])
    out = recluster_mod.recluster(df)
    primary = out[out["is_primary"]]
    assert len(primary) == 1
    assert primary.iloc[0]["guid"] == "b"  # tier 1 wins


def test_singleton_article_is_its_own_cluster(recluster_mod):
    df = pd.DataFrame([
        _row("some completely unique story 12345"),
    ])
    out = recluster_mod.recluster(df)
    assert out.iloc[0]["is_primary"] is True or out.iloc[0]["is_primary"]
    assert isinstance(out.iloc[0]["cluster_id"], str)


def test_empty_title_uses_guid_for_cluster_id(recluster_mod):
    df = pd.DataFrame([
        {"title": "", "guid": "g1", "source_tier": 2, "pub_iso": "2026-04-24T00:00:00+00:00",
         "source_name": "src"},
    ])
    out = recluster_mod.recluster(df)
    assert out.iloc[0]["is_primary"]


def test_each_row_always_has_cluster_id(recluster_mod):
    df = pd.DataFrame([
        _row("Chinese chipmaker announces fab expansion", tier=1, guid="a"),
        _row("Chinese chipmaker announces fab expansion plan", tier=2, guid="b"),
        _row("Intel Q1 beat", tier=1, guid="c"),
        _row("", tier=2, guid="d"),
        _row("NVIDIA unveils Rubin-Ultra with HBM4", tier=1, guid="e"),
    ])
    out = recluster_mod.recluster(df)
    assert out["cluster_id"].notna().all()
    assert out["is_primary"].notna().all()


def test_cluster_count_matches_sibling_expectation(recluster_mod):
    """A multi-source story produces 1 primary + N siblings. Using titles
    that are actually ≥0.7 SequenceMatcher-similar (same phrasing with
    small variations)."""
    df = pd.DataFrame([
        _row("Intel Q1 earnings beat analyst estimates", tier=1, guid="b0"),
        _row("Intel Q1 earnings beat analysts estimates", tier=2, guid="r1"),
        _row("Intel Q1 earnings beat estimates analysts", tier=2, guid="r2"),
        _row("Intel Q1 earnings beat the analyst estimates", tier=2, guid="r3"),
    ])
    out = recluster_mod.recluster(df)
    # Find the cluster that b0 belongs to
    cid = out[out["guid"] == "b0"].iloc[0]["cluster_id"]
    members = (out["cluster_id"] == cid).sum()
    assert members >= 3  # at least three of the four variations cluster
    primary = out[(out["cluster_id"] == cid) & (out["is_primary"])]
    assert len(primary) == 1
    assert primary.iloc[0]["guid"] == "b0"  # tier 1 wins
