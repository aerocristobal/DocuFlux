/**
 * DocuFlux Capture - Popup Controller
 */

'use strict';

// ─── State ───────────────────────────────────────────────────────────────────

let autoModeActive = false;
let pollInterval = null;
let activeSocket = null;
const SOCKET_CONNECT_TIMEOUT_MS = 5000;

// ─── DOM References ───────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

const els = {
  serverUrl: $('server-url'),
  serverUrlDisplay: $('server-url-display'),
  saveConfig: $('save-config'),
  sessionTitle: $('session-title'),
  targetFormat: $('target-format'),
  forceOcr: $('force-ocr'),
  startCaptureBtn: $('start-capture-btn'),
  capturePageBtn: $('capture-page-btn'),
  toggleAutoBtn: $('toggle-auto-btn'),
  autoSettings: $('auto-settings'),
  nextMethod: $('next-method'),
  nextSelector: $('next-selector'),
  pageTurnDelay: $('page-turn-delay'),
  maxPages: $('max-pages'),
  pauseBtn: $('pause-btn'),
  endBtn: $('end-btn'),
  resumeBtn: $('resume-btn'),
  endBtnPaused: $('end-btn-paused'),
  sessionTitleDisplay: $('session-title-display'),
  pageCount: $('page-count'),
  pausedTitleDisplay: $('paused-title-display'),
  pausedPageCount: $('paused-page-count'),
  captureToggle: $('capture-toggle'),
  progressLabel: $('progress-label'),
  progressFill: $('progress-fill'),
  progressDetail: $('progress-detail'),
  downloadLink: $('download-link'),
  newCaptureBtn: $('new-capture-btn'),
  cancelAssemblyBtn: $('cancel-assembly-btn'),
  statusMsg: $('status-msg'),
};

// ─── UI Helpers ───────────────────────────────────────────────────────────────

const SECTIONS = ['no-session-section', 'active-section', 'paused-section', 'progress-section', 'result-section'];

function showSection(id) {
  SECTIONS.forEach(s => $(s).classList.toggle('hidden', s !== id));
  updateToggleChip(id);
}

function updateToggleChip(section) {
  const toggle = els.captureToggle;
  if (section === 'active-section') {
    toggle.textContent = '\u25CF Capture ON';
    toggle.className = 'capture-toggle on';
  } else if (section === 'paused-section') {
    toggle.textContent = '\u25CF Capture ON \u00B7 PAUSED';
    toggle.className = 'capture-toggle paused';
  } else {
    toggle.textContent = '\u25CF Capture OFF';
    toggle.className = 'capture-toggle off';
  }
}

function showStatus(msg, type = 'info') {
  els.statusMsg.textContent = msg;
  els.statusMsg.className = `status-msg status-${type}`;
  clearTimeout(showStatus._timer);
  showStatus._timer = setTimeout(() => els.statusMsg.classList.add('hidden'), 4000);
}

function setProgress(pct, detail = '') {
  els.progressFill.style.width = `${pct}%`;
  els.progressDetail.textContent = detail;
}

// ─── Background Communication ─────────────────────────────────────────────────

function bg(type, data = {}, retries = 2) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ type, ...data }, response => {
      const err = chrome.runtime.lastError;
      if (err) {
        // Background event page may not be ready yet — retry once
        if (retries > 0 && err.message && err.message.includes('Receiving end does not exist')) {
          setTimeout(() => bg(type, data, retries - 1).then(resolve).catch(reject), 150);
        } else {
          reject(new Error(err.message));
        }
        return;
      }
      if (response?.error) return reject(new Error(response.error));
      resolve(response);
    });
  });
}

function getActiveTab() {
  return new Promise((resolve, reject) => {
    chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      if (!tabs || !tabs[0]) return reject(new Error('No active tab found'));
      resolve(tabs[0]);
    });
  });
}

