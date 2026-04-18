"""
Pricing API router -- serves component price trends from multiple sources.

GET /pricing/trends              -- PCPartPicker aggregate price trends
GET /pricing/trends/categories   -- list PCPartPicker categories
GET /pricing/camel               -- CamelCamelCamel individual product price history
GET /pricing/camel/products      -- list tracked CamelCamelCamel products
"""

from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Query

router = APIRouter()

PCPP_MONTHLY = Path("backend/data/market_data/pcpartpicker_trends/_combined.parquet")
PCPP_WEEKLY = Path("backend/data/market_data/pcpartpicker_trends/_combined_weekly.parquet")
CAMEL_PATH = Path("backend/data/market_data/camelcamelcamel/camelcamelcamel_prices.parquet")
CAMEL_MANIFEST = Path("backend/data/market_data/camelcamelcamel/manifest.json")


def _load_pcpp(granularity: str = "monthly"):
    path = PCPP_WEEKLY if granularity == "weekly" else PCPP_MONTHLY
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["category", "component", "date"])
    return df


def _load_camel():
    if not CAMEL_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(CAMEL_PATH)
    return df


@router.get("/trends/categories")
def list_categories():
    df = _load_pcpp()
    if df.empty:
        return {"categories": [], "has_weekly": PCPP_WEEKLY.exists()}
    cats = []
    for cat in sorted(df["category"].unique()):
        components = sorted(df[df["category"] == cat]["component"].unique().tolist())
        cats.append({"category": cat, "components": components})
    return {"categories": cats, "has_weekly": PCPP_WEEKLY.exists()}


@router.get("/trends")
def get_trends(
    category: str | None = Query(None),
    component: str | None = Query(None),
    granularity: str = Query("monthly", description="monthly or weekly"),
):
    df = _load_pcpp(granularity)
    if df.empty:
        return {"rows": [], "granularity": granularity}
    if category:
        df = df[df["category"] == category]
    if component:
        df = df[df["component"] == component]

    has_month = "month" in df.columns
    rows = []
    for _, r in df.iterrows():
        d = {
            "category": r["category"],
            "component": r["component"],
            "date": r["date"].strftime("%Y-%m-%d") if pd.notna(r["date"]) else "",
            "avg_price_usd": float(r["avg_price_usd"]),
        }
        if has_month and pd.notna(r.get("month")):
            d["month"] = r["month"]
        rows.append(d)
    return {"rows": rows, "granularity": granularity}


@router.get("/camel/products")
def list_camel_products():
    import json

    if not CAMEL_MANIFEST.exists():
        return {"products": []}
    with open(CAMEL_MANIFEST) as f:
        manifest = json.load(f)

    df = _load_camel()
    products = []
    for entry in manifest:
        asin = entry["asin"]
        name = entry["product_name"]
        sub = df[(df["asin"] == asin) & (~df["quarter"].str.startswith("__"))]
        meta = df[(df["asin"] == asin) & (df["quarter"].str.startswith("__"))]
        info = {"asin": asin, "product_name": name, "quarters": len(sub)}
        for _, m in meta.iterrows():
            key = m["quarter"].strip("_")
            info[key] = m["approx_price_usd"]
        products.append(info)
    return {"products": products}


@router.get("/camel")
def get_camel_data(
    asin: str | None = Query(None),
):
    df = _load_camel()
    if df.empty:
        return {"rows": []}

    # Exclude metadata rows
    df = df[~df["quarter"].str.startswith("__")]

    if asin:
        df = df[df["asin"] == asin]

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "asin": r["asin"],
            "product_name": r["product_name"],
            "quarter": r["quarter"],
            "approx_price_usd": r["approx_price_usd"],
        })
    return {"rows": rows}
