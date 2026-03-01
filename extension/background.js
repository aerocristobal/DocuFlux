/**
 * DocuFlux Capture - Background Service Worker
 *
 * Manages session state in chrome.storage.local and communicates with
 * the DocuFlux REST API on behalf of the popup and content scripts.
 *
 * All chrome.storage calls use the callback form for Firefox MV2 compatibility
 * (Firefox's chrome shim does not always promisify storage APIs).
 *
 * Reliability: An IndexedDB outbox is used as a write-ahead log before each
 * page POST. If the service worker is killed mid-submission, pages left in the
 * outbox are re-submitted on the next startup/wake, preventing data loss.
 */

const DEFAULT_SERVER_URL = 'http://localhost:5000';

// ─── Storage Helpers (callback → Promise wrappers) ───────────────────────────

function storageGet(keys) {
  return new Promise((resolve, reject) => {
    chrome.storage.local.get(keys, result => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      resolve(result || {});
    });
  });
}

function storageSet(items) {
  return new Promise((resolve, reject) => {
    chrome.storage.local.set(items, () => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      resolve();
    });
  });
}

function storageRemove(keys) {
  return new Promise((resolve, reject) => {
    chrome.storage.local.remove(keys, () => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      resolve();
    });
  });
}

// ─── IndexedDB Outbox ────────────────────────────────────────────────────────
//
// Schema: database "docuflux_outbox" v1
//   object store "pending_pages"
//     keyPath: "sequence" (auto-increment)
//     index: "sessionId"
//
// Each record:
//   { sequence, sessionId, pageData, addedAt, retryCount }

const OUTBOX_DB_NAME = 'docuflux_outbox';
const OUTBOX_DB_VERSION = 1;
const OUTBOX_STORE = 'pending_pages';
const OUTBOX_MAX_RETRIES = 5;

let _outboxDb = null;

function openOutboxDB() {
  if (_outboxDb) return Promise.resolve(_outboxDb);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(OUTBOX_DB_NAME, OUTBOX_DB_VERSION);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(OUTBOX_STORE)) {
        const store = db.createObjectStore(OUTBOX_STORE, { keyPath: 'sequence', autoIncrement: true });
        store.createIndex('sessionId', 'sessionId', { unique: false });
      }
    };
    req.onsuccess = e => { _outboxDb = e.target.result; resolve(_outboxDb); };
    req.onerror = e => reject(e.target.error);
  });
}

async function writeToOutbox(sessionId, pageData) {
  const db = await openOutboxDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(OUTBOX_STORE, 'readwrite');
    const req = tx.objectStore(OUTBOX_STORE).add({
      sessionId,
      pageData,
      addedAt: Date.now(),
      retryCount: 0,
    });
    req.onsuccess = () => resolve(req.result); // returns auto-assigned sequence
    req.onerror = e => reject(e.target.error);
  });
}

async function removeFromOutbox(sequence) {
  const db = await openOutboxDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(OUTBOX_STORE, 'readwrite');
    const req = tx.objectStore(OUTBOX_STORE).delete(sequence);
    req.onsuccess = () => resolve();
    req.onerror = e => reject(e.target.error);
  });
}

async function getPendingForSession(sessionId) {
  const db = await openOutboxDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(OUTBOX_STORE, 'readonly');
    const idx = tx.objectStore(OUTBOX_STORE).index('sessionId');
    const req = idx.getAll(sessionId);
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = e => reject(e.target.error);
  });
}

async function clearOutboxForSession(sessionId) {
  const db = await openOutboxDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(OUTBOX_STORE, 'readwrite');
    const idx = tx.objectStore(OUTBOX_STORE).index('sessionId');
    const req = idx.openCursor(IDBKeyRange.only(sessionId));
    req.onsuccess = e => {
      const cursor = e.target.result;
      if (cursor) { cursor.delete(); cursor.continue(); }
      else resolve();
    };
    req.onerror = e => reject(e.target.error);
  });
}

