const formats = JSON.parse(document.getElementById('formats-data').textContent);
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
let localJobs = [];

// --- Theme Logic ---
function updateToggleIcon() {
    const i = document.querySelector('#theme-toggle md-icon');
    if (!i) return;
    const saved = localStorage.getItem('theme') || 'system';
    i.textContent = saved === 'system' ? 'brightness_auto' : (saved === 'dark' ? 'dark_mode' : 'light_mode');
}
document.getElementById('theme-toggle').addEventListener('click', () => { document.getElementById('theme-menu').open = true; });
// md-menu does not emit 'action' — attach click to each item instead
document.querySelectorAll('#theme-menu md-menu-item').forEach(item => {
    item.addEventListener('click', () => {
        const t = item.dataset.theme;
        if (!t) return;
        localStorage.setItem('theme', t);
        window.applyTheme(t);
        updateToggleIcon();
    });
});
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (localStorage.getItem('theme') === 'system') window.applyTheme('system'); });
updateToggleIcon();

// --- Utils ---
function escapeHtml(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
function showAlert(type, msg) {
    const area = document.getElementById('alert-area');
    area.innerHTML = `<div class="alert-card alert-${type}"><md-icon>${type==='error'?'error':'check_circle'}</md-icon><span>${escapeHtml(msg)}</span></div>`;
    setTimeout(() => area.innerHTML = '', 5000);
}

// --- Dialog ---
const diag = document.getElementById('action-dialog');
function confirmAction(title, body) {
    document.getElementById('dialog-headline').textContent = title;
    document.getElementById('dialog-body').textContent = body;
    diag.show();
    return new Promise(res => {
        const close = (v) => { diag.close(); res(v); };
        const ok = () => close(true); const no = () => close(false);
        document.getElementById('dialog-confirm').onclick = ok;
        document.getElementById('dialog-cancel').onclick = no;
    });
}

// --- Form ---
const fromSelect = document.getElementById('from_format');
const toSelect = document.getElementById('to_format');
const fileInput = document.getElementById('file');
const dropZone = document.getElementById('drop-zone');

fileInput.addEventListener('change', () => {
    if (!fileInput.files.length) return;
    document.getElementById('drop-zone-prompt').textContent = fileInput.files.length === 1 ? fileInput.files[0].name : `${fileInput.files.length} files selected`;
    const ext = '.' + fileInput.files[0].name.toLowerCase().split('.').pop();
    const m = formats.find(f => (f.extension === ext || (f.key === 'markdown' && (ext === '.md' || ext === '.markdown'))) && f.direction !== 'Output Only');
    if (m) { fromSelect.value = m.key; updateToOptions(m.key); }
});

// Prevent browser from opening dropped files as a new tab anywhere on the page
document.addEventListener('dragover', (e) => e.preventDefault());
document.addEventListener('drop', (e) => e.preventDefault());

// Drag-and-drop visual feedback
['dragover', 'dragenter'].forEach(ev => {
    dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add('drop-zone--dragover'); });
    fileInput.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add('drop-zone--dragover'); });
});
['dragleave'].forEach(ev => {
    dropZone.addEventListener(ev, () => dropZone.classList.remove('drop-zone--dragover'));
    fileInput.addEventListener(ev, () => dropZone.classList.remove('drop-zone--dragover'));
});
// Handle drop: prevent navigation, read files from dataTransfer
function handleFileDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.remove('drop-zone--dragover');
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) {
        const dt = new DataTransfer();
        for (const f of files) dt.items.add(f);
        fileInput.files = dt.files;
        fileInput.dispatchEvent(new Event('change'));
    }
}
dropZone.addEventListener('drop', handleFileDrop);
fileInput.addEventListener('drop', handleFileDrop);

fromSelect.addEventListener('change', () => updateToOptions(fromSelect.value));
function updateToOptions(key) {
    toSelect.disabled = false;
    toSelect.innerHTML = '<md-select-option value="" disabled selected><div slot="headline">Select target</div></md-select-option>';
    formats.forEach(f => {
        if (f.key !== key && f.direction !== 'Input Only') {
            const opt = document.createElement('md-select-option');
            opt.value = f.key; opt.innerHTML = `<div slot="headline">${f.name}</div>`;
            toSelect.appendChild(opt);
        }
    });
    const opts = document.getElementById('marker-options');
    if (key === 'pdf_marker') opts.classList.remove('hidden'); else opts.classList.add('hidden');
}

