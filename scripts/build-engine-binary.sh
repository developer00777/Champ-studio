#!/bin/zsh
# Build a self-contained engine binary (PyInstaller one-dir) for bundling into
# the Mac app. Output: engine/dist/champstudio-engine/
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR/engine"

[ -x .venv/bin/python ] || { echo "engine venv missing — run scripts/install-mac.sh"; exit 1; }
uv pip install -q -p .venv/bin/python pyinstaller

rm -rf build dist
.venv/bin/pyinstaller --noconfirm --onedir --name champstudio-engine \
  --collect-all rembg \
  --collect-all onnxruntime \
  --collect-all vtracer \
  --collect-all skimage \
  --collect-all cv2 \
  --collect-all pooch \
  --hidden-import champstudio \
  --add-data "$REPO_DIR/web:web" \
  pyi_entry.py

echo "✔ Binary at engine/dist/champstudio-engine/champstudio-engine"