async function sendToContent(type, data = {}, _retried = false) {
  const tab = await getActiveTab();
  try {
    return await new Promise((resolve, reject) => {
      chrome.tabs.sendMessage(tab.id, { type, ...data }, response => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (response?.error) return reject(new Error(response.error));
        resolve(response);
      });
    });
  } catch (e) {
    // Content script not injected (e.g. extension updated while tab was open).
    // Inject on-demand via chrome.scripting, then retry once.
    if (!_retried && e.message.includes('Receiving end does not exist')) {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ['purify.min.js', 'content.js'],
      });
      // Brief delay for script initialization
      await new Promise(r => setTimeout(r, 200));
      return sendToContent(type, data, true);
    }
    throw e;
  }
}

// ─── Init ─────────────────────────────────────────────────────────────────────

function saveAutoCaptureSettings() {
  const settings = {
    nextMethod: els.nextMethod.value,
    nextSelector: els.nextSelector.value,
    pageTurnDelay: els.pageTurnDelay.value,
    maxPages: els.maxPages.value,
  };
  return new Promise(resolve => chrome.storage.local.set({ autoCaptureSettings: settings }, resolve));
}

function restoreAutoCaptureSettings() {
  return new Promise(resolve => {
    chrome.storage.local.get('autoCaptureSettings', result => {
      const s = result?.autoCaptureSettings;
      if (!s) return resolve();
      if (s.nextMethod) els.nextMethod.value = s.nextMethod;
      if (s.nextSelector) els.nextSelector.value = s.nextSelector;
      if (s.pageTurnDelay) els.pageTurnDelay.value = s.pageTurnDelay;
      if (s.maxPages) els.maxPages.value = s.maxPages;
      resolve();
    });
  });
}

async function detectSiteRecommendations(tabUrl) {
  if (!tabUrl) return;
  try {
    const hostname = new URL(tabUrl).hostname;
    if (hostname === 'read.amazon.com') {
      // Only set default if user hasn't saved a preference
      if (els.nextMethod.value === 'selector') els.nextMethod.value = 'key-right';
      showStatus('Kindle detected: using Arrow Right for page advance', 'info');
    } else if (hostname.endsWith('.percipio.com')) {
      if (els.nextMethod.value === 'selector') els.nextMethod.value = 'passive';

      // Check if we have host permissions for cross-origin frame access
      const hasPerms = await new Promise(resolve => {
        chrome.permissions.contains({ origins: ['https://cdn2.percipio.com/*'] }, resolve);
      });
      if (!hasPerms) {
        showStatus('Percipio: grant "On all sites" access (click Grant below)', 'error');
        // Insert a one-time grant button after the status message
        const btn = document.createElement('button');
        btn.textContent = 'Grant Site Access';
        btn.className = 'btn-secondary';
        btn.style.marginTop = '6px';
        btn.addEventListener('click', async () => {
          const granted = await new Promise(resolve => {
            chrome.permissions.request({ origins: ['<all_urls>'] }, resolve);
          });
          if (granted) {
            btn.remove();
            showStatus('Permissions granted! Reload the Percipio page, then capture.', 'success');
          } else {
            showStatus('Permission denied. Right-click extension icon → "On all sites"', 'error');
          }
        });
        els.statusMsg.after(btn);
      } else {
        showStatus('Percipio detected: Passive mode (or use Playwright CDP for auto-capture)', 'info');
      }
    }
  } catch (e) {}
}

