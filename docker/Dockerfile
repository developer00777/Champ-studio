# Champ Studio — single-container image (engine + web UI + editor)
# Identical behavior on Mac, Windows (Docker Desktop/WSL2), Linux, or any cloud host.

# ── editor bundle (Vite + TypeScript); runtime image never needs Node ──
FROM node:22-slim AS editor
WORKDIR /editor
COPY web/editor-src/package.json web/editor-src/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY web/editor-src ./
RUN npm run build

# ── python dependencies (cached until pyproject.toml changes) ──
FROM python:3.12-slim AS deps

# libgl/libglib for OpenCV; ffmpeg for rendering, proxies, probes; curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY engine/pyproject.toml /tmp/pyproject.toml
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('/tmp/pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# Pre-fetch the rembg U^2-Net weights so Remove Background works offline/instantly
RUN python -c "from rembg import new_session; new_session('u2net')"

# ── dev/test image: deps + pytest; mount the repo at /src ──
FROM deps AS dev
RUN pip install --no-cache-dir "pytest>=8.0" "pytest-asyncio>=0.23"
ENV PYTHONPATH=/src/engine
WORKDIR /src/engine
CMD ["pytest", "-q"]

# ── runtime ──
FROM deps AS runtime
WORKDIR /app
COPY engine /app/engine
RUN pip install --no-cache-dir --no-deps /app/engine
COPY web /app/web
COPY --from=editor /editor/dist /app/web/editor

ENV CHAMP_DATA_DIR=/data \
    CHAMP_WEB_DIR=/app/web \
    CHAMP_HOST=0.0.0.0 \
    CHAMP_PORT=8787

VOLUME /data
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD curl -fsS http://localhost:8787/api/health || exit 1

CMD ["python", "-m", "champstudio.main"]