async function incrementOutboxRetry(sequence) {
  const db = await openOutboxDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(OUTBOX_STORE, 'readwrite');
    const store = tx.objectStore(OUTBOX_STORE);
    const getReq = store.get(sequence);
    getReq.onsuccess = () => {
      const record = getReq.result;
      if (!record) { resolve(); return; }
      record.retryCount = (record.retryCount || 0) + 1;
      const putReq = store.put(record);
      putReq.onsuccess = () => resolve();
      putReq.onerror = e => reject(e.target.error);
    };
    getReq.onerror = e => reject(e.target.error);
  });
}

// ─── API Client ──────────────────────────────────────────────────────────────

async function getServerUrl() {
  const result = await storageGet({ serverUrl: DEFAULT_SERVER_URL });
  return (result.serverUrl || DEFAULT_SERVER_URL).replace(/\/$/, '');
}

async function apiPost(path, body) {
  const base = await getServerUrl();
  const result = await storageGet({ clientId: generateId() });
  const clientId = result.clientId;
  await storageSet({ clientId });

  const response = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Client-ID': clientId,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(err.error || `HTTP ${response.status}`);
  }
  return response.json();
}

async function apiGet(path) {
  const base = await getServerUrl();
  const response = await fetch(`${base}${path}`);
  if (!response.ok) {
    const err = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(err.error || `HTTP ${response.status}`);
  }
  return response.json();
}

// ─── Session Management ───────────────────────────────────────────────────────

function captureScreenshot() {
  return new Promise((resolve, reject) => {
    chrome.tabs.captureVisibleTab(null, { format: 'png' }, dataUrl => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      resolve(dataUrl);
    });
  });
}

async function createSession(title, toFormat, sourceUrl, forceOcr) {
  const data = await apiPost('/api/v1/capture/sessions', {
    title, to_format: toFormat, source_url: sourceUrl, force_ocr: forceOcr || false,
  });
  const session = {
    sessionId: data.session_id,
    jobId: data.job_id || null,
    title,
    toFormat,
    pageCount: 0,
    status: 'active',
    nextSequence: 0,
  };
  await storageSet({ activeSession: session });
  return session;
}

async function submitPageWithSequence(pageData, sequence) {
  const result = await storageGet('activeSession');
  const activeSession = result.activeSession;
  if (!activeSession) throw new Error('No active session');

  // If content script flagged the page as needing a screenshot (canvas-rendered,
  // e.g. Kindle Cloud Reader), capture the visible tab and attach it as an image.
  if (pageData.needs_screenshot) {
    try {
      const screenshotDataUrl = await captureScreenshot();
      const filename = `screenshot_${Date.now()}.png`;
      pageData.images = [
        { filename, b64: screenshotDataUrl, alt: '', is_screenshot: true },
        ...(pageData.images || []),
      ];
    } catch (e) {
      console.warn('[DocuFlux] Tab screenshot failed:', e.message);
    }
  }

  const apiResult = await apiPost(
    `/api/v1/capture/sessions/${activeSession.sessionId}/pages`,
    { ...pageData, page_sequence: sequence }
  );
  activeSession.pageCount = apiResult.page_count;
  await storageSet({ activeSession });
  return apiResult;
}

async function submitPage(pageData) {
  // Allocate a monotonically increasing sequence number for deduplication.
  const result = await storageGet('activeSession');
  const activeSession = result.activeSession;
  if (!activeSession) throw new Error('No active session');

  const sequence = await writeToOutbox(activeSession.sessionId, pageData);

  try {
    const apiResult = await submitPageWithSequence(pageData, sequence);
    await removeFromOutbox(sequence);
    return apiResult;
  } catch (e) {
    // Page left in outbox — will be drained on next startup
    await incrementOutboxRetry(sequence).catch(() => {});
    throw e;
  }
}