async function init() {
  const config = await bg('GET_CONFIG');
  els.serverUrl.value = config.serverUrl;
  els.serverUrlDisplay.textContent = config.serverUrl.replace(/^https?:\/\//, '');

  const tab = await getActiveTab().catch(() => null);
  // Apply site-specific defaults first, then restore saved settings on top
  await detectSiteRecommendations(tab?.url);
  await restoreAutoCaptureSettings();

  const session = await bg('GET_SESSION');
  if (!session) {
    showSection('no-session-section');
  } else if (session.status === 'active') {
    showActiveSection(session);
    checkServerPageCount(session);
  } else if (session.status === 'paused') {
    showPausedSection(session);
    checkServerPageCount(session);
  } else if (session.status === 'assembling' && session.jobId) {
    showSection('progress-section');
    startWatching(session.jobId);
  } else {
    showSection('no-session-section');
  }
}

/**
 * Reconcile local page count with server page count.
 * If pages were lost (e.g. service worker killed mid-submit), warn the user.
 * Also triggers outbox drain in background.
 */
async function checkServerPageCount(session) {
  try {
    const serverStatus = await bg('GET_SESSION_SERVER_STATUS');
    if (!serverStatus) return;
    const serverCount = serverStatus.page_count || 0;
    const localCount = session.pageCount || 0;
    if (localCount > serverCount) {
      const diff = localCount - serverCount;
      showStatus(`Syncing ${diff} page(s) not yet on server...`, 'info');
    }
  } catch (e) {
    // Non-critical — silently ignore
  }
}

function showActiveSection(session) {
  showSection('active-section');
  els.sessionTitleDisplay.textContent = session.title;
  els.pageCount.textContent = `${session.pageCount} page${session.pageCount !== 1 ? 's' : ''}`;
  // Show batch progress if a job_id is available (force_ocr sessions)
  if (session.jobId) {
    bg('POLL_STATUS').then(status => {
      const batchesQueued = parseInt(status.batches_queued, 10) || 0;
      const batchesDone = parseInt(status.batches_done, 10) || 0;
      if (batchesQueued > 0) {
        const batchPct = Math.round((batchesDone / batchesQueued) * 75);
        els.progressFill.style.width = `${batchPct}%`;
        els.progressLabel.textContent = `Batch ${batchesDone}/${batchesQueued} processed`;
      }
    }).catch(() => {});
  }
}

function showPausedSection(session) {
  showSection('paused-section');
  els.pausedTitleDisplay.textContent = session.title;
  els.pausedPageCount.textContent = `${session.pageCount} page${session.pageCount !== 1 ? 's' : ''}`;
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

els.saveConfig.addEventListener('click', async () => {
  try {
    await bg('SET_CONFIG', { serverUrl: els.serverUrl.value.trim() });
    els.serverUrlDisplay.textContent = els.serverUrl.value.trim().replace(/^https?:\/\//, '');
    showStatus('Server URL saved', 'success');
  } catch (e) {
    showStatus(e.message, 'error');
  }
});

els.startCaptureBtn.addEventListener('click', async () => {
  try {
    els.startCaptureBtn.disabled = true;
    const title = els.sessionTitle.value.trim() || 'Captured Document';
    const toFormat = els.targetFormat.value;
    const tab = await getActiveTab().catch(() => null);
    const sourceUrl = tab?.url || '';
    const session = await bg('CREATE_SESSION', { title, toFormat, sourceUrl, forceOcr: els.forceOcr.checked });
    showActiveSection(session);
    showStatus('Session started', 'success');
  } catch (e) {
    showStatus(e.message, 'error');
  } finally {
    els.startCaptureBtn.disabled = false;
  }
});

els.capturePageBtn.addEventListener('click', async () => {
  try {
    els.capturePageBtn.disabled = true;
    els.capturePageBtn.textContent = 'Capturing...';

    const pageData = await sendToContent('CAPTURE_PAGE');
    const result = await bg('SUBMIT_PAGE', { pageData });

    els.pageCount.textContent = `${result.page_count} page${result.page_count !== 1 ? 's' : ''}`;
    const skipped = result.skipped_image_count || 0;
    if (skipped > 0) {
      showStatus(`Page ${result.page_count} captured (${skipped} image${skipped !== 1 ? 's' : ''} skipped — cross-origin)`, 'info');
    } else {
      showStatus(`Page ${result.page_count} captured`, 'success');
    }
  } catch (e) {
    showStatus(e.message, 'error');
  } finally {
    els.capturePageBtn.disabled = false;
    els.capturePageBtn.textContent = 'Capture This Page';
  }
});

els.toggleAutoBtn.addEventListener('click', async () => {
  if (autoModeActive) {
    await sendToContent('STOP_AUTO_CAPTURE');
    autoModeActive = false;
    els.toggleAutoBtn.textContent = '\u25B6 Auto';
    els.autoSettings.classList.add('hidden');
    showStatus('Auto-capture stopped', 'info');
  } else {
    els.autoSettings.classList.remove('hidden');
    const config = {
      nextMethod: els.nextMethod.value || 'selector',
      nextButtonSelector: els.nextSelector.value.trim() || null,
      pageTurnDelayMs: parseInt(els.pageTurnDelay.value, 10) || 1500,
      maxPages: parseInt(els.maxPages.value, 10) || 100,
    };
    saveAutoCaptureSettings();
    await sendToContent('START_AUTO_CAPTURE', { config });
    autoModeActive = true;
    els.toggleAutoBtn.textContent = '\u25A0 Stop';
    showStatus('Auto-capture started', 'success');
  }
});

els.pauseBtn.addEventListener('click', () => {
  chrome.storage.local.get('activeSession', result => {
    const activeSession = result && result.activeSession;
    if (!activeSession) return;
    activeSession.status = 'paused';
    chrome.storage.local.set({ activeSession }, () => {
      showPausedSection(activeSession);
      showStatus('Session paused', 'info');
    });
  });
});

els.resumeBtn.addEventListener('click', () => {
  chrome.storage.local.get('activeSession', result => {
    const activeSession = result && result.activeSession;
    if (!activeSession) return;
    activeSession.status = 'active';
    chrome.storage.local.set({ activeSession }, () => {
      showActiveSection(activeSession);
      showStatus('Session resumed', 'success');
    });
  });
});

async function doFinishSession(buttonEl) {
  try {
    buttonEl.disabled = true;
    const result = await bg('FINISH_SESSION');
    showSection('progress-section');
    setProgress(5, 'Assembling pages...');
    startWatching(result.job_id);
  } catch (e) {
    showStatus(e.message, 'error');
    buttonEl.disabled = false;
  }
}

els.endBtn.addEventListener('click', () => doFinishSession(els.endBtn));
els.endBtnPaused.addEventListener('click', () => doFinishSession(els.endBtnPaused));

els.newCaptureBtn.addEventListener('click', async () => {
  stopWatching();
  await bg('CLEAR_SESSION');
  showSection('no-session-section');
});

els.cancelAssemblyBtn.addEventListener('click', async () => {
  stopWatching();
  await bg('CLEAR_SESSION');
  showSection('no-session-section');
  showStatus('Assembly cancelled', 'info');
});

// Listen for background events
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === 'AUTO_CAPTURE_DONE') {
    autoModeActive = false;
    els.toggleAutoBtn.textContent = '\u25B6 Auto';
    showStatus(`Auto-capture complete: ${message.pageCount} pages`, 'success');
  }
  if (message.type === 'AUTO_CAPTURE_ERROR') {
    autoModeActive = false;
    els.toggleAutoBtn.textContent = '\u25B6 Auto';
    const reasons = {
      page_turn_timeout: 'Page did not turn — check your advance method',
      max_retries: 'Too many submit failures — check server connection',
      submit_failed: 'Could not submit first page',
    };
    const detail = reasons[message.reason] || message.reason || 'Unknown error';
    showStatus(`Auto-capture stopped: ${detail}`, 'error');
    els.statusMsg.classList.remove('hidden'); // keep visible longer
  }
  if (message.type === 'PAGE_SUBMITTED') {
    els.pageCount.textContent = `${message.pageCount} page${message.pageCount !== 1 ? 's' : ''}`;
  }
});

