# Multi-stage build: Node for the frontend, Python for backend + scheduler.
FROM node:20-bookworm-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
WORKDIR /app

# System deps for lxml, playwright, fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libxml2-dev libxslt1-dev \
    chromium fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
    && pip install --no-cache-dir playwright \
    && playwright install chromium

# Copy source + frontend build
COPY backend/ /app/backend/
COPY --from=frontend-builder /app/frontend/.next /app/frontend/.next
COPY --from=frontend-builder /app/frontend/public /app/frontend/public
COPY --from=frontend-builder /app/frontend/package.json /app/frontend/

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Default process entrypoint — fly.toml overrides per-process.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
