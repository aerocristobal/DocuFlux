/**
 * DocuFlux Capture - Percipio EPUB Reader Frame Script
 *
 * Runs inside the cdn2.percipio.com EPUB reader iframe.
 * Extracts text from the sandboxed about:srcdoc child iframes and caches it
 * in the background service worker (used as a secondary signal alongside the
 * on-demand chrome.scripting.executeScript approach in background.js).
 */

(function () {
  'use strict';

  console.log('[DocuFlux-Percipio] Frame script loaded in', location.href.substring(0, 80));

  let lastText = '';

  function extractPageContent() {
    const frames = document.querySelectorAll('iframe');
    let text = '';
    const images = [];

    console.log('[DocuFlux-Percipio] Scanning', frames.length, 'iframes');

    for (const frame of frames) {
      // Skip invisible frames (pre-rendered off-screen pages)
      const rect = frame.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      const style = getComputedStyle(frame);
      if (style.display === 'none' || style.visibility === 'hidden') continue;

      const sandbox = frame.getAttribute('sandbox');

      // Method 1: Try contentDocument (works if sandbox has allow-same-origin)
      try {
        const doc = frame.contentDocument;
        if (doc && doc.body) {
          const frameText = doc.body.innerText || '';
          if (frameText.length > 20) {
            console.log('[DocuFlux-Percipio] contentDocument OK, sandbox="' + sandbox + '" text:', frameText.length);
            text += frameText + '\n';
            for (const img of doc.querySelectorAll('img')) {
              if (img.naturalWidth >= 50 && img.naturalHeight >= 50) {
                images.push({ src: img.src, alt: img.alt || '' });
              }
            }
            continue;
          }
        }
      } catch (e) {
        console.log('[DocuFlux-Percipio] contentDocument blocked (sandbox="' + sandbox + '"):', e.name);
      }

      // Method 2: Read the srcdoc attribute directly from the parent DOM.
      // This bypasses the sandbox — it's just reading an HTML attribute on a DOM element.
      const srcdoc = frame.getAttribute('srcdoc');
      if (srcdoc && srcdoc.length > 50) {
        const parser = new DOMParser();
        const parsed = parser.parseFromString(srcdoc, 'text/html');
        const bodyText = parsed.body ? (parsed.body.innerText || parsed.body.textContent || '') : '';
        if (bodyText.length > 20) {
          console.log('[DocuFlux-Percipio] srcdoc fallback OK, text:', bodyText.length);
          text += bodyText + '\n';
          // Extract images from parsed srcdoc
          if (parsed.body) {
            for (const img of parsed.body.querySelectorAll('img[src]')) {
              images.push({ src: img.getAttribute('src'), alt: img.getAttribute('alt') || '' });
            }
          }
        }
      }
    }

    return { text: text.trim(), images };
  }

  function maybeNotify() {
    const { text, images } = extractPageContent();
    console.log('[DocuFlux-Percipio] Extracted text length:', text.length, '| previous:', lastText.length);
    if (text.length < 20 || text === lastText) return;
    lastText = text;
    console.log('[DocuFlux-Percipio] Sending PERCIPIO_CONTENT to background');
    chrome.runtime.sendMessage({
      type: 'PERCIPIO_CONTENT',
      text,
      images,
      url: window.location.href,
      title: document.title,
    }).catch(e => console.warn('[DocuFlux-Percipio] sendMessage failed:', e));
  }

  // Watch for new iframes added to the body (childList) and for srcdoc/src attribute
  // changes on existing iframes (Percipio may update srcdoc in-place when turning pages).
  const observer = new MutationObserver((mutations) => {
    const relevant = mutations.some(m =>
      m.type === 'childList' ||
      (m.type === 'attributes' && (m.attributeName === 'srcdoc' || m.attributeName === 'src'))
    );
    if (relevant) setTimeout(maybeNotify, 300);
  });

  observer.observe(document.body, { childList: true, subtree: true });

  // Also watch existing iframes for srcdoc attribute changes
  document.querySelectorAll('iframe').forEach(frame => {
    observer.observe(frame, { attributes: true, attributeFilter: ['srcdoc', 'src'] });
  });

  // Run immediately — content script injects at document_idle (after load),
  // so the 'load' event won't fire. Extract on injection and again after a delay.
  setTimeout(maybeNotify, 300);
  setTimeout(maybeNotify, 1500);
})();
