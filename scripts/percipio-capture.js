#!/usr/bin/env node
/**
 * DocuFlux - Percipio EPUB Capture via Playwright CDP
 *
 * Connects to an already-running Chrome instance (with the DocuFlux extension loaded)
 * and orchestrates page-by-page capture of a Percipio EPUB book.
 *
 * Playwright dispatches real input events via CDP that cross iframe boundaries,
 * while the extension's content script extracts page content and writes results
 * to DOM attributes (the "DOM attribute bridge").
 *
 * Prerequisites:
 *   1. Launch Chrome with: google-chrome --remote-debugging-port=9222
 *   2. Log into Percipio in that browser (SSO/SAML)
 *   3. Install the DocuFlux extension
 *   4. Navigate to the target Percipio EPUB book
 *
 * Usage:
 *   node scripts/percipio-capture.js [options]
 *
 * Options:
 *   --cdp-url     CDP endpoint (default: http://localhost:9222)
 *   --max-pages   Maximum pages to capture (default: 200)
 *   --output      Output file path (default: stdout)
 *   --delay       Delay between page turns in ms (default: 2000)
 *   --method      Page turn method: arrow | click-right (default: arrow)
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// ─── CLI Arguments ──────────────────────────────────────────────────────────

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    cdpUrl: 'http://localhost:9222',
    maxPages: 200,
    output: null,
    delay: 2000,
    method: 'arrow',
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--cdp-url':   opts.cdpUrl = args[++i]; break;
      case '--max-pages': opts.maxPages = parseInt(args[++i], 10); break;
      case '--output':    opts.output = args[++i]; break;
      case '--delay':     opts.delay = parseInt(args[++i], 10); break;
      case '--method':    opts.method = args[++i]; break;
      case '--help':
        console.log(`Usage: node ${path.basename(__filename)} [options]
  --cdp-url <url>    CDP endpoint (default: http://localhost:9222)
  --max-pages <n>    Max pages to capture (default: 200)
  --output <path>    Output file (default: stdout)
  --delay <ms>       Delay between turns (default: 2000)
  --method <method>  arrow | click-right (default: arrow)`);
        process.exit(0);
    }
  }
  return opts;
}

// ─── Core Logic ─────────────────────────────────────────────────────────────

async function findPercipioPage(browser) {
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      const url = page.url();
      if (url.includes('percipio.com')) return page;
    }
  }
  return null;
}

async function requestCapture(page, timeout = 15000) {
  const ts = Date.now().toString();

  // Set the request attribute — the extension's MutationObserver picks this up
  await page.evaluate((t) => {
    document.body.dataset.docufluxCaptureRequest = t;
  }, ts);

  // Wait for the extension to write the result with a matching timestamp
  const resultJson = await page.waitForFunction((expectedTs) => {
    const raw = document.body.dataset.docufluxCaptureResult;
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      if (parsed.ts === expectedTs) return raw;
    } catch { /* ignore parse errors */ }
    return null;
  }, ts, { timeout });

  return JSON.parse(await resultJson.jsonValue());
}

async function advancePage(page, method) {
  if (method === 'click-right') {
    // Click the right 75% of the viewport to advance
    const viewport = page.viewportSize() || { width: 1280, height: 720 };
    await page.mouse.click(viewport.width * 0.75, viewport.height / 2);
  } else {
    // Default: ArrowRight key — CDP delivers real key events that cross iframes
    await page.keyboard.press('ArrowRight');
  }
}

async function main() {
  const opts = parseArgs();
  let browser;

  // Connect to existing Chrome via CDP
  try {
    browser = await chromium.connectOverCDP(opts.cdpUrl);
  } catch (e) {
    console.error(`Failed to connect to Chrome at ${opts.cdpUrl}`);
    console.error('Make sure Chrome is running with: google-chrome --remote-debugging-port=9222');
    console.error(`Error: ${e.message}`);
    process.exit(1);
  }

  console.error(`Connected to Chrome via CDP at ${opts.cdpUrl}`);

  // Find the Percipio tab
  const page = await findPercipioPage(browser);
  if (!page) {
    console.error('No Percipio tab found. Navigate to a Percipio EPUB book first.');
    await browser.close();
    process.exit(1);
  }

  console.error(`Found Percipio page: ${page.url()}`);

  const pages = [];
  let duplicateCount = 0;
  const MAX_DUPLICATES = 3; // Stop after 3 consecutive duplicate pages (end of book)

  for (let i = 0; i < opts.maxPages; i++) {
    try {
      const result = await requestCapture(page);

      if (!result.text || result.text.length < 20) {
        console.error(`  Page ${i + 1}: no text extracted (method: ${result.method})`);
        // Still advance — might be an image-only page
      } else {
        // Check for duplicate (end-of-book detection)
        const lastPage = pages[pages.length - 1];
        if (lastPage && lastPage.text === result.text) {
          duplicateCount++;
          console.error(`  Page ${i + 1}: duplicate content (${duplicateCount}/${MAX_DUPLICATES})`);
          if (duplicateCount >= MAX_DUPLICATES) {
            console.error('End of book detected (consecutive duplicates). Stopping.');
            break;
          }
        } else {
          duplicateCount = 0;
          pages.push(result);
          console.error(`  Page ${i + 1}: captured ${result.text.length} chars (method: ${result.method})`);
        }
      }
    } catch (e) {
      console.error(`  Page ${i + 1}: capture failed — ${e.message}`);
    }

    // Advance to next page
    if (i < opts.maxPages - 1) {
      await advancePage(page, opts.method);
      await new Promise(r => setTimeout(r, opts.delay));
    }
  }

  // Assemble output
  const output = pages.map((p, i) =>
    `<!-- Page ${i + 1} -->\n\n${p.text}`
  ).join('\n\n---\n\n');

  if (opts.output) {
    fs.writeFileSync(opts.output, output, 'utf-8');
    console.error(`Wrote ${pages.length} pages to ${opts.output}`);
  } else {
    process.stdout.write(output);
  }

  console.error(`Done. Captured ${pages.length} pages.`);
  await browser.close();
}

main().catch(e => {
  console.error(`Fatal: ${e.message}`);
  process.exit(1);
});
