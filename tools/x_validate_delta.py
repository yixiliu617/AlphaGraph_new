"""
Targeted re-validation: probe only the handles in x_config.json that
aren't already in validated_accounts.json, then rewrite the latter to
reflect the current config (dropping handles we removed; keeping
already-valid ones; adding newly-probed ones).
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))

from backend.app.services.social.sources.x_twitterapi import (
    TwitterApiIoClient,
    TwitterApiIoError,
)

CONFIG = ROOT / "backend" / "data" / "market_data" / "x" / "x_config.json"
VALIDATED = ROOT / "backend" / "data" / "market_data" / "x" / "validated_accounts.json"


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    existing = json.loads(VALIDATED.read_text(encoding="utf-8")) if VALIDATED.exists() else {"valid": [], "invalid": []}
    existing_by_requested = {v["requested_handle"].lower(): v for v in existing["valid"]}

    # Flatten current config
    configured: list[tuple[str, str, str]] = []  # (tier, handle, note)
    for tier in cfg["tiers"]:
        for a in tier["accounts"]:
            configured.append((tier["name"], a["handle"], a.get("note", "")))

    # Decide what to probe
    to_probe = [(t, h, n) for (t, h, n) in configured
                if h.lower() not in existing_by_requested]
    to_keep = [v for v in existing["valid"]
               if v["requested_handle"].lower() in {h.lower() for (_, h, _) in configured}]

    print(f"[x_validate_delta] config={len(configured)} "
          f"already_valid={len(to_keep)} to_probe={len(to_probe)}")

    valid = list(to_keep)
    invalid = []

    if not to_probe:
        print("[x_validate_delta] nothing to probe")
    else:
        try:
            client = TwitterApiIoClient()
        except TwitterApiIoError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1

        for tier_name, handle, note in to_probe:
            try:
                profile = client.get_user_info(handle)
            except TwitterApiIoError as exc:
                print(f"  [ERR ]  @{handle:20s}  — {exc}")
                invalid.append({"handle": handle, "tier": tier_name, "reason": str(exc)})
                continue
            if profile is None:
                print(f"  [MISS]  @{handle:20s}  ({note})")
                invalid.append({"handle": handle, "tier": tier_name, "reason": "not_found"})
                continue
            print(f"  [OK  ]  @{profile.handle:20s}  "
                  f"followers={profile.followers:>10,}  "
                  f"tweets={profile.tweet_count:>8,}  {profile.name}")
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

    # Sort by tier + handle for stable diff
    valid.sort(key=lambda v: (v["tier"], v["handle"].lower()))

    out = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "valid": valid,
        "invalid": invalid,
    }
    VALIDATED.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[x_validate_delta] valid: {len(valid)}  invalid: {len(invalid)}")
    print(f"[x_validate_delta] wrote {VALIDATED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
