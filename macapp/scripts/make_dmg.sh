#!/bin/bash
# Build a distributable DMG containing the self-contained "Champ Studio.app"
# (with the embedded PyInstaller engine), an /Applications symlink, and a
# first-run README.
# Output: <repo root>/dist/ChampStudio-1.0.0.dmg
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"

VERSION="1.0.0"
APP="$ROOT/Champ Studio.app"
DIST_DIR="$REPO_ROOT/dist"
DMG="$DIST_DIR/ChampStudio-$VERSION.dmg"

echo "==> Building app with embedded engine"
"$SCRIPT_DIR/make_app.sh" --embed-engine

echo "==> Staging DMG contents"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

ditto "$APP" "$STAGING/Champ Studio.app"
ln -s /Applications "$STAGING/Applications"

cat > "$STAGING/READ ME FIRST.txt" <<'EOF'
Champ Studio — first run
========================

1. Drag "Champ Studio" onto the Applications folder icon.

2. FIRST OPEN: right-click (or Control-click) the app in Applications
   and choose Open, then click Open in the dialog. This is needed once
   because the app is ad-hoc signed (not notarized). After that it
   opens normally.

3. The engine starts automatically with the app. On first launch it
   can take about 30 seconds — the sidebar shows "Starting engine…"
   until it is ready.

4. Add your OpenRouter API key in Settings (and a MuAPI key if you
   have one), then hit Save Keys. You are ready to Create.

Questions? Ask the Champ team.
EOF

echo "==> Creating DMG"
mkdir -p "$DIST_DIR"
rm -f "$DMG"
hdiutil create \
  -volname "Champ Studio" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$DMG"

echo "==> Done: $DMG"
du -sh "$DMG"
