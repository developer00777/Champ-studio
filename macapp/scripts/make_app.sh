#!/bin/bash
# Assemble "Champ Studio.app" from the SPM release build.
# Usage: scripts/make_app.sh [--embed-engine]
#   --embed-engine  Copy the PyInstaller engine (engine/dist/champstudio-engine)
#                   into Contents/Resources/engine/ for a self-contained app.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"

EMBED_ENGINE=0
for arg in "$@"; do
  case "$arg" in
    --embed-engine) EMBED_ENGINE=1 ;;
    *) echo "error: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

echo "==> Building release binary"
cd "$ROOT"
swift build -c release

BIN="$ROOT/.build/release/ChampStudio"
if [[ ! -x "$BIN" ]]; then
  echo "error: release binary not found at $BIN" >&2
  exit 1
fi

APP="$ROOT/Champ Studio.app"
echo "==> Assembling bundle: $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cp "$BIN" "$APP/Contents/MacOS/Champ Studio"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key>
	<string>en</string>
	<key>CFBundleExecutable</key>
	<string>Champ Studio</string>
	<key>CFBundleIdentifier</key>
	<string>com.championsgroup.champstudio</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundleName</key>
	<string>Champ Studio</string>
	<key>CFBundleDisplayName</key>
	<string>Champ Studio</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0.0</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>LSMinimumSystemVersion</key>
	<string>14.0</string>
	<key>LSUIElement</key>
	<false/>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>NSPrincipalClass</key>
	<string>NSApplication</string>
	<key>NSAppTransportSecurity</key>
	<dict>
		<key>NSAllowsLocalNetworking</key>
		<true/>
	</dict>
</dict>
</plist>
PLIST

printf 'APPL????' > "$APP/Contents/PkgInfo"

if [[ "$EMBED_ENGINE" == "1" ]]; then
  ENGINE_DIST="$REPO_ROOT/engine/dist/champstudio-engine"
  if [[ ! -x "$ENGINE_DIST/champstudio-engine" ]]; then
    echo "error: bundled engine not found at $ENGINE_DIST (build it with PyInstaller first)" >&2
    exit 1
  fi
  echo "==> Embedding engine ($(du -sh "$ENGINE_DIST" | cut -f1)) into Contents/Resources/engine"
  mkdir -p "$APP/Contents/Resources"
  ditto "$ENGINE_DIST" "$APP/Contents/Resources/engine"
fi

echo "==> Ad-hoc code signing (may take a while with an embedded engine)"
codesign --force --deep -s - "$APP"

echo "==> Done: $APP"
