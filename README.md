# DocuFlux

![Coverage](https://img.shields.io/badge/coverage-73%25-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)

Containerized document conversion service combining **Pandoc** (universal converter), **Marker AI** (deep learning PDF), and a local **SLM** for intelligent metadata extraction — all behind a modern Material Design UI and a full REST API.

## Features

- **AI-Powered PDF Conversion**: Marker 2.0 (deep learning) converts PDFs to clean Markdown, delegating OCR/layout inference to a shared GPU-accelerated Surya VLM inference server.
- **Hybrid PDF Reconstruction**: `pdf_hybrid` engine tries Pandoc first (fast), then falls back to Marker AI if quality is poor — best of both worlds.
- **Local Document Intelligence**: Built-in Small Language Model (`llama-cpp-python`) extracts titles, summaries, and tags without external API calls. Models load eagerly at worker startup via `warmup.py`.
- **Browser Extension Capture API**: Capture sessions let Chrome/Firefox extensions POST page screenshots and HTML in batches, which are assembled asynchronously into a single document.
- **Vision-Based Extraction (MCP)**: A Model Context Protocol server backed by Playwright enables vision-capable agents to interact with web pages for content extraction.
- **Agentic Page Turning**: Autonomous multi-page extraction from web readers via generated navigation scripts.
- **API Key Authentication**: `dk_`-prefixed keys managed via `/api/v1/auth/keys`. Pass as `X-API-Key` header.
- **Webhook Callbacks**: Register a POST URL per job — fired on completion or failure.
- **End-to-End Security**: AES-256-GCM encryption at rest for files and metadata; Redis TLS in transit; optional Cloudflare Tunnel helper (`deploy/cloudflare/setup.sh`) for HTTPS.
- **On-Demand GPU OCR**: With `OCR_MODE=on_demand` (default), the Surya VLM is started when a Marker job needs it and unloaded after an idle timeout, freeing VRAM. `cpu_only` mode never starts it (Tesseract OCR instead).
- **Observability**: Prometheus metrics endpoint, Grafana dashboard, structured JSON logging with X-Request-ID correlation.
- **Kubernetes-Ready**: Seven manifests (`deploy/k8s/`) with HPA scaling 1–10 replicas.

## Supported Formats

| Format | Key | Direction | Engine |
|--------|-----|-----------|--------|
| Pandoc Markdown | `markdown` | Input & Output | Pandoc |
| GitHub Flavored Markdown | `gfm` | Input & Output | Pandoc |
| HTML5 | `html` | Input & Output | Pandoc |
| Jupyter Notebook | `ipynb` | Input & Output | Pandoc |
| Microsoft Word | `docx` | Input & Output | Pandoc |
| Microsoft PowerPoint | `pptx` | Output Only | Pandoc |
| OpenOffice / LibreOffice | `odt` | Input & Output | Pandoc |
| Rich Text Format | `rtf` | Input & Output | Pandoc |
| EPUB (v3) | `epub3` | Input & Output | Pandoc |
| EPUB (v2) | `epub2` | Input & Output | Pandoc |
| LaTeX | `latex` | Input & Output | Pandoc |
| AsciiDoc | `asciidoc` | Input & Output | Pandoc |
| reStructuredText | `rst` | Input & Output | Pandoc |
| BibTeX | `bibtex` | Input & Output | Pandoc |
| MediaWiki | `mediawiki` | Input & Output | Pandoc |
| Jira Wiki | `jira` | Input & Output | Pandoc |
| PDF (via XeLaTeX) | `pdf` | Output Only | Pandoc |
| PDF (High Accuracy) | `pdf_marker` | Input Only | Marker AI |
| PDF (Hybrid) | `pdf_hybrid` | Input Only | Pandoc → Marker |
| PDF (SLM) | `pdf_marker_slm` | Input Only | Marker + SLM metadata |

## Tech Stack

- **Frontend**: Flask, HTML5, [Material Web Components](https://github.com/material-components/material-web), Socket.IO
- **Task Queue**: Celery with Redis broker (15 task types)
- **Conversion Engines**: [Pandoc](https://pandoc.org/), [Marker](https://github.com/VikParuchuri/marker), [llama-cpp-python](https://github.com/abetlen/llama-cpp-python), [Playwright](https://playwright.dev/)
- **Observability**: Prometheus, Grafana, structured JSON logging
- **Infrastructure**: Docker Compose (base + cpu/gpu/tls overlays), Kubernetes, NVIDIA Container Toolkit, optional [Cloudflare Tunnel](https://www.cloudflare.com/products/tunnel/)
- **Configuration**: Pydantic Settings — type-safe, hierarchical (env vars → `.env` → Docker secrets)

## Architecture

| Service | Description |
|---------|-------------|
| **`web`** | Flask frontend: uploads, UI, REST API, WebSocket server (port 5000) |
| **`worker`** | Heavy-lane Celery worker (`-Q gpu`, concurrency 1): Marker AI (thin client), hybrid, SLM-assisted conversion |
| **`worker-light`** | Light-lane Celery worker (`-Q high_priority,default`, no GPU): Pandoc, OCR, metadata, capture assembly, maintenance — never blocked by long Marker jobs (15 task types total across both lanes) |
| **`surya-vlm`** | Shared Surya VLM inference server (vLLM, port 8000 internal) — hosts the model weights Marker uses; started/stopped on demand by `surya-controller` |
| **`surya-controller`** | Owns start/stop of `surya-vlm`: reconciles the container against desired state in Redis (idle unload, manual load/unload, `OCR_MODE`) |
| **`docker-socket-proxy`** | Allowlist-only Docker API proxy (port 2375, isolated `control` network) — the only path from `surya-controller` to the host Docker socket |
| **`mcp-server`** | Playwright server for vision-based and agentic extraction (port 8080 internal) |
| **`redis`** | Celery broker + job metadata store |
| **`beat`** | Celery Beat scheduler for cleanup and metrics tasks |

```
Browser → Flask (5000) → Redis → Celery Worker
                ↓                      ↓
           WebSocket              Pandoc / Marker / SLM
                ↑                      ↓
           job_update ←─────── Shared Volume (data/)
```

## Quick Start

**Prerequisites**: Docker, Docker Compose. GPU: [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) (optional).

```bash
git clone https://github.com/aerocristobal/DocuFlux.git
cd DocuFlux
cp .env.example .env   # edit SECRET_KEY and any optional settings
```

```bash
# Auto-detect GPU (recommended)
./scripts/build.sh auto
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up -d

# CPU-only (~3 GB image, no Marker AI)
./scripts/build.sh cpu
docker-compose -f docker-compose.yml -f docker-compose.cpu.yml up -d

# With Redis TLS
docker-compose -f docker-compose.yml -f docker-compose.tls.yml up -d
```

Open `http://localhost:5000`. For HTTPS via Cloudflare Tunnel, run `deploy/cloudflare/setup.sh` (the former `docker-compose.cloudflare.yml` overlay was removed; see the [archived setup guide](docs/archive/CLOUDFLARE_TUNNEL_SETUP.md), deprecated).

With the default `OCR_MODE=on_demand`, `surya-vlm` is **not** running at idle (an `exited` container is normal): `surya-controller` starts it when a Marker job needs it (allow a few minutes of cold start while the Surya OCR weights load) and stops it after an idle timeout.

## On-Demand OCR (Surya)

`OCR_MODE` selects how the GPU OCR engine is managed (Compose only; Kubernetes runs `surya-vlm` as a standing Deployment):

- `on_demand` (default): a conversion needing Marker asks `surya-controller` to load `surya-vlm`; the worker waits up to `SURYA_COLD_START_TIMEOUT_SECONDS` (600). After `SURYA_IDLE_TIMEOUT_MINUTES` (15) with no running or queued Marker work it is unloaded and VRAM is freed.
- `cpu_only`: Surya is never started; scanned PDFs use Tesseract and Marker formats are hidden from the UI. Use this on hosts without a suitable GPU (the CPU overlay disables `surya-vlm`, `surya-controller` and `docker-socket-proxy`).

The dashboard GPU modal ("AI engine (Surya)") shows the live state and lets an admin switch OCR mode, set the idle timeout, and Load/Unload manually (a manual load pins the server until unloaded). The same controls are available over the API (`ADMIN_API_SECRET` bearer for changes): `GET/POST /api/v1/admin/surya` and `GET/PUT /api/v1/admin/ocr-settings`. Details: [ARCHITECTURE.md §7.3](docs/ARCHITECTURE.md#73-model-lifecycle) and ADR-002a, [CONFIGURATION.md](docs/CONFIGURATION.md#on-demand-ocr-settings).

## REST API

All `/api/v1/` endpoints require `X-API-Key: dk_...` where noted. Keys are managed via `/api/v1/auth/keys`.

### Authentication

API key and webhook endpoints require admin authentication via `Authorization: Bearer <ADMIN_API_SECRET>`.

```bash
# Create an API key (requires admin secret)
curl -X POST http://localhost:5000/api/v1/auth/keys \
  -H "Authorization: Bearer $ADMIN_API_SECRET" \
  -H "Content-Type: application/json" -d '{"label": "my-app"}'
# {"api_key": "dk_abc123...", "created_at": "1711036800.0", "label": "my-app"}

# Revoke a key (requires admin secret)
curl -X DELETE http://localhost:5000/api/v1/auth/keys/dk_abc123... \
  -H "Authorization: Bearer $ADMIN_API_SECRET"
```

### Document Conversion

```bash
# Submit a conversion job (requires API key)
curl -X POST http://localhost:5000/api/v1/convert \
  -H "X-API-Key: dk_abc123..." \
  -F "file=@report.pdf" \
  -F "to_format=markdown" \
  -F "engine=pdf_hybrid"

# Convert with advanced Pandoc options (TOC, custom font size)
curl -X POST http://localhost:5000/api/v1/convert \
  -H "X-API-Key: dk_abc123..." \
  -F "file=@report.md" \
  -F "to_format=pdf" \
  -F 'pandoc_options={"toc": true, "variables": {"fontsize": "11pt"}}'

# → {"job_id": "550e8400-...", "status": "queued", "status_url": "/api/v1/status/550e8400-..."}

# Poll status (no key required)
curl http://localhost:5000/api/v1/status/550e8400-e29b-41d4-a716-446655440000

# Download result
curl -OJ http://localhost:5000/api/v1/download/550e8400-e29b-41d4-a716-446655440000
```

**POST /api/v1/convert parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `file` | Yes | Document to convert |
| `to_format` | Yes | Target format key (e.g. `markdown`, `docx`) |
| `from_format` | No | Source format key (auto-detected from extension) |
| `engine` | No | `pandoc`, `marker`, or `pdf_hybrid` |
| `force_ocr` | No | Enable OCR in Marker (default: false) |
| `use_llm` | No | Use LLM assist in Marker (default: false) |
| `pandoc_options` | No | JSON object of Pandoc options (engine=pandoc only, see [API docs](docs/API.md#pandoc-options)) |

### All Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/convert` | POST | Key | Submit conversion job |
| `/api/v1/status/{job_id}` | GET | — | Job status and progress |
| `/api/v1/download/{job_id}` | GET | Key | Download converted file |
| `/api/v1/formats` | GET | — | List all supported formats |
| `/api/v1/jobs/{job_id}/extract-metadata` | POST | Key | Run SLM metadata extraction |
| `/api/v1/auth/keys` | POST | Admin | Create API key |
| `/api/v1/auth/keys/{key}` | DELETE | Admin | Revoke API key |
| `/api/v1/admin/dlq` | GET | Admin | Dead letter queue contents |
| `/api/v1/admin/captures` | GET | Admin | Full capture index |
| `/api/v1/admin/surya` | GET | — | Surya lifecycle state (state, desired, pinned, mode) |
| `/api/v1/admin/surya` | POST | Admin | Manual Surya `{"action": "load"\|"unload"}` |
| `/api/v1/admin/ocr-settings` | GET | — | OCR mode and idle timeout |
| `/api/v1/admin/ocr-settings` | PUT | Admin | Update OCR mode and/or idle timeout |
| `/api/v1/webhooks` | POST | Key | Register webhook for a job |
| `/api/v1/webhooks/{job_id}` | GET | Key | Get webhook registration |
| `/api/v1/capture/sessions` | POST | — | Create browser capture session |
| `/api/v1/capture/sessions/{id}/pages` | POST | — | Upload pages to session |
| `/api/v1/capture/sessions/{id}/images` | POST | — | Upload image to session |
| `/api/v1/capture/sessions/{id}/finish` | POST | — | Assemble session into document |
| `/api/v1/capture/sessions/{id}/status` | GET | — | Capture session status |
| `/api/status/services` | GET | — | Service + GPU health |
| `/api/health` | GET | — | Detailed structured health check |
| `/healthz` | GET | — | Liveness probe |
| `/readyz` | GET | — | Readiness probe |
| `/metrics` | GET | — | Prometheus scrape (worker, port 9090) |

Auth legend: **Key** = `X-API-Key: dk_...` header, **Admin** = `Authorization: Bearer <ADMIN_API_SECRET>` header.

### Webhooks

```bash
# Register a callback URL for a job (requires API key)
curl -X POST http://localhost:5000/api/v1/webhooks \
  -H "X-API-Key: dk_abc123..." \
  -H "Content-Type: application/json" \
  -d '{"job_id": "550e8400-...", "webhook_url": "https://yourapp.com/callback"}'

# DocuFlux POSTs this payload on completion:
# {"job_id": "...", "status": "SUCCESS", "download_url": "/api/v1/download/..."}
```

### Browser Extension Capture API

The capture API accepts page content (screenshots + HTML) in batches from browser extensions. The CORS policy allows `chrome-extension://*` and `moz-extension://*` origins.

```bash
# 1. Create a capture session
SESSION=$(curl -s -X POST http://localhost:5000/api/v1/capture/sessions \
  -H "Content-Type: application/json" \
  -d '{"to_format": "markdown", "title": "My Book"}' | jq -r .session_id)

# 2. Upload pages as JSON (up to 1000/hr, 500 pages/session)
curl -X POST http://localhost:5000/api/v1/capture/sessions/$SESSION/pages \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/page/1", "title": "Chapter 1", "text": "# Chapter 1\nContent..."}'

# 3. Finish and assemble
curl -X POST http://localhost:5000/api/v1/capture/sessions/$SESSION/finish

# 4. Poll the returned job_id with /api/v1/status/{job_id}
```

## Kubernetes

Seven manifests are provided under `deploy/k8s/` (`namespace`, `secrets`, `network-policies`, `redis`, `web`, `worker`, `surya-vlm`), including `surya-vlm.yaml` for the shared inference server. The on-demand lifecycle (`OCR_MODE`, `surya-controller`) is Compose-only. The web deployment uses an HPA (1–10 replicas).

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/secrets.yaml
kubectl apply -f deploy/k8s/network-policies.yaml
kubectl apply -f deploy/k8s/redis.yaml
kubectl apply -f deploy/k8s/web.yaml    # 2 initial replicas + HPA
kubectl apply -f deploy/k8s/worker.yaml # CPU + GPU worker deployments
kubectl apply -f deploy/k8s/surya-vlm.yaml
```

## Observability

**Prometheus metrics** (scraped from worker on port 9090):

| Metric | Type | Description |
|--------|------|-------------|
| `docuflux_conversion_total` | Counter | Conversions by format and status |
| `docuflux_conversion_duration_seconds` | Histogram | Conversion latency |
| `docuflux_conversion_failures_total` | Counter | Failures by format and error type |
| `docuflux_queue_depth` | Gauge | Tasks waiting per queue |
| `docuflux_worker_tasks_active` | Gauge | Tasks in progress |
| `docuflux_gpu_utilization_percent` | Gauge | GPU utilization |
| `docuflux_gpu_memory_used_bytes` | Gauge | GPU VRAM in use |
| `docuflux_gpu_memory_total_bytes` | Gauge | Total GPU VRAM |
| `docuflux_gpu_temperature_celsius` | Gauge | GPU temperature |
| `docuflux_disk_usage_bytes` | Gauge | Data directory usage |
| `docuflux_disk_total_bytes` | Gauge | Total disk capacity |

Import `docs/grafana-dashboard.json` into Grafana to get a pre-built dashboard.

**Load testing** (SLAs: p95 < 2 s for `/api/v1/convert`, p95 < 200 ms for `/api/v1/status`, error rate < 1%):

```bash
pip install locust
locust -f tests/load/locustfile.py --host http://localhost:5000
# Headless CI run:
locust -f tests/load/locustfile.py --headless -u 20 -r 5 --run-time 60s \
  --host http://localhost:5000 --csv=tests/load/results
```

## Configuration

Key environment variables (full list in [docs/CONFIGURATION.md](docs/CONFIGURATION.md)):

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | *(required)* | Flask session key |
| `MASTER_ENCRYPTION_KEY` | *(required)* | AES-256 master key for file encryption |
| `REDIS_METADATA_URL` | `redis://redis:6379/1` | Job metadata store |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Task broker |
| `MAX_CONTENT_LENGTH` | `209715200` (200 MB) | Max upload size |
| `MARKER_ENABLED` | `false` | Enable Marker AI (set `true` in GPU builds) |
| `MAX_MARKER_PAGES` | `600` | PDF page limit for Marker/Hybrid |
| `SLM_MODEL_PATH` | *(none)* | Path to GGUF model for SLM extraction |
| `MAX_SLM_CONTEXT` | `2000` | Token context limit for SLM |
| `CAPTURE_SESSION_TTL` | `86400` | Browser capture session TTL (seconds) |
| `MAX_CAPTURE_PAGES` | `500` | Max pages per capture session |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `BEHIND_PROXY` | `false` | Enable ProxyFix for Cloudflare/nginx |
| `ADMIN_API_SECRET` | *(none)* | Bearer secret for admin endpoints (API keys, DLQ, Surya/OCR controls); admin API is disabled if unset |
| `OCR_MODE` | `on_demand` | `on_demand` (Surya loaded when needed, unloaded when idle) or `cpu_only` |
| `SURYA_IDLE_TIMEOUT_MINUTES` | `15` | Idle minutes before Surya is unloaded |
| `SURYA_COLD_START_TIMEOUT_SECONDS` | `600` | Max wait for Surya to become healthy after a load request |

## Data Retention

| Status | Downloaded | Deleted After |
|--------|-----------|---------------|
| Completed | Yes | 10 minutes post-download |
| Completed | No | 1 hour post-completion |
| Failed | — | 5 minutes post-failure |

Cleanup runs every 5 minutes via Celery Beat (`tasks.cleanup_old_files`).

## Development

### Project Structure

```
docuflux/
├── web/
│   ├── app.py              # Flask app, middleware, auth
│   ├── routes/             # 6 route blueprints (admin_surya, auth, capture, conversion, health, webhooks)
│   ├── validation.py       # Input validation (MIME, UUID, SSRF, filename)
│   ├── templates/          # Material Design 3 UI
│   └── Dockerfile
├── worker/
│   ├── tasks/              # Celery tasks (capture, conversion, maintenance, metadata, surya_lifecycle)
│   ├── warmup.py           # GPU detection + SLM eager load
│   ├── metrics.py          # Prometheus metrics definitions
│   └── Dockerfile          # Multi-stage GPU/CPU build
├── shared/                 # Shared modules (encryption, storage, formats, config, keys, secrets, surya_state)
├── surya-controller/       # On-demand start/stop controller for surya-vlm
├── extension-src/          # Chrome/Firefox browser extension source
├── mcp_server/             # Playwright MCP server for vision extraction
├── oscal/                  # NIST SP 800-53 component definition + SSP
├── deploy/
│   ├── cloudflare/         # Cloudflare Tunnel config + setup
│   ├── certs/              # TLS certificates
│   ├── docker-socket-proxy/ # HAProxy allowlist for the controller's Docker access
│   ├── monitoring/         # Prometheus alert rules
│   └── k8s/                # Kubernetes manifests (namespace, secrets, network-policies, redis, web, worker, surya-vlm)
├── scripts/build.sh        # Build wrapper (auto/gpu/cpu)
├── tests/
│   ├── unit/               # Pytest unit tests (~1000 tests; ~1360 across the full suite)
│   ├── integration/        # E2E + encryption pipeline tests
│   └── load/locustfile.py  # Locust load tests
├── docs/                   # 12 markdown docs + OpenAPI spec + Grafana dashboard (historical docs in docs/archive/)
└── docker-compose*.yml     # 4 Compose files (base + gpu/cpu/tls overlays)
```

### Testing

```bash
# Run all tests
pytest

# With coverage report
pytest --cov=web --cov=worker --cov-report=term-missing

# Run specific test file
pytest tests/unit/test_web.py -v

# Integration tests (requires running services)
pytest tests/integration/ -v
```

### Code Validation

```bash
python3 -m py_compile web/app.py worker/tasks/conversion.py worker/warmup.py
```

## Documentation

| Document | Description |
|----------|-------------|
| [Product Requirements (PRD)](docs/PRD.md) | Product overview, personas, requirements, success metrics |
| [Architecture](docs/ARCHITECTURE.md) | C4-style system views, data flows, security architecture, ADRs |
| [Improvement Backlog](docs/user-stories/BACKLOG.md) | Prioritized epics: conversion quality, OCR, hardening, optimization |
| [Build Guide](BUILD.md) | GPU/CPU build profiles and deployment |
| [API Reference](docs/API.md) | Full REST API documentation |
| [UI Specification](docs/UI_SPECIFICATION.md) | UI elements, workflows, and state machine |
| [OpenAPI Spec](docs/openapi.yaml) | Machine-readable API spec |
| [Configuration](docs/CONFIGURATION.md) | All environment variables |
| [Supported Formats](docs/FORMATS.md) | Format matrix and conversion rules |
| [Deployment Guide](docs/DEPLOYMENT.md) | Production deployment instructions |
| [AI Integration](docs/AI_INTEGRATION.md) | Marker, SLM, MCP details |
| [Cloudflare Tunnel Setup](docs/archive/CLOUDFLARE_TUNNEL_SETUP.md) | Deprecated/archived; use `deploy/cloudflare/setup.sh` |
| [Certificate Management](docs/CERTIFICATE_MANAGEMENT.md) | TLS certificate setup |
| [Alerting](docs/ALERTING.md) | Prometheus alerting rules |
| [Security Fixes](docs/archive/SECURITY_FIXES.md) | Historical security remediation stories |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [CJK Implementation](docs/archive/IMPLEMENTATION_SUMMARY_CJK.md) | CJK/Unicode support implementation record |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and PR guidelines.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
