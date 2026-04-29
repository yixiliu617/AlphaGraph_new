# Backend-only image for Render. Frontend deploys separately on Vercel.
#
# Build context expected: repo root (so `backend/` is a subdir).
#   docker build -f backend/Dockerfile -t alphagraph-backend .
#
# Two process types live in this image:
#   - web:    uvicorn (FastAPI)   → CMD below; Render runs this by default
#   - worker: APScheduler runner  → render.yaml overrides startCommand
#
# Why one image, two commands: same code, same deps, different entrypoint.
# Mirrors how Heroku-style Procfiles + AWS Fargate task definitions work.
# Migration to AWS later: re-tag this image, push to ECR, run on Fargate
# with the same two task definitions. No code change.

FROM python:3.13-slim-bookworm

WORKDIR /app

# System deps:
#   - build-essential + libxml2-dev + libxslt1-dev — for lxml (some scrapers use it)
#   - chromium + fonts-noto-cjk — for Playwright (Taiwan MOPS, etc.)
#   - libpq-dev — for psycopg2 (Postgres driver)
#   - tini — proper PID-1 signal handling (graceful shutdown matters for
#     APScheduler so jobs in-flight aren't killed mid-write)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev libxslt1-dev libpq-dev \
    chromium fonts-noto-cjk \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Python deps. Two-step to maximize Docker layer caching: requirements.txt
# changes far less often than app code, so layer reuse on rebuild is fast.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
    && pip install --no-cache-dir playwright \
    && playwright install chromium

# App code. Copied after deps so source-only edits don't rebuild the
# (slow) Python deps + Playwright Chromium layer.
COPY backend/ /app/backend/

# Some scrapers/extractors expect to find sibling configs / writable
# state under /app/backend/data. In production we mount a Render Disk
# (or AWS EFS later) at /app/backend/data so parquets persist across
# deploys. The Dockerfile creates the directory so the mount target
# exists even when no disk is attached (e.g. CI smoke tests).
RUN mkdir -p /app/backend/data && chmod -R u+rw /app/backend/data

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONIOENCODING=utf-8 \
    PORT=8000

EXPOSE 8000

# Tini handles SIGTERM cleanly so worker dyno restarts (deploys) don't
# leave half-written parquets or stuck DB transactions.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default: web process. The worker process overrides this in render.yaml.
# `--workers ${WEB_CONCURRENCY:-2}` lets us scale CPU per dyno via env.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
