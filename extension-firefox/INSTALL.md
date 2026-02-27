# Firefox Extension — Permanent Installation

The extension must be installed permanently (not via `about:debugging`) to
persist across browser restarts. Two methods are provided:

---

## Method A: Enterprise Policy (any Firefox release, recommended)

Works on stable Firefox, Developer Edition, ESR, and Nightly. Requires `sudo`.

**Step 1 — Build the extension**

```bash
# Install web-ext once
npm install -g web-ext

# Build the .xpi
./scripts/build-extensions.sh
```

**Step 2 — Install the enterprise policy**

```bash
sudo ./scripts/install-firefox-extension.sh
```

**Step 3 — Restart Firefox**

The extension will appear in `about:addons` as permanently installed with a
"Managed by your organization" label. It will survive all browser restarts and
updates.

**To uninstall:**

```bash
sudo ./scripts/install-firefox-extension.sh --uninstall
# Then remove the extension from about:addons and restart Firefox.
```

---

## Method B: Developer Edition / Nightly (no sudo required)

Works only on Firefox Developer Edition, Firefox Nightly, and Firefox ESR.
Does **not** work on stable Firefox release.

**Step 1 — Copy `user.js` to your Firefox profile**

```bash
# Find your profile folder: open about:profiles in Firefox,
# click "Open Folder" next to Root Directory for your active profile.
cp extension-firefox/user.js /path/to/your/firefox/profile/
```

**Step 2 — Restart Firefox**

**Step 3 — Build and install the extension**

```bash
./scripts/build-extensions.sh
# Drag extension-firefox/dist/docuflux_capture.xpi onto the Firefox window
```

The extension installs permanently and survives restarts.

---

## Verifying the installation

1. Open `about:addons` in Firefox
2. Go to Extensions
3. You should see **DocuFlux Capture** listed
   - Method A: shows "Managed by your organization"
   - Method B: shows as a normal user-installed extension

## Troubleshooting

**"This add-on could not be installed because it has not been verified"**
→ You are on stable Firefox. Use Method A (enterprise policy) instead.

**Extension not appearing after restart**
→ Check the policy file was written correctly:
```bash
cat /etc/firefox/policies/policies.json   # Linux
cat "/Library/Application Support/Mozilla/policies/policies.json"  # macOS
```

**Policy directory does not exist**
→ Create it: `sudo mkdir -p /etc/firefox/policies`
