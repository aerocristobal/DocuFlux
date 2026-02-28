/**
 * DocuFlux Capture - Content Script
 *
 * Extracts page content and images from the DOM.
 * Supports Kindle Cloud Reader and generic article/main content.
 * Provides MutationObserver-based auto-capture mode (DOM readers) and
 * screenshot-comparison polling mode (canvas readers like Kindle Cloud Reader).
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

  const MIN_IMAGE_SIZE = 50;            // px — minimum image dimension to include
  const AUTO_CAPTURE_DEBOUNCE = 800;    // ms — DOM mutation debounce
  const PAGE_TURN_POLL_MS = 1500;       // ms — screenshot poll interval (canvas mode)
  const PAGE_TURN_TIMEOUT_MS = 8000;    // ms — give up waiting for page turn
  const MAX_AUTO_RETRIES = 3;           // max submit retries before stopping auto-capture

  // ─── State ───────────────────────────────────────────────────────────────────

  let lastCapturedContent = '';
  let autoModeActive = false;
  let autoModeObserver = null;
  let debounceTimer = null;
  let pageTurnTimer = null;
  let screenshotPollInterval = null;
  let autoModeConfig = {};
  let autoRetryCount = 0;
  let lastScreenshotHash = null;

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

    // Strip remaining tags and decode HTML entities via the browser DOM API.
    // A detached element strips all tags through textContent without regex
    // edge-cases (partial tags, bad tag filters, double-unescaping). The
    // element is never inserted into the document so no scripts execute.
    const tmpDiv = document.createElement('div');
    tmpDiv.innerHTML = html;
    let text = (tmpDiv.textContent || tmpDiv.innerText || '')
      .replace(/\u00A0/g, ' ');  // normalise non-breaking spaces (&nbsp;)

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

  // ─── Page Advancement ────────────────────────────────────────────────────────

  /**
   * Attempt to advance to the next page using the configured method.
   * Returns the method used ('click' | 'key' | 'area' | null).
   */
  function advancePage(config) {
    const method = config.nextMethod || 'selector';

    // CSS selector click
    if (method === 'selector' && config.nextButtonSelector) {
      const btn = document.querySelector(config.nextButtonSelector);
      if (btn) {
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        return 'click';
      }
    }

    // Keyboard: ArrowRight (default for Kindle)
    if (method === 'key-right') {
      document.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowRight', keyCode: 39, which: 39,
        bubbles: true, cancelable: true
      }));
      return 'key';
    }

    // Keyboard: Space
    if (method === 'key-space') {
      document.dispatchEvent(new KeyboardEvent('keydown', {
        key: ' ', keyCode: 32, which: 32,
        bubbles: true, cancelable: true
      }));
      return 'key';
    }

    // Area click: right 75% of viewport
    if (method === 'area-click') {
      const el = document.elementFromPoint(window.innerWidth * 0.75, window.innerHeight / 2);
      if (el) {
        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        return 'area';
      }
    }

    return null;
  }

  // ─── Screenshot Helpers ───────────────────────────────────────────────────────

  function requestScreenshot() {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: 'REQUEST_SCREENSHOT' }, response => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (response?.error) return reject(new Error(response.error));
        resolve(response?.dataUrl || null);
      });
    });
  }

  /** Simple hash of a data URL string for change detection. */
  function hashString(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) {
      h = (Math.imul(31, h) + str.charCodeAt(i)) | 0;
    }
    return h;
  }

  // ─── Auto-capture Mode ────────────────────────────────────────────────────────

  async function startAutoCapture(config) {
    autoModeConfig = config;
    autoModeActive = true;
    autoRetryCount = 0;

    // Detect canvas pages by capturing first page
    const firstPageData = await captureCurrentPage();
    const isCanvasPage = firstPageData.needs_screenshot;

    // Submit first page
    chrome.runtime.sendMessage({ type: 'SUBMIT_PAGE', pageData: firstPageData }, response => {
      if (!autoModeActive) return;
      if (response?.error) {
        console.warn('[DocuFlux] Failed to submit first page:', response.error);
        stopAutoCapture();
        chrome.runtime.sendMessage({ type: 'AUTO_CAPTURE_ERROR', reason: 'submit_failed' });
        return;
      }

      const pageCount = response?.page_count || 1;
      const maxPages = config.maxPages || 100;
      if (pageCount >= maxPages) {
        stopAutoCapture();
        chrome.runtime.sendMessage({ type: 'AUTO_CAPTURE_DONE', pageCount });
        return;
      }

      if (isCanvasPage) {
        // Canvas mode: use screenshot polling
        requestScreenshot().then(dataUrl => {
          lastScreenshotHash = dataUrl ? hashString(dataUrl) : null;
          advancePage(config);
          startScreenshotPoll(pageCount, maxPages);
        }).catch(() => {
          // No screenshot capability — fall through to DOM mode
          startDomObserver();
          advancePage(config);
          armPageTurnTimeout();
        });
      } else {
        // DOM mode: use MutationObserver
        startDomObserver();
        advancePage(config);
        armPageTurnTimeout();
      }
    });

    console.log('[DocuFlux] Auto-capture started');
  }

  function startDomObserver() {
    const { element } = findContentElement();
    autoModeObserver = new MutationObserver(() => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(handleMutation, AUTO_CAPTURE_DEBOUNCE);
    });
    autoModeObserver.observe(element, { childList: true, subtree: true });
  }

  function startScreenshotPoll(currentPageCount, maxPages) {
    if (!autoModeActive) return;
    clearInterval(screenshotPollInterval);
    screenshotPollInterval = setInterval(async () => {
      if (!autoModeActive) { clearInterval(screenshotPollInterval); return; }

      let dataUrl;
      try {
        dataUrl = await requestScreenshot();
      } catch (e) {
        console.warn('[DocuFlux] Screenshot failed during poll:', e.message);
        return;
      }
      if (!dataUrl) return;

      const newHash = hashString(dataUrl);
      if (newHash === lastScreenshotHash) return; // Page hasn't changed yet

      // Page changed — clear turn timeout, capture the new page
      clearTimeout(pageTurnTimer);
      lastScreenshotHash = newHash;

      const pageData = {
        url: location.href,
        title: document.title,
        text: '',
        images: [],
        extraction_method: 'screenshot',
        page_hint: getPageHint(),
        needs_screenshot: true,
      };

      chrome.runtime.sendMessage({ type: 'SUBMIT_PAGE', pageData }, response => {
        if (!autoModeActive) return;
        if (response?.error) {
          autoRetryCount++;
          if (autoRetryCount >= MAX_AUTO_RETRIES) {
            stopAutoCapture();
            chrome.runtime.sendMessage({ type: 'AUTO_CAPTURE_ERROR', reason: 'max_retries' });
          }
          return;
        }
        autoRetryCount = 0;
        const newCount = response?.page_count || currentPageCount + 1;
        currentPageCount = newCount;

        if (newCount >= maxPages) {
          stopAutoCapture();
          chrome.runtime.sendMessage({ type: 'AUTO_CAPTURE_DONE', pageCount: newCount });
          return;
        }

        advancePage(autoModeConfig);
        armPageTurnTimeout();
      });
    }, PAGE_TURN_POLL_MS);

    armPageTurnTimeout();
  }

  function armPageTurnTimeout() {
    clearTimeout(pageTurnTimer);
    pageTurnTimer = setTimeout(() => {
      if (!autoModeActive) return;
      stopAutoCapture();
      chrome.runtime.sendMessage({ type: 'AUTO_CAPTURE_ERROR', reason: 'page_turn_timeout' });
    }, PAGE_TURN_TIMEOUT_MS);
  }

  async function handleMutation() {
    if (!autoModeActive) return;

    const { element, method } = findContentElement();
    const text = elementToMarkdown(element);

    if (text === lastCapturedContent) return; // No real change

    // Page changed — clear turn timeout
    clearTimeout(pageTurnTimer);

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
      if (!autoModeActive) return;

      if (response?.error) {
        autoRetryCount++;
        console.warn('[DocuFlux] Failed to submit page (attempt', autoRetryCount, '):', response.error);
        if (autoRetryCount < MAX_AUTO_RETRIES) {
          // Retry: re-arm timer so handleMutation fires again shortly
          debounceTimer = setTimeout(handleMutation, 1500);
        } else {
          stopAutoCapture();
          chrome.runtime.sendMessage({ type: 'AUTO_CAPTURE_ERROR', reason: 'max_retries' });
        }
        return;
      }

      autoRetryCount = 0;
      lastCapturedContent = text;
      const pageCount = response?.page_count || 0;
      const maxPages = autoModeConfig.maxPages || 100;

      if (pageCount >= maxPages) {
        stopAutoCapture();
        chrome.runtime.sendMessage({ type: 'AUTO_CAPTURE_DONE', pageCount });
        return;
      }

      advancePage(autoModeConfig);
      armPageTurnTimeout();
    });
  }

  function stopAutoCapture() {
    autoModeActive = false;
    if (autoModeObserver) {
      autoModeObserver.disconnect();
      autoModeObserver = null;
    }
    clearTimeout(debounceTimer);
    clearTimeout(pageTurnTimer);
    clearInterval(screenshotPollInterval);
    screenshotPollInterval = null;
    console.log('[DocuFlux] Auto-capture stopped');
  }

  // ─── Message Handler ──────────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'CAPTURE_PAGE') {
      captureCurrentPage().then(sendResponse).catch(e => sendResponse({ error: e.message }));
      return true;
    }
    if (message.type === 'START_AUTO_CAPTURE') {
      startAutoCapture(message.config || {}).catch(e => {
        chrome.runtime.sendMessage({ type: 'AUTO_CAPTURE_ERROR', reason: e.message });
      });
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
