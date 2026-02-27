/**
 * DocuFlux Capture - Content Script
 *
 * Extracts page content and images from the DOM.
 * Supports Kindle Cloud Reader and generic article/main content.
 * Provides MutationObserver-based auto-capture mode.
 */

(function () {
  'use strict';

  // ─── Constants ───────────────────────────────────────────────────────────────

  const KINDLE_SELECTORS = [
    '.kg-page-text',
    '[data-page-number]',
    '#KindleReaderPage',
    '.kindleReader',
    '#book-reader',
  ];

  const GENERIC_SELECTORS = [
    'article',
    'main',
    '[role="main"]',
    '.content',
    '#content',
    '.post-content',
    '.article-body',
  ];

  const MIN_IMAGE_SIZE = 50; // px
  const AUTO_CAPTURE_DEBOUNCE = 800; // ms

  // ─── State ───────────────────────────────────────────────────────────────────

  let lastCapturedContent = '';
  let autoModeActive = false;
  let autoModeObserver = null;
  let debounceTimer = null;
  let autoModeConfig = {};

  // ─── Content Extraction ──────────────────────────────────────────────────────

  function findContentElement() {
    for (const sel of KINDLE_SELECTORS) {
      const el = document.querySelector(sel);
      if (el) return { element: el, method: 'kindle' };
    }
    for (const sel of GENERIC_SELECTORS) {
      const el = document.querySelector(sel);
      if (el && el.innerText.trim().length > 100) return { element: el, method: 'generic' };
    }
    return { element: document.body, method: 'body' };
  }

  function elementToMarkdown(el) {
    // Basic HTML-to-Markdown conversion
    let html = DOMPurify.sanitize(el.innerHTML || '', {
      ALLOWED_TAGS: ['h1','h2','h3','h4','h5','h6','p','a','img','ul','ol','li',
                     'b','strong','i','em','code','pre','blockquote','br','hr',
                     'div','span','table','thead','tbody','tr','th','td'],
      ALLOWED_ATTR: ['href','src','alt','title','class']
    });

    // Headings
    html = html.replace(/<h1[^>]*>(.*?)<\/h1>/gis, '\n# $1\n');
    html = html.replace(/<h2[^>]*>(.*?)<\/h2>/gis, '\n## $1\n');
    html = html.replace(/<h3[^>]*>(.*?)<\/h3>/gis, '\n### $1\n');
    html = html.replace(/<h4[^>]*>(.*?)<\/h4>/gis, '\n#### $1\n');

    // Strong / em
    html = html.replace(/<(strong|b)[^>]*>(.*?)<\/\1>/gis, '**$2**');
    html = html.replace(/<(em|i)[^>]*>(.*?)<\/\1>/gis, '_$2_');

    // Links
    html = html.replace(/<a[^>]*href="([^"]*)"[^>]*>(.*?)<\/a>/gis, '[$2]($1)');

    // Images — replace with placeholder; actual images captured separately
    html = html.replace(/<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*\/?>/gi, '![$2]($1)');
    html = html.replace(/<img[^>]*src="([^"]*)"[^>]*\/?>/gi, '![]($1)');

    // Paragraphs and breaks
    html = html.replace(/<br\s*\/?>/gi, '\n');
    html = html.replace(/<p[^>]*>/gi, '\n');
    html = html.replace(/<\/p>/gi, '\n');

    // Block quotes
    html = html.replace(/<blockquote[^>]*>(.*?)<\/blockquote>/gis, (_, inner) =>
      inner.trim().split('\n').map(l => `> ${l}`).join('\n')
    );

    // Code
    html = html.replace(/<code[^>]*>(.*?)<\/code>/gis, '`$1`');
    html = html.replace(/<pre[^>]*>(.*?)<\/pre>/gis, '\n```\n$1\n```\n');

    // Lists
    html = html.replace(/<li[^>]*>(.*?)<\/li>/gis, '- $1\n');
    html = html.replace(/<\/?[uo]l[^>]*>/gi, '\n');

    // Explicitly strip script/style content before generic tag removal (defence-in-depth)
    html = html.replace(/<script\b[\s\S]*?<\/script>/gi, '');
    html = html.replace(/<style\b[\s\S]*?<\/style>/gi, '');
    // Strip remaining tags (use * so empty <> sequences are also caught)
    html = html.replace(/<[^>]*>/g, '');

    // Decode HTML entities without innerHTML
    // &amp; is decoded last to prevent double-unescaping of sequences like &amp;lt;
    let text = html
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&#x27;/g, "'")
      .replace(/&nbsp;/g, ' ')
      .replace(/&#(\d+);/g, (_, c) => String.fromCharCode(parseInt(c, 10)))
      .replace(/&#x([0-9a-f]+);/gi, (_, c) => String.fromCharCode(parseInt(c, 16)))
      .replace(/&amp;/g, '&');

    // Normalise whitespace
    text = text.replace(/\n{3,}/g, '\n\n').trim();
    return text;
  }

  async function captureImages(contentElement) {
    const images = [];
    const imgEls = contentElement.querySelectorAll('img');

    for (const img of imgEls) {
      if (img.naturalWidth < MIN_IMAGE_SIZE || img.naturalHeight < MIN_IMAGE_SIZE) continue;

      const filename = `img_${Date.now()}_${images.length}.png`;
      try {
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        const b64 = canvas.toDataURL('image/png');
        images.push({ filename, b64, alt: img.alt || '' });
      } catch (e) {
        // CORS-tainted image — skip silently
        console.debug('[DocuFlux] Skipping tainted image:', img.src, e.message);
      }
    }

    return images;
  }

  // ─── Main Capture ─────────────────────────────────────────────────────────────

  async function captureCurrentPage() {
    const { element, method } = findContentElement();
    const text = elementToMarkdown(element);
    const images = await captureImages(element);

    // If content is too short (canvas-rendered pages like Kindle Cloud Reader),
    // signal the background to take a tab screenshot for OCR.
    const needsScreenshot = text.trim().length < 50;

    const pageData = {
      url: location.href,
      title: document.title,
      text,
      images,
      extraction_method: method,
      page_hint: getPageHint(),
      needs_screenshot: needsScreenshot,
    };

    lastCapturedContent = text;
    return pageData;
  }

  function getPageHint() {
    // Try Kindle page number attribute
    const el = document.querySelector('[data-page-number]');
    if (el) return parseInt(el.dataset.pageNumber, 10) || 0;
    return 0;
  }

  // ─── Auto-capture Mode ────────────────────────────────────────────────────────

  function startAutoCapture(config) {
    autoModeConfig = config;
    autoModeActive = true;

    const { element } = findContentElement();
    autoModeObserver = new MutationObserver(() => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(handleMutation, AUTO_CAPTURE_DEBOUNCE);
    });

    autoModeObserver.observe(element, { childList: true, subtree: true });
    console.log('[DocuFlux] Auto-capture started');
  }

  async function handleMutation() {
    if (!autoModeActive) return;

    const { element, method } = findContentElement();
    const text = elementToMarkdown(element);

    if (text === lastCapturedContent) return; // No real change

    const images = await captureImages(element);
    const pageData = {
      url: location.href,
      title: document.title,
      text,
      images,
      extraction_method: method,
      page_hint: getPageHint(),
    };

    chrome.runtime.sendMessage({ type: 'SUBMIT_PAGE', pageData }, response => {
      if (response?.error) {
        console.warn('[DocuFlux] Failed to submit page:', response.error);
        return;
      }
      lastCapturedContent = text;
      const pageCount = response?.page_count || 0;
      const maxPages = autoModeConfig.maxPages || 500;

      // Click next page button
      const nextSel = autoModeConfig.nextButtonSelector;
      if (nextSel) {
        const btn = document.querySelector(nextSel);
        if (btn && pageCount < maxPages) {
          btn.click();
        } else {
          stopAutoCapture();
          chrome.runtime.sendMessage({ type: 'AUTO_CAPTURE_DONE', pageCount });
        }
      }
    });
  }

  function stopAutoCapture() {
    autoModeActive = false;
    if (autoModeObserver) {
      autoModeObserver.disconnect();
      autoModeObserver = null;
    }
    clearTimeout(debounceTimer);
    console.log('[DocuFlux] Auto-capture stopped');
  }

  // ─── Message Handler ──────────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'CAPTURE_PAGE') {
      captureCurrentPage().then(sendResponse).catch(e => sendResponse({ error: e.message }));
      return true;
    }
    if (message.type === 'START_AUTO_CAPTURE') {
      startAutoCapture(message.config || {});
      sendResponse({ ok: true });
    }
    if (message.type === 'STOP_AUTO_CAPTURE') {
      stopAutoCapture();
      sendResponse({ ok: true });
    }
  });

  // Expose for scripting.executeScript
  window.__docufluxCapture = { captureCurrentPage };
})();
