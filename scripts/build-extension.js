#!/usr/bin/env node
// Build Chrome and Firefox browser extensions from unified extension-src/.
// Outputs:
//   dist/chrome/   → Chrome .zip (via web-ext)
//   dist/firefox/  → Firefox .xpi (via web-ext)
//
// Requirements: npm install -g web-ext

'use strict';

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'extension-src');
const DIST_CHROME = path.join(ROOT, 'dist', 'chrome');
const DIST_FIREFOX = path.join(ROOT, 'dist', 'firefox');

const SHARED_EXTENSIONS = ['.js', '.html', '.css'];
const EXCLUDED_FILES = ['vitest.config.js', 'package.json', 'package-lock.json'];

function clean(dir) {
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true });
  }
  fs.mkdirSync(dir, { recursive: true });
}

function copyFile(src, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

function copySharedFiles(destDir) {
  // Copy top-level shared files (.js, .html, .css) — exclude test/config files
  for (const entry of fs.readdirSync(SRC)) {
    const ext = path.extname(entry);
    if (SHARED_EXTENSIONS.includes(ext) && !EXCLUDED_FILES.includes(entry) && !entry.startsWith('.')) {
      copyFile(path.join(SRC, entry), path.join(destDir, entry));
    }
  }

  // Copy vendor/ files (purify.min.js, socket.io.min.js)
  const vendorDir = path.join(SRC, 'vendor');
  if (fs.existsSync(vendorDir)) {
    for (const entry of fs.readdirSync(vendorDir)) {
      copyFile(path.join(vendorDir, entry), path.join(destDir, entry));
    }
  }

  // Copy vendor files from node_modules if available (overrides vendor/)
  const nodeModules = path.join(SRC, 'node_modules');
  if (fs.existsSync(nodeModules)) {
    const purifyPath = path.join(nodeModules, 'dompurify', 'dist', 'purify.min.js');
    if (fs.existsSync(purifyPath)) {
      copyFile(purifyPath, path.join(destDir, 'purify.min.js'));
    }
    const socketPath = path.join(nodeModules, 'socket.io-client', 'dist', 'socket.io.min.js');
    if (fs.existsSync(socketPath)) {
      copyFile(socketPath, path.join(destDir, 'socket.io.min.js'));
    }
  }
}

function buildWithWebExt(sourceDir, artifactsDir, label) {
  try {
    execSync('command -v web-ext', { stdio: 'ignore' });
  } catch {
    console.error('ERROR: web-ext not found. Install with: npm install -g web-ext');
    process.exit(1);
  }

  console.log(`==> Building ${label} extension...`);
  execSync(
    `web-ext build --source-dir "${sourceDir}" --artifacts-dir "${artifactsDir}" --overwrite-dest`,
    { stdio: 'inherit' }
  );
  console.log(`    ${label} artifacts: ${artifactsDir}/`);
}

function renameFirefoxXpi(artifactsDir) {
  const files = fs.readdirSync(artifactsDir);
  const archive = files.find(f => f.endsWith('.zip') || f.endsWith('.xpi'));
  if (archive) {
    const dest = path.join(artifactsDir, 'docuflux_capture.xpi');
    fs.renameSync(path.join(artifactsDir, archive), dest);
    console.log(`    Firefox .xpi: ${dest}`);
  } else {
    console.error(`ERROR: web-ext build produced no output in ${artifactsDir}`);
    process.exit(1);
  }
}

// ─── Main ────────────────────────────────────────────────────────────────────

console.log('Cleaning dist directories...');
clean(DIST_CHROME);
clean(DIST_FIREFOX);

console.log('Copying shared files...');
copySharedFiles(DIST_CHROME);
copySharedFiles(DIST_FIREFOX);

console.log('Writing manifests...');
copyFile(path.join(SRC, 'manifest.chrome.json'), path.join(DIST_CHROME, 'manifest.json'));
copyFile(path.join(SRC, 'manifest.firefox.json'), path.join(DIST_FIREFOX, 'manifest.json'));

const chromeArtifacts = path.join(DIST_CHROME, 'web-ext-artifacts');
const firefoxArtifacts = path.join(DIST_FIREFOX, 'web-ext-artifacts');

buildWithWebExt(DIST_CHROME, chromeArtifacts, 'Chrome');
buildWithWebExt(DIST_FIREFOX, firefoxArtifacts, 'Firefox');
renameFirefoxXpi(firefoxArtifacts);

console.log('\nDone.');
console.log('To permanently install the Firefox extension, run:');
console.log(`  sudo ${path.join(ROOT, 'scripts', 'install-firefox-extension.sh')}`);
