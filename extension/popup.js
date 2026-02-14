/**
 * DocuFlux Capture - Popup Controller
 */

'use strict';

// ─── State ───────────────────────────────────────────────────────────────────

let autoModeActive = false;
let pollInterval = null;

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
  nextSelector: $('next-selector'),
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

async function sendToContent(type, data = {}) {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  if (!tab) throw new Error('No active tab found');
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tab.id, { type, ...data }, response => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      if (response?.error) return reject(new Error(response.error));
      resolve(response);
    });
  });
}

// ─── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  const config = await bg('GET_CONFIG');
  els.serverUrl.value = config.serverUrl;
  els.serverUrlDisplay.textContent = config.serverUrl.replace(/^https?:\/\//, '');

  const session = await bg('GET_SESSION');
  if (!session) {
    showSection('no-session-section');
  } else if (session.status === 'active') {
    showActiveSection(session);
  } else if (session.status === 'paused') {
    showPausedSection(session);
  } else if (session.status === 'assembling' && session.jobId) {
    showSection('progress-section');
    startPolling(session.jobId);
  } else {
    showSection('no-session-section');
  }
}

function showActiveSection(session) {
  showSection('active-section');
  els.sessionTitleDisplay.textContent = session.title;
  els.pageCount.textContent = `${session.pageCount} page${session.pageCount !== 1 ? 's' : ''}`;
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
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const sourceUrl = tabs[0]?.url || '';
    const session = await bg('CREATE_SESSION', { title, toFormat, sourceUrl });
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
    showStatus(`Page ${result.page_count} captured`, 'success');
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
      nextButtonSelector: els.nextSelector.value.trim() || null,
      maxPages: parseInt(els.maxPages.value, 10) || 100,
    };
    await sendToContent('START_AUTO_CAPTURE', { config });
    autoModeActive = true;
    els.toggleAutoBtn.textContent = '\u25A0 Stop';
    showStatus('Auto-capture started', 'success');
  }
});

els.pauseBtn.addEventListener('click', async () => {
  const { activeSession } = await chrome.storage.local.get('activeSession');
  if (!activeSession) return;
  activeSession.status = 'paused';
  await chrome.storage.local.set({ activeSession });
  showPausedSection(activeSession);
  showStatus('Session paused', 'info');
});

els.resumeBtn.addEventListener('click', async () => {
  const { activeSession } = await chrome.storage.local.get('activeSession');
  if (!activeSession) return;
  activeSession.status = 'active';
  await chrome.storage.local.set({ activeSession });
  showActiveSection(activeSession);
  showStatus('Session resumed', 'success');
});

async function doFinishSession(buttonEl) {
  try {
    buttonEl.disabled = true;
    const result = await bg('FINISH_SESSION');
    showSection('progress-section');
    setProgress(5, 'Assembling pages...');
    startPolling(result.job_id);
  } catch (e) {
    showStatus(e.message, 'error');
    buttonEl.disabled = false;
  }
}

els.endBtn.addEventListener('click', () => doFinishSession(els.endBtn));
els.endBtnPaused.addEventListener('click', () => doFinishSession(els.endBtnPaused));

els.newCaptureBtn.addEventListener('click', async () => {
  await bg('CLEAR_SESSION');
  showSection('no-session-section');
});

// Listen for background events
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === 'AUTO_CAPTURE_DONE') {
    autoModeActive = false;
    els.toggleAutoBtn.textContent = '\u25B6 Auto';
    showStatus(`Auto-capture complete: ${message.pageCount} pages`, 'success');
  }
  if (message.type === 'PAGE_SUBMITTED') {
    els.pageCount.textContent = `${message.pageCount} page${message.pageCount !== 1 ? 's' : ''}`;
  }
});

// ─── Polling ──────────────────────────────────────────────────────────────────

function startPolling(jobId) {
  clearInterval(pollInterval);
  pollInterval = setInterval(() => pollJob(jobId), 2000);
}

async function pollJob(jobId) {
  try {
    const status = await bg('POLL_STATUS');
    const pct = parseInt(status.progress, 10) || 0;
    setProgress(pct, `Status: ${status.status}`);

    if (status.status === 'success') {
      clearInterval(pollInterval);
      const { serverUrl } = await bg('GET_CONFIG');
      const url = `${serverUrl.replace(/\/$/, '')}${status.download_url}`;
      els.downloadLink.href = url;
      showSection('result-section');
    } else if (status.status === 'failure') {
      clearInterval(pollInterval);
      showStatus(`Assembly failed: ${status.error || 'Unknown error'}`, 'error');
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
    console.warn('[DocuFlux] Poll error:', e.message);
  }
}

// ─── Start ────────────────────────────────────────────────────────────────────

init().catch(e => {
  showStatus(e.message, 'error');
  showSection('no-session-section'); // fallback so popup is never blank
});
