# ASTRA API. Python is pinned to 3.11 to match the locked toolchain.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System libraries the geospatial stack links against in later slices.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/pyproject.toml /app/apps/api/pyproject.toml
RUN pip install --upgrade pip && pip install /app/apps/api

# The API resolves the repository root from its own location, so the layout
# inside the image mirrors the repository.
COPY apps/api /app/apps/api
COPY data /app/data
COPY scripts /app/scripts

ENV PYTHONPATH=/app/apps/api \
    ASTRA_DATA_DIR=/app/data \
    ASTRA_DB_PATH=/app/data/astra.sqlite \
    ASTRA_API_PORT=8000

EXPOSE 8000

# The integrity gate runs at startup; the container stays down on invalid data.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:${ASTRA_API_PORT}/health || exit 1

CMD ["sh", "-c", "uvicorn astra.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
