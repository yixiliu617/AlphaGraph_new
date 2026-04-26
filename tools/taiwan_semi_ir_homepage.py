"""
Round-2 IR URL discovery: for the peers whose specific candidate URLs
404'd, visit the company homepage and follow the "investor relations"
link.

This is more robust than guessing the exact IR path per company.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path("C:/Users/Sharo/.alphagraph_tsmc_profile")
OUT_DIR = REPO_ROOT / "backend" / "data" / "financials" / "peers"

# (ticker, name_en, segment, homepage candidates)
PEERS = [
    ("2303.TW", "UMC",                          "foundry",     ["https://www.umc.com/", "https://www.umc.com/en/"]),
    ("5347.TW", "Vanguard",                     "foundry",     ["https://www.vis.com.tw/"]),
    ("6770.TW", "Powerchip (PSMC)",             "foundry",     ["https://www.psmc.com/"]),
    ("3711.TW", "ASE Technology Holding",       "osat",        ["https://www.aseholdings.com/", "https://www.aseglobal.com/"]),
    ("2449.TW", "King Yuan Electronics (KYEC)", "osat",        ["https://www.kyec.com.tw/"]),
    ("2344.TW", "Winbond",                      "memory_idm",  ["https://www.winbond.com/"]),
    ("2337.TW", "Macronix",                     "memory_idm",  ["https://www.macronix.com/"]),
    ("2454.TW", "MediaTek",                     "fabless",     ["https://corp.mediatek.com/"]),
    ("2379.TW", "Realtek",                      "fabless",     ["https://www.realtek.com/"]),
    ("3034.TW", "Novatek",                      "fabless",     ["https://www.novatek.com.tw/"]),
    ("6271.TW", "Phison Electronics",           "fabless",     ["https://www.phison.com/"]),
    ("8016.TW", "Sitronix",                     "fabless",     ["https://www.sitronix.com.tw/"]),
    ("5483.TW", "Sino-American Silicon (SAS)",  "wafer",       ["https://www.sas.com.tw/"]),
    ("6488.TW", "GlobalWafers",                 "wafer",       ["https://www.gws.com.tw/", "https://www.globalwafers.com/"]),
    ("3037.TW", "Unimicron",                    "substrate",   ["https://www.unimicron.com/"]),
]


def find_ir_link(page) -> dict | None:
    """Inspect the homepage for a link whose visible text or href looks
    like the IR landing. Tries Chinese first, then English."""
    return page.evaluate(
        """() => {
            const tests = [
                /投資人關係/i, /投資人專區/i, /投資人/i,
                /investor relations/i, /\\binvestors?\\b/i,
            ];
            const seen = [];
            for (const a of document.querySelectorAll('a[href]')) {
                const text = (a.innerText || '').trim();
                const href = a.href;
                for (const re of tests) {
                    if (re.test(text) || re.test(href)) {
                        seen.push({text, href});
                        break;
                    }
                }
            }
            // Prefer ones whose URL has "investor" — nav-bar links
            const ranked = seen.sort((a, b) =>
                (b.href.toLowerCase().includes('investor') ? 1 : 0) -
                (a.href.toLowerCase().includes('investor') ? 1 : 0)
            );
            return ranked.slice(0, 5);
        }"""
    )


def main() -> int:
    with sync_playwright() as plw:
        ctx = plw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False, channel="chrome",
            args=["--start-maximized"], no_viewport=True,
        )
        page = ctx.new_page()

        results = []
        for ticker, name, segment, candidates in PEERS:
            print(f"\n=== {ticker}  {name} ({segment}) ===")
            best_homepage = None
            ir_links: list[dict] = []
            for hp in candidates:
                try:
                    resp = page.goto(hp, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1500)
                    status = resp.status if resp else 0
                    if status != 200:
                        print(f"  homepage {hp}: status={status}")
                        continue
                    best_homepage = hp
                    title = page.title()
                    print(f"  homepage {hp}: status=200 title={title!r:.50}")
                    found = find_ir_link(page)
                    if found:
                        for f in found[:3]:
                            print(f"    IR-like: text={f['text'][:30]!r} href={f['href'][:120]}")
                        ir_links = found
                        break
                except Exception as e:
                    print(f"  homepage {hp}: ERR {type(e).__name__}: {str(e)[:60]}")
                    continue
            results.append({
                "ticker": ticker, "name_en": name, "segment": segment,
                "homepage": best_homepage,
                "ir_link_candidates": ir_links,
                "ir_url_best_guess": ir_links[0]["href"] if ir_links else None,
            })

        out_path = OUT_DIR / "taiwan_semi_ir_round2.json"
        out_path.write_text(json.dumps({
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "peers": results,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n-> {out_path}")
        for r in results:
            mark = "OK" if r["ir_url_best_guess"] else "??"
            print(f"  [{mark}] {r['ticker']}  {r['name_en']:30}  {r['ir_url_best_guess']}")
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
