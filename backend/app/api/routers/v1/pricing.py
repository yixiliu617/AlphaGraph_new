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
GPU_LATEST = Path("backend/data/market_data/gpu_prices/gpu_price_latest.parquet")
GPU_HISTORY = Path("backend/data/market_data/gpu_prices/gpu_price_history.parquet")


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


# ---------------------------------------------------------------------------
# Cloud GPU pricing
# ---------------------------------------------------------------------------

@router.get("/gpu/latest")
def gpu_latest(
    gpu: str | None = Query(None, description="Filter by GPU name"),
    market: str | None = Query(None, description="on_demand or spot"),
):
    if not GPU_LATEST.exists():
        return {"rows": []}
    df = pd.read_parquet(GPU_LATEST)
    if gpu:
        df = df[df["gpu_name"].str.contains(gpu, case=False, na=False)]
    if market:
        df = df[df["market_type"] == market]
    df = df.sort_values(["gpu_name", "market_type"])

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "gpu_name": r["gpu_name"],
            "market_type": r["market_type"],
            "min_price": float(r["min_price"]),
            "max_price": float(r["max_price"]),
            "median_price": float(r["median_price"]),
            "mean_price": float(r["mean_price"]),
            "num_offers": int(r["num_offers"]),
            "providers": r["providers"],
            "timestamp": r.get("timestamp", ""),
        })
    return {"rows": rows, "timestamp": df["timestamp"].iloc[0] if len(df) else ""}


@router.get("/gpu/history")
def gpu_history(
    gpu: str | None = Query(None, description="Filter by GPU name"),
    market: str = Query("on_demand"),
):
    if not GPU_HISTORY.exists():
        return {"rows": []}
    df = pd.read_parquet(GPU_HISTORY)
    df = df[df["market_type"] == market]
    if gpu:
        df = df[df["gpu_name"].str.contains(gpu, case=False, na=False)]
    df = df.sort_values(["gpu_name", "timestamp"])

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "gpu_name": r["gpu_name"],
            "date": r["date"],
            "hour": r.get("hour", ""),
            "min_price": float(r["min_price"]),
            "median_price": float(r["median_price"]),
            "num_offers": int(r["num_offers"]),
        })
    return {"rows": rows}
