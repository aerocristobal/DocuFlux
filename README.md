# DocuFlux

![Coverage](https://img.shields.io/badge/coverage-73.79%25-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)

Containerized document conversion service combining **Pandoc** (universal converter), **Marker AI** (deep learning PDF), and a local **SLM** for intelligent metadata extraction — all behind a modern Material Design UI and a full REST API.

## Features

- **AI-Powered PDF Conversion**: Marker (deep learning) converts PDFs to clean Markdown with GPU acceleration, falling back to CPU automatically.
- **Hybrid PDF Reconstruction**: `pdf_hybrid` engine tries Pandoc first (fast), then falls back to Marker AI if quality is poor — best of both worlds.
- **Local Document Intelligence**: Built-in Small Language Model (`llama-cpp-python`) extracts titles, summaries, and tags without external API calls. Models load eagerly at worker startup via `warmup.py`.
- **Browser Extension Capture API**: Capture sessions let Chrome/Firefox extensions POST page screenshots and HTML in batches, which are assembled asynchronously into a single document.
- **Vision-Based Extraction (MCP)**: A Model Context Protocol server backed by Playwright enables vision-capable agents to interact with web pages for content extraction.
- **Agentic Page Turning**: Autonomous multi-page extraction from web readers via generated navigation scripts.
- **API Key Authentication**: `dk_`-prefixed keys managed via `/api/v1/auth/keys`. Pass as `X-API-Key` header.
- **Webhook Callbacks**: Register a POST URL per job — fired on completion or failure.
- **End-to-End Security**: AES-256-GCM encryption at rest for files and metadata; Redis TLS in transit; Cloudflare Tunnel for zero-touch HTTPS.
- **Observability**: Prometheus metrics endpoint, Grafana dashboard, structured JSON logging with X-Request-ID correlation.
- **Kubernetes-Ready**: Five manifests (`k8s/`) with HPA scaling 1–10 replicas.

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

## Tech Stack

- **Frontend**: Flask, HTML5, [Material Web Components](https://github.com/material-components/material-web), Socket.IO
- **Task Queue**: Celery with Redis broker (11 task types)
- **Conversion Engines**: [Pandoc](https://pandoc.org/), [Marker](https://github.com/VikParuchuri/marker), [llama-cpp-python](https://github.com/abetlen/llama-cpp-python), [Playwright](https://playwright.dev/)
- **Observability**: Prometheus, Grafana, structured JSON logging
- **Infrastructure**: Docker Compose (5 variants), Kubernetes, NVIDIA Container Toolkit, [Cloudflare Tunnel](https://www.cloudflare.com/products/tunnel/)
- **Configuration**: Pydantic Settings — type-safe, hierarchical (env vars → `.env` → Docker secrets)

## Architecture

| Service | Description |
|---------|-------------|
| **`web`** | Flask frontend: uploads, UI, REST API, WebSocket server |
| **`worker`** | Celery worker: Pandoc, Marker AI, SLM, capture assembly (11 tasks) |
| **`mcp-server`** | Playwright server for vision-based and agentic extraction |
| **`redis`** | Celery broker + job metadata store |
| **`beat`** | Celery Beat scheduler for cleanup and metrics tasks |
| **`cloudflare-tunnel`** | Optional zero-touch HTTPS (add `-f docker-compose.cloudflare.yml`) |

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
git clone https://github.com/yourusername/docuflux.git
cd docuflux
cp .env.example .env   # edit SECRET_KEY and any optional settings
```

```bash
# Auto-detect GPU (recommended)
./scripts/build.sh auto
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up -d

# CPU-only (~3 GB image, no Marker AI)
./scripts/build.sh cpu
docker-compose -f docker-compose.yml -f docker-compose.cpu.yml up -d

# With Cloudflare Tunnel (HTTPS)
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.cloudflare.yml up -d

# With Redis TLS
docker-compose -f docker-compose.yml -f docker-compose.tls.yml up -d
```

Open `http://localhost:5000`. GPU models are pre-cached during build; first run is fast.

## REST API

All `/api/v1/` endpoints require `X-API-Key: dk_...` where noted. Keys are managed via `/api/v1/auth/keys`.

### Authentication

```bash
# Create an API key
curl -X POST http://localhost:5000/api/v1/auth/keys \
  -H "Content-Type: application/json" -d '{"name": "my-app"}'
# {"key": "dk_abc123...", "name": "my-app"}

# Delete a key
curl -X DELETE http://localhost:5000/api/v1/auth/keys/dk_abc123...
```

### Document Conversion

```bash
# Submit a conversion job (requires API key)
curl -X POST http://localhost:5000/api/v1/convert \
  -H "X-API-Key: dk_abc123..." \
  -F "file=@report.pdf" \
  -F "to_format=markdown" \
  -F "engine=pdf_hybrid"

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

### All Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/convert` | POST | Key | Submit conversion job |
| `/api/v1/status/{job_id}` | GET | — | Job status and progress |
| `/api/v1/download/{job_id}` | GET | — | Download converted file |
| `/api/v1/formats` | GET | — | List all supported formats |
| `/api/v1/auth/keys` | POST | — | Create API key |
| `/api/v1/auth/keys/{key}` | DELETE | — | Revoke API key |
| `/api/v1/webhooks` | POST | — | Register webhook for a job |
| `/api/v1/webhooks/{job_id}` | GET | — | Get webhook registration |
| `/api/v1/jobs/{job_id}/extract-metadata` | POST | Key | Run SLM metadata extraction |
| `/api/v1/capture/sessions` | POST | — | Create browser capture session |
| `/api/v1/capture/sessions/{id}/pages` | POST | — | Upload pages to session |
| `/api/v1/capture/sessions/{id}/finish` | POST | — | Assemble session into document |
| `/api/v1/capture/sessions/{id}/status` | GET | — | Capture session status |
| `/api/status/services` | GET | — | Service + GPU health |
| `/api/health` | GET | — | Detailed structured health check |
| `/healthz` | GET | — | Liveness probe |
| `/readyz` | GET | — | Readiness probe |
| `/metrics` | GET | — | Prometheus scrape (worker, port 9090) |

### Webhooks

```bash
# Register a callback URL for a job
curl -X POST http://localhost:5000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{"job_id": "550e8400-...", "url": "https://yourapp.com/callback"}'

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

# 2. Upload pages (up to 1000/hr, 500 pages/session, 50 pages/batch)
curl -X POST http://localhost:5000/api/v1/capture/sessions/$SESSION/pages \
  -F "page_index=0" -F "screenshot=@page0.png" -F "html=<page0.html"

# 3. Finish and assemble
curl -X POST http://localhost:5000/api/v1/capture/sessions/$SESSION/finish

# 4. Poll the returned job_id with /api/v1/status/{job_id}
```

## Kubernetes

Five manifests are provided under `k8s/`. The web deployment uses an HPA (1–10 replicas).

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/redis.yaml
kubectl apply -f k8s/web.yaml    # 2 initial replicas + HPA
kubectl apply -f k8s/worker.yaml # CPU + GPU worker deployments
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
| `MAX_MARKER_PAGES` | `300` | PDF page limit for Marker/Hybrid |
| `SLM_MODEL_PATH` | *(none)* | Path to GGUF model for SLM extraction |
| `MAX_SLM_CONTEXT` | `2000` | Token context limit for SLM |
| `CAPTURE_SESSION_TTL` | `86400` | Browser capture session TTL (seconds) |
| `MAX_CAPTURE_PAGES` | `500` | Max pages per capture session |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `BEHIND_PROXY` | `false` | Enable ProxyFix for Cloudflare/nginx |
| `CLOUDFLARE_TUNNEL_TOKEN` | *(none)* | Cloudflare Tunnel credential |

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
├── web/app.py              # Flask routes + REST API (1707 lines)
├── web/templates/          # Material Design 3 UI
├── worker/tasks.py         # 11 Celery tasks (1727 lines)
├── worker/warmup.py        # GPU detection + SLM eager load (213 lines)
├── worker/metrics.py       # Prometheus metrics definitions
├── worker/Dockerfile       # Multi-stage GPU/CPU build
├── config.py               # Pydantic Settings (25+ env vars)
├── k8s/                    # 5 Kubernetes manifests
├── scripts/build.sh        # Build wrapper (auto/gpu/cpu)
├── tests/
│   ├── unit/               # Pytest unit tests
│   ├── integration/        # WebSocket + pipeline E2E tests
│   └── load/locustfile.py  # Locust load tests
├── docs/                   # 13 markdown docs + OpenAPI spec + Grafana dashboard
└── docker-compose*.yml     # 5 Compose variants (base/gpu/cpu/tls/cloudflare)
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

# API integration smoke test
./tests/test_api_v1_integration.sh
```

### Code Validation

```bash
python3 -m py_compile web/app.py worker/tasks.py worker/warmup.py
```

## Documentation

| Document | Description |
|----------|-------------|
| [API Reference](docs/API.md) | Full REST API documentation |
| [OpenAPI Spec](docs/openapi.yaml) | Machine-readable API spec |
| [Configuration](docs/CONFIGURATION.md) | All environment variables |
| [Supported Formats](docs/FORMATS.md) | Format matrix and conversion rules |
| [Deployment Guide](docs/DEPLOYMENT.md) | Production deployment instructions |
| [AI Integration](docs/AI_INTEGRATION.md) | Marker, SLM, MCP details |
| [Cloudflare Tunnel Setup](docs/CLOUDFLARE_TUNNEL_SETUP.md) | HTTPS via Cloudflare |
| [Certificate Management](docs/CERTIFICATE_MANAGEMENT.md) | TLS certificate setup |
| [Alerting](docs/ALERTING.md) | Prometheus alerting rules |
| [Security Fixes](docs/SECURITY_FIXES.md) | Security changelog |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and solutions |

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
