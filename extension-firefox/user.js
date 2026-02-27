/**
 * Firefox Developer Edition / Nightly preference override.
 *
 * Allows installing the unsigned DocuFlux extension without enterprise policy.
 * Only works on Firefox Developer Edition, Nightly, or ESR (not stable release).
 *
 * Usage:
 *   1. Find your Firefox profile folder: about:profiles → Root Directory → Open Folder
 *   2. Copy this file into that folder (alongside prefs.js)
 *   3. Restart Firefox
 *   4. Build the extension: ./scripts/build-extensions.sh
 *   5. Drag extension-firefox/dist/docuflux_capture.xpi onto the Firefox window
 *   6. The extension installs permanently (survives restarts)
 *
 * For stable Firefox: use the enterprise policy method instead.
 *   See: INSTALL.md
 */
user_pref("xpinstall.signatures.required", false);
