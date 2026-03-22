# DocuFlux UI Specification

Source of truth for the web UI. Backend changes must not break the behaviors documented here. Regression tests in `tests/ui/test_ui_workflows.py` enforce these contracts.

## Page Layout

- **Header** (64px, sticky): Logo (`a.logo` → `/`), GPU status chip (`#gpu-status-chip`), theme toggle (`#theme-toggle` + `#theme-menu`)
- **Main** (max-width 1200px, centered): 2-column grid on desktop (≥900px), 1-column on mobile
  - **Left column** (360px): "New Conversion" card with upload form
  - **Right column** (flex): "My Conversions" card with job list
  - **Below** (full width): "Recent Captures" section (`#captures-section`, hidden until captures exist)
- **Dialogs**: Confirmation (`#action-dialog`), GPU details (`#gpu-details-modal`)

## User Workflows

### 1. File Conversion

1. User drags file onto drop zone (`#drop-zone`) or clicks to open file picker (`input#file`)
2. Drop zone shows filename(s); "From Format" auto-detects from file extension
3. User selects "From Format" (`#from_format`) → "To Format" (`#to_format`) enables with compatible options
4. If `from_format === 'pdf_marker'`: Marker options panel (`#marker-options`) appears with Force OCR and Use LLM checkboxes
5. User clicks "Convert Now" (`#convert-btn`)
6. Submit progress bar (`#submit-progress`) appears; button disables
7. `POST /convert` sends `FormData` with CSRF token
8. On success: "Submitted!" alert, form clears, `fetchJobs()` called
9. On error: error alert with server message
10. On network error: "Network error" alert

### 2. Job Monitoring

- **Initial load**: WebSocket connect → `fetchJobs()` → render job list
- **Real-time**: `socket.on('job_update')` merges update into `localJobs` → re-render
- **Polling**: `fetchJobs()` every 30 seconds (skipped if tab hidden)
- **Elapsed timer**: Re-renders every 5 seconds while any job is PENDING/PROCESSING/STARTED
- **Progress bar**: Indeterminate spinner when `progress == 0`, determinate bar when `progress > 0`

### 3. Job Actions

| Action | Trigger | Confirmation | Endpoint | Response |
|--------|---------|-------------|----------|----------|
| Cancel | Click `close` icon on active job | "Cancel?" / "Stop?" dialog | `POST /api/cancel/{id}` | `{status: 'cancelled'}` |
| Delete | Click `delete` icon on inactive job | "Delete?" / "Remove?" dialog | `POST /api/delete/{id}` | `{status: 'deleted'}` |
| Retry | Click `replay` icon on failed/revoked job | None | `POST /api/retry/{id}` | `{status: 'retried', new_job_id: '...'}` |
| Download | Click `download`/`folder_zip` on completed job | None | `GET /download/{id}` or `/download_zip/{id}` | Binary file |

All mutating actions send `X-CSRFToken` header. All trigger `fetchJobs()` after completion.

### 4. Capture Session Monitoring

- Fetched via `GET /api/captures` every 30 seconds
- WebSocket `job_update` events where `from === 'capture'` update capture list
- Section hidden when no captures exist
- Completed captures show download button; failures show error icon

### 5. Service Status

- Polled via `GET /api/status/services` every 10 seconds
- **Marker banner** (`#status-banner`): Shows when `marker !== 'ready'` with status text and ETA
  - Disables `pdf_marker` format option when Marker not ready
- **GPU chip**: Updates status (available/unavailable/initializing), model name
  - Click opens GPU details modal with VRAM bar, CUDA version, driver, utilization
- **WebSocket**: `gpu_status_update` events update GPU chip in real-time

### 6. Theme Switching

- Three modes: System (default), Light, Dark
- Stored in `localStorage('theme')`
- Applied via `window.applyTheme()` toggling `.dark-theme` class on `<html>`
- System mode responds to `prefers-color-scheme` media query changes

## Job Status State Machine

```
PENDING → PROCESSING → SUCCESS
                    → FAILURE
       → REVOKED (via cancel)
```

### Per-Status UI Rendering

