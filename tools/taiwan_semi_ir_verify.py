"""
Verify the IR-page URLs for major Taiwan semiconductor peers, so we can
plan cross-foundry comparison ingestion.

For each ticker:
  1. Visit the candidate IR landing URL.
  2. Record status, page title, and a few signals (presence of "investor",
     "quarterly", "earnings", "annual report" keywords + at least one
     downloadable PDF).
  3. Also probe a couple of common alternate paths if the primary 404s.

Output: backend/data/financials/peers/taiwan_semi_ir_catalog.json
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path("C:/Users/Sharo/.alphagraph_tsmc_profile")
OUT_DIR = REPO_ROOT / "backend" / "data" / "financials" / "peers"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# Curated list of Taiwan semiconductor peers grouped by sub-segment.
# `candidates` is tried in order — first 200 with reasonable signal wins.
PEERS: list[dict] = [
    # === Pure-play foundry ===
    {"ticker": "2330.TW", "name_en": "TSMC", "name_zh": "台灣積體電路製造", "segment": "foundry",
     "candidates": ["https://investor.tsmc.com/chinese/quarterly-results"]},
    {"ticker": "2303.TW", "name_en": "UMC", "name_zh": "聯華電子", "segment": "foundry",
     "candidates": [
        "https://www.umc.com/zh-TW/Investors/index",
        "https://www.umc.com/en/Investors",
        "https://www.umc.com/Investors",
     ]},
    {"ticker": "5347.TW", "name_en": "Vanguard", "name_zh": "世界先進", "segment": "foundry",
     "candidates": [
        "https://www.vis.com.tw/zh-tw/Investors",
        "https://www.vis.com.tw/en/Investors",
        "https://www.vis.com.tw/zh-tw/investors/financial-information",
     ]},
    {"ticker": "6770.TW", "name_en": "Powerchip (PSMC)", "name_zh": "力積電", "segment": "foundry",
     "candidates": [
        "https://www.psmc.com/psmc/zh-TW/investors",
        "https://www.psmc.com/psmc/en/investors",
     ]},

    # === OSAT (Outsourced Semiconductor Assembly & Test) ===
    {"ticker": "3711.TW", "name_en": "ASE Technology Holding", "name_zh": "日月光投控", "segment": "osat",
     "candidates": [
        "https://www.aseglobal.com/zh/investors/",
        "https://www.aseglobal.com/en/investors/",
     ]},
    {"ticker": "2449.TW", "name_en": "King Yuan Electronics (KYEC)", "name_zh": "京元電子", "segment": "osat",
     "candidates": [
        "https://www.kyec.com.tw/InvestorService/InvestorService.aspx",
        "https://www.kyec.com.tw/en/InvestorService/InvestorService.aspx",
     ]},

    # === Memory IDM ===
    {"ticker": "2344.TW", "name_en": "Winbond", "name_zh": "華邦電子", "segment": "memory_idm",
     "candidates": [
        "https://www.winbond.com/hq/about-winbond/investor-relations/?__locale=zh_TW",
        "https://www.winbond.com/hq/about-winbond/investor-relations/?__locale=en",
     ]},
    {"ticker": "2337.TW", "name_en": "Macronix", "name_zh": "旺宏電子", "segment": "memory_idm",
     "candidates": [
        "https://www.macronix.com/zh-tw/about/Pages/InvestorRelations.aspx",
        "https://www.macronix.com/en-us/about/Pages/InvestorRelations.aspx",
     ]},

    # === Fabless / Design House ===
    {"ticker": "2454.TW", "name_en": "MediaTek", "name_zh": "聯發科", "segment": "fabless",
     "candidates": [
        "https://corp.mediatek.com/zh-tw/investor-relations",
        "https://corp.mediatek.com/investor-relations",
     ]},
    {"ticker": "2379.TW", "name_en": "Realtek", "name_zh": "瑞昱半導體", "segment": "fabless",
     "candidates": [
        "https://www.realtek.com/zh-tw/investor",
        "https://www.realtek.com/en/investor",
     ]},
    {"ticker": "3034.TW", "name_en": "Novatek", "name_zh": "聯詠科技", "segment": "fabless",
     "candidates": [
        "https://www.novatek.com.tw/zh-tw/Investor",
        "https://www.novatek.com.tw/en/Investor",
     ]},
    {"ticker": "6271.TW", "name_en": "Phison Electronics", "name_zh": "群聯電子", "segment": "fabless",
     "candidates": [
        "https://www.phison.com/zh-tw/about-phison/investors",
        "https://www.phison.com/en/about-phison/investors",
     ]},
    {"ticker": "8016.TW", "name_en": "Sitronix", "name_zh": "矽創電子", "segment": "fabless",
     "candidates": [
        "https://www.sitronix.com.tw/zh/investors",
        "https://www.sitronix.com.tw/en/investors",
     ]},

    # === Wafer / Materials ===
    {"ticker": "5483.TW", "name_en": "Sino-American Silicon (SAS)", "name_zh": "中美晶", "segment": "wafer",
     "candidates": [
        "https://www.saswafer.com/zh-tw/Investors",
        "https://www.saswafer.com/en/Investors",
     ]},
    {"ticker": "6488.TW", "name_en": "GlobalWafers", "name_zh": "環球晶圓", "segment": "wafer",
     "candidates": [
        "https://www.gwafers.com/zh-tw/investor-information",
        "https://www.gwafers.com/en/investor-information",
     ]},

    # === Substrate / Packaging ===
    {"ticker": "3037.TW", "name_en": "Unimicron", "name_zh": "欣興電子", "segment": "substrate",
     "candidates": [
        "https://www.unimicron.com/zh-tw/Investor",
        "https://www.unimicron.com/en/Investor",
     ]},

    # === Specialty ===
    {"ticker": "2351.TW", "name_en": "SiTronix Technology",   "name_zh": "順德工業", "segment": "specialty",
     "candidates": ["https://www.shun-on.com.tw/en/investor/index.aspx"]},
]


def probe(page: Page, url: str) -> dict:
    out: dict = {"url": url, "status": None, "title": None, "pdf_count": 0, "signals": []}
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        out["status"] = resp.status if resp else 0
    except Exception as e:
        out["error"] = f"goto: {e}"
        return out
    try:
        out["title"] = page.title()
    except Exception:
        out["title"] = "?"
    # Quick signal pass — keywords on page + PDF count
    try:
        body_text = page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        body_text = ""
    body_lc = body_text.lower()
    for needle in ["investor", "investors", "quarterly", "earnings", "annual report",
                   "investor relations", "投資人關係", "財務報告", "法人說明會",
                   "歷年股東", "重大訊息"]:
        if needle.lower() in body_lc:
            out["signals"].append(needle)
    try:
        out["pdf_count"] = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]')).filter(a => /\\.pdf(\\?|$)/i.test(a.href)).length"""
        )
    except Exception:
        out["pdf_count"] = 0
    return out


