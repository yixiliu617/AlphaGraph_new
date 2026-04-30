"""
One-shot migration: copy on-disk parquets/files to S3-compatible storage.

Use case:
  - You're moving from Render Disk (or local FS) to Backblaze B2 / AWS S3
  - You want both web + worker dynos to share the same persistent storage
  - You're prepping for AWS migration (S3 is the AWS-native answer)

Run modes:
  --dry-run      Walk the local tree, show what would be uploaded, no writes
  --commit       Actually upload to S3 (default is --dry-run for safety)
  --filter PFX   Only migrate keys starting with this prefix (e.g. "financials/prices/")
  --skip-existing  (default true) skip keys already present in S3 by name+size

Required env vars (set via Render dashboard or shell):
  STORAGE_BACKEND=s3
  S3_BUCKET=alphagraph-data
  S3_ENDPOINT_URL=https://s3.us-west-002.backblazeb2.com   (B2; blank for AWS S3)
  S3_REGION=us-west-002
  S3_ACCESS_KEY_ID=<from B2 App Key>
  S3_SECRET_ACCESS_KEY=<from B2 App Key>

Local dev:
  ALPHAGRAPH_DATA_DIR=backend/data python -m backend.scripts.migrate_data_to_s3 --dry-run

Render Shell:
  cd /app && python -m backend.scripts.migrate_data_to_s3 --commit

Estimated time: ~1-3 minutes for 1 GB of parquets at typical broadband.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Load .env when run outside FastAPI context.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name, default)
    return v.strip() if v else v


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="Actually upload (default is dry-run)")
    parser.add_argument("--filter", default="",
                        help="Only migrate keys with this prefix")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="Skip keys already in S3 with matching size (default true)")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "backend/data"),
                        help="Local data root to walk (default: $DATA_DIR or backend/data)")
    args = parser.parse_args()

    bucket   = _env("S3_BUCKET")
    endpoint = _env("S3_ENDPOINT_URL")
    region   = _env("S3_REGION") or "us-east-1"
    prefix   = _env("S3_PREFIX") or ""

    if not bucket:
        print("[ERROR] S3_BUCKET not set", file=sys.stderr)
        print("Set the four S3_* env vars from .env.production.example before running.",
              file=sys.stderr)
        return 2

    try:
        import boto3
    except ImportError:
        print("[ERROR] boto3 not installed. Run: pip install 'boto3>=1.34'", file=sys.stderr)
        return 2

    s3_kwargs = {"region_name": region}
    if endpoint:
        s3_kwargs["endpoint_url"] = endpoint
    s3 = boto3.client("s3", **s3_kwargs)

    data_root = Path(args.data_dir).resolve()
    if not data_root.exists():
        print(f"[ERROR] data root not found: {data_root}", file=sys.stderr)
        return 2

    print(f"Migration plan:")
    print(f"  data root:    {data_root}")
    print(f"  bucket:       s3://{bucket}/{prefix}")
    print(f"  endpoint:     {endpoint or '(default AWS)'}")
    print(f"  region:       {region}")
    print(f"  filter:       {args.filter or '(all)'}")
    print(f"  mode:         {'COMMIT' if args.commit else 'DRY-RUN (no uploads)'}")
    print()

    # Build local file inventory
    files = []
    for p in data_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(data_root).as_posix()
        if args.filter and not rel.startswith(args.filter):
            continue
        files.append((rel, p, p.stat().st_size))

    if not files:
        print("[INFO] no files matched", file=sys.stderr)
        return 0

    total_bytes = sum(s for _, _, s in files)
    print(f"Found {len(files)} files ({total_bytes / 1e6:.1f} MB total)")

    # Build S3 inventory of existing keys for skip-existing
    existing: dict[str, int] = {}
    if args.skip_existing:
        print("[INFO] enumerating existing S3 keys...", file=sys.stderr)
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents") or []:
                k = obj["Key"]
                if prefix and k.startswith(prefix):
                    k = k[len(prefix):].lstrip("/")
                existing[k] = obj["Size"]
        print(f"[INFO] {len(existing)} keys already in bucket")

    uploaded = 0
    skipped = 0
    failed = 0
    bytes_uploaded = 0

    for i, (rel, src, size) in enumerate(files, 1):
        key = f"{prefix.rstrip('/')}/{rel}" if prefix else rel

        if args.skip_existing and rel in existing and existing[rel] == size:
            skipped += 1
            if i % 50 == 0:
                print(f"  [{i}/{len(files)}] skipped {rel} (already exists, same size)")
            continue

        if not args.commit:
            print(f"  [{i}/{len(files)}] DRY-RUN would upload {rel} ({size:,} bytes)")
            uploaded += 1
            bytes_uploaded += size
            continue

        try:
            with src.open("rb") as f:
                s3.put_object(Bucket=bucket, Key=key, Body=f)
            uploaded += 1
            bytes_uploaded += size
            if i % 25 == 0 or size > 5 * 1024 * 1024:
                print(f"  [{i}/{len(files)}] uploaded {rel} ({size:,} bytes)")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(files)}] FAILED {rel}: {e}", file=sys.stderr)

    print()
    print(f"=== Summary ===")
    print(f"  uploaded:      {uploaded} ({bytes_uploaded / 1e6:.1f} MB)")
    print(f"  skipped:       {skipped} (already in bucket, same size)")
    print(f"  failed:        {failed}")
    if not args.commit:
        print()
        print("DRY-RUN complete. To actually upload, re-run with --commit")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
