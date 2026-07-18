#!/bin/zsh
# Champ Studio — one-shot Mac installer (engine + Mac app).
# Requires: macOS 14+, Xcode Command Line Tools (for the app), internet.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "▸ Champ Studio installer"

# 1. uv (fast Python manager with self-contained CPython)
if ! command -v uv >/dev/null 2>&1; then
  echo "▸ Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Engine venv (uv-managed CPython 3.12 — avoids broken system/pyenv builds)
echo "▸ Setting up engine (Python 3.12)..."
cd "$REPO_DIR/engine"
uv venv -q --python-preference only-managed -p 3.12 .venv
uv pip install -q -p .venv/bin/python -e .

# 3. Pre-fetch background-removal model weights (~170 MB, one time)
echo "▸ Fetching Remove-Background model weights..."
.venv/bin/python -c "from rembg import new_session; new_session('u2net')" || \
  echo "  (skipped — will download on first use)"

# 4. Mac app (optional — needs swift)
if command -v swift >/dev/null 2>&1; then
  echo "▸ Building Champ Studio.app..."
  cd "$REPO_DIR/macapp"
  swift build -c release
  ./scripts/make_app.sh release
  echo "▸ App at: $REPO_DIR/macapp/dist/Champ Studio.app  (drag to /Applications if you like)"
else
  echo "▸ swift not found — skipping Mac app (engine + web UI still fully usable)"
fi

echo ""
echo "✔ Done. Start the engine with:  $REPO_DIR/scripts/run-local.sh"
echo "  Web UI:  http://127.0.0.1:8787   (add your OpenRouter key in Settings)"