async function submitPageWithRetry(pageData) {
  const MAX_RETRIES = 3;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      return await submitPage(pageData);
    } catch (e) {
      if (attempt === MAX_RETRIES) throw e;
      const delay = 2000 * Math.pow(2, attempt);
      console.warn(`[DocuFlux] Page submit failed (attempt ${attempt + 1}): ${e.message}. Retrying in ${delay}ms`);
      await new Promise(r => setTimeout(r, delay));
    }
  }
}

async function finishSession() {
  const result = await storageGet('activeSession');
  const activeSession = result.activeSession;
  if (!activeSession) throw new Error('No active session');

  const apiResult = await apiPost(`/api/v1/capture/sessions/${activeSession.sessionId}/finish`, {});
  activeSession.status = 'assembling';
  activeSession.jobId = apiResult.job_id;
  await storageSet({ activeSession });
  return apiResult;
}

async function pollJobStatus() {
  const result = await storageGet('activeSession');
  const activeSession = result.activeSession;
  if (!activeSession?.jobId) throw new Error('No job in progress');
  return apiGet(`/api/v1/status/${activeSession.jobId}`);
}

async function clearSession() {
  const result = await storageGet('activeSession');
  const activeSession = result.activeSession;
  if (activeSession?.sessionId) {
    await clearOutboxForSession(activeSession.sessionId).catch(() => {});
  }
  await storageRemove('activeSession');
}

async function getSessionServerStatus() {
  const result = await storageGet('activeSession');
  const activeSession = result.activeSession;
  if (!activeSession?.sessionId) return null;
  try {
    return await apiGet(`/api/v1/capture/sessions/${activeSession.sessionId}/status`);
  } catch (e) {
    return null;
  }
}

// ─── Outbox Drain (run on startup/wake) ──────────────────────────────────────

async function drainOutbox() {
  const result = await storageGet('activeSession');
  const activeSession = result.activeSession;
  if (!activeSession?.sessionId) return;

  const pending = await getPendingForSession(activeSession.sessionId).catch(() => []);
  if (!pending.length) return;

  // Verify session is still active on server before draining
  let serverStatus;
  try {
    serverStatus = await apiGet(`/api/v1/capture/sessions/${activeSession.sessionId}/status`);
  } catch (e) {
    console.warn('[DocuFlux] Cannot drain outbox: session status check failed:', e.message);
    return;
  }

  if (serverStatus?.status !== 'active') {
    await clearOutboxForSession(activeSession.sessionId).catch(() => {});
    return;
  }

  console.log(`[DocuFlux] Draining ${pending.length} pending page(s) from outbox`);
  for (const item of pending) {
    if ((item.retryCount || 0) > OUTBOX_MAX_RETRIES) {
      await removeFromOutbox(item.sequence).catch(() => {});
      continue;
    }
    try {
      await submitPageWithSequence(item.pageData, item.sequence);
      await removeFromOutbox(item.sequence);
    } catch (e) {
      console.warn('[DocuFlux] Outbox drain failed for seq', item.sequence, ':', e.message);
      await incrementOutboxRetry(item.sequence).catch(() => {});
    }
  }
}

// ─── Auto-capture coordination ────────────────────────────────────────────────

function triggerContentCapture(tabId) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, { type: 'CAPTURE_PAGE' }, response => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      if (response?.error) return reject(new Error(response.error));
      resolve(response);
    });
  });
}

// ─── Startup Hook ─────────────────────────────────────────────────────────────

chrome.runtime.onStartup.addListener(() => {
  drainOutbox().catch(e => console.warn('[DocuFlux] Outbox drain error on startup:', e.message));
});

chrome.runtime.onInstalled.addListener(() => {
  drainOutbox().catch(e => console.warn('[DocuFlux] Outbox drain error on install:', e.message));
});

// ─── Message Handler ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender).then(sendResponse).catch(err => sendResponse({ error: err.message }));
  return true; // Keep channel open for async response
});

