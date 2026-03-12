# DocuFlux API Reference (AI Agent Optimized)

Machine-readable API documentation for AI coding agents.
Base URL: `{{ base_url }}`

## Quick Start

1. **Create an API key** → `POST /api/v1/auth/keys`
2. **Submit a conversion** → `POST /api/v1/convert` (multipart: file + to_format)
3. **Poll for completion** → `GET /api/v1/status/<job_id>` (or register a webhook)
4. **Download the result** → `GET /api/v1/download/<job_id>`

## Authentication

Most endpoints are open (IP-rate-limited). Endpoints that require auth use the `X-API-Key` header.

```
X-API-Key: dk_...
```

### Create a key

```
POST /api/v1/auth/keys
Content-Type: application/json

{"label": "my-integration"}
```

Response (201):
```json
{"api_key": "dk_...", "created_at": "1709000000.0", "label": "my-integration"}
```

Store the key — it is not shown again.

### Revoke a key

```
DELETE /api/v1/auth/keys/<key>
```

## Endpoint Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/auth/keys` | No | Create API key |
| DELETE | `/api/v1/auth/keys/<key>` | No | Revoke API key |
| POST | `/api/v1/convert` | No | Submit conversion job |
| GET | `/api/v1/status/<job_id>` | No | Poll job status |
| GET | `/api/v1/download/<job_id>` | No | Download result |
| GET | `/api/v1/formats` | No | List supported formats |
| POST | `/api/v1/webhooks` | No | Register webhook for job notifications |
| GET | `/api/v1/webhooks/<job_id>` | No | Get registered webhook for a job |
| POST | `/api/v1/jobs/<job_id>/extract-metadata` | Yes | Trigger SLM metadata extraction |

### POST /api/v1/convert

Multipart form data:

| Field | Required | Description |
|-------|----------|-------------|
| file | Yes | The document file |
| to_format | Yes | Target format key (e.g. `markdown`, `docx`, `pdf`) |
| from_format | No | Source format key (auto-detected from extension if omitted) |
| engine | No | `pandoc` (default) or `marker` |
| force_ocr | No | Force OCR for Marker engine (default: false) |
| use_llm | No | Use LLM for Marker engine (default: false) |
| pandoc_options | No | JSON object of Pandoc options (engine=pandoc only). See [Pandoc Options](#pandoc-options-reference) |

Response (202):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "status_url": "/api/v1/status/550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-02-01T19:42:10Z"
}
```

### GET /api/v1/status/<job_id>

Response (200):
```json
{
  "job_id": "...",
  "status": "success",
  "progress": 100,
  "filename": "document.pdf",
  "from_format": "pdf_marker",
  "to_format": "markdown",
  "engine": "marker",
  "created_at": "...",
  "download_url": "/api/v1/download/...",
  "is_multifile": false,
  "file_count": 1
}
```

### GET /api/v1/download/<job_id>

Returns the converted file directly. Multi-file outputs are returned as a ZIP archive.

### GET /api/v1/formats

Returns JSON array of all supported formats with keys, directions, and extensions.

### POST /api/v1/webhooks

```json
{"job_id": "<uuid>", "webhook_url": "https://example.com/hook"}
```

When the job completes (SUCCESS or FAILURE), DocuFlux POSTs a JSON payload to the registered URL.

## Supported Formats

### Input formats
{% for f in formats_in %}
- `{{ f }}`
{% endfor %}

### Output formats
{% for f in formats_out %}
- `{{ f }}`
{% endfor %}

## Status Values

| Status | Meaning |
|--------|---------|
| `queued` | Job accepted, waiting for worker |
| `processing` | Conversion in progress |
| `success` | Done — download available |
| `failure` | Conversion failed — `error` field has details |

## Rate Limits

- **1000 requests/day** per IP
- **200 requests/hour** per IP
- API key creation: **10/hour**
- Key revocation: **30/hour**
- Webhook registration: **60/hour**

## Curl Examples

```bash
# 1. Create an API key
curl -s -X POST {{ base_url }}/api/v1/auth/keys \
  -H "Content-Type: application/json" \
  -d '{"label": "ai-agent"}'

# 2. Convert a PDF to Markdown
curl -s -X POST {{ base_url }}/api/v1/convert \
  -F "file=@document.pdf" \
  -F "to_format=markdown"

# 3. Poll status (replace JOB_ID)
curl -s {{ base_url }}/api/v1/status/JOB_ID

# 4. Download result
curl -s -O {{ base_url }}/api/v1/download/JOB_ID

# 5. Convert DOCX to HTML
curl -s -X POST {{ base_url }}/api/v1/convert \
  -F "file=@report.docx" \
  -F "to_format=html"

# 6. Register a webhook instead of polling
curl -s -X POST {{ base_url }}/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{"job_id": "JOB_ID", "webhook_url": "https://example.com/hook"}'

# 7. List supported formats
curl -s {{ base_url }}/api/v1/formats
```

## Pandoc Options Reference

When using `engine=pandoc`, you can pass a `pandoc_options` JSON object to control Pandoc behavior. For PDF output, CJK font defaults (`xelatex`, `Noto Sans CJK SC`) apply automatically unless overridden.

| Key | Pandoc Flag | Type | Constraint |
|-----|-------------|------|------------|
| `pdf_engine` | `--pdf-engine` | enum | `xelatex`, `lualatex`, `pdflatex`, `tectonic`, `wkhtmltopdf` |
| `toc` | `--toc` | bool | |
| `toc_depth` | `--toc-depth` | int | 1–6 |
| `number_sections` | `--number-sections` | bool | |
| `highlight_style` | `--highlight-style` | enum | `pygments`, `tango`, `espresso`, `zenburn`, `kate`, `monochrome`, `breezedark`, `haddock` |
| `listings` | `--listings` | bool | |
| `dpi` | `--dpi` | int | 72–600 |
| `columns` | `--columns` | int | 1–200 |
| `standalone` | `--standalone` | bool | |
| `wrap` | `--wrap` | enum | `auto`, `none`, `preserve` |
| `strip_comments` | `--strip-comments` | bool | |
| `shift_heading_level_by` | `--shift-heading-level-by` | int | -5 to 5 |
| `variables` | `--variable` | object | Keys: `mainfont`, `CJKmainfont`, `monofont`, `fontsize`, `geometry`, `linestretch`, `margin-left`, `margin-right`, `margin-top`, `margin-bottom`, `papersize`, `documentclass` |
| `metadata` | `--metadata` | object | Keys: `title`, `author`, `date`, `lang`, `subject`, `description` |

### Example with Pandoc options

```bash
curl -s -X POST {{ base_url }}/api/v1/convert \
  -F "file=@report.md" \
  -F "to_format=pdf" \
  -F 'pandoc_options={"toc": true, "number_sections": true, "variables": {"fontsize": "11pt", "geometry": "margin=0.75in"}}'
```

**Security note:** Options like `--filter`, `--lua-filter`, `--template`, and `--include-*` are deliberately excluded to prevent arbitrary file reads or code execution.

## Error Responses

All errors return JSON with an `error` field:
```json
{"error": "description of what went wrong"}
```

Common HTTP status codes: 400 (bad request), 404 (not found), 410 (expired), 422 (unsupported format), 429 (rate limited), 507 (storage full).
