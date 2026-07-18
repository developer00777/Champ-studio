# Champ Studio — single-container image (engine + web UI)
# Identical behavior on Mac, Windows (Docker Desktop/WSL2), Linux, or any cloud host.

FROM python:3.12-slim AS base

# libgl/libglib for OpenCV; curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY engine /app/engine
COPY web /app/web
RUN pip install --no-cache-dir /app/engine

# Pre-fetch the rembg U^2-Net weights so Remove Background works offline/instantly
RUN python -c "from rembg import new_session; new_session('u2net')"

ENV CHAMP_DATA_DIR=/data \
    CHAMP_WEB_DIR=/app/web \
    CHAMP_HOST=0.0.0.0 \
    CHAMP_PORT=8787

VOLUME /data
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD curl -fsS http://localhost:8787/api/health || exit 1

CMD ["python", "-m", "champstudio.main"]