def main() -> int:
    with sync_playwright() as plw:
        ctx = plw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False, channel="chrome",
            args=["--start-maximized"], no_viewport=True,
        )
        page = ctx.new_page()

        catalog: list[dict] = []
        for peer in PEERS:
            print(f"\n=== {peer['ticker']}  {peer['name_en']} ({peer['name_zh']}) — {peer['segment']} ===")
            attempts = []
            best = None
            for url in peer["candidates"]:
                r = probe(page, url)
                attempts.append(r)
                msg = (
                    f"  status={r.get('status')}  pdfs={r.get('pdf_count')}  "
                    f"signals={len(r.get('signals',[]))}  title={(r.get('title') or '')!r:.60}"
                )
                if "error" in r:
                    msg += f"  ERR: {r['error']}"
                print(msg)
                # First good probe wins: status 200 AND (some signal OR some PDF)
                if r.get("status") == 200 and (r.get("signals") or r.get("pdf_count", 0) > 0):
                    best = r
                    break
                time.sleep(0.5)
            catalog.append({
                **{k: v for k, v in peer.items() if k != "candidates"},
                "candidates_tried": attempts,
                "best": best,
                "ir_url": best["url"] if best else None,
                "verified": best is not None,
            })

        out_path = OUT_DIR / "taiwan_semi_ir_catalog.json"
        out_path.write_text(json.dumps({
            "ticker_count": len(catalog),
            "verified_count": sum(1 for c in catalog if c["verified"]),
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "peers": catalog,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nCatalog -> {out_path}")
        print(f"Verified: {sum(1 for c in catalog if c['verified'])}/{len(catalog)}")
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
