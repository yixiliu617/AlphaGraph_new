"""
Validate every handle in x_config.json against twitterapi.io /user/info.

For each handle:
  PRINT [tier]  handle  status  followers  tweets

Writes a validated_accounts.json alongside the config with:
  { valid: [...], invalid: [...], resolved: {handle: user_id} }

Run:
    python tools/x_validate.py
Requires TWITTERAPI_IO_KEY in .env.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime, timezone

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load .env
ROOT = pathlib.Path(__file__).resolve().parents[1]
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))

from backend.app.services.social.sources.x_twitterapi import (
    TwitterApiIoClient,
    TwitterApiIoError,
)

CONFIG_PATH = ROOT / "backend" / "data" / "market_data" / "x" / "x_config.json"
OUT_PATH = ROOT / "backend" / "data" / "market_data" / "x" / "validated_accounts.json"


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    total = sum(len(t["accounts"]) for t in cfg["tiers"])
    print(f"[x_validate] config has {total} accounts across {len(cfg['tiers'])} tiers")
    print(f"[x_validate] free-tier QPS is 1 req / 5s → expected runtime ~{total * 5 // 60 + 1} min\n")

    try:
        client = TwitterApiIoClient()
    except TwitterApiIoError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    valid: list[dict] = []
    invalid: list[dict] = []

    for tier in cfg["tiers"]:
        tier_name = tier["name"]
        print(f"--- tier: {tier_name} ---")
        for entry in tier["accounts"]:
            handle = entry["handle"]
            note = entry.get("note", "")
            try:
                profile = client.get_user_info(handle)
            except TwitterApiIoError as exc:
                print(f"  [ERR ]  @{handle:20s}  ({note}) — {exc}")
                invalid.append({"handle": handle, "tier": tier_name, "reason": str(exc)})
                continue

            if profile is None:
                print(f"  [MISS]  @{handle:20s}  ({note}) — user not found")
                invalid.append({"handle": handle, "tier": tier_name, "reason": "not_found"})
                continue

            print(
                f"  [OK  ]  @{profile.handle:20s}  "
                f"followers={profile.followers:>10,}  "
                f"tweets={profile.tweet_count:>8,}  {profile.name}"
            )
            valid.append({
                "handle": profile.handle,
                "requested_handle": handle,
                "user_id": profile.user_id,
                "name": profile.name,
                "followers": profile.followers,
                "tweet_count": profile.tweet_count,
                "tier": tier_name,
                "note": note,
            })

    print()
    print(f"[x_validate] valid: {len(valid)} / {total}    invalid: {len(invalid)}")

    out = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "valid": valid,
        "invalid": invalid,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[x_validate] wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
