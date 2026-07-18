#!/bin/zsh
# Start the Champ Studio engine locally (web UI at http://127.0.0.1:8787).
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR/engine"
[ -x .venv/bin/python ] || { echo "Run scripts/install-mac.sh first"; exit 1; }
exec .venv/bin/python -m champstudio.main
