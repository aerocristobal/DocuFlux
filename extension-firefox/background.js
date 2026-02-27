/**
 * DocuFlux Capture - Background Service Worker
 *
 * Manages session state in chrome.storage.local and communicates with
 * the DocuFlux REST API on behalf of the popup and content scripts.
 *
 * All chrome.storage calls use the callback form for Firefox MV2 compatibility
 * (Firefox's chrome shim does not always promisify storage APIs).
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
  };
  await storageSet({ activeSession: session });
  return session;
}

async function submitPage(pageData) {
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
    pageData
  );
  activeSession.pageCount = apiResult.page_count;
  await storageSet({ activeSession });
  return apiResult;
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
  await storageRemove('activeSession');
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

// ─── Message Handler ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message).then(sendResponse).catch(err => sendResponse({ error: err.message }));
  return true; // Keep channel open for async response
});

async function handleMessage(message) {
  switch (message.type) {
    case 'CREATE_SESSION':
      return createSession(message.title, message.toFormat, message.sourceUrl, message.forceOcr);

    case 'SUBMIT_PAGE':
      return submitPageWithRetry(message.pageData);

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

    default:
      throw new Error(`Unknown message type: ${message.type}`);
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function generateId() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
