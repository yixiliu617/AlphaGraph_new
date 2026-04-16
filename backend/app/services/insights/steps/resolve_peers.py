"""
Phase 2 — Entity + Peer Resolution.

Given a list of primary entities (tickers), resolves their GICS subindustry
and builds a tiered peer set:

  Coverage tier  — companies in the same GICS subindustry that are also
                   in the user's coverage universe (useUniverseStore tickers).
                   These get full individual summaries in the narrative.

  Benchmark tier — all other companies in the same GICS subindustry.
                   These appear in the data table but are only called out
                   in the narrative when they are outliers.

Replacing or skipping this step: remove the call in runner.py.
No other file is affected.
"""

from typing import List, Set

from backend.app.interfaces.db_repository import DBRepository
from backend.app.models.domain.insight_models import (
    InsightTemplate,
    PeerWithTier,
    PeerTier,
)
from backend.app.models.domain.universe import PublicCompany


def resolve_peers(
    entities: List[str],
    tenant_id: str,
    template: InsightTemplate,
    db_repo: DBRepository,
) -> List[PeerWithTier]:
    """
    Phase 2: For each primary entity, find all GICS-subindustry peers and
    tag them as 'coverage' or 'benchmark'.

    Returns a deduplicated, ordered list:
      [coverage peers first, then benchmark peers up to max_benchmark_peers]
    """
    if not entities:
        return []

    # Collect the user's coverage universe tickers for this tenant.
    try:
        user_universe = db_repo.get_user_universe(tenant_id)
        coverage_tickers: Set[str] = {c.ticker.upper() for c in user_universe if c.is_active}
    except Exception as e:
        print(f"[resolve_peers] Could not load user universe: {e}. Treating all peers as benchmark.")
        coverage_tickers = set()

    # Add the primary entities to the coverage set so they are always
    # shown with full summaries regardless of explicit universe membership.
    for e in entities:
        coverage_tickers.add(e.upper())

    all_peers: List[PeerWithTier] = []
    seen_tickers: Set[str] = set()

    for ticker in entities:
        ticker = ticker.upper()

        # Look up the primary entity's GICS subindustry.
        try:
            primary: PublicCompany | None = db_repo.get_public_company(ticker)
        except AttributeError:
            # get_public_company may not exist on all DB implementations — fall back.
            primary = None
            companies = db_repo.get_public_companies()
            for c in companies:
                if c.ticker.upper() == ticker:
                    primary = c
                    break

        if not primary or not primary.gics_subindustry:
            print(f"[resolve_peers] No GICS subindustry found for {ticker}. Skipping peer resolution.")
            continue

        subindustry = primary.gics_subindustry

        # Fetch all companies in the same subindustry.
        try:
            subindustry_companies: List[PublicCompany] = db_repo.get_public_companies(
                sector=primary.gics_sector
            )
        except Exception as e:
            print(f"[resolve_peers] Could not fetch subindustry peers: {e}.")
            subindustry_companies = []

        # Filter to same subindustry.
        subindustry_companies = [
            c for c in subindustry_companies
            if c.gics_subindustry == subindustry
        ]

        # Tag and deduplicate.
        coverage_in_subindustry: List[PeerWithTier] = []
        benchmark_in_subindustry: List[PeerWithTier] = []

        for company in subindustry_companies:
            t = company.ticker.upper()
            if t in seen_tickers:
                continue
            seen_tickers.add(t)

            tier = PeerTier.COVERAGE if t in coverage_tickers else PeerTier.BENCHMARK
            peer = PeerWithTier(ticker=t, name=company.name, tier=tier)

            if tier == PeerTier.COVERAGE:
                coverage_in_subindustry.append(peer)
            else:
                benchmark_in_subindustry.append(peer)

        # Enforce benchmark cap.
        benchmark_in_subindustry = benchmark_in_subindustry[: template.max_benchmark_peers]

        all_peers.extend(coverage_in_subindustry)
        all_peers.extend(benchmark_in_subindustry)

    return all_peers
