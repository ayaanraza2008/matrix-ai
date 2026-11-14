# Multi-stage production container for MATRIX AI
# Security posture:
# - Minimal debian slim base
# - Non-root execution under UID/GID 10001
# - Zero secrets baked into image
# - Healthcheck verified via loopback /api/health
# - Persistent volume mount at /data

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MATRIX_HOME=/data \
    MATRIX_HOST=0.0.0.0 \
    MATRIX_PORT=8765 \
    MATRIX_ALLOW_WEB_FETCH=false \
    MATRIX_ALLOW_CLOUD_MODELS=false

# Install minimal OS dependencies for health check, sqlite3, and secure networking
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user and group
RUN groupadd -g 10001 matrix && \
    useradd -u 10001 -g matrix -m -s /bin/bash -d /matrix matrix && \
    mkdir -p /data && \
    chown -R matrix:matrix /data

WORKDIR /app

# Install dependencies first for optimal caching
COPY --chown=matrix:matrix pyproject.toml README.md ./
COPY --chown=matrix:matrix src/ ./src/

RUN pip install --no-cache-dir .

# Switch to non-root user
USER matrix:matrix

EXPOSE 8765

VOLUME ["/data"]

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://127.0.0.1:8765/api/health || exit 1

ENTRYPOINT ["python", "-m", "matrix_ai", "serve", "--host", "0.0.0.0", "--port", "8765"]
