# DocuFlux REST API v1 Documentation

Comprehensive documentation for the DocuFlux REST API v1.

## Table of Contents

- [Overview](#overview)
- [Authentication](#authentication)
- [Rate Limiting](#rate-limiting)
- [Endpoints](#endpoints)
  - [Document Conversion](#document-conversion)
  - [API Key Management](#api-key-management)
  - [Webhooks](#webhooks)
  - [Browser Capture Sessions](#browser-capture-sessions)
  - [Surya VLM Lifecycle](#surya-vlm-lifecycle)
  - [Health Checks](#health-checks)
  - [Pandoc Options](#pandoc-options)
- [Error Handling](#error-handling)
- [Examples](#examples)

## Overview

The DocuFlux REST API v1 provides:

- **Asynchronous document conversion** with job tracking
- **Multiple conversion engines**: Pandoc (universal) and Marker (AI-powered PDF)
- **API key authentication** for secure access
- **Webhook callbacks** for job completion notifications
- **Browser capture sessions** for multi-page content extraction
- **SLM metadata extraction** (titles, summaries, tags)
- **Format auto-detection** from file extensions
- **Multi-file output** with automatic ZIP bundling

**Base URL**: `http://localhost:5000` (or your deployment URL)

**API Version**: v1 (spec 1.4.0)

**Content Types**:
- Request: `multipart/form-data` (file uploads) or `application/json`
- Response: `application/json`

## Authentication

The API uses two authentication mechanisms:

### API Key Authentication

Most endpoints require an API key passed via the `X-API-Key` header. Keys use the `dk_` prefix and are managed via the admin endpoints below.

```bash
curl -H "X-API-Key: dk_abc123..." http://localhost:5000/api/v1/convert ...
```

### Admin Authentication

Key management and admin endpoints require the `ADMIN_API_SECRET` via a Bearer token:

```bash
curl -H "Authorization: Bearer $ADMIN_API_SECRET" http://localhost:5000/api/v1/auth/keys ...
```

### Public Endpoints

These endpoints require no authentication:
- `GET /api/v1/status/{job_id}` — job status polling
- `GET /api/v1/formats` — list supported formats
- `POST /api/v1/capture/sessions` — create capture session
- `POST /api/v1/capture/sessions/{id}/pages` — upload pages
- `POST /api/v1/capture/sessions/{id}/images` — upload images
- `POST /api/v1/capture/sessions/{id}/finish` — finalize session
- `GET /api/v1/capture/sessions/{id}/status` — session status
- `GET /api/v1/admin/surya` — read-only Surya lifecycle state
- `GET /api/v1/admin/ocr-settings` — read-only OCR settings
- `GET /healthz`, `GET /readyz`, `GET /api/health`, `GET /api/status/services`

## Rate Limiting

Endpoints have per-route rate limits enforced via Redis-backed Flask-Limiter:

| Endpoint | Limit |
|----------|-------|
| `POST /api/v1/convert` | 200/hour |
| `POST /api/v1/capture/sessions` | 200/hour |
| `POST /api/v1/capture/sessions/{id}/pages` | 1000/hour |
| `POST /api/v1/capture/sessions/{id}/images` | 2000/hour |
| `POST /api/v1/capture/sessions/{id}/finish` | 200/hour |
| `POST /api/v1/webhooks` | 60/hour |
| `POST /api/v1/auth/keys` | 10/hour |
| `DELETE /api/v1/auth/keys/{key}` | 30/hour |
| `GET /api/v1/admin/dlq` | 30/hour |
| `GET /api/v1/admin/captures` | 30/hour |
| `GET /api/v1/admin/surya` | 60/minute |
| `POST /api/v1/admin/surya` | 30/minute |
| `GET /api/v1/admin/ocr-settings` | 60/minute |
| `PUT /api/v1/admin/ocr-settings` | 30/minute |

When the rate limit is exceeded:

```json
HTTP/1.1 429 Too Many Requests
{"error": "429 Too Many Requests: Rate limit exceeded"}
```

## Endpoints

### Document Conversion

#### POST /api/v1/convert

Submit a document conversion job. Requires API key.

**Request**: `multipart/form-data`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | File | Yes | Document file to convert |
| `to_format` | String | Yes | Target format key (e.g., `markdown`, `pdf`, `docx`) |
| `from_format` | String | No | Source format key (auto-detected from extension if omitted) |
| `engine` | String | No | `pandoc` (default) or `marker` |
| `force_ocr` | Boolean | No | Force OCR for Marker engine (default: false) |
| `use_llm` | Boolean | No | Use LLM for Marker engine (default: false) |
| `pandoc_options` | JSON | No | Pandoc options object (engine=pandoc only). See [Pandoc Options](#pandoc-options) |

**Success Response (202 Accepted)**:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "status_url": "/api/v1/status/550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-02-01T19:42:10Z"
}
```

**Error Responses**: 400 (missing field), 401 (missing/invalid API key), 409 (OCR mode forbids the AI engine, see below), 422 (invalid format/engine), 507 (disk full)

**OCR mode (`OCR_MODE`)**: Marker-backed conversions (`engine=marker`, i.e. internal formats `pdf_marker`, `pdf_hybrid`, `pdf_marker_slm`) depend on the Surya VLM, whose lifecycle is governed by the OCR mode (see [Surya VLM Lifecycle](#surya-vlm-lifecycle)). The check runs before the upload is saved, so a rejection leaves no job behind.

| OCR mode | Marker conversion behavior |
|----------|----------------------------|
| `cpu_only` | Rejected with `409 {"error": "AI engine disabled (OCR mode is cpu_only); use the OCR engine instead"}` |
| `on_demand` (default) | Accepted (202). The API flags Surya as desired and restarts its idle clock; the lifecycle controller starts it if stopped, and the queued job waits for it. Pandoc and plain OCR conversions are unaffected. |

The same 409 applies to the Web UI endpoints `POST /convert` and `POST /api/retry/{job_id}`.

**Example**:

```bash
curl -X POST http://localhost:5000/api/v1/convert \
  -H "X-API-Key: dk_abc123..." \
  -F "file=@document.pdf" \
  -F "to_format=markdown" \
  -F "engine=marker"
```

---

#### GET /api/v1/status/{job_id}

Check job status. No authentication required.

**Success Response (200 OK)**:

```json
{
  "job_id": "550e8400-...",
  "status": "pending",
  "progress": 0,
  "filename": "document.pdf",
  "from_format": "pdf",
  "to_format": "markdown",
  "engine": "pandoc",
  "created_at": "2026-02-01T19:42:10Z"
}
```

Additional fields appear based on status:
- **processing**: `started_at`
- **success**: `completed_at`, `download_url`, `is_multifile`, `file_count`, `metadata` (pages, images_extracted, tables_detected)
- **failure**: `completed_at`, `error`

**Status Values**: `pending`, `processing`, `success`, `failure`, `completed-with-warnings`

`completed-with-warnings` (Story 1.3) means the job converted successfully
but the output scored `poor` on the quality report — `download_url` is
still present, this is not a failure. Whenever a `quality` report exists
for the job (Markdown/GFM output via Pandoc, hybrid's accepted Pandoc
path, or OCR), it's included regardless of grade:

```json
"quality": {
  "grade": "poor",
  "score": 22,
  "reasons": ["low_word_density", "no_headings"],
  "metrics": {
    "total_words": 340, "page_count": 12, "words_per_page": 28.3,
    "garbage_ratio": 0.01, "has_headings": 0, "malformed_tables": 0,
    "empty_page_ratio": 0.4
  }
}
```

**Error Responses**: 400 (invalid UUID), 404 (not found)

---

#### GET /api/v1/download/{job_id}

Download converted file(s). Requires API key.

- **Single file**: Returns file with `Content-Disposition: attachment`
- **Multi-file**: Returns ZIP archive

**Error Responses**: 404 (not found / not completed), 410 (files expired)

---

#### GET /api/v1/formats

List supported formats and conversions. No authentication required.

**Success Response (200 OK)**:

```json
{
  "input_formats": [
    {"name": "PDF Document", "key": "pdf", "extension": ".pdf", "mime_types": [...], "supports_marker": true, "supports_pandoc": true}
  ],
  "output_formats": [
    {"name": "Pandoc Markdown", "key": "markdown", "extension": ".md"}
  ],
  "conversions": [
    {"from": "pdf", "to": "markdown", "engines": ["pandoc", "marker"], "recommended_engine": "marker"}
  ]
}
```

---

#### POST /api/v1/jobs/{job_id}/extract-metadata

Trigger SLM metadata extraction on a completed job. Requires API key.

**Success Response (202 Accepted)**:

```json
{"job_id": "550e8400-...", "status": "queued", "message": "SLM extraction queued"}
```

**Error Responses**: 404 (not found / no markdown output), 409 (job not in SUCCESS state)

---

### API Key Management

All key management endpoints require admin authentication (`Authorization: Bearer <ADMIN_API_SECRET>`).

#### POST /api/v1/auth/keys

Create a new API key.

**Request**: `application/json`

```json
{"label": "my-integration"}
```

**Success Response (201 Created)**:

```json
{"api_key": "dk_abc123...", "created_at": "1711036800.0", "label": "my-integration"}
```

**Error Responses**: 401 (missing auth), 403 (invalid secret), 503 (admin secret not configured)

---

#### DELETE /api/v1/auth/keys/{key}

Revoke an API key.

**Success Response (200 OK)**:

```json
{"revoked": true}
```

**Error Responses**: 401, 403, 404 (key not found)

---

#### GET /api/v1/admin/captures

Global capture index across every client (operator view; `GET /api/captures` is scoped to the caller). Requires the admin secret.

**Query Parameters**: `limit` (int, default 50, max 1000)

**Error Responses**: 401, 403, 503 (admin secret not configured)

---

#### GET /api/v1/admin/dlq

Retrieve dead letter queue contents. Requires the admin secret.

**Query Parameters**: `limit` (int, default 100, max 1000)

**Success Response (200 OK)**:

```json
{"count": 2, "total": 5, "entries": [{...}, {...}]}
```

---

### Webhooks

Webhook endpoints require API key authentication (`X-API-Key`).

#### POST /api/v1/webhooks

Register a webhook URL for job completion notifications.

**Request**: `application/json`

```json
{"job_id": "550e8400-...", "webhook_url": "https://yourapp.com/callback"}
```

**Success Response (201 Created)**:

```json
{"job_id": "550e8400-...", "webhook_url": "https://yourapp.com/callback", "registered": true}
```

**Validation**: HTTPS required, private IPs blocked (SSRF protection).

**Error Responses**: 400 (invalid job_id or URL), 404 (job not found)

---

#### GET /api/v1/webhooks/{job_id}

Get the registered webhook for a job. Requires API key.

**Success Response (200 OK)**:

```json
{"job_id": "550e8400-...", "webhook_url": "https://yourapp.com/callback"}
```

**Error Responses**: 404 (not found / no webhook registered)

---

### Browser Capture Sessions

The capture API accepts page content from browser extensions. CORS allows `chrome-extension://*` and `moz-extension://*` origins. No authentication required.

#### POST /api/v1/capture/sessions

Create a new capture session.

**Request**: `application/json`

```json
{"title": "My Document", "to_format": "markdown", "source_url": "https://...", "force_ocr": false}
```

**Success Response (201 Created)**:

```json
{"session_id": "uuid", "job_id": "uuid", "status": "active", "max_pages": 500}
```

---

#### POST /api/v1/capture/sessions/{session_id}/pages

Submit a captured page to a session.

**Request**: `application/json`

```json
{
  "url": "https://example.com/page/1",
  "title": "Page Title",
  "text": "Page content...",
  "images": [],
  "extraction_method": "generic",
  "page_hint": 1,
  "page_sequence": 42
}
```

**Success Response (200 OK)**:

```json
{"status": "accepted", "page_count": 5}
```

Duplicate pages (same `page_sequence`) return `{"status": "duplicate", "page_count": 5}`.

**Error Responses**: 404 (session not found), 409 (session not active), 422 (max pages reached)

---

#### POST /api/v1/capture/sessions/{session_id}/images

Upload a large image separately from page submission.

**Request**: `multipart/form-data`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `image` | File | Yes | Image file |
| `alt` | String | No | Alt text |
| `is_screenshot` | Boolean | No | Whether this is a screenshot |

**Success Response (200 OK)**:

```json
{"image_ref": "images/hash.jpg", "status": "uploaded"}
```

**Error Responses**: 400 (no image / invalid path), 404 (session not found), 409 (session not active)

---

#### POST /api/v1/capture/sessions/{session_id}/finish

Finalize capture session and queue assembly into a document.

**Success Response (202 Accepted)**:

```json
{"job_id": "uuid", "status": "assembling", "status_url": "/api/v1/status/{job_id}"}
```

**Error Responses**: 404 (not found), 409 (already finished), 422 (no pages captured)

---

#### GET /api/v1/capture/sessions/{session_id}/status

Poll capture session status.

**Success Response (200 OK)**:

```json
{
  "session_id": "uuid",
  "status": "active",
  "page_count": 5,
  "title": "My Document",
  "to_format": "markdown",
  "job_id": "uuid",
  "status_url": "/api/v1/status/{job_id}"
}
```

**Error Responses**: 404 (not found)

---

### Surya VLM Lifecycle

The Surya VLM (GPU AI engine behind Marker conversions) is started and stopped on demand by a lifecycle controller. State lives in Redis; these endpoints inspect and steer it. Reads are public; writes require `Authorization: Bearer $ADMIN_API_SECRET`.

Lifecycle states: `stopped`, `starting`, `running`, `stopping`, `error`. Surya is *supported* only on hosts with a GPU reporting at least 16 GB total VRAM.

#### GET /api/v1/admin/surya

No authentication. Rate limit 60/minute.

**Success Response (200 OK)**:

```json
{
  "state": "stopped",
  "desired": "stopped",
  "pinned": false,
  "surya_supported": true,
  "mode": "on_demand",
  "idle_timeout_min": 15
}
```

`desired` is `running` or `stopped` (what the controller is driving toward); `pinned` is true after a manual load.

---

#### POST /api/v1/admin/surya

Manually load or unload Surya. Admin Bearer auth. Rate limit 30/minute.

**Request**: `application/json`

```json
{"action": "load"}
```

| `action` | Effect |
|----------|--------|
| `load` | Sets desired state to `running` and **pins** Surya: a pinned instance is not unloaded by the idle timeout |
| `unload` | Sets desired state to `stopped` and clears the pin |

**Success Response (202 Accepted)**: the snapshot shown above (the controller applies the change asynchronously; poll `GET /api/v1/admin/surya` or listen for the `surya_state_update` WebSocket event). A `surya_state_update` event is also emitted to connected dashboards.

**Error Responses**: 400 (`action` not `load`/`unload`), 401 (missing/malformed Authorization header), 403 (invalid admin secret), 409 (`load` while the host has no GPU with enough VRAM, or while OCR mode is `cpu_only`), 429, 503 (`ADMIN_API_SECRET` not configured)

---

#### GET /api/v1/admin/ocr-settings

No authentication. Rate limit 60/minute.

**Success Response (200 OK)**: `{"mode": "on_demand", "idle_timeout_min": 15}`

---

#### PUT /api/v1/admin/ocr-settings

Update either or both fields at runtime (stored in Redis, overriding `OCR_MODE` / `SURYA_IDLE_TIMEOUT_MINUTES`). Admin Bearer auth. Rate limit 30/minute.

**Request**: `application/json`

| Field | Type | Description |
|-------|------|-------------|
| `mode` | String | `cpu_only` (Marker formats rejected with 409, Surya never started) or `on_demand` |
| `idle_timeout_min` | Integer | Minutes of inactivity before an unpinned Surya is unloaded; must be >= 1 |

**Success Response (200 OK)**: the updated settings, `{"mode": ..., "idle_timeout_min": ...}`. Also emits `surya_state_update`.

**Error Responses**: 400 (body not a JSON object, invalid `mode`, or `idle_timeout_min` not an integer >= 1), 401, 403, 429, 503

---

#### WebSocket event: `surya_state_update`

Emitted over Socket.IO after a successful `POST /api/v1/admin/surya` or `PUT /api/v1/admin/ocr-settings`. Controller-driven transitions (e.g. `starting` to `running`) are not pushed; they are visible via polling `GET /api/status/services`. Payload:

```json
{"surya_state": "running", "ocr_mode": "on_demand", "idle_timeout_min": 15, "surya_supported": true}
```

---

### Health Checks

No authentication required.

| Endpoint | Response |
|----------|----------|
| `GET /healthz` | `OK` (200) — liveness probe |
| `GET /readyz` | `{"status": "ready", "redis": "connected", "timestamp": ...}` (200) or 503 |
| `GET /api/health` | `{"status": "healthy", "timestamp": ..., "components": {...}}` (200) with Redis, disk, GPU, Celery worker status |
| `GET /api/status/services` | `{"disk_space": "ok", "marker": "ready", ...}` (200); includes Surya fields below |

`GET /api/status/services` fields: `disk_space` (`ok`/`low`), `marker`, `marker_status`, `llm_download_eta`, `models_cached`, `gpu_status`, `gpu_info`, and the Surya lifecycle fields `surya_state` (`stopped`/`starting`/`running`/`stopping`/`error`), `ocr_mode` (`cpu_only`/`on_demand`), `idle_timeout_min` (int) and `surya_supported` (bool). `GET /api/health` reports the GPU under `components.gpu` (`status`, `info`) and does not include Surya fields.

---

### Pandoc Options

When using `engine=pandoc`, pass a `pandoc_options` JSON object to control Pandoc behavior. For PDF output, CJK font defaults (`xelatex`, `Noto Sans CJK SC`) apply automatically unless overridden.

**Whitelisted Options**:

| Key | Pandoc Flag | Type | Constraint |
|-----|-------------|------|------------|
| `pdf_engine` | `--pdf-engine` | enum | `xelatex`, `lualatex`, `pdflatex`, `tectonic`, `wkhtmltopdf` |
| `toc` | `--toc` | bool | |
| `toc_depth` | `--toc-depth` | int | 1-6 |
| `number_sections` | `--number-sections` | bool | |
| `highlight_style` | `--highlight-style` | enum | `pygments`, `tango`, `espresso`, `zenburn`, `kate`, `monochrome`, `breezedark`, `haddock` |
| `listings` | `--listings` | bool | |
| `dpi` | `--dpi` | int | 72-600 |
| `columns` | `--columns` | int | 1-200 |
| `standalone` | `--standalone` | bool | |
| `wrap` | `--wrap` | enum | `auto`, `none`, `preserve` |
| `strip_comments` | `--strip-comments` | bool | |
| `shift_heading_level_by` | `--shift-heading-level-by` | int | -5 to 5 |
| `variables` | `--variable` | object | Keys: `mainfont`, `CJKmainfont`, `monofont`, `fontsize`, `geometry`, `linestretch`, `margin-left/right/top/bottom`, `papersize`, `documentclass` |
| `metadata` | `--metadata` | object | Keys: `title`, `author`, `date`, `lang`, `subject`, `description` |

> **Security note:** Options like `--filter`, `--lua-filter`, `--template`, and `--include-*` are deliberately excluded to prevent arbitrary file reads or code execution.

**Example**:

```bash
curl -X POST http://localhost:5000/api/v1/convert \
  -H "X-API-Key: dk_abc123..." \
  -F "file=@report.md" \
  -F "to_format=pdf" \
  -F 'pandoc_options={"toc": true, "number_sections": true, "variables": {"fontsize": "11pt", "geometry": "margin=0.75in"}}'
```

---

## Error Handling

All error responses follow a consistent JSON structure:

```json
{"error": "Human-readable error message"}
```

Some 422 responses include additional detail:

```json
{"error": "Invalid pandoc_options", "details": ["unknown key: foo"]}
```

**Common Error Codes**:

| Code | Description |
|------|-------------|
| 400 | Invalid request parameters |
| 401 | Missing authentication |
| 403 | Invalid API key or admin secret |
| 404 | Resource not found |
| 409 | State conflict (e.g., session already finished, OCR mode `cpu_only` rejecting an AI conversion, Surya load unsupported) |
| 410 | Resource expired/deleted |
| 413 | File exceeds max upload size (200 MB) |
| 422 | Validation error (invalid format, engine, options) |
| 429 | Rate limit exceeded |
| 503 | Admin API disabled (`ADMIN_API_SECRET` not configured) |
| 507 | Server disk full |

## Examples

### Complete Workflow

```bash
# 1. Submit conversion job
RESPONSE=$(curl -s -X POST http://localhost:5000/api/v1/convert \
  -H "X-API-Key: dk_abc123..." \
  -F "file=@document.pdf" \
  -F "to_format=markdown" \
  -F "engine=marker")

JOB_ID=$(echo $RESPONSE | jq -r '.job_id')

# 2. Poll for completion
while true; do
  STATUS=$(curl -s http://localhost:5000/api/v1/status/$JOB_ID | jq -r '.status')
  echo "Status: $STATUS"
  [ "$STATUS" = "success" ] || [ "$STATUS" = "failure" ] && break
  sleep 2
done

# 3. Download result
curl -OJ -H "X-API-Key: dk_abc123..." \
  http://localhost:5000/api/v1/download/$JOB_ID
```

### Python Example

```python
import requests, time, json

BASE = "http://localhost:5000"
HEADERS = {"X-API-Key": "dk_abc123..."}

# Submit
with open("document.pdf", "rb") as f:
    r = requests.post(f"{BASE}/api/v1/convert", headers=HEADERS,
                      files={"file": f}, data={"to_format": "markdown", "engine": "marker"})
job_id = r.json()["job_id"]

# Poll
while True:
    status = requests.get(f"{BASE}/api/v1/status/{job_id}").json()
    if status["status"] in ("success", "failure"):
        break
    time.sleep(2)

# Download
if status["status"] == "success":
    r = requests.get(f"{BASE}{status['download_url']}", headers=HEADERS)
    with open("result.md", "wb") as f:
        f.write(r.content)
```

## Best Practices

1. **Polling interval**: 2-5 seconds for `/api/v1/status/{job_id}`.
2. **Download promptly**: Files are deleted 10 min after download, 1 hour if not downloaded, 5 min after failure.
3. **Format validation**: Call `/api/v1/formats` to verify supported conversions before submitting.
4. **Engine selection**: Use `marker` for PDF-to-Markdown (high quality); `pandoc` for everything else (fast).
5. **Multi-file output**: Check `is_multifile` in status response — download will be a ZIP.
6. **Webhook over polling**: Register a webhook to avoid polling overhead.

## Support

- **GitHub Issues**: https://github.com/aerocristobal/DocuFlux/issues
- **API Docs (machine-readable)**: `GET /api` returns a Markdown reference for AI agents
- **OpenAPI Spec**: [openapi.yaml](openapi.yaml)

## Changelog

### v1.4.0 (2026-10-22)

- On-demand Surya VLM lifecycle: `GET`/`POST /api/v1/admin/surya`, `GET`/`PUT /api/v1/admin/ocr-settings`
- `OCR_MODE`: Marker conversions return `409` in `cpu_only` mode; in `on_demand` mode they request a Surya load
- `GET /api/status/services` adds `surya_state`, `ocr_mode`, `idle_timeout_min`, `surya_supported`
- New `surya_state_update` Socket.IO event
- Documented `GET /api/v1/admin/captures` and `POST /api/v1/capture/sessions/{id}/images` in the OpenAPI spec; OpenAPI version aligned with this changelog

### v1.3.0 (2026-03-21)

- OSCAL compliance integration (NIST SP 800-53)
- Contract tests for all API response schemas
- Encryption pipeline integration tests

### v1.2.0 (2026-03-20)

- API key authentication (`X-API-Key` header)
- Admin key management endpoints
- Webhook registration and callbacks (SSRF-protected)
- Browser capture session endpoints (sessions, pages, images, finish, status)
- SLM metadata extraction endpoint
- Dead letter queue admin endpoint
- Per-endpoint rate limiting
- AES-256-GCM encryption at rest

### v1.1.0 (2026-03-11)

- Advanced Pandoc options support via `pandoc_options` parameter

### v1.0.0 (2026-02-01)

- Initial REST API release
- Endpoints: convert, status, download, formats
- Marker AI engine support