async function handleMessage(message, sender) {
  switch (message.type) {
    case 'CREATE_SESSION':
      return createSession(message.title, message.toFormat, message.sourceUrl, message.forceOcr);

    case 'SUBMIT_PAGE': {
      const result = await submitPageWithRetry(message.pageData);
      return { ...result, skipped_image_count: message.pageData.skipped_image_count || 0 };
    }

    case 'FINISH_SESSION':
      return finishSession();

    case 'POLL_STATUS':
      return pollJobStatus();

    case 'GET_SESSION': {
      const result = await storageGet('activeSession');
      return result.activeSession || null;
    }

    case 'CLEAR_SESSION':
      await clearSession();
      return { ok: true };

    case 'GET_CONFIG': {
      const result = await storageGet({ serverUrl: DEFAULT_SERVER_URL });
      return { serverUrl: result.serverUrl || DEFAULT_SERVER_URL };
    }

    case 'SET_CONFIG':
      await storageSet({ serverUrl: message.serverUrl });
      return { ok: true };

    case 'TRIGGER_CAPTURE': {
      const tabs = await new Promise(resolve =>
        chrome.tabs.query({ active: true, currentWindow: true }, t => resolve(t || []))
      );
      if (!tabs[0]) throw new Error('No active tab');
      await triggerContentCapture(tabs[0].id);
      return { ok: true };
    }

    case 'REQUEST_SCREENSHOT': {
      // Called by content script — only background can call captureVisibleTab
      const dataUrl = await captureScreenshot();
      return { dataUrl };
    }

    case 'AUTO_CAPTURE_ERROR':
    case 'AUTO_CAPTURE_DONE': {
      // Forward to popup if it's open
      chrome.runtime.sendMessage(message).catch(() => {});
      return { ok: true };
    }

    case 'GET_SESSION_SERVER_STATUS':
      return getSessionServerStatus();

    case 'PERCIPIO_CONTENT': {
      // Cache latest page content from the EPUB reader frame (used by percipio-frame.js)
      await storageSet({ percipio_last_content: { ...message, timestamp: Date.now() } });
      return { ok: true };
    }

    case 'GET_PERCIPIO_CONTENT': {
      const data = await storageGet('percipio_last_content');
      const content = data?.percipio_last_content;
      // Only return if fresh (within last 2 minutes)
      if (content && Date.now() - content.timestamp < 120000) {
        return content;
      }
      return null;
    }

    case 'CAPTURE_PERCIPIO_CONTENT': {
      // On-demand extraction: execute in all frames and scan for srcdoc iframes
      // containing EPUB page content. The reader may render srcdoc iframes on
      // the main page OR inside a cdn2.percipio.com intermediate frame.
      const tabs = await new Promise(resolve =>
        chrome.tabs.query({ active: true, currentWindow: true }, t => resolve(t || []))
      );
      if (!tabs[0]) return null;
      try {
        const results = await chrome.scripting.executeScript({
          target: { tabId: tabs[0].id, allFrames: true },
          func: function () {
            var diag = {
              hostname: location.hostname,
              isCdn2: location.hostname.includes('cdn2.percipio.com'),
              isPercipio: location.hostname.includes('percipio.com'),
            };

            // Skip frames that aren't part of Percipio (e.g. ads)
            if (!diag.isPercipio && !diag.isCdn2 && location.hostname !== '') return diag;

            /** Draw img elements to canvas and return as base64 data URLs. */
            function extractImages(imgRoot) {
              var result = [];
              var imgEls = imgRoot.querySelectorAll('img');
              for (var j = 0; j < imgEls.length; j++) {
                var img = imgEls[j];
                if (img.naturalWidth < 50 || img.naturalHeight < 50) continue;
                try {
                  var c = document.createElement('canvas');
                  c.width = img.naturalWidth;
                  c.height = img.naturalHeight;
                  c.getContext('2d').drawImage(img, 0, 0);
                  result.push({
                    filename: 'percipio_img_' + result.length + '.png',
                    b64: c.toDataURL('image/png'),
                    alt: img.alt || '',
                  });
                } catch (e) { /* CORS-tainted — skip */ }
              }
              return result;
            }

            // For about:srcdoc frames: return own body text and images directly
            if (location.hostname === '' && document.body) {
              var bodyText = (document.body.innerText || '').trim();
              if (bodyText.length > 20) {
                diag.text = bodyText;
                diag.textLen = bodyText.length;
                diag.source = 'self_body';
                diag.images = extractImages(document.body);
              }
              return diag;
            }

            // For Percipio / cdn2 frames: scan child iframes for srcdoc content
            var frames = document.querySelectorAll('iframe');
            diag.iframeCount = frames.length;
            diag.frames = [];
            var text = '';
            var images = [];

            for (var i = 0; i < frames.length; i++) {
              var frame = frames[i];
              var fd = {
                i: i,
                sandbox: frame.getAttribute('sandbox'),
                hasSrcdoc: frame.hasAttribute('srcdoc'),
                srcdocLen: (frame.getAttribute('srcdoc') || '').length,
                src: (frame.src || '').substring(0, 60),
                w: Math.round(frame.getBoundingClientRect().width),
                h: Math.round(frame.getBoundingClientRect().height),
              };

              // Skip invisible frames
              if (fd.w === 0 || fd.h === 0) { fd.skip = 'zero-size'; diag.frames.push(fd); continue; }
              var style = getComputedStyle(frame);
              if (style.display === 'none' || style.visibility === 'hidden') {
                fd.skip = 'hidden'; diag.frames.push(fd); continue;
              }

              // Method 1: contentDocument (live DOM — can extract text + images)
              try {
                var doc = frame.contentDocument;
                fd.cdAccess = true;
                fd.cdHasBody = !!(doc && doc.body);
                if (doc && doc.body) {
                  fd.cdTextLen = (doc.body.innerText || '').length;
                  if (fd.cdTextLen > 20) {
                    text += (doc.body.innerText || '') + '\n';
                    var frameImgs = extractImages(doc.body);
                    for (var k = 0; k < frameImgs.length; k++) {
                      frameImgs[k].filename = 'percipio_img_' + images.length + '.png';
                      images.push(frameImgs[k]);
                    }
                  }
                }
              } catch (e) {
                fd.cdAccess = false;
                fd.cdErr = e.name;
              }

              // Method 2: srcdoc attribute (parsed DOM — text only, images not rendered)
              if (fd.srcdocLen > 50) {
                try {
                  var parser = new DOMParser();
                  var parsed = parser.parseFromString(frame.getAttribute('srcdoc'), 'text/html');
                  fd.srcdocTextLen = parsed.body ? (parsed.body.innerText || '').length : 0;
                  if (fd.srcdocTextLen > 20 && !(fd.cdAccess && fd.cdTextLen > 20)) {
                    text += (parsed.body.innerText || '') + '\n';
                  }
                } catch (e) {
                  fd.srcdocErr = e.name;
                }
              }

              diag.frames.push(fd);
            }

            diag.textLen = text.trim().length;
            diag.text = text.trim() || null;
            diag.images = images;
            diag.source = diag.isCdn2 ? 'cdn2_iframes' : 'main_iframes';
            return diag;
          },
        });
        // Return text + images from the best frame (prefer cdn2, then main, then about:srcdoc)
        const allResults = results.map(function (r) { return r.result; }).filter(Boolean);
        const withText = allResults.filter(function (d) { return d.text && d.text.length > 50; });
        const best = withText.find(function (d) { return d.isCdn2; })
          || withText.find(function (d) { return d.isPercipio; })
          || withText[0];
        // Strip large image data from diagnostic payload
        const allDiag = allResults.map(function (d) {
          var copy = Object.assign({}, d);
          if (copy.images) { copy.imageCount = copy.images.length; delete copy.images; }
          return copy;
        });
        return {
          text: best?.text || null,
          images: best?.images || [],
          _diag: allDiag,
        };
      } catch (e) {
        console.warn('[DocuFlux] Percipio executeScript failed:', e.message);
        return { text: null, _diag: [{ error: e.message }] };
      }
    }

    default:
      throw new Error(`Unknown message type: ${message.type}`);
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function generateId() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