// ─── WebSocket Watching ───────────────────────────────────────────────────────

function stopWatching() {
  clearInterval(pollInterval);
  if (activeSocket) { activeSocket.disconnect(); activeSocket = null; }
}

async function startWatching(jobId) {
  stopWatching();
  pollErrorCount = 0;

  const config = await bg('GET_CONFIG');
  let socketConnected = false;

  const connectTimeoutId = setTimeout(() => {
    if (!socketConnected) {
      if (activeSocket) { activeSocket.disconnect(); activeSocket = null; }
      startPolling(jobId);
    }
  }, SOCKET_CONNECT_TIMEOUT_MS);

  const socket = io(config.serverUrl, { transports: ['websocket', 'polling'] });
  activeSocket = socket;

  socket.on('connect', () => {
    socketConnected = true;
    clearTimeout(connectTimeoutId);
  });

  socket.on('connect_error', () => {
    if (!socketConnected) {
      clearTimeout(connectTimeoutId);
      socket.disconnect(); activeSocket = null;
      startPolling(jobId);
    }
  });

  socket.on('job_update', (update) => {
    if (update.id !== jobId) return;
    handleJobUpdate(update);
  });

  socket.on('disconnect', () => {
    if (activeSocket) { activeSocket = null; startPolling(jobId); }
  });
}

