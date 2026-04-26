"""
One-shot seeder for backend/data/config/platform_universe.csv.

Reads every existing data store on disk and stamps the right `has_*` flags
+ first_imported_at + last_updated_at. Idempotent: safe to re-run; tickers
already in the registry just get their data-coverage flags refreshed
(they don't get re-added).

Sources scanned:

  has_topline           : backend/data/filing_data/backbone/ticker={T}.parquet
  has_filings_raw       : backend/data/filing_data/filings/ticker={T}/*.parquet
  has_earnings_releases : backend/data/earnings_releases/ticker={T}.parquet
  has_monthly_revenue   : backend/data/taiwan/monthly_revenue/data.parquet
  has_x_posts           : backend/data/social/x/data.parquet
  has_news              : backend/data/market_data/news/google_news.parquet
                           (string-match on ticker -- imprecise but fine for a
                            "have we seen this name pop up?" flag)

Also seeds the Taiwan watchlist and the existing US semi universe with
sector / sub-sector / custom-sector metadata so the registry isn't just a
flag dump.

Run:
    python -m backend.scripts.seed_platform_universe
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.services.universe_registry import (
    add_ticker,
    mark_data_coverage,
    read_universe,
)

# Skill / CLAUDE.md rule: no Unicode in print statements anywhere in scripts/services.
# All output below uses ASCII only (no arrows, em-dashes, etc.).

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Data lives under backend/data/, not <project_root>/data/.
DATA = PROJECT_ROOT / "backend" / "data"

# ---------------------------------------------------------------------------
# Static metadata for known tickers. Used to seed name / sector / sub-sector
# columns. Keys missing here still get a row (added during scanning) but
# with empty metadata -- the user can fill them in.
# ---------------------------------------------------------------------------

US_METADATA: dict[str, dict[str, str]] = {
    # Existing 15 with EDGAR data
    "NVDA":  {"name": "NVIDIA Corporation",       "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Compute/AI"},
    "AVGO":  {"name": "Broadcom Inc.",            "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Compute/AI"},
    "INTC":  {"name": "Intel Corporation",        "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Compute/AI"},
    "MU":    {"name": "Micron Technology",        "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Memory"},
    "QCOM":  {"name": "QUALCOMM Inc.",            "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "RF/Connectivity"},
    "MRVL":  {"name": "Marvell Technology",       "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Compute/AI"},
    "AMAT":  {"name": "Applied Materials",        "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment (Wafer Fab)"},
    "LRCX":  {"name": "Lam Research",              "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment (Wafer Fab)"},
    "KLAC":  {"name": "KLA Corporation",           "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment (Metrology)"},
    "TER":   {"name": "Teradyne",                  "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment (ATE)"},
    "CDNS":  {"name": "Cadence Design Systems",    "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Software",                  "custom_sector": "Semi",        "custom_subsector": "EDA"},
    "SNPS":  {"name": "Synopsys",                  "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Software",                  "custom_sector": "Semi",        "custom_subsector": "EDA"},
    "AAPL":  {"name": "Apple Inc.",                "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Hardware",    "custom_subsector": "Consumer Electronics"},
    "DELL":  {"name": "Dell Technologies",         "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Hardware",    "custom_subsector": "Servers"},
    "LITE":  {"name": "Lumentum Holdings",         "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Semi",        "custom_subsector": "Optical"},
    # New semi additions
    "AMD":   {"name": "Advanced Micro Devices",    "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Compute/AI"},
    "ALAB":  {"name": "Astera Labs",               "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Compute/AI"},
    "AMKR":  {"name": "Amkor Technology",          "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "OSAT"},
    "SNDK":  {"name": "Sandisk Corporation",       "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Semi",        "custom_subsector": "Memory/Storage"},
    "WDC":   {"name": "Western Digital",           "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Hardware",    "custom_subsector": "Storage"},
    "STX":   {"name": "Seagate Technology",        "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Hardware",    "custom_subsector": "Storage"},
    "SWKS":  {"name": "Skyworks Solutions",        "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "RF/Connectivity"},
    "QRVO":  {"name": "Qorvo",                     "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "RF/Connectivity"},
    "CRUS":  {"name": "Cirrus Logic",              "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "RF/Connectivity"},
    "SLAB":  {"name": "Silicon Laboratories",      "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "RF/Connectivity"},
    "ADI":   {"name": "Analog Devices",            "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Analog/Power"},
    "TXN":   {"name": "Texas Instruments",         "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Analog/Power"},
    "MCHP":  {"name": "Microchip Technology",      "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Analog/Power"},
    "MPWR":  {"name": "Monolithic Power Systems",  "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Analog/Power"},
    "ON":    {"name": "ON Semiconductor",          "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Analog/Power"},
    "POWI":  {"name": "Power Integrations",        "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Analog/Power"},
    "VICR":  {"name": "Vicor Corporation",         "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "Analog/Power"},
    "COHR":  {"name": "Coherent Corp.",            "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Semi",        "custom_subsector": "Optical"},
    "FN":    {"name": "Fabrinet",                  "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Optical"},
    "IPGP":  {"name": "IPG Photonics",             "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Semi",        "custom_subsector": "Optical"},
    "OLED":  {"name": "Universal Display",         "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Semi",        "custom_subsector": "Optical/Display"},
    "ENTG":  {"name": "Entegris",                  "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment/Materials"},
    "MKSI":  {"name": "MKS Instruments",           "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment/Materials"},
    "FORM":  {"name": "FormFactor",                "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment/Materials"},
    "ICHR":  {"name": "Ichor Holdings",            "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment/Materials"},
    "NVMI":  {"name": "Nova Ltd.",                 "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment/Materials"},
    "ONTO":  {"name": "Onto Innovation",           "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Semiconductor Equipment",   "custom_sector": "Semi",        "custom_subsector": "Equipment/Materials"},
    "RMBS":  {"name": "Rambus",                    "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Semiconductors",            "custom_sector": "Semi",        "custom_subsector": "EDA/IP"},
    "ANSS":  {"name": "ANSYS",                     "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Software",                  "custom_sector": "Semi",        "custom_subsector": "EDA/IP"},
    "KEYS":  {"name": "Keysight Technologies",     "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Semi",        "custom_subsector": "Test/Measurement"},
    "TDY":   {"name": "Teledyne Technologies",     "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Tech Hardware",             "custom_sector": "Semi",        "custom_subsector": "Test/Measurement"},
    "FTV":   {"name": "Fortive",                   "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Tech Hardware",             "custom_sector": "Semi",        "custom_subsector": "Test/Measurement"},
    "APD":   {"name": "Air Products and Chemicals","exchange": "NYSE",   "gics_sector": "Materials",              "gics_subsector": "Industrial Gases",          "custom_sector": "Semi",        "custom_subsector": "Industrial Gases"},
    "LIN":   {"name": "Linde plc",                 "exchange": "NYSE",   "gics_sector": "Materials",              "gics_subsector": "Industrial Gases",          "custom_sector": "Semi",        "custom_subsector": "Industrial Gases"},
    "TEL":   {"name": "TE Connectivity",           "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Semi",        "custom_subsector": "Connectors"},
    "APH":   {"name": "Amphenol",                  "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Semi",        "custom_subsector": "Connectors"},
    "GLW":   {"name": "Corning Inc.",              "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Semi",        "custom_subsector": "Materials"},
    "AAOI":  {"name": "Applied Optoelectronics",   "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Semi",        "custom_subsector": "Optical Networking"},
    "CIEN":  {"name": "Ciena",                     "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Hardware",    "custom_subsector": "Optical Networking"},
    "COMM":  {"name": "CommScope Holding",         "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Hardware",    "custom_subsector": "Optical Networking"},
    # Hardware / Servers / Networking
    "HPE":   {"name": "Hewlett Packard Enterprise","exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Hardware",    "custom_subsector": "Servers"},
    "SMCI":  {"name": "Super Micro Computer",      "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Hardware",    "custom_subsector": "Servers"},
    "IBM":   {"name": "IBM",                       "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "IT Services",               "custom_sector": "Hardware",    "custom_subsector": "Enterprise"},
    "NTAP":  {"name": "NetApp",                    "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Hardware",    "custom_subsector": "Storage Systems"},
    "PSTG":  {"name": "Pure Storage",              "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Tech Hardware",             "custom_sector": "Hardware",    "custom_subsector": "Storage Systems"},
    "CSCO":  {"name": "Cisco Systems",             "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Hardware",    "custom_subsector": "Networking"},
    "ANET":  {"name": "Arista Networks",           "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Hardware",    "custom_subsector": "Networking"},
    "JNPR":  {"name": "Juniper Networks",          "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Hardware",    "custom_subsector": "Networking"},
    "FFIV":  {"name": "F5",                        "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Hardware",    "custom_subsector": "Networking"},
    "EXTR":  {"name": "Extreme Networks",          "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Communications Equipment",  "custom_sector": "Hardware",    "custom_subsector": "Networking"},
    # Hyperscalers / Cloud
    "MSFT":  {"name": "Microsoft",                 "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Software",                  "custom_sector": "Hyperscaler", "custom_subsector": "Cloud (Azure)"},
    "AMZN":  {"name": "Amazon.com",                "exchange": "NASDAQ", "gics_sector": "Consumer Discretionary", "gics_subsector": "Internet Retail",           "custom_sector": "Hyperscaler", "custom_subsector": "Cloud (AWS)"},
    "ORCL":  {"name": "Oracle Corporation",        "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Software",                  "custom_sector": "Hyperscaler", "custom_subsector": "Cloud (OCI)"},
    "GOOGL": {"name": "Alphabet (Class A)",        "exchange": "NASDAQ", "gics_sector": "Communication Services", "gics_subsector": "Interactive Media",         "custom_sector": "Hyperscaler", "custom_subsector": "Cloud (GCP)"},
    "META":  {"name": "Meta Platforms",            "exchange": "NASDAQ", "gics_sector": "Communication Services", "gics_subsector": "Interactive Media",         "custom_sector": "Hyperscaler", "custom_subsector": "AI Infra (Llama)"},
    "PLTR":  {"name": "Palantir Technologies",     "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "Software",                  "custom_sector": "Software",    "custom_subsector": "AI"},
    "SNOW":  {"name": "Snowflake",                 "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Software",                  "custom_sector": "Software",    "custom_subsector": "Database/Cloud"},
    "CRM":   {"name": "Salesforce",                "exchange": "NYSE",   "gics_sector": "Information Technology", "gics_subsector": "Software",                  "custom_sector": "Software",    "custom_subsector": "SaaS"},
    # Neoclouds / GPU rental
    "WULF":  {"name": "TeraWulf",                  "exchange": "NASDAQ", "gics_sector": "Financials",             "gics_subsector": "Capital Markets",           "custom_sector": "Neocloud",    "custom_subsector": "GPU Rental"},
    "IREN":  {"name": "Iris Energy",               "exchange": "NASDAQ", "gics_sector": "Financials",             "gics_subsector": "Capital Markets",           "custom_sector": "Neocloud",    "custom_subsector": "GPU Rental"},
    "CIFR":  {"name": "Cipher Mining",             "exchange": "NASDAQ", "gics_sector": "Financials",             "gics_subsector": "Capital Markets",           "custom_sector": "Neocloud",    "custom_subsector": "GPU Rental"},
    "HUT":   {"name": "Hut 8",                     "exchange": "NASDAQ", "gics_sector": "Financials",             "gics_subsector": "Capital Markets",           "custom_sector": "Neocloud",    "custom_subsector": "GPU Rental"},
    "APLD":  {"name": "Applied Digital",           "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "IT Services",               "custom_sector": "Neocloud",    "custom_subsector": "AI Datacenter"},
    "CORZ":  {"name": "Core Scientific",           "exchange": "NASDAQ", "gics_sector": "Financials",             "gics_subsector": "Capital Markets",           "custom_sector": "Neocloud",    "custom_subsector": "GPU Rental"},
    "BTBT":  {"name": "Bit Digital",               "exchange": "NASDAQ", "gics_sector": "Financials",             "gics_subsector": "Capital Markets",           "custom_sector": "Neocloud",    "custom_subsector": "GPU Rental"},
    "CRWV":  {"name": "CoreWeave",                 "exchange": "NASDAQ", "gics_sector": "Information Technology", "gics_subsector": "IT Services",               "custom_sector": "Neocloud",    "custom_subsector": "GPU Cloud"},
    # Power infra / cooling
    "VRT":   {"name": "Vertiv Holdings",           "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Electrical Equipment",      "custom_sector": "Power",       "custom_subsector": "Datacenter Power/Cooling"},
    "ETN":   {"name": "Eaton Corporation",         "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Electrical Equipment",      "custom_sector": "Power",       "custom_subsector": "Power Management"},
    "HUBB":  {"name": "Hubbell Incorporated",      "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Electrical Equipment",      "custom_sector": "Power",       "custom_subsector": "Electrical"},
    "PWR":   {"name": "Quanta Services",           "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Construction & Engineering","custom_sector": "Power",       "custom_subsector": "Utility Infrastructure"},
    "JCI":   {"name": "Johnson Controls",          "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Building Products",         "custom_sector": "Power",       "custom_subsector": "Cooling/HVAC"},
    "BE":    {"name": "Bloom Energy",              "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Electrical Equipment",      "custom_sector": "Power",       "custom_subsector": "Fuel Cells"},
    "SMR":   {"name": "NuScale Power",             "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Electrical Equipment",      "custom_sector": "Nuclear",     "custom_subsector": "SMR"},
    "OKLO":  {"name": "Oklo",                      "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Electrical Equipment",      "custom_sector": "Nuclear",     "custom_subsector": "SMR"},
    "CEG":   {"name": "Constellation Energy",      "exchange": "NASDAQ", "gics_sector": "Utilities",              "gics_subsector": "Electric Utilities",        "custom_sector": "Nuclear",     "custom_subsector": "Nuclear Utility"},
    "VST":   {"name": "Vistra",                    "exchange": "NYSE",   "gics_sector": "Utilities",              "gics_subsector": "Independent Power",         "custom_sector": "Nuclear",     "custom_subsector": "Nuclear Utility"},
    "TLN":   {"name": "Talen Energy",              "exchange": "NASDAQ", "gics_sector": "Utilities",              "gics_subsector": "Independent Power",         "custom_sector": "Nuclear",     "custom_subsector": "Nuclear Utility"},
    "LEU":   {"name": "Centrus Energy",            "exchange": "NYSE",   "gics_sector": "Energy",                 "gics_subsector": "Energy Equipment",          "custom_sector": "Nuclear",     "custom_subsector": "Uranium"},
    "GEV":   {"name": "GE Vernova",                "exchange": "NYSE",   "gics_sector": "Industrials",            "gics_subsector": "Electrical Equipment",      "custom_sector": "Power",       "custom_subsector": "Power Generation"},
    "NEE":   {"name": "NextEra Energy",            "exchange": "NYSE",   "gics_sector": "Utilities",              "gics_subsector": "Electric Utilities",        "custom_sector": "Power",       "custom_subsector": "Utility (AI demand)"},
    "AEP":   {"name": "American Electric Power",   "exchange": "NASDAQ", "gics_sector": "Utilities",              "gics_subsector": "Electric Utilities",        "custom_sector": "Power",       "custom_subsector": "Utility (AI demand)"},
    "SO":    {"name": "Southern Company",          "exchange": "NYSE",   "gics_sector": "Utilities",              "gics_subsector": "Electric Utilities",        "custom_sector": "Power",       "custom_subsector": "Utility (AI demand)"},
    "D":     {"name": "Dominion Energy",           "exchange": "NYSE",   "gics_sector": "Utilities",              "gics_subsector": "Electric Utilities",        "custom_sector": "Power",       "custom_subsector": "Utility (AI demand)"},
    "DUK":   {"name": "Duke Energy",               "exchange": "NYSE",   "gics_sector": "Utilities",              "gics_subsector": "Electric Utilities",        "custom_sector": "Power",       "custom_subsector": "Utility (AI demand)"},
}


def _scan_topline() -> list[str]:
    # has_topline = the cleaned topline parquet exists. The real output of
    # ToplineBuilder lives under topline/income_statement/, NOT backbone/.
    # (backbone/ is raw XBRL facts; topline/ is the curated layer the agent
    # actually queries.)
    p = DATA / "filing_data" / "topline" / "income_statement"
    if not p.exists():
        return []
    out = []
    for f in p.glob("ticker=*.parquet"):
        out.append(f.stem.replace("ticker=", ""))
    return out


def _scan_filings_raw() -> list[str]:
    p = DATA / "filing_data" / "filings"
    if not p.exists():
        return []
    out = []
    for d in p.iterdir():
        if d.is_dir() and d.name.startswith("ticker="):
            out.append(d.name.replace("ticker=", ""))
    return out


def _scan_earnings_releases() -> list[str]:
    p = DATA / "earnings_releases"
    if not p.exists():
        return []
    return [f.stem.replace("ticker=", "") for f in p.glob("ticker=*.parquet")]


def _scan_taiwan_revenue() -> list[str]:
    p = DATA / "taiwan" / "monthly_revenue" / "data.parquet"
    if not p.exists():
        return []
    df = pd.read_parquet(p, columns=["ticker"])
    return sorted(df["ticker"].astype(str).unique().tolist())


def _scan_x_handles() -> list[str]:
    p = DATA / "social" / "x" / "data.parquet"
    if not p.exists():
        return []
    df = pd.read_parquet(p)
    if "handle" in df.columns:
        return sorted(df["handle"].astype(str).unique().tolist())
    return []


def _read_taiwan_watchlist() -> list[dict]:
    """Read the Taiwan watchlist CSV that's already maintained by the
    Taiwan ingestion service."""
    p = DATA / "taiwan" / "watchlist_semi.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p, dtype=str).fillna("")
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "ticker":           r["ticker"],
            "name":             r.get("name") or r["ticker"],
            "market":           "TW",
            "exchange":         r.get("market", "TWSE"),   # CSV's "market" column is TWSE/TPEx
            "country":          "TW",
            "domicile":         "TW",
            "gics_sector":      r.get("sector", ""),
            "gics_subsector":   r.get("subsector", ""),
            "custom_sector":    "Semi" if "Semi" in (r.get("sector") or "") else "",
            "custom_subsector": r.get("subsector", ""),
            "filing_type":      "MOPS",
            "notes":            r.get("notes", ""),
        })
    return rows


def main() -> None:
    print("[seed] reading existing universe")
    df_before = read_universe()
    print(f"[seed] before: {len(df_before)} rows")

    # 1. Add Taiwan watchlist with metadata.
    tw_rows = _read_taiwan_watchlist()
    for r in tw_rows:
        add_ticker(**r, update_existing=True)
    print(f"[seed] taiwan watchlist: {len(tw_rows)} tickers added/updated")

    # 2. Add US universe with metadata. Tickers without EDGAR data yet
    # still get a row so the registry knows about them.
    us_count = 0
    for ticker, meta in US_METADATA.items():
        add_ticker(
            ticker=ticker,
            market="US",
            country="US",
            domicile="US",
            filing_type="10-K",
            **meta,
            update_existing=True,
        )
        us_count += 1
    print(f"[seed] us metadata: {us_count} tickers added/updated")

    # 3. Set has_topline = True for tickers with EDGAR backbone parquet.
    n_topline = 0
    for ticker in _scan_topline():
        mark_data_coverage(ticker, has_topline=True)
        n_topline += 1
    print(f"[seed] has_topline: {n_topline} flagged")

    # 4. has_filings_raw
    n_filings = 0
    for ticker in _scan_filings_raw():
        mark_data_coverage(ticker, has_filings_raw=True)
        n_filings += 1
    print(f"[seed] has_filings_raw: {n_filings} flagged")

    # 5. has_earnings_releases
    n_releases = 0
    for ticker in _scan_earnings_releases():
        mark_data_coverage(ticker, has_earnings_releases=True)
        n_releases += 1
    print(f"[seed] has_earnings_releases: {n_releases} flagged")

    # 6. has_monthly_revenue (Taiwan)
    n_tw_rev = 0
    for ticker in _scan_taiwan_revenue():
        mark_data_coverage(ticker, has_monthly_revenue=True)
        n_tw_rev += 1
    print(f"[seed] has_monthly_revenue: {n_tw_rev} flagged")

    # 7. has_x_posts -- only matters when ticker is the same as handle, which
    # generally isn't. Skip the auto-flag for X; user can mark manually.
    handles = _scan_x_handles()
    print(f"[seed] x handles in storage: {len(handles)} (not auto-flagged; handle != ticker)")

    df_after = read_universe()
    print(f"[seed] after: {len(df_after)} rows")
    print(f"[seed] file: {df_after.attrs.get('source_path', 'platform_universe.csv')}")
    print()
    print("[seed] coverage summary:")
    for col in ("has_topline", "has_monthly_revenue", "has_filings_raw",
                "has_earnings_releases"):
        n = int((df_after[col] == 1).sum())
        print(f"  {col:<25} {n:>3}")


if __name__ == "__main__":
    main()
