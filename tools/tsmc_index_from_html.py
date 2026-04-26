"""
One-off helper: rebuild _index.json from the per-quarter HTML pages we
already crawled via tsmc_guidance_crawler.py. The deep-link approach
caught quarters the SPA-click crawler couldn't reach (2010-2019, etc.),
so this gives us a complete index.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from tools.tsmc_archive_crawler import classify_pdf  # noqa: E402

TICKER = "2330.TW"
DATA_ROOT = REPO_ROOT / "backend" / "data" / "financials"
RAW_TICKER_ROOT = DATA_ROOT / "raw" / TICKER
INDEX_FILE = RAW_TICKER_ROOT / "_index.json"


# Anchor extraction — pure regex over saved HTML, no JS/Playwright needed
_ANCHOR_RE = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def extract_pdfs_from_html(html: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for m in _ANCHOR_RE.finditer(html):
        href = m.group(1)
        if not re.search(r"\.pdf(\?|$)", href, re.IGNORECASE):
            continue
        # Skip generic site-wide PDFs (NYSE 303A, etc.)
        if "encrypt_file" not in href and "reports/" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        label = _strip_html(m.group(2))
        out.append({
            "label": label,
            "type": classify_pdf(href, label),
            "url": href,
        })
    return out


def main() -> int:
    quarters: dict[str, dict] = {}
    # Scan all per-quarter dirs that have a page.html
    for year_dir in sorted(RAW_TICKER_ROOT.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for q_dir in sorted(year_dir.iterdir()):
            if not q_dir.is_dir() or not q_dir.name.startswith("Q"):
                continue
            html_path = q_dir / "page.html"
            if not html_path.exists():
                continue
            html = html_path.read_text(encoding="utf-8", errors="replace")
            # Get title from <title>...</title>
            tm = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
            title = tm.group(1).strip() if tm else ""
            pdfs = extract_pdfs_from_html(html)
            yq = f"{year_dir.name}/{q_dir.name}"
            quarters[yq] = {
                "title": title,
                "pdfs": pdfs,
            }
            print(f"  {yq}: {len(pdfs)} PDFs  ({title[:50]})")
    print(f"\nTotal quarters: {len(quarters)}")
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(
        json.dumps({
            "ticker": TICKER,
            "enumerated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "index_url": "https://investor.tsmc.com/chinese/quarterly-results",
            "quarters": dict(sorted(quarters.items())),
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Saved -> {INDEX_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