function handleJobUpdate(status) {
  const pct = parseInt(status.progress, 10) || 0;
  setProgress(pct, `Status: ${status.status}`);

  if (status.status === 'SUCCESS' || status.status === 'success') {
    stopWatching();
    bg('GET_CONFIG').then(config => {
      els.downloadLink.href = `${config.serverUrl.replace(/\/$/, '')}${status.download_url}`;
      showSection('result-section');
      if (status.batch_warnings) showStatus(`⚠ ${status.batch_warnings}`, 'error');
    });
  } else if (status.status === 'FAILURE' || status.status === 'failure') {
    stopWatching();
    showStatus(`Assembly failed: ${status.error || status.result || 'Unknown error'}`, 'error');
    bg('GET_SESSION').then(session => {
      if (session?.status === 'paused') showPausedSection(session);
      else if (session) showActiveSection(session);
      else showSection('no-session-section');
    });
  }
}

// ─── Polling ──────────────────────────────────────────────────────────────────

let pollErrorCount = 0;
const POLL_ERROR_LIMIT = 5;

function startPolling(jobId) {
  clearInterval(pollInterval);
  pollErrorCount = 0;
  pollInterval = setInterval(() => pollJob(jobId), 2000);
}

async function pollJob(jobId) {
  try {
    const status = await bg('POLL_STATUS');
    pollErrorCount = 0;
    const pct = parseInt(status.progress, 10) || 0;
    setProgress(pct, `Status: ${status.status}`);

    if (status.status === 'SUCCESS' || status.status === 'success') {
      clearInterval(pollInterval);
      const config = await bg('GET_CONFIG');
      const url = `${config.serverUrl.replace(/\/$/, '')}${status.download_url}`;
      els.downloadLink.href = url;
      showSection('result-section');
      if (status.batch_warnings) {
        showStatus(`⚠ ${status.batch_warnings}`, 'error');
      }
    } else if (status.status === 'FAILURE' || status.status === 'failure') {
      clearInterval(pollInterval);
      showStatus(`Assembly failed: ${status.error || status.result || 'Unknown error'}`, 'error');
      const session = await bg('GET_SESSION');
      if (session?.status === 'paused') {
        showPausedSection(session);
      } else if (session) {
        showActiveSection(session);
      } else {
        showSection('no-session-section');
      }
    }
  } catch (e) {
    pollErrorCount++;
    console.warn(`[DocuFlux] Poll error (${pollErrorCount}/${POLL_ERROR_LIMIT}):`, e.message);
    if (pollErrorCount >= POLL_ERROR_LIMIT) {
      clearInterval(pollInterval);
      showStatus('Assembly status unavailable — job may have failed', 'error');
      setProgress(0, 'Could not reach server');
    }
  }
}

// ─── Start ────────────────────────────────────────────────────────────────────

init().catch(e => {
  showStatus(e.message, 'error');
  showSection('no-session-section'); // fallback so popup is never blank
});