| Status | Icon | Color | Label | Progress | Actions | Border |
|--------|------|-------|-------|----------|---------|--------|
| PENDING | `pending` | secondary | "Waiting" | Indeterminate spinner | Cancel | outline-variant left |
| PROCESSING | `pending` | secondary | "Waiting" | Determinate bar (if progress > 0) or spinner | Cancel | primary left |
| STARTED | `pending` | secondary | "Waiting" | Determinate bar or spinner | Cancel | primary left |
| SUCCESS | `check_circle` | primary | "Done" | None | Download, Delete | green (#22c55e) left |
| FAILURE | `error` | error | "Failed" | None | Retry, Delete | error left |
| REVOKED | `cancel` | outline | "Cancelled" | None | Retry, Delete | — |

### Additional State-Dependent Elements

- **Stage text**: Shows `j.stage` + page count when job is active
- **Elapsed timer**: Shows `Xm YYs` since `started_at` when job is active
- **SLM metadata**: Shows AI title, summary, and tag chips when `j.slm` exists (SUCCESS only)
- **Error message**: Available via `j.result` when status is FAILURE

## Backend Endpoints Consumed by UI

### Job List (Critical Path)

| Endpoint | Method | Rate Limited | Purpose | Expected Response |
|----------|--------|-------------|---------|-------------------|
| `/api/jobs` | GET | **Exempt** | Poll job list | `[{id, filename, from, to, created_at, status, progress, result, download_url, is_zip, file_count, slm, stage, page_count, started_at}]` |
| `/api/captures` | GET | **Exempt** | Poll capture list | `[{id, filename, from, to, created_at, status, progress, result, download_url, is_zip}]` |

**Critical contract**: These endpoints MUST be rate-limit-exempt. The UI polls every 30 seconds. If rate-limited, the job list goes stale and users see stuck/missing jobs.

### Conversion Submission

| Endpoint | Method | Body | Expected Response |
|----------|--------|------|-------------------|
| `POST /convert` | POST | `multipart/form-data` (file, from_format, to_format, force_ocr?, use_llm?) | `{job_ids: [...], status: 'queued'}` |

**Critical contract**: The submitted job MUST appear in `/api/jobs` on the next poll. This requires the job to be added to both `job:{id}` Redis hash AND `history:{session_id}` Redis list.

### Job Actions

| Endpoint | Method | Expected Response |
|----------|--------|-------------------|
| `POST /api/cancel/{id}` | POST | `{status: 'cancelled'}` |
| `POST /api/delete/{id}` | POST | `{status: 'deleted'}` |
| `POST /api/retry/{id}` | POST | `{status: 'retried', new_job_id: '...'}` |

### Service Status

| Endpoint | Method | Expected Response |
|----------|--------|-------------------|
| `GET /api/status/services` | GET | `{disk_space: str, marker?: str, marker_status?: str, llm_download_eta?: str, gpu_status?: str, gpu_info?: {model, cuda_version, driver_version, utilization, vram_total, vram_available}}` |

### Static Content

| Endpoint | Method | Expected Response |
|----------|--------|-------------------|
| `GET /` | GET | HTML with `formats` JSON embedded, CSRF token in meta tag, Material Design components |

## Job Data Shape (`/api/jobs` response items)

```
{
  id: string              // UUID
  filename: string        // Original filename
  from: string            // Source format key
  to: string              // Target format key
  created_at: number      // Unix timestamp (float)
  status: string          // PENDING | PROCESSING | STARTED | SUCCESS | FAILURE | REVOKED
  progress: string        // "0" to "100" (string from Redis)
  result: string | null   // Error message on FAILURE, null otherwise
  download_url: string | null  // Set on SUCCESS
  is_zip: boolean         // True if multiple output files
  file_count: number      // Number of output files
  slm: object | null      // {title, tags: [], summary} on SUCCESS with SLM
  stage: string           // Current processing stage (empty if not set)
  page_count: string      // Page count (empty if not set)
  started_at: string      // Unix timestamp string (empty if not started)
}
```

**Known quirk**: `progress` is a string (from Redis). The UI must use `parseInt(j.progress)` to compare numerically. The string `'0'` is truthy in JavaScript — use `parseInt(j.progress) > 0` for progress bar logic.

## UI Element IDs Reference

### Header
- `#gpu-status-chip` — GPU status button (classes: `gpu-available`, `gpu-unavailable`, `gpu-initializing`)
- `#theme-toggle` — Theme toggle icon button
- `#theme-menu` — Theme dropdown (items: `[data-theme="system|light|dark"]`)

### Conversion Form
- `#convert-form` — Main form element
- `#drop-zone` — File drop area (class: `drop-zone--dragover` when active)
- `#drop-zone-prompt` — Drop zone text
- `#file` — Hidden file input (multiple)
- `#from_format` — Source format select
- `#to_format` — Target format select (disabled until source selected)
- `#marker-options` — Marker checkbox panel (hidden unless pdf_marker)
- `#force_ocr` — Force OCR checkbox
- `#use_llm` — Use LLM checkbox
- `#submit-progress` — Indeterminate progress bar (hidden during idle)
- `#convert-btn` — Submit button

### Status & Alerts
- `#status-banner` — Marker status warning banner (hidden when ready)
- `#status-text` — Banner message text
- `#status-progress` — Banner progress bar
- `#alert-area` — Temporary alert container (auto-clears after 5s)

### Job List
- `#jobs-list` — Job list container (`md-list`)
- `#no-jobs-msg` — Empty state message (hidden when jobs exist)
- `.job-item` — Individual job items (has `data-status` attribute)

### Captures
- `#captures-section` — Captures container (hidden when empty)
- `#captures-list` — Capture list container
- `#no-captures-msg` — Empty state for captures

### Dialogs
- `#action-dialog` — Confirmation dialog
- `#dialog-headline` — Dialog title
- `#dialog-body` — Dialog message
- `#dialog-cancel` — Cancel button
- `#dialog-confirm` — Confirm button
- `#gpu-details-modal` — GPU details dialog
- `#modal-gpu-status` — GPU status badge
- `#modal-gpu-model` — GPU model text
- `#modal-gpu-vram` — VRAM text
- `#vram-bar` — VRAM usage bar (width = percentage)
- `#modal-gpu-cuda` — CUDA version
- `#modal-gpu-driver` — Driver version
- `#modal-gpu-util` — Utilization percentage

## WebSocket Events

| Event | Direction | Data | UI Behavior |
|-------|-----------|------|-------------|
| `connect` | Server → Client | — | Triggers `fetchJobs()` |
| `job_update` | Server → Client | Job object (partial) | Merges into `localJobs` or `captureJobs`, re-renders |
| `gpu_status_update` | Server → Client | `{gpu_status, gpu_info}` | Updates GPU chip |

## Polling Intervals

| What | Interval | Endpoint | Condition |
|------|----------|----------|-----------|
| Job list | 30s | `/api/jobs` | Tab visible |
| Captures | 30s | `/api/captures` | Always |
| Service status | 10s | `/api/status/services` | Always |
| Elapsed timer | 5s | — (re-render only) | Any active job |
