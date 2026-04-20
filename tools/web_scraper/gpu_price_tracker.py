"""
Cloud GPU Price Tracker — monitors rental prices across providers.

Usage:
    python tools/web_scraper/gpu_price_tracker.py snapshot        # take price snapshot
    python tools/web_scraper/gpu_price_tracker.py snapshot --provider vastai
    python tools/web_scraper/gpu_price_tracker.py summary         # current price summary
    python tools/web_scraper/gpu_price_tracker.py history          # show price history
    python tools/web_scraper/gpu_price_tracker.py config           # show config

Providers:
    - Vast.ai (free API, 50+ GPU types, on-demand + spot)
    - RunPod (GraphQL, secure + community pricing)
    - Tensordock (free API, marketplace pricing)

Runs every 2 hours via scheduled_scrape.bat.
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path("backend/data/market_data/gpu_prices")
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip, deflate"}

KEY_GPUS = [
    "H200 NVL", "H100 SXM", "H100 PCIE", "H100",
    "A100 SXM4", "A100 PCIE", "A100",
    "L40S", "L40", "A40",
    "RTX 5090", "RTX 5080", "RTX 5070 Ti", "RTX 5070",
    "RTX 4090", "RTX 4080S", "RTX 4080", "RTX 4070 Ti",
    "RTX 3090", "RTX 3080",
    "RTX A6000", "RTX 6000Ada",
]


def fetch_vastai(market_type="on-demand", limit=500):
    """Fetch GPU offers from Vast.ai public API."""
    q = json.dumps({
        "rentable": {"eq": True},
        "order": [["dph_total", "asc"]],
        "type": market_type,
        "limit": limit,
    })
    resp = requests.get(
        "https://cloud.vast.ai/api/v0/bundles/",
        params={"q": q},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    offers = resp.json().get("offers", [])

    results = []
    for o in offers:
        results.append({
            "gpu_name": o.get("gpu_name", ""),
            "num_gpus": o.get("num_gpus", 1),
            "price_per_hr": o.get("dph_total", 0),
            "price_per_gpu_hr": o.get("dph_total", 0) / max(o.get("num_gpus", 1), 1),
            "gpu_ram_gb": o.get("gpu_ram", 0) / 1024 if o.get("gpu_ram", 0) > 100 else o.get("gpu_ram", 0),
            "cpu_cores": o.get("cpu_cores_effective", 0),
            "ram_gb": round(o.get("cpu_ram", 0) / 1024, 1),
            "storage_gb": o.get("disk_space", 0),
            "reliability": o.get("reliability2", 0),
            "verified": o.get("verification", "") == "verified",
            "market_type": "spot" if market_type == "bid" else "on-demand",
            "provider": "vastai",
        })
    return results


def fetch_runpod():
    """Fetch GPU pricing from RunPod GraphQL API."""
    query = """
    {
      gpuTypes {
        id
        displayName
        memoryInGb
        securePrice
        communityPrice
        secureSpotPrice
        communitySpotPrice
      }
    }
    """
    try:
        resp = requests.post(
            "https://api.runpod.io/graphql",
            json={"query": query},
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        gpu_types = resp.json().get("data", {}).get("gpuTypes", [])

        results = []
        for g in gpu_types:
            for pricing_type, price_field, market in [
                ("secure", "securePrice", "on-demand"),
                ("community", "communityPrice", "on-demand"),
                ("secure_spot", "secureSpotPrice", "spot"),
                ("community_spot", "communitySpotPrice", "spot"),
            ]:
                price = g.get(price_field)
                if price and price > 0:
                    results.append({
                        "gpu_name": g["displayName"],
                        "num_gpus": 1,
                        "price_per_hr": price,
                        "price_per_gpu_hr": price,
                        "gpu_ram_gb": g.get("memoryInGb", 0),
                        "cpu_cores": 0,
                        "ram_gb": 0,
                        "storage_gb": 0,
                        "reliability": 0,
                        "verified": pricing_type.startswith("secure"),
                        "market_type": market,
                        "provider": f"runpod_{pricing_type}",
                    })
        return results
    except Exception as e:
        print(f"  RunPod error: {e}")
        return []


def fetch_tensordock():
    """Fetch GPU pricing from Tensordock marketplace API."""
    try:
        resp = requests.get(
            "https://marketplace.tensordock.com/api/v0/client/deploy/hostnodes",
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        nodes = resp.json().get("hostnodes", {})

        results = []
        for nid, node in nodes.items():
            location = node.get("location", {})
            for gpu_name, gpu_info in node.get("specs", {}).get("gpu", {}).items():
                price = gpu_info.get("price", 0)
                amount = gpu_info.get("amount", 0)
                if price > 0 and amount > 0:
                    results.append({
                        "gpu_name": gpu_name,
                        "num_gpus": 1,
                        "price_per_hr": price,
                        "price_per_gpu_hr": price,
                        "gpu_ram_gb": gpu_info.get("vram", 0),
                        "cpu_cores": 0,
                        "ram_gb": 0,
                        "storage_gb": 0,
                        "reliability": 0,
                        "verified": True,
                        "market_type": "on-demand",
                        "provider": "tensordock",
                    })
        return results
    except Exception as e:
        print(f"  Tensordock error: {e}")
        return []


def aggregate_prices(all_offers):
    """Aggregate offers into per-GPU price summary."""
    gpu_data = defaultdict(lambda: {"on_demand": [], "spot": []})

    for o in all_offers:
        name = o["gpu_name"]
        price = o["price_per_gpu_hr"]
        if price <= 0:
            continue
        bucket = "spot" if o["market_type"] == "spot" else "on_demand"
        gpu_data[name][bucket].append({
            "price": price,
            "provider": o["provider"],
            "verified": o["verified"],
        })

    rows = []
    ts = datetime.now(timezone.utc)
    for gpu_name, markets in gpu_data.items():
        for market_type, offers in markets.items():
            if not offers:
                continue
            prices = [o["price"] for o in offers]
            providers = list(set(o["provider"] for o in offers))
            rows.append({
                "timestamp": ts.isoformat(),
                "date": ts.strftime("%Y-%m-%d"),
                "hour": ts.strftime("%H:00"),
                "gpu_name": gpu_name,
                "market_type": market_type,
                "min_price": round(min(prices), 4),
                "max_price": round(max(prices), 4),
                "median_price": round(sorted(prices)[len(prices) // 2], 4),
                "mean_price": round(sum(prices) / len(prices), 4),
                "num_offers": len(offers),
                "providers": ",".join(sorted(set(providers))),
            })

    return pd.DataFrame(rows)


def cmd_snapshot(args):
    """Take a price snapshot from all providers."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    providers = args.provider.split(",") if args.provider else ["vastai", "runpod", "tensordock"]
    all_offers = []

    for prov in providers:
        print(f"  Fetching {prov}...", end=" ", flush=True)
        if prov == "vastai":
            on_demand = fetch_vastai("on-demand", 500)
            time.sleep(2)
            spot = fetch_vastai("bid", 500)
            all_offers.extend(on_demand + spot)
            print(f"{len(on_demand)} on-demand + {len(spot)} spot")
        elif prov == "runpod":
            rp = fetch_runpod()
            all_offers.extend(rp)
            print(f"{len(rp)} pricing entries")
        elif prov == "tensordock":
            td = fetch_tensordock()
            all_offers.extend(td)
            print(f"{len(td)} offers")

    if not all_offers:
        print("No offers fetched!")
        return

    # Aggregate
    summary = aggregate_prices(all_offers)
    print(f"\nAggregated: {len(summary)} GPU/market combinations")

    # Save snapshot
    snapshot_path = DATA_DIR / "gpu_price_history.parquet"
    if snapshot_path.exists():
        existing = pd.read_parquet(snapshot_path)
        combined = pd.concat([existing, summary], ignore_index=True)
        combined.to_parquet(snapshot_path, index=False, compression="zstd")
        print(f"Appended to history: {len(combined)} total rows")
    else:
        summary.to_parquet(snapshot_path, index=False, compression="zstd")
        print(f"Created history: {len(summary)} rows")

    # Save latest snapshot separately
    latest_path = DATA_DIR / "gpu_price_latest.parquet"
    summary.to_parquet(latest_path, index=False, compression="zstd")

    # Also save raw offers for this snapshot
    raw_df = pd.DataFrame(all_offers)
    raw_df["timestamp"] = datetime.now(timezone.utc).isoformat()
    raw_path = DATA_DIR / "gpu_offers_latest.parquet"
    raw_df.to_parquet(raw_path, index=False, compression="zstd")

    # Print key GPU summary
    print(f"\nKey GPU Prices (per GPU/hr):")
    print(f"{'GPU':25s} {'On-Demand':>12s} {'Spot':>12s} {'Offers':>8s}")
    print("-" * 60)
    for gpu in KEY_GPUS:
        od = summary[(summary["gpu_name"] == gpu) & (summary["market_type"] == "on_demand")]
        sp = summary[(summary["gpu_name"] == gpu) & (summary["market_type"] == "spot")]
        od_str = f"${od.iloc[0]['min_price']:.2f}-${od.iloc[0]['max_price']:.2f}" if len(od) else "-"
        sp_str = f"${sp.iloc[0]['min_price']:.2f}-${sp.iloc[0]['max_price']:.2f}" if len(sp) else "-"
        n = (od.iloc[0]["num_offers"] if len(od) else 0) + (sp.iloc[0]["num_offers"] if len(sp) else 0)
        if n > 0:
            print(f"  {gpu:23s} {od_str:>12s} {sp_str:>12s} {n:>8d}")


