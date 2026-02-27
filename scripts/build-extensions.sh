#!/usr/bin/env bash
# Build Chrome and Firefox browser extensions using web-ext.
# Outputs:
#   extension/web-ext-artifacts/          Chrome .zip
#   extension-firefox/dist/               Firefox .xpi
#
# Requirements:
#   npm install -g web-ext

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHROME_DIR="$REPO_ROOT/extension"
FIREFOX_DIR="$REPO_ROOT/extension-firefox"
FIREFOX_DIST="$FIREFOX_DIR/dist"

if ! command -v web-ext &>/dev/null; then
  echo "ERROR: web-ext not found. Install with: npm install -g web-ext" >&2
  exit 1
fi

echo "==> Building Chrome extension..."
web-ext build \
  --source-dir "$CHROME_DIR" \
  --artifacts-dir "$CHROME_DIR/web-ext-artifacts" \
  --overwrite-dest \
  --ignore-files "manifest-firefox.json" "web-ext-artifacts/**"
echo "    Chrome .zip: $CHROME_DIR/web-ext-artifacts/"

echo "==> Building Firefox extension..."
mkdir -p "$FIREFOX_DIST"
web-ext build \
  --source-dir "$FIREFOX_DIR" \
  --artifacts-dir "$FIREFOX_DIST" \
  --overwrite-dest \
  --ignore-files "dist/**" "user.js" "INSTALL.md"

# Rename to stable filename for policy install script
XPI_SRC=$(ls "$FIREFOX_DIST"/*.zip 2>/dev/null | head -1 || ls "$FIREFOX_DIST"/*.xpi 2>/dev/null | head -1)
if [[ -n "$XPI_SRC" ]]; then
  XPI_DEST="$FIREFOX_DIST/docuflux_capture.xpi"
  mv "$XPI_SRC" "$XPI_DEST"
  echo "    Firefox .xpi: $XPI_DEST"
else
  echo "ERROR: web-ext build produced no output in $FIREFOX_DIST" >&2
  exit 1
fi

echo ""
echo "Done. To permanently install the Firefox extension, run:"
echo "  sudo $REPO_ROOT/scripts/install-firefox-extension.sh"
