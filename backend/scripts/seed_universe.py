"""
Seed loader for the Phase 2 universe v2 schema.

Reads:
  - backend/data/universe/broad_universe_seed_v1.csv         (curated thesis groups)
  - backend/data/universe/broad_universe_seed_v1_addendum_*.csv (any addendum CSVs in the same dir)
  - backend/data/universe/pre_ipo_watchlist_v1.json           (pre-IPO + recently-listed audit)

Writes:
  - company                — slug-keyed analytical entities
  - listing                — tradeable instruments (one company → many listings)
  - universe_group         — thesis groups (display_name + layer derived from group_id)
  - universe_group_member  — many-to-many (ticker × group) with weight
  - pre_ipo_watch          — private companies, metadata-only

Idempotent: reruns UPSERT; safe to invoke multiple times.

Run:
    cd backend
    POSTGRES_URI="postgresql+psycopg2://alphagraph:alphagraph_dev@localhost:5432/alphagraph" \\
        python -m backend.scripts.seed_universe

Or rely on .env from project root:
    cd <repo root>
    python -m backend.scripts.seed_universe
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# Ensure .env loads when this script runs from a non-FastAPI context.
try:
    from dotenv import load_dotenv
    _ENV = Path(__file__).resolve().parents[2] / ".env"
    if _ENV.exists():
        load_dotenv(_ENV, override=False)
except ImportError:
    pass

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.db.phase2_session import Phase2SessionLocal
from backend.app.models.orm.universe_v2_orm import (
    Company, Listing, UniverseGroup, UniverseGroupMember, PreIPOWatch,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Seed files live UNDER scripts/, not under backend/data/. Why:
# Render Disks mount over the existing backend/data/ contents at runtime,
# masking anything baked into the image at that path. Keeping seeds at
# backend/scripts/seed_data/universe/ — outside the mount — means the
# image's bundled seeds are always visible to the loader.
# Same pattern works on AWS EFS / Fargate volume / any disk-mounted host.
UNIVERSE_DIR = Path(__file__).resolve().parent / "seed_data" / "universe"
SEED_CSV     = UNIVERSE_DIR / "broad_universe_seed_v1.csv"
PRE_IPO_JSON = UNIVERSE_DIR / "pre_ipo_watchlist_v1.json"

# Group display names + layer + sort_order. Anything not in here gets
# auto-titled from the group_id with the layer guessed from the prefix.
GROUP_METADATA: dict[str, dict[str, Any]] = {
    # Compute layer
    "ai_compute_design":          {"display": "AI Compute — Designers (GPU/ASIC)",   "layer": "compute",   "sort": 100},
    "ai_compute_foundry":         {"display": "AI Compute — Foundries",              "layer": "compute",   "sort": 110},
    "ai_compute_hbm_memory":      {"display": "AI Compute — HBM & Memory",           "layer": "compute",   "sort": 120},
    "ai_compute_packaging":       {"display": "AI Compute — Packaging (OSAT)",       "layer": "compute",   "sort": 130},
    "ai_compute_semi_cap_eq":     {"display": "AI Compute — Semi-cap Equipment",     "layer": "compute",   "sort": 140},
    "ai_compute_eda_ip":          {"display": "AI Compute — EDA & IP",               "layer": "compute",   "sort": 150},
    # Infra layer
    "ai_infra_networking":        {"display": "AI Infra — Networking",               "layer": "infra",     "sort": 200},
    "ai_infra_optical":           {"display": "AI Infra — Optical",                  "layer": "infra",     "sort": 210},
    "ai_infra_servers_oem":       {"display": "AI Infra — Servers & ODMs",           "layer": "infra",     "sort": 220},
    # Hosting layer
    "ai_hosting_hyperscalers":    {"display": "AI Hosting — Hyperscalers",           "layer": "hosting",   "sort": 300},
    "ai_hosting_neoclouds":       {"display": "AI Hosting — Neoclouds",              "layer": "hosting",   "sort": 310},
    "ai_hosting_dc_reits":        {"display": "AI Hosting — DC REITs",               "layer": "hosting",   "sort": 320},
    # Energy layer
    "ai_energy_utilities":        {"display": "AI Energy — Utilities",               "layer": "energy",    "sort": 400},
    "ai_energy_nuclear_smr":      {"display": "AI Energy — Nuclear / SMR",           "layer": "energy",    "sort": 410},
    "ai_energy_grid_electric":    {"display": "AI Energy — Grid & Electrical",       "layer": "energy",    "sort": 420},
    "ai_energy_cooling_hvac":     {"display": "AI Energy — Cooling & HVAC",          "layer": "energy",    "sort": 430},
    # Software layer
    "ai_software_models":         {"display": "AI Software — Models",                "layer": "software",  "sort": 500},
    "ai_software_apps":           {"display": "AI Software — Apps",                  "layer": "software",  "sort": 510},
    # China layer (cross-cutting)
    "cn_ai_internet":             {"display": "China — Internet & Models",           "layer": "china",     "sort": 600},
    "cn_ai_consumer":             {"display": "China — Consumer & Auto",             "layer": "china",     "sort": 610},
    "cn_ai_semi":                 {"display": "China — Semis (cross-cutting tag)",   "layer": "china",     "sort": 620},
    # Japan layer
    "jp_ai_robotics":             {"display": "Japan — Robotics",                    "layer": "japan",     "sort": 700},
    "jp_ai_components":           {"display": "Japan — Components",                  "layer": "japan",     "sort": 710},
    "jp_consumer":                {"display": "Japan — Consumer",                    "layer": "japan",     "sort": 720},
    # Industrial
    "industrial_dc_construction": {"display": "Industrial — DC Construction",        "layer": "industrial","sort": 800},
    "industrial_aerospace_def":   {"display": "Industrial — Aerospace & Defense",    "layer": "industrial","sort": 810},
    "industrial_capgoods":        {"display": "Industrial — Capital Goods",          "layer": "industrial","sort": 820},
    # Materials
    "ai_materials_semi":          {"display": "Materials — Semi (fab inputs)",       "layer": "materials", "sort": 900},
    "ai_materials_critical_minerals": {"display": "Materials — Critical Minerals",   "layer": "materials", "sort": 910},
    "ai_materials_dc_build":      {"display": "Materials — DC Build (steel/glass)",  "layer": "materials", "sort": 920},
    # Emerging IPOs
    "ai_emerging_companies":      {"display": "Emerging — Recent IPOs & Pre-IPOs",   "layer": "emerging",  "sort": 1000},
    # Auto-fetched index baselines
    "index_spx":                  {"display": "Index — S&P 500 (auto)",              "layer": "index",     "sort": 2000, "is_index": True},
    "index_smh":                  {"display": "Index — SMH Semiconductor (auto)",    "layer": "index",     "sort": 2010, "is_index": True},
    "index_twse":                 {"display": "Index — TWSE 50 (auto)",              "layer": "index",     "sort": 2020, "is_index": True},
    "index_nikkei225":            {"display": "Index — Nikkei 225 (auto)",           "layer": "index",     "sort": 2030, "is_index": True},
    "index_kospi200":             {"display": "Index — KOSPI 200 (auto)",            "layer": "index",     "sort": 2040, "is_index": True},
}

# Dual-listings: (ticker, ticker, ...) → canonical company_id.
# These pairs/groups share fundamentals; they're different listings of
# the SAME company. Slug heuristic from company_name would assign
# different slugs (e.g. "Alibaba Group H" vs "Alibaba Group ADR"),
# so we override here.
DUAL_LISTING_OVERRIDES: dict[tuple[str, ...], str] = {
    ("TSM", "2330.TW"):                       "tsmc",
    ("UMC", "2303.TW"):                       "umc",
    ("ASX", "3711.TW"):                       "ase",
    ("HIMX", "3257.TW"):                      "himax",
    ("BABA", "9988.HK"):                      "alibaba",
    ("JD", "9618.HK"):                        "jd_com",
    ("NTES", "9999.HK"):                      "netease",
    ("BIDU", "9888.HK"):                      "baidu",
    ("BILI", "9626.HK"):                      "bilibili",
    ("TCOM", "9961.HK"):                      "trip_com",
    ("TME", "1698.HK"):                       "tencent_music",
    ("LI", "2015.HK"):                        "li_auto",
    ("NIO", "9866.HK"):                       "nio",
    ("XPEV", "9868.HK"):                      "xpeng",
    ("YUMC", "9987.HK"):                      "yum_china",
    ("GDS", "9698.HK"):                       "gds_holdings",
    ("ABB", "ABBN.SW"):                       "abb",
    ("ASML", "ASML.AS"):                      "asml",
    ("ACMR", "688082.SS"):                    "acm_research",
    ("0700.HK",):                             "tencent",
    ("3690.HK",):                             "meituan",
    ("1024.HK",):                             "kuaishou",
    ("1810.HK",):                             "xiaomi",
    ("PDD",):                                 "pinduoduo",
    # China A/H share pairs
    ("1211.HK", "002594.SZ"):                 "byd",
    ("0285.HK",):                             "byd_electronics",
    ("0175.HK",):                             "geely_auto",
    ("2333.HK",):                             "great_wall_motor",
    ("6618.HK",):                             "jd_health",
    ("0241.HK",):                             "alibaba_health",
    ("2382.HK",):                             "sunny_optical",
    ("0992.HK",):                             "lenovo",
    ("600011.SS", "0902.HK"):                 "huaneng_power",
    ("601727.SS", "2727.HK"):                 "shanghai_electric",
    ("600690.SS", "6690.HK"):                 "haier",
    ("600362.SS", "0358.HK"):                 "jiangxi_copper",
    ("601899.SS", "2899.HK"):                 "zijin_mining",
    ("002460.SZ", "1772.HK"):                 "ganfeng_lithium",
    ("002466.SZ", "9696.HK"):                 "tianqi_lithium",
    ("0763.HK", "000063.SZ"):                 "zte",
    ("0981.HK", "688981.SS"):                 "smic",
    ("1347.HK", "688347.SS"):                 "hua_hong",
    ("003816.SZ", "1816.HK"):                 "cgn_power",
    # Recently listed Chinese AI IPOs
    ("02513.HK",):                            "zhipu_ai",
    ("00100.HK",):                            "minimax",
    ("06082.HK",):                            "biren",
    ("9903.HK",):                             "iluvatar_corex",
    ("1879.HK",):                             "lightelligence",
    ("0600.HK",):                             "axera",
    ("688795.SS",):                           "moore_threads",
}

# Tickers we drop from the seed because they're placeholders, not real
# tradeable instruments.
TICKER_BLOCKLIST = {
    "__INDEX__SPX__", "__INDEX__SMH__", "__INDEX__TWSE__",
    "__INDEX__NIKKEI225__", "__INDEX__KOSPI200__",
    "__METAX__", "__METAX_VERIFY__",
}

# Exchange → currency fallback when the row has 'N/A'.
EXCHANGE_DEFAULTS = {
    "NYSE": "USD", "NASDAQ": "USD", "TWSE": "TWD", "TPEx": "TWD",
    "HKEX": "HKD", "JPX": "JPY", "KOSPI": "KRW", "KOSDAQ": "KRW",
    "SSE": "CNY", "SZSE": "CNY", "Euronext": "EUR", "Xetra": "EUR",
    "LSE": "GBP", "SIX": "CHF", "ASX": "AUD", "TSX": "CAD",
}

# Country derivation by ticker suffix or known prefix.
def _hq_country_from_ticker(ticker: str, exchange: str) -> str | None:
    if ticker.endswith(".TW") or ticker.endswith(".TWO"): return "TW"
    if ticker.endswith(".HK"):                            return "HK"
    if ticker.endswith(".T"):                             return "JP"
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):  return "KR"
    if ticker.endswith(".SS") or ticker.endswith(".SZ"):  return "CN"
    if ticker.endswith(".AS"):                            return "NL"
    if ticker.endswith(".PA"):                            return "FR"
    if ticker.endswith(".DE"):                            return "DE"
    if ticker.endswith(".SW"):                            return "CH"
    if ticker.endswith(".L"):                             return "GB"
    if ticker.endswith(".AX"):                            return "AU"
    if ticker.endswith(".TO"):                            return "CA"
    if ticker.endswith(".IL"):                            return "GB"  # London GDR
    if exchange in {"NYSE", "NASDAQ"}:                    return "US"
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")

def _slugify_company(name: str) -> str:
    """Lowercased, hyphenated, alnum-only, capped at 60 chars."""
    s = _SLUG_RE.sub("_", name.strip().lower()).strip("_")
    return s[:60] or "unknown"


def _company_id_for(ticker: str, name: str, dual_lookup: dict[str, str]) -> str:
    """Resolve the canonical company_id for a ticker. Dual-listing
    overrides win first; otherwise slugify the name."""
    if ticker in dual_lookup:
        return dual_lookup[ticker]
    return _slugify_company(name)


def _build_dual_lookup() -> dict[str, str]:
    """Flatten DUAL_LISTING_OVERRIDES into ticker→company_id dict."""
    out = {}
    for tickers, cid in DUAL_LISTING_OVERRIDES.items():
        for t in tickers:
            out[t] = cid
    return out


def _parse_csv(path: Path) -> Iterable[dict[str, str]]:
    """Yield dict rows from a seed CSV, skipping comment / placeholder rows."""
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("ticker"):
                continue
            t = row["ticker"]
            if t.startswith("#") or t.startswith("__INDEX__") or t in TICKER_BLOCKLIST:
                continue
            yield row


def _resolve_currency(row: dict[str, str]) -> str:
    cur = (row.get("currency") or "").strip()
    if cur and cur != "N/A":
        return cur
    return EXCHANGE_DEFAULTS.get(row.get("exchange", ""), "USD")


def _group_metadata(group_id: str) -> dict[str, Any]:
    """Look up display_name/layer/sort/is_index for a group_id, with
    sensible fallbacks for unknown ones (so a typo doesn't break load)."""
    md = GROUP_METADATA.get(group_id)
    if md:
        return {
            "display_name": md["display"],
            "layer":        md.get("layer", "other"),
            "sort_order":   md.get("sort", 999),
            "is_index":     md.get("is_index", False),
        }
    # Unknown group: derive a layer from the prefix.
    layer = "other"
    if group_id.startswith("ai_compute"):    layer = "compute"
    elif group_id.startswith("ai_infra"):    layer = "infra"
    elif group_id.startswith("ai_hosting"):  layer = "hosting"
    elif group_id.startswith("ai_energy"):   layer = "energy"
    elif group_id.startswith("ai_software"): layer = "software"
    elif group_id.startswith("ai_materials"):layer = "materials"
    elif group_id.startswith("cn_"):         layer = "china"
    elif group_id.startswith("jp_"):         layer = "japan"
    elif group_id.startswith("industrial_"): layer = "industrial"
    elif group_id.startswith("index_"):      layer = "index"
    return {
        "display_name": group_id.replace("_", " ").title(),
        "layer":        layer,
        "sort_order":   999,
        "is_index":     layer == "index",
    }


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def upsert_groups(db, group_ids: set[str]) -> None:
    for gid in sorted(group_ids):
        md = _group_metadata(gid)
        stmt = pg_insert(UniverseGroup).values(
            group_id=gid, **md,
        ).on_conflict_do_update(
            index_elements=["group_id"],
            set_={
                "display_name": md["display_name"],
                "layer":        md["layer"],
                "sort_order":   md["sort_order"],
                "is_index":     md["is_index"],
            },
        )
        db.execute(stmt)
    db.commit()


def upsert_company_and_listing(
    db,
    company_id: str,
    company_name: str,
    ticker: str,
    exchange: str,
    currency: str,
    status: str,
    hq_country: str | None,
    is_primary_listing: bool,
) -> None:
    # Company UPSERT (display_name updated only on insert; later listings
    # of the same company may have variant names like "BABA" vs
    # "Alibaba Group H" — stick with first-seen).
    stmt_c = pg_insert(Company).values(
        company_id=company_id,
        display_name=company_name,
        hq_country=hq_country,
    ).on_conflict_do_update(
        index_elements=["company_id"],
        set_={"updated_at": __import__("sqlalchemy").text("CURRENT_TIMESTAMP")},
    )
    db.execute(stmt_c)

    stmt_l = pg_insert(Listing).values(
        ticker=ticker,
        company_id=company_id,
        exchange=exchange,
        currency=currency,
        status=status,
        is_primary=is_primary_listing,
    ).on_conflict_do_update(
        index_elements=["ticker"],
        set_={
            "company_id": company_id,
            "exchange":   exchange,
            "currency":   currency,
            "status":     status,
            "updated_at": __import__("sqlalchemy").text("CURRENT_TIMESTAMP"),
        },
    )
    db.execute(stmt_l)


def upsert_member(
    db,
    group_id: str,
    ticker: str,
    is_primary: bool,
    weight: float,
    notes: str | None,
) -> None:
    stmt = pg_insert(UniverseGroupMember).values(
        group_id=group_id,
        ticker=ticker,
        is_primary=is_primary,
        weight=weight,
        notes=notes,
    ).on_conflict_do_update(
        index_elements=["group_id", "ticker"],
        set_={
            "is_primary": is_primary,
            "weight":     weight,
            "notes":      notes,
        },
    )
    db.execute(stmt)


def upsert_pre_ipo(db, entry: dict[str, Any]) -> None:
    payload = {
        "id":                 entry["id"],
        "display_name":       entry.get("display_name") or entry["id"],
        "country":            entry.get("country"),
        "category":           entry.get("category"),
        "summary":            entry.get("summary"),
        "filings_status":     entry.get("filings_status"),
        "expected_listing":   entry.get("expected_listing"),
        "expected_exchange":  entry.get("expected_exchange"),
        "last_round_payload": entry.get("last_round"),
        "tags":               entry.get("tags") or [],
        "groups":             entry.get("groups") or [],
        "post_ipo_ticker":    entry.get("post_ipo_ticker"),
    }
    stmt = pg_insert(PreIPOWatch).values(**payload).on_conflict_do_update(
        index_elements=["id"],
        set_={k: v for k, v in payload.items() if k != "id"},
    )
    db.execute(stmt)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not SEED_CSV.exists():
        print(f"[ERROR] seed CSV not found: {SEED_CSV}", file=sys.stderr)
        return 2

    # Discover any addendum CSVs alongside the main seed.
    addendum_paths = sorted(UNIVERSE_DIR.glob("broad_universe_seed_v1_addendum_*.csv"))

    dual_lookup = _build_dual_lookup()

    # Pass 1 — collect tickers, groups, multi-row metadata.
    seen_tickers:        dict[str, dict[str, Any]] = {}  # ticker → row meta (first occurrence wins for company-name)
    memberships:         list[tuple[str, str, bool, float, str | None]] = []
    group_ids:           set[str] = set()
    primary_per_ticker:  dict[str, str] = {}  # ticker → group_id of first is_primary=true row
    ticker_first_group:  dict[str, str] = {}  # for unflagged tickers, fall back to first-seen group

    sources = [SEED_CSV] + addendum_paths
    for src in sources:
        for row in _parse_csv(src):
            ticker     = row["ticker"]
            group_id   = row["group_id"]
            is_primary = row.get("is_primary", "false").strip().lower() == "true"
            weight     = float(row.get("weight") or 1.0)
            company    = row.get("company_name", ticker).strip()
            exchange   = row.get("exchange", "").strip()
            currency   = _resolve_currency(row)
            status     = (row.get("status") or "active").strip()
            notes      = (row.get("notes") or "").strip() or None

            group_ids.add(group_id)

            if ticker not in seen_tickers:
                seen_tickers[ticker] = {
                    "company_name": company,
                    "exchange":     exchange,
                    "currency":     currency,
                    "status":       status,
                    "hq_country":   _hq_country_from_ticker(ticker, exchange),
                }
                ticker_first_group[ticker] = group_id
            elif company and not seen_tickers[ticker]["company_name"]:
                seen_tickers[ticker]["company_name"] = company

            if is_primary and ticker not in primary_per_ticker:
                primary_per_ticker[ticker] = group_id

            memberships.append((group_id, ticker, is_primary, weight, notes))

    # For tickers without an explicit is_primary row, default to first
    # group encountered. Partial unique index allows one is_primary per ticker.
    for ticker in seen_tickers:
        primary_per_ticker.setdefault(ticker, ticker_first_group[ticker])

    # Pass 2 — write to DB.
    db = Phase2SessionLocal()
    try:
        # 2a. Groups (must exist before members FK).
        upsert_groups(db, group_ids)
        print(f"[ok] universe_group rows: {len(group_ids)}")

        # 2b. Companies + listings. Determine primary listing per company.
        # Heuristic: the LISTING with primary status (US ADR > local share
        # > others) wins. Simpler v1 rule: first ticker seen for that
        # company is primary.
        primary_listing_per_company: dict[str, str] = {}
        company_inserts = 0
        listing_inserts = 0
        for ticker, meta in seen_tickers.items():
            company_id = _company_id_for(ticker, meta["company_name"], dual_lookup)
            is_primary_listing = primary_listing_per_company.setdefault(company_id, ticker) == ticker
            upsert_company_and_listing(
                db,
                company_id=company_id,
                company_name=meta["company_name"] or ticker,
                ticker=ticker,
                exchange=meta["exchange"] or "NASDAQ",
                currency=meta["currency"],
                status=meta["status"],
                hq_country=meta["hq_country"],
                is_primary_listing=is_primary_listing,
            )
            company_inserts += 1   # accounts for upsert; not strictly inserts
            listing_inserts += 1
        db.commit()
        print(f"[ok] listings UPSERTed: {len(seen_tickers)}; "
              f"unique companies: {len(set(_company_id_for(t, m['company_name'], dual_lookup) for t, m in seen_tickers.items()))}")

        # 2c. Memberships. is_primary is decided centrally in
        # primary_per_ticker (explicit flags from CSV first, falling back
        # to first-seen group). For each membership row, set is_primary=true
        # ONLY if this row's group matches that canonical pick — guarantees
        # the partial unique index `uq_ugm_primary_per_ticker` holds.
        for group_id, ticker, _is_primary_flag, weight, notes in memberships:
            effective_primary = (primary_per_ticker.get(ticker) == group_id)
            upsert_member(db, group_id, ticker, effective_primary, weight, notes)
        db.commit()
        print(f"[ok] universe_group_member rows: {len(memberships)}")

        # 2d. Pre-IPO watch.
        if PRE_IPO_JSON.exists():
            data = json.loads(PRE_IPO_JSON.read_text(encoding="utf-8"))
            entries = data.get("watchlist", [])
            for entry in entries:
                upsert_pre_ipo(db, entry)
            db.commit()
            print(f"[ok] pre_ipo_watch rows: {len(entries)}")

        # 3. Validation summary.
        print("\n--- Validation ---")
        total_companies   = db.query(Company).count()
        total_listings    = db.query(Listing).count()
        total_groups      = db.query(UniverseGroup).count()
        total_members     = db.query(UniverseGroupMember).count()
        total_preipo      = db.query(PreIPOWatch).count()
        primary_listings  = db.query(Listing).filter(Listing.is_primary.is_(True)).count()
        primary_members   = db.query(UniverseGroupMember).filter(
            UniverseGroupMember.is_primary.is_(True)).count()
        print(f"  companies:               {total_companies}")
        print(f"  listings:                {total_listings} ({primary_listings} primary)")
        print(f"  universe_groups:         {total_groups}")
        print(f"  universe_group_members:  {total_members} ({primary_members} primary)")
        print(f"  pre_ipo_watch:           {total_preipo}")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