def cmd_summary(args):
    """Show current price summary."""
    latest_path = DATA_DIR / "gpu_price_latest.parquet"
    if not latest_path.exists():
        print("No data. Run 'snapshot' first.")
        return

    df = pd.read_parquet(latest_path)
    ts = df["timestamp"].iloc[0] if len(df) else "?"
    print(f"Latest snapshot: {ts}")
    print(f"\n{'GPU':25s} {'On-Demand':>12s} {'Spot':>12s} {'Providers':>15s}")
    print("-" * 65)

    for gpu in KEY_GPUS:
        od = df[(df["gpu_name"] == gpu) & (df["market_type"] == "on_demand")]
        sp = df[(df["gpu_name"] == gpu) & (df["market_type"] == "spot")]
        if len(od) == 0 and len(sp) == 0:
            continue
        od_str = f"${od.iloc[0]['min_price']:.2f}-${od.iloc[0]['max_price']:.2f}" if len(od) else "-"
        sp_str = f"${sp.iloc[0]['min_price']:.2f}-${sp.iloc[0]['max_price']:.2f}" if len(sp) else "-"
        provs = set()
        if len(od): provs.update(od.iloc[0]["providers"].split(","))
        if len(sp): provs.update(sp.iloc[0]["providers"].split(","))
        print(f"  {gpu:23s} {od_str:>12s} {sp_str:>12s} {','.join(sorted(provs)):>15s}")


