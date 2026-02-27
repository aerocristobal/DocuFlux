#!/usr/bin/env bash
# Permanently install the Firefox extension via enterprise policy.
# This does NOT require AMO signing and works on any Firefox release.
#
# Run as root (sudo) on Linux or macOS.
# After running, restart Firefox — the extension will appear in about:addons.
#
# Usage:
#   sudo ./scripts/install-firefox-extension.sh
#   sudo ./scripts/install-firefox-extension.sh --uninstall

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XPI_PATH="$REPO_ROOT/extension-firefox/dist/docuflux_capture.xpi"
EXT_ID="docuflux-capture@docuflux"

# Platform-specific policy directory
case "$(uname -s)" in
  Linux*)
    POLICY_DIR="/etc/firefox/policies"
    ;;
  Darwin*)
    POLICY_DIR="/Library/Application Support/Mozilla/policies"
    ;;
  *)
    echo "ERROR: Unsupported platform. Set POLICY_DIR manually." >&2
    exit 1
    ;;
esac

POLICY_FILE="$POLICY_DIR/policies.json"

if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ -f "$POLICY_FILE" ]]; then
    rm -f "$POLICY_FILE"
    echo "Removed $POLICY_FILE"
    echo "Restart Firefox and remove the extension from about:addons."
  else
    echo "No policy file found at $POLICY_FILE — nothing to uninstall."
  fi
  exit 0
fi

# Verify .xpi exists
if [[ ! -f "$XPI_PATH" ]]; then
  echo "ERROR: Extension not built. Run first:" >&2
  echo "  ./scripts/build-extensions.sh" >&2
  exit 1
fi

mkdir -p "$POLICY_DIR"

cat > "$POLICY_FILE" <<EOF
{
  "policies": {
    "ExtensionSettings": {
      "$EXT_ID": {
        "installation_mode": "force_installed",
        "install_url": "file://$XPI_PATH"
      }
    }
  }
}
EOF

echo "Wrote enterprise policy to: $POLICY_FILE"
echo ""
echo "Extension ID : $EXT_ID"
echo "XPI path     : $XPI_PATH"
echo ""
echo "Next step: restart Firefox."
echo "The extension will appear in about:addons as permanently installed."
echo ""
echo "To uninstall: sudo $REPO_ROOT/scripts/install-firefox-extension.sh --uninstall"
