"""
Tests for news-article clustering (primary selection + cross-scrape matching
+ anchor-token fallback for multi-outlet coverage of the same story).

Imports the recluster helper from tools/web_scraper/recluster_news.py,
which shares the canonical clustering behaviour with news_tracker.py's
scrape-time clustering. Both use tools/web_scraper/_news_cluster.py for
title normalization, cluster_id hashing, and anchor extraction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]  # repo root
WEB_SCRAPER = ROOT / "tools" / "web_scraper"
sys.path.insert(0, str(WEB_SCRAPER))


@pytest.fixture(scope="module")
def recluster_mod():
    import recluster_news  # noqa: PLC0415
    return recluster_news


@pytest.fixture(scope="module")
def cluster_helpers():
    import _news_cluster  # noqa: PLC0415
    return _news_cluster


def _row(title, tier=2, guid=None, pub="2026-04-24T10:00:00+00:00"):
    return {
        "title": title,
        "guid": guid or title,
        "source_tier": tier,
        "pub_iso": pub,
        "source_name": f"src-{tier}",
    }


def test_cluster_id_is_deterministic(cluster_helpers):
    assert cluster_helpers.cluster_id("foo bar") == cluster_helpers.cluster_id("foo bar")
    assert cluster_helpers.cluster_id("foo bar") != cluster_helpers.cluster_id("baz")


def test_extract_anchors_digit_token(cluster_helpers):
    """Mixed alpha+digit tokens (products/versions) become digit anchors."""
    norm = cluster_helpers.norm_title("NVIDIA H100 ships to Meta")
    digit, alpha = cluster_helpers.extract_anchors(norm)
    assert "h100" in digit
    assert "nvidia" in alpha


def test_extract_anchors_coalesces_space_separated_version(cluster_helpers):
    """'GPT 5.5' (space) and 'GPT-5.5' (hyphen) should both yield 'gpt55'."""
    d1, _ = cluster_helpers.extract_anchors(cluster_helpers.norm_title("OpenAI launches GPT-5.5"))
    d2, _ = cluster_helpers.extract_anchors(cluster_helpers.norm_title("OpenAI launches GPT 5.5"))
    assert "gpt55" in d1
    assert "gpt55" in d2


def test_extract_anchors_skips_pure_digits_and_short_alpha(cluster_helpers):
    """Bare years and short words shouldn't become anchors."""
    norm = cluster_helpers.norm_title("2026 is here")
    digit, alpha = cluster_helpers.extract_anchors(norm)
    assert digit == frozenset()  # '2026' is pure-digit, not an anchor
    assert "here" not in alpha  # length 4 — below threshold


def test_anchors_match_digit_anchor_is_sufficient(cluster_helpers):
    """One shared digit anchor merges."""
    assert cluster_helpers.anchors_match(
        frozenset({"gpt55"}), frozenset(),
        frozenset({"gpt55"}), frozenset({"openai"}),
    )


def test_anchors_match_requires_two_alpha_anchors(cluster_helpers):
    """One shared alpha anchor alone is NOT sufficient."""
    assert not cluster_helpers.anchors_match(
        frozenset(), frozenset({"openai"}),
        frozenset(), frozenset({"openai", "nvidia"}),
    )
    assert cluster_helpers.anchors_match(
        frozenset(), frozenset({"openai", "chatgpt"}),
        frozenset(), frozenset({"openai", "chatgpt", "anthropic"}),
    )


def test_within_hours(cluster_helpers):
    a = "2026-04-23T18:00:00+00:00"
    b = "2026-04-23T22:00:00+00:00"
    c = "2026-04-27T18:00:00+00:00"
    assert cluster_helpers.within_hours(a, b, 48)
    assert not cluster_helpers.within_hours(a, c, 48)


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


def test_anchor_pass_clusters_multi_outlet_framings(recluster_mod):
    """Different outlets paraphrase the same story heavily — fuzzy match
    alone misses this, but shared anchor tokens catch it. The GPT-5.5 case.
    """
    df = pd.DataFrame([
        _row("OpenAI confirms GPT-5.5 release on April 23",
             tier=1, guid="a", pub="2026-04-23T18:00:00+00:00"),
        _row("OpenAI launches GPT-5.5 to take on messier workloads",
             tier=2, guid="b", pub="2026-04-23T18:30:00+00:00"),
        _row("Chatbots take a back seat as new GPT-5.5 model focuses on work",
             tier=2, guid="c", pub="2026-04-23T19:00:00+00:00"),
        _row("OpenAI GPT 5.5 launches",  # space-delimited — coalesce must handle
             tier=2, guid="d", pub="2026-04-23T19:30:00+00:00"),
        # A stale story 10 days later — same entity, should NOT merge
        _row("GPT-5.5 usage continues to grow months after launch",
             tier=2, guid="e", pub="2026-05-05T10:00:00+00:00"),
    ])
    out = recluster_mod.recluster(df)
    ids = out.set_index("guid")["cluster_id"]
    # a, b, c, d all share the gpt55 digit anchor within 48h
    assert ids["a"] == ids["b"] == ids["c"] == ids["d"]
    # e is outside the 48h window — should fork a new cluster
    assert ids["e"] != ids["a"]


def test_anchor_pass_respects_time_window(recluster_mod):
    """Two articles sharing a digit anchor but 60h apart should NOT cluster."""
    df = pd.DataFrame([
        _row("NVIDIA H100 cluster delivered to Meta",
             tier=2, guid="a", pub="2026-04-20T10:00:00+00:00"),
        _row("H100 supply tight as order book stretches",
             tier=2, guid="b", pub="2026-04-23T02:00:00+00:00"),  # 64h later
    ])
    out = recluster_mod.recluster(df)
    ids = out.set_index("guid")["cluster_id"]
    assert ids["a"] != ids["b"]


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