def cmd_history(args):
    """Show price history for key GPUs."""
    hist_path = DATA_DIR / "gpu_price_history.parquet"
    if not hist_path.exists():
        print("No history. Run 'snapshot' multiple times to build history.")
        return

    df = pd.read_parquet(hist_path)
    print(f"History: {len(df)} rows, {df['date'].nunique()} dates")
    print(f"Range: {df['date'].min()} to {df['date'].max()}")
    print(f"Snapshots: {df['timestamp'].nunique()}")

    for gpu in ["H100 SXM", "A100 SXM4", "RTX 4090", "L40S"]:
        sub = df[(df["gpu_name"] == gpu) & (df["market_type"] == "on_demand")]
        if len(sub) == 0:
            continue
        print(f"\n  {gpu} on-demand:")
        for _, r in sub.sort_values("timestamp").iterrows():
            print(f"    {r['date']} {r['hour']} ${r['min_price']:.3f}-${r['max_price']:.3f} ({r['num_offers']} offers)")


def cmd_config(args):
    """Show tracked GPUs."""
    print("Key GPUs tracked:")
    for g in KEY_GPUS:
        print(f"  {g}")
    print(f"\nProviders: Vast.ai, RunPod, Tensordock")
    print(f"Data: {DATA_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Cloud GPU Price Tracker")
    sub = parser.add_subparsers(dest="command")

    p_snap = sub.add_parser("snapshot", help="Take price snapshot")
    p_snap.add_argument("--provider", help="Provider(s), comma-separated")

    sub.add_parser("summary", help="Current price summary")
    sub.add_parser("history", help="Price history")
    sub.add_parser("config", help="Show config")

    args = parser.parse_args()
    cmds = {"snapshot": cmd_snapshot, "summary": cmd_summary, "history": cmd_history, "config": cmd_config}

    if args.command in cmds:
        cmds[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
