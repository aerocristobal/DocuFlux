#!/usr/bin/env bash
# Build Chrome and Firefox browser extensions from unified extension-src/.
# Outputs:
#   dist/chrome/   Chrome .zip (via web-ext)
#   dist/firefox/  Firefox .xpi (via web-ext)
#
# Requirements:
#   npm install -g web-ext
#   Node.js >= 18

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
node "$REPO_ROOT/scripts/build-extension.js"