document.getElementById('convert-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData();
    for(let f of fileInput.files) fd.append('file', f);
    fd.append('from_format', fromSelect.value);
    fd.append('to_format', toSelect.value);
    document.getElementById('convert-btn').disabled = true;
    document.getElementById('submit-progress').classList.remove('hidden');
    try {
        const r = await fetch('/convert', { method: 'POST', headers: { 'X-CSRFToken': csrfToken }, body: fd });
        if (r.ok) {
            showAlert('success', 'Submitted!');
            fileInput.value = ''; fromSelect.value = ''; toSelect.value = '';
            document.getElementById('drop-zone-prompt').textContent = "Drag files here or click";
            fetchJobs();
        } else { const d = await r.json(); showAlert('error', d.error || 'Failed'); }
    } catch (e) { showAlert('error', 'Network error'); }
    finally { document.getElementById('convert-btn').disabled = false; document.getElementById('submit-progress').classList.add('hidden'); }
});

// --- Rendering ---
const socket = io();
socket.on('connect', () => fetchJobs());
socket.on('job_update', (u) => {
    const i = localJobs.findIndex(j => j.id === u.id);
    if (i !== -1) localJobs[i] = { ...localJobs[i], ...u }; else localJobs.unshift(u);
    renderJobs();
});

// Re-render every 5s to tick elapsed time while any job is active
let elapsedTimer = null;
function startElapsedTimer() {
    if (elapsedTimer) return;
    elapsedTimer = setInterval(() => {
        const hasActive = localJobs.some(j => ['PENDING','PROCESSING','STARTED'].includes(j.status));
        if (hasActive) { renderJobs(); } else { clearInterval(elapsedTimer); elapsedTimer = null; }
    }, 5000);
}

// Poll for job list changes (picks up server-side retention cleanup)
setInterval(() => {
    if (!document.hidden) fetchJobs();
}, 30000);

async function fetchJobs() {
    try {
        const r = await fetch('/api/jobs');
        localJobs = await r.json();
        renderJobs();
    } catch (e) {}
}

function renderJobs() {
    const list = document.getElementById('jobs-list');
    const msg = document.getElementById('no-jobs-msg');
    if (!localJobs.length) { list.innerHTML = ''; msg.classList.remove('hidden'); return; }
    msg.classList.add('hidden');
    list.innerHTML = localJobs.map(j => {
        const ts = parseFloat(j.created_at);
        const time = new Date(ts * (ts < 10000000000 ? 1000 : 1)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const active = ['PENDING', 'PROCESSING', 'STARTED'].includes(j.status);
        let icon = 'pending', color = 'secondary', label = 'Waiting', actions = '';
        if (j.status === 'SUCCESS') {
            icon = 'check_circle'; color = 'primary'; label = 'Done';
            const isZip = parseInt(j.file_count) > 1 || j.is_zip;
            const dlUrl = isZip ? `/download_zip/${j.id}` : `/download/${j.id}`;
            const dlIcon = isZip ? 'folder_zip' : 'download';
            const dlTitle = isZip ? 'Download ZIP' : 'Download File';
            actions = `<a href="${dlUrl}" download slot="end" title="${dlTitle}"><md-icon-button><md-icon>${dlIcon}</md-icon></md-icon-button></a>`;
        }
        else if (j.status === 'FAILURE') { icon = 'error'; color = 'error'; label = 'Failed'; actions = `<md-icon-button slot="end" data-action="retry" data-job-id="${j.id}"><md-icon>replay</md-icon></md-icon-button>`; }
        else if (j.status === 'REVOKED') { icon = 'cancel'; color = 'outline'; label = 'Cancelled'; actions = `<md-icon-button slot="end" data-action="retry" data-job-id="${j.id}"><md-icon>replay</md-icon></md-icon-button>`; }
        else actions = `<md-icon-button slot="end" data-action="cancel" data-job-id="${j.id}"><md-icon>close</md-icon></md-icon-button>`;

        const prog = active && parseInt(j.progress) > 0
            ? `<md-linear-progress value="${j.progress/100}" class="job-progress"></md-linear-progress>`
            : active ? `<md-linear-progress indeterminate class="job-progress"></md-linear-progress>` : '';

        let stageHtml = '';
        if (j.stage && active) {
            const pageCtx = j.page_count ? ` · ${j.page_count} pages` : '';
            stageHtml = `<div class="job-stage">${escapeHtml(j.stage)}${pageCtx}</div>`;
        }
        let elapsedHtml = '';
        if (active && j.started_at) {
            const secs = Math.floor(Date.now() / 1000 - parseFloat(j.started_at));
            if (secs > 0) {
                const m = Math.floor(secs / 60), s = secs % 60;
                elapsedHtml = `<div class="job-elapsed">${m}m ${s.toString().padStart(2,'0')}s</div>`;
            }
        }

        let slmHtml = '';
        if (j.slm) {
            const slmTags = (j.slm.tags || []).map(t => `<md-assist-chip label="${escapeHtml(t)}" class="job-slm-chip"></md-assist-chip>`).join('');
            slmHtml = `<div class="job-slm-block">
                <span class="job-slm-label">AI: </span>${escapeHtml(j.slm.title || '')}
                ${j.slm.summary ? `<div class="job-slm-summary">${escapeHtml(j.slm.summary)}</div>` : ''}
                ${slmTags ? `<div class="job-slm-tags">${slmTags}</div>` : ''}
            </div>`;
        }
        return `<md-list-item class="job-item" data-status="${escapeHtml(j.status)}">
            <md-icon slot="start" class="job-status-icon" data-color="${color}">${icon}</md-icon>
            <div slot="headline">${escapeHtml(j.filename)}</div>
            <div slot="supporting-text">${escapeHtml(j.from)} &rarr; ${escapeHtml(j.to)} · <span class="job-time">${time}</span>${prog}${stageHtml}${elapsedHtml}${slmHtml}</div>
            <div slot="trailing-supporting-text"><md-assist-chip label="${label}" class="status-chip"></md-assist-chip></div>
            ${actions}${!active ? `<md-icon-button slot="end" data-action="delete" data-job-id="${j.id}"><md-icon>delete</md-icon></md-icon-button>` : ''}
        </md-list-item><md-divider></md-divider>`;
    }).join('');
    if (localJobs.some(j => ['PENDING','PROCESSING','STARTED'].includes(j.status))) startElapsedTimer();
}

window.cancelJob = async (id) => { if (await confirmAction('Cancel?', 'Stop?')) { await fetch(`/api/cancel/${id}`, { method: 'POST', headers: { 'X-CSRFToken': csrfToken } }); fetchJobs(); } };
window.deleteJob = async (id) => { if (await confirmAction('Delete?', 'Remove?')) { await fetch(`/api/delete/${id}`, { method: 'POST', headers: { 'X-CSRFToken': csrfToken } }); fetchJobs(); } };
window.retryJob = async (id) => { await fetch(`/api/retry/${id}`, { method: 'POST', headers: { 'X-CSRFToken': csrfToken } }); fetchJobs(); showAlert('success', 'Retried.'); };

// Delegated click handler for job-row action buttons (retry/cancel/delete) —
// these buttons are rendered into innerHTML above, so onclick="" attributes
// would need script-src 'unsafe-inline'; data-action + delegation doesn't.
document.getElementById('jobs-list').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const { action, jobId } = btn.dataset;
    if (action === 'retry') window.retryJob(jobId);
    else if (action === 'cancel') window.cancelJob(jobId);
    else if (action === 'delete') window.deleteJob(jobId);
});

