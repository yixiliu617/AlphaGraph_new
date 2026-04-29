"""
Storage abstraction — the lever that makes Render → AWS migration cheap.

Why this exists:

  Most code in this project does Path("backend/data/financials/prices/NVDA.parquet").
  When deploying to Render that path is on the Render Disk; when migrating
  to AWS it could be EFS or S3. We want the SAME code to work in all three
  modes by changing one env var.

  This module provides:
    - data_path(*parts)  → absolute path under the configured DATA_DIR
    - read_bytes(key)    → bytes (filesystem OR S3, transparent)
    - write_bytes(key,b) → idempotent write
    - read_parquet(key)  → pd.DataFrame
    - write_parquet(key, df) → idempotent overwrite
    - exists(key)        → bool
    - list_keys(prefix)  → iterator over keys

  Where `key` is a logical path like "financials/prices/NVDA.parquet"
  (no leading slash, no `backend/data/` prefix).

Backends:
  - "fs"    (default) — DATA_DIR on local filesystem (or mounted disk)
  - "s3"    — boto3 against any S3-compatible endpoint (Backblaze B2, AWS S3,
              MinIO). Set S3_ENDPOINT_URL for non-AWS.

Migration paths:
  - localhost dev → no change; DATA_DIR defaults to backend/data.
  - Render pilot  → set DATA_DIR=/app/backend/data + mount Render Disk there.
                    OR flip STORAGE_BACKEND=s3 + Backblaze B2 from day 1.
  - AWS Fargate   → set STORAGE_BACKEND=s3 + AWS S3. Same code, different env.

The current code-base ALREADY does Path("backend/data/...") all over. We do
NOT bulk-rewrite those — that's a high-risk refactor with little immediate
payoff. Instead:

  - New code uses storage.data_path() / storage.read_parquet().
  - Existing code keeps working because DATA_DIR defaults to the legacy
    relative path "backend/data" and we run with cwd=repo-root.
  - When AWS migration time comes, do the mass refactor in one PR; it's
    mechanical (replace `Path("backend/data/...")` with
    `storage.data_path("...")`). The abstraction is here waiting.
"""
from __future__ import annotations

import io
import os
from functools import lru_cache
from pathlib import Path
from typing import IO, Iterator, Optional, Union

import pandas as pd


# ---------------------------------------------------------------------------
# Configuration — all env-driven so behavior changes without code changes
# ---------------------------------------------------------------------------

def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name, default)
    return v.strip() if v else v


STORAGE_BACKEND = (_env("STORAGE_BACKEND") or "fs").lower()
DATA_DIR        = Path(_env("DATA_DIR") or "backend/data")
S3_BUCKET       = _env("S3_BUCKET")
S3_PREFIX       = _env("S3_PREFIX") or ""
S3_ENDPOINT_URL = _env("S3_ENDPOINT_URL")          # None → default AWS endpoint
S3_REGION       = _env("S3_REGION") or "us-east-1"


# ---------------------------------------------------------------------------
# Filesystem helpers — work for both fs backend AND for code that still
# reaches into Path("backend/data/...") directly.
# ---------------------------------------------------------------------------

def data_path(*parts: str) -> Path:
    """Returns the absolute path of `<DATA_DIR>/<*parts>`.

    Use this in NEW code. Existing code that uses Path("backend/data/...")
    keeps working because DATA_DIR defaults to "backend/data".

    Example:
        data_path("financials", "prices", "NVDA.parquet")
        # → backend/data/financials/prices/NVDA.parquet (dev)
        # → /app/backend/data/financials/prices/NVDA.parquet (Render Disk)
    """
    return DATA_DIR.joinpath(*parts)


# ---------------------------------------------------------------------------
# S3 backend — lazy-loaded so dev doesn't need boto3
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _s3_client():
    """boto3 S3 client. Cached because client init costs ~50ms."""
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError(
            "STORAGE_BACKEND=s3 requires `boto3`. Add it to requirements.txt: "
            "boto3>=1.34"
        ) from e
    kwargs = {"region_name": S3_REGION}
    if S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = S3_ENDPOINT_URL
    return boto3.client("s3", **kwargs)