// --- Captures Section ---
let captureJobs = [];

async function fetchCaptures() {
    try {
        const r = await fetch('/api/captures');
        captureJobs = await r.json();
        renderCaptures();
    } catch (e) {}
}

function renderCaptures() {
    const section = document.getElementById('captures-section');
    const list = document.getElementById('captures-list');
    const msg = document.getElementById('no-captures-msg');
    if (!captureJobs.length) {
        section.classList.add('hidden');
        return;
    }
    section.classList.remove('hidden');
    msg.classList.add('hidden');
    list.innerHTML = captureJobs.map(j => {
        const ts = parseFloat(j.created_at);
        const time = new Date(ts * (ts < 10000000000 ? 1000 : 1)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const active = ['PENDING', 'PROCESSING', 'STARTED'].includes(j.status);
        let icon = 'pending', color = 'secondary', label = 'Waiting', actions = '';
        if (j.status === 'SUCCESS') {
            icon = 'check_circle'; color = 'primary'; label = 'Done';
            const dlUrl = j.download_url || `/download/${j.id}`;
            actions = `<a href="${dlUrl}" download slot="end" title="Download"><md-icon-button><md-icon>download</md-icon></md-icon-button></a>`;
        } else if (j.status === 'FAILURE') {
            icon = 'error'; color = 'error'; label = 'Failed';
        }
        const prog = active
            ? (j.progress ? `<md-linear-progress value="${j.progress/100}" class="job-progress"></md-linear-progress>`
                          : `<md-linear-progress indeterminate class="job-progress"></md-linear-progress>`)
            : '';
        return `<md-list-item class="job-item">
            <md-icon slot="start" class="job-status-icon" data-color="${color}">${icon}</md-icon>
            <div slot="headline">${escapeHtml(j.filename || 'Captured Document')}</div>
            <div slot="supporting-text">capture &rarr; ${escapeHtml(j.to || 'markdown')} · <span class="job-time">${time}</span>${prog}</div>
            <div slot="trailing-supporting-text"><md-assist-chip label="${label}" class="status-chip"></md-assist-chip></div>
            ${actions}
        </md-list-item><md-divider></md-divider>`;
    }).join('');
}

// Merge capture job_updates into captureJobs
socket.on('job_update', (u) => {
    if (u.from === 'capture') {
        const i = captureJobs.findIndex(j => j.id === u.id);
        if (i !== -1) captureJobs[i] = { ...captureJobs[i], ...u }; else captureJobs.unshift(u);
        renderCaptures();
    }
});

fetchCaptures();
setInterval(fetchCaptures, 30000);

// --- Service Status (single fetch per 10s interval) ---
async function checkServices() {
    try {
        const r = await fetch('/api/status/services');
        const d = await r.json();

        // Marker service banner
        const banner = document.getElementById('status-banner');
        const txt = document.getElementById('status-text');
        const statusProgress = document.getElementById('status-progress');
        const pdfOpt = document.querySelector('md-select-option[value="pdf_marker"]');

        if (d.marker && d.marker !== 'ready') {
            banner.classList.remove('hidden');
            const isError = d.marker === 'error';
            banner.className = `alert-card alert-${isError ? 'error' : 'warning'}`;
            const icon = banner.querySelector('md-icon');
            if (icon) icon.textContent = isError ? 'error' : 'hourglass_empty';
            txt.textContent = `Marker: ${d.marker.toUpperCase()}${d.llm_download_eta ? ` · ETA ${d.llm_download_eta}` : ''}`;
            if (statusProgress) statusProgress.classList.toggle('hidden', isError);
            if (pdfOpt) { pdfOpt.disabled = true; if (fromSelect.value === 'pdf_marker') fromSelect.value = ''; }
        } else {
            banner.classList.add('hidden');
            if (pdfOpt) pdfOpt.disabled = false;
        }

        // GPU status — reuse same response, no second fetch
        updateGPUStatus(d);
    } catch(e) { console.error('checkServices error:', e); }
}
setInterval(checkServices, 10000);
checkServices();

// --- GPU Status ---
function updateGPUStatus(data) {
    const chip = document.getElementById('gpu-status-chip');
    const label = chip && chip.querySelector('.gpu-label');
    if (!chip || !label) return;

    const gpu_status = data.gpu_status || 'initializing';
    const gpu_info = data.gpu_info || {};

    chip.classList.remove('gpu-available', 'gpu-unavailable', 'gpu-initializing');

    if (gpu_status === 'available') {
        chip.classList.add('gpu-available');
        const model = gpu_info.model || 'GPU';
        label.textContent = model.replace('NVIDIA GeForce ', '').replace('NVIDIA ', '');
    } else if (gpu_status === 'unavailable') {
        chip.classList.add('gpu-unavailable');
        label.textContent = 'CPU Mode';
        const pdfMarkerOpt = document.querySelector('md-select-option[value="pdf_marker"]');
        if (pdfMarkerOpt) pdfMarkerOpt.disabled = true;
    } else {
        chip.classList.add('gpu-initializing');
        label.textContent = 'Detecting...';
    }
}

async function refreshGPUDetails() {
    try {
        const r = await fetch('/api/status/services');
        const data = await r.json();
        const gpu_info = data.gpu_info || {};

        const statusEl = document.getElementById('modal-gpu-status');
        statusEl.textContent = gpu_info.status || 'unknown';
        statusEl.className = 'status-badge status-' + (gpu_info.status || 'unknown');

        document.getElementById('modal-gpu-model').textContent = gpu_info.model || 'N/A';
        document.getElementById('modal-gpu-cuda').textContent = gpu_info.cuda_version || 'N/A';
        document.getElementById('modal-gpu-driver').textContent = gpu_info.driver_version || 'N/A';
        document.getElementById('modal-gpu-util').textContent = gpu_info.utilization !== undefined ? `${gpu_info.utilization}%` : 'N/A';

        if (gpu_info.vram_total) {
            const used = gpu_info.vram_total - (gpu_info.vram_available || 0);
            const pct = Math.round((used / gpu_info.vram_total) * 100);
            document.getElementById('modal-gpu-vram').textContent = `${(gpu_info.vram_available || 0).toFixed(1)} / ${gpu_info.vram_total} GB free`;
            const bar = document.getElementById('vram-bar');
            if (bar) bar.style.width = `${pct}%`;
        } else {
            document.getElementById('modal-gpu-vram').textContent = 'N/A';
        }
    } catch(e) { console.error('Error refreshing GPU details:', e); }
}

// GPU chip click — open modal and refresh details
const gpuChip = document.getElementById('gpu-status-chip');
if (gpuChip) {
    gpuChip.addEventListener('click', () => {
        refreshGPUDetails();
        document.getElementById('gpu-details-modal').show();
    });
}

// GPU details modal close button
const gpuModalCloseBtn = document.getElementById('gpu-modal-close-btn');
if (gpuModalCloseBtn) {
    gpuModalCloseBtn.addEventListener('click', () => {
        document.getElementById('gpu-details-modal').close();
    });
}

// WebSocket listener for real-time GPU status updates from warmup
socket.on('gpu_status_update', (data) => {
    updateGPUStatus(data);
});