def _s3_key(key: str) -> str:
    """Apply S3_PREFIX to logical keys."""
    if S3_PREFIX:
        return f"{S3_PREFIX.rstrip('/')}/{key.lstrip('/')}"
    return key.lstrip("/")


# ---------------------------------------------------------------------------
# Public API — backend-agnostic
# ---------------------------------------------------------------------------

def read_bytes(key: str) -> bytes:
    """Read raw bytes for a logical key. Raises FileNotFoundError if absent."""
    if STORAGE_BACKEND == "s3":
        if not S3_BUCKET:
            raise RuntimeError("STORAGE_BACKEND=s3 but S3_BUCKET unset")
        try:
            obj = _s3_client().get_object(Bucket=S3_BUCKET, Key=_s3_key(key))
        except _s3_client().exceptions.NoSuchKey as e:
            raise FileNotFoundError(key) from e
        return obj["Body"].read()
    p = data_path(key)
    return p.read_bytes()


def write_bytes(key: str, data: bytes) -> None:
    """Idempotent overwrite. Creates parent dirs on filesystem backend."""
    if STORAGE_BACKEND == "s3":
        if not S3_BUCKET:
            raise RuntimeError("STORAGE_BACKEND=s3 but S3_BUCKET unset")
        _s3_client().put_object(Bucket=S3_BUCKET, Key=_s3_key(key), Body=data)
        return
    p = data_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def exists(key: str) -> bool:
    """True if the key resolves to a real object."""
    if STORAGE_BACKEND == "s3":
        try:
            _s3_client().head_object(Bucket=S3_BUCKET, Key=_s3_key(key))
            return True
        except Exception:
            return False
    return data_path(key).exists()


def read_parquet(
    key: str, *, columns: Optional[list[str]] = None
) -> pd.DataFrame:
    """Read a parquet file by logical key. Use this instead of pd.read_parquet
    for new code — it transparently handles both FS and S3 backends.

    For S3 we fetch via get_object and pass to pyarrow via BytesIO. For very
    large files, future optimization: pass the s3:// URI to pyarrow directly
    (uses range requests, lower memory). Not needed at current scale.
    """
    if STORAGE_BACKEND == "s3":
        return pd.read_parquet(io.BytesIO(read_bytes(key)), columns=columns)
    return pd.read_parquet(data_path(key), columns=columns)


def write_parquet(
    key: str, df: pd.DataFrame, *, compression: str = "snappy",
) -> None:
    """Write a DataFrame to parquet at a logical key. Idempotent overwrite."""
    if STORAGE_BACKEND == "s3":
        buf = io.BytesIO()
        df.to_parquet(buf, compression=compression, index=False)
        write_bytes(key, buf.getvalue())
        return
    p = data_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, compression=compression, index=False)


def list_keys(prefix: str) -> Iterator[str]:
    """Yield all logical keys under a prefix.

    On FS: relative paths under DATA_DIR/prefix.
    On S3: ListObjectsV2 with prefix.
    """
    if STORAGE_BACKEND == "s3":
        paginator = _s3_client().get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=S3_BUCKET, Prefix=_s3_key(prefix),
        ):
            for obj in page.get("Contents", []) or []:
                yield obj["Key"][len(_s3_key("")) :].lstrip("/")
        return
    base = data_path(prefix)
    if not base.exists():
        return
    if base.is_file():
        yield prefix
        return
    for p in base.rglob("*"):
        if p.is_file():
            yield str(p.relative_to(DATA_DIR)).replace("\\", "/")


# ---------------------------------------------------------------------------
# Diagnostics — call from a /admin/storage endpoint to confirm config
# ---------------------------------------------------------------------------

def describe() -> dict:
    out = {
        "backend":  STORAGE_BACKEND,
        "data_dir": str(DATA_DIR.resolve()) if STORAGE_BACKEND == "fs" else None,
        "s3_bucket": S3_BUCKET if STORAGE_BACKEND == "s3" else None,
        "s3_prefix": S3_PREFIX if STORAGE_BACKEND == "s3" else None,
        "s3_endpoint": S3_ENDPOINT_URL if STORAGE_BACKEND == "s3" else None,
        "s3_region": S3_REGION if STORAGE_BACKEND == "s3" else None,
    }
    return out
