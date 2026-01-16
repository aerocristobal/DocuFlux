# DocuFlux - Implementation Plan

## Architecture Overview
This project implements a containerized document conversion service using a Task Queue pattern.

- **Web UI (Flask)**: Handles file uploads, status polling, and downloads.
- **Task Queue (Redis)**: Manages communication between the web app and workers.
- **Worker (Celery)**: Executes Pandoc commands and runs local Marker AI models for conversion.
- **Shared Volume**: A shared storage space for input and output files.
- **Ephemeral Data Store (Redis)**: Tracks job metadata to enforce strict data retention policies.

## Tech Stack
- **Backend**: Python 3.11, Flask, Celery.
- **Frontend**: HTML5, Material Web Components (@material/web), Vanilla JavaScript.
- **Infrastructure**: Docker, Docker Compose, Redis, NVIDIA Container Toolkit (for AI).
- **Conversion Engines**: Pandoc 3.1, Marker (AI-powered PDF engine).

---

## Quick Start for New Sessions

**Last Updated**: 2026-01-16

### Critical Files to Understand
| File | Purpose | Lines |
|------|---------|-------|
| `web/app.py` | Flask backend - routes, security, Redis integration, ZIP logic | ~350 |
| `worker/tasks.py` | Celery tasks - Pandoc & Marker (Python API), cleanup | ~320 |
| `worker/warmup.py` | Model pre-caching and health check server | ~80 |
| `web/templates/index.html` | Material Design 3 UI - SPA with Socket.IO | ~290 |
| `docker-compose.yml` | Service orchestration (web, worker, beat, redis) | ~100 |

### Running the Application
```bash
# Start all services (add --build on first run or after code changes)
docker-compose up --build

# Run tests
pytest tests/ -v

# Check test coverage
pytest tests/ --cov=web --cov=worker --cov-report=term-missing

# View logs for a specific service
docker-compose logs -f web
docker-compose logs -f worker
```

### Verifying Everything Works
1. Open http://localhost:5000 in browser
2. Upload a Markdown file, convert to PDF - should complete in seconds
3. Upload a PDF, select "PDF (High Accuracy)" as source - uses Marker AI (runs in worker, requires GPU for best performance)
4. Check `/api/status/services` endpoint for health status

### Common Issues
| Issue | Solution |
|-------|----------|
| Redis connection refused | Ensure redis container is running: `docker-compose up redis` |
| Permission denied on data/ | `chmod -R 777 data/` or fix volume ownership |
| CSRF token missing | Clear browser cookies, reload page |
| GPU not detected | Ensure NVIDIA Container Toolkit is installed and `nvidia-smi` works on host |

### Architecture Notes
- **Marker Integration**: Marker AI engine (`marker-pdf`) is installed directly in the `worker` container.
- **Python API**: The worker uses `PdfConverter` Python API directly (not CLI subprocess) for better control and configuration.
- **Model Caching**: Models are pre-downloaded during Docker build and warmed up on container start (`warmup.py`).
- **Worker Pool**: Celery worker runs with `--pool=solo` to support synchronous GPU operations and avoid Gevent conflicts with PyTorch.

---

## Current Session State

### Status Summary
| Category | Status | Notes |
|----------|--------|-------|
| Core Conversion | **Working** | Pandoc conversions (17 formats) fully functional |
| AI Conversion | **Working** | Marker Python API integration with options (OCR, LLM), GPU/CPU detection |
| Web UI | **Working** | Material Design 3, drag-drop, real-time updates, GPU status indicator |
| Security | **Strong** | HTTPS (Cloudflare Tunnel), secrets management, non-root containers, capability dropping, input validation, secure cookies; needs encryption at rest, Redis TLS |
| Testing | **Partial** | pytest suite exists, but needs updates for recent Marker API changes |
| Observability | **Working** | Prometheus metrics (/metrics), health checks (/healthz, /readyz, /api/health), alerting rules; needs Grafana dashboards |
| Deployment | **Working** | Docker Compose with GPU/CPU/HTTPS profiles, conditional builds, K8s manifests missing |
| Resource Efficiency | **Working** | GPU detection, lazy model loading, intelligent cleanup, 3GB CPU image, ~15GB GPU image |

### Recent Changes
- **2026-01-16 (Epic 21 - GPU Detection & Resource Optimization)**: Completed all 13 stories implementing GPU detection, Prometheus metrics, intelligent cleanup, secrets management, container hardening, input validation, health checks, alerting, and graceful shutdown. Added conditional Docker builds (GPU/CPU), lazy model loading, Prometheus /metrics endpoint, intelligent data retention with disk monitoring, Docker Swarm secrets support, non-root containers with capability dropping, comprehensive input validators/sanitizers, /healthz and /readyz probes, Prometheus alerting rules with runbooks, and SIGTERM handlers with GPU cleanup.
- **2026-01-16 (Epic 22 - HTTPS Support)**: Implemented Cloudflare Tunnel integration for zero-touch HTTPS. Added `cloudflare-tunnel` service to docker-compose.yml with `https` profile, created automated setup script (cloudflare/setup.sh), implemented ProxyFix middleware for secure cookies and proxy header trust, updated CSP headers for wss:// WebSocket support, created comprehensive setup documentation (docs/CLOUDFLARE_TUNNEL_SETUP.md).
- **2026-01-15 (Plan Restructuring)**: Transformed plan.md with BDD user stories for all epics. Embedded Epics 21-25 inline for self-contained context. Added Epic 21.13 for GPU/CPU visual indicator in UI.
- **2026-01-14 (Epics 22-25 Planning)**: Completed comprehensive planning for HTTPS support via Cloudflare Tunnel, application-level encryption at rest, Redis TLS with CA certificates, and automated certificate management with Certbot + Cloudflare DNS.
- **2026-01-14 (Epic 21 Planning)**: Completed comprehensive planning for GPU detection, resource optimization, security hardening, and operational excellence.
- **Epic 18 (Marker Migration)**: Completed migration to direct library usage.
- **Epic 19.1 (ZIP Download)**: Implemented automatic ZIP bundling for multi-file outputs (images + markdown).
- **Startup Optimization**: Added build-time model download and runtime `warmup.py` to prevent cold start delays.
- **Status Reporting**: Added real-time Marker status polling (Initialization/Ready) to the UI.
- **Bug Fixes**: Resolved worker hang by switching to `solo` pool and removing Gevent patching in worker. Fixed `TypeError` in `PdfConverter` call.

### Known Gaps Identified in Planning

**Epic 21 (GPU & Resource Management) - ✅ COMPLETED:**
- **✅ GPU detection**: Implemented build-time and runtime detection (Stories 21.1, 21.2)
- **✅ Deployment profiles**: GPU/CPU/HTTPS profiles implemented (Story 21.3)
- **✅ Memory optimization**: Lazy model loading reduces idle memory from 8GB to <1GB (Story 21.4)
- **✅ Prometheus metrics**: /metrics endpoint with comprehensive monitoring (Story 21.5)
- **✅ Intelligent cleanup**: Prioritized deletion by size and recency, emergency cleanup (Story 21.6)
- **✅ Secrets management**: Docker Swarm secrets support with production validation (Story 21.7)
- **✅ Container hardening**: Non-root users, capability dropping, tmpfs (Story 21.8)
- **✅ Input validation**: UUID, filename sanitization, path traversal prevention (Story 21.9)
- **✅ Health checks**: /healthz, /readyz, /api/health endpoints (Story 21.10)
- **✅ Alerting**: Prometheus alert rules with runbooks (Story 21.11)
- **✅ Graceful shutdown**: SIGTERM handlers with GPU cleanup (Story 21.12)
- **✅ GPU UI indicator**: Visual status chip with detailed modal (Story 21.13)

**Security & Encryption (Epics 22-25):**
- **✅ HTTPS support**: Implemented via Cloudflare Tunnel with automatic SSL (Epic 22)
- **✅ Secure cookies**: SESSION_COOKIE_SECURE enabled with ProxyFix middleware (Epic 22)
- **✅ WebSocket encryption**: wss:// protocol supported through Cloudflare Tunnel (Epic 22)
- **No encryption at rest**: Files stored in plaintext (~53MB in data/), 777 permissions
- **Redis exposed**: Port 6379 exposed on 0.0.0.0 (security vulnerability, but mitigated by container network)
- **No encryption in transit**: All Redis connections unencrypted, Celery messages in plaintext
- **No certificate infrastructure for Redis**: No internal PKI, no certificate management

**Remaining Work:**
- **Epic 23**: Application-level encryption at rest (AES-256-GCM)
- **Epic 24**: Redis TLS with CA certificates
- **Epic 25**: Certbot integration for certificate management

---

## Epics

## Epic 1: Project Setup & Infrastructure
**Status**: ✅ Completed (2025-12-01)

- [x] 1.1 **Story: Initialize project structure**
  - **As a** developer, **I want** organized project directories, **So that** code is maintainable
  - **Implementation**: Created `/web`, `/worker`, `/data`, `/tests`, `/docs` directories
  - **Files**: Created project root structure with 5 top-level directories
  - **Verification**: `tree -L 1` shows organized structure
  - ✅ **Completed**: 2025-12-01

- [x] 1.2 **Story: Create docker-compose orchestration**
  - **As a** DevOps engineer, **I want** container orchestration, **So that** services start automatically
  - **Implementation**: Created `docker-compose.yml` with web, redis, worker, beat services
  - **Files**: `docker-compose.yml` (~100 lines)
  - **Verification**: `docker-compose up` starts all services
  - ✅ **Completed**: 2025-12-01

- [x] 1.3 **Story: Configure shared volume**
  - **As a** system architect, **I want** shared file storage, **So that** services can exchange files
  - **Implementation**: Mounted `./data:/app/data` volume in web and worker containers
  - **Files**: Modified `docker-compose.yml` volume mappings
  - **Verification**: `docker-compose exec web ls /app/data` shows shared directory
  - ✅ **Completed**: 2025-12-01

## Epic 2: Web UI Development
**Status**: ✅ Completed (2025-12-03)

- [x] 2.1 **Story: File upload form with format selection**
  - **As a** user, **I want** to upload files and choose formats, **So that** I can convert documents
  - **Implementation**: Created Flask upload route with source/target format dropdowns
  - **Files**: `web/app.py` (upload route), `web/templates/index.html` (form)
  - **Verification**: Upload form renders with format options
  - ✅ **Completed**: 2025-12-02

- [x] 2.2 **Story: Generate unique Job IDs**
  - **As a** system, **I want** unique job identifiers, **So that** jobs don't collide
  - **Implementation**: Used `uuid.uuid4()` for job ID generation
  - **Files**: `web/app.py` (job creation logic)
  - **Verification**: Multiple uploads create unique IDs
  - ✅ **Completed**: 2025-12-02

- [x] 2.3 **Story: Save uploaded files to shared volume**
  - **As a** web service, **I want** to persist uploads, **So that** worker can access them
  - **Implementation**: Saved files to `data/uploads/{job_id}/`
  - **Files**: `web/app.py` (file save logic)
  - **Verification**: Files appear in `data/uploads/` directory
  - ✅ **Completed**: 2025-12-02

- [x] 2.4 **Story: Status and download endpoints**
  - **As a** user, **I want** to check job status and download results, **So that** I get my converted files
  - **Implementation**: Created `/status/<job_id>` and `/download/<job_id>` endpoints
  - **Files**: `web/app.py` (status/download routes)
  - **Verification**: Endpoints return job status and serve files
  - ✅ **Completed**: 2025-12-03

## Epic 3: Task Queue & Worker
**Status**: ✅ Completed (2025-12-05)

- [x] 3.1 **Story: Set up Celery with Redis broker**
  - **As a** backend developer, **I want** asynchronous task processing, **So that** conversions don't block web requests
  - **Implementation**: Configured Celery app with Redis broker in worker
  - **Files**: `worker/tasks.py` (Celery config), `docker-compose.yml` (Redis service)
  - **Verification**: `docker-compose logs worker` shows Celery connected
  - ✅ **Completed**: 2025-12-04

- [x] 3.2 **Story: Implement convert_document task**
  - **As a** worker, **I want** a conversion task, **So that** I can process jobs
  - **Implementation**: Created `convert_document` Celery task
  - **Files**: `worker/tasks.py` (task definition)
  - **Verification**: Task appears in Celery worker logs
  - ✅ **Completed**: 2025-12-04

- [x] 3.3 **Story: Integrate Pandoc CLI in worker**
  - **As a** conversion engine, **I want** to call Pandoc, **So that** documents are converted
  - **Implementation**: Used `subprocess.run` to call Pandoc with format arguments
  - **Files**: `worker/tasks.py` (Pandoc subprocess calls)
  - **Verification**: Markdown → PDF conversion succeeds
  - ✅ **Completed**: 2025-12-05

- [x] 3.4 **Story: Handle error states and update task status**
  - **As a** worker, **I want** to track task status, **So that** users see progress and errors
  - **Implementation**: Updated Redis job hash with status (pending, processing, success, failed)
  - **Files**: `worker/tasks.py` (status updates), `web/app.py` (status reads)
  - **Verification**: Failed conversions show error status in UI
  - ✅ **Completed**: 2025-12-05

## Epic 4: Frontend Polling & UX
**Status**: ✅ Completed (2025-12-06)

- [x] 4.1 **Story: AJAX polling on status page**
  - **As a** user, **I want** automatic status updates, **So that** I don't need to refresh
  - **Implementation**: JavaScript polls `/status/<job_id>` every 2 seconds
  - **Files**: `web/templates/index.html` (JavaScript polling)
  - **Verification**: Status updates automatically during conversion
  - ✅ **Completed**: 2025-12-06

- [x] 4.2 **Story: Progress indicators and error messages**
  - **As a** user, **I want** visual feedback, **So that** I know what's happening
  - **Implementation**: Added progress spinner, success/error badges
  - **Files**: `web/templates/index.html` (UI components)
  - **Verification**: Spinner shows during processing, badges appear on completion
  - ✅ **Completed**: 2025-12-06

- [x] 4.3 **Story: One-click downloads for finished jobs**
  - **As a** user, **I want** easy downloads, **So that** I can get my files quickly
  - **Implementation**: Download button appears when job completes
  - **Files**: `web/templates/index.html` (download button logic)
  - **Verification**: Click download button retrieves converted file
  - ✅ **Completed**: 2025-12-06

## Epic 5: Resource Management (Ephemeral Data)
**Status**: ✅ Completed (2025-12-08)

- [x] 5.1 **Story: Periodic cleanup task with granular policies**
  - **As a** system administrator, **I want** automatic file cleanup, **So that** disk space doesn't fill up
  - **Implementation**: Celery Beat task runs every 15 minutes with retention rules:
    - Success (Not Downloaded): Delete after 1 hour
    - Success (Downloaded): Delete after 10 minutes
    - Failure: Delete after 5 minutes
  - **Files**: `worker/tasks.py` (cleanup task), `docker-compose.yml` (beat service)
  - **Verification**: Old files are deleted according to policy
  - ✅ **Completed**: 2025-12-08

- [x] 5.2 **Story: Ensure data not in code repository**
  - **As a** developer, **I want** to exclude generated data from git, **So that** repo stays clean
  - **Implementation**: Added `data/*` to `.gitignore`
  - **Files**: `.gitignore`
  - **Verification**: `git status` does not show files in `data/`
  - ✅ **Completed**: 2025-12-07

- [x] 5.3 **Story: Robust retry logic**
  - **As a** user, **I want** retry functionality, **So that** I can recover from failures
  - **Implementation**: Retry creates new job, copies input file to new job ID
  - **Files**: `web/app.py` (retry endpoint)
  - **Verification**: Retry button creates new job with same input
  - ✅ **Completed**: 2025-12-08

## Epic 6: Initial Verification
**Status**: ✅ Completed (2025-12-09)

- [x] 6.1 **Story: Test Markdown to PDF (LaTeX)**
  - **Implementation**: Verified Pandoc LaTeX rendering
  - **Verification**: `pandoc test.md -o test.pdf` succeeds
  - ✅ **Completed**: 2025-12-09

- [x] 6.2 **Story: Test Word to PDF**
  - **Implementation**: Verified Word document conversion
  - **Verification**: DOCX file converts to PDF successfully
  - ✅ **Completed**: 2025-12-09

- [x] 6.3 **Story: Test HTML to EPUB**
  - **Implementation**: Verified EPUB generation
  - **Verification**: HTML converts to valid EPUB
  - ✅ **Completed**: 2025-12-09

- [x] 6.4 **Story: Verify cleanup script**
  - **Implementation**: Monitored cleanup task logs
  - **Verification**: Files deleted according to retention policy
  - ✅ **Completed**: 2025-12-09

## Epic 7: AI-Powered PDF Conversion (Marker) - LEGACY
**Status**: ❌ Superseded by Epic 18

*Original implementation used a separate API service. Replaced by direct library integration in Epic 18.*

- [x] 7.1-7.5: Legacy implementation (deprecated)
  - ❌ **Superseded**: 2026-01-10

## Epic 8: Intelligent File Ingestion
**Status**: ✅ Completed (2025-12-12)

- [x] 8.1 **Story: Drag and drop zone**
  - **As a** user, **I want** drag-and-drop upload, **So that** file selection is easier
  - **Implementation**: Added drop zone with `dragover` and `drop` event handlers
  - **Files**: `web/templates/index.html` (drag-drop JavaScript)
  - **Verification**: Dragging file onto zone triggers upload
  - ✅ **Completed**: 2025-12-11

- [x] 8.2-8.4 **Story: Auto-detection logic with manual override**
  - **As a** user, **I want** automatic format detection, **So that** I don't need to select formats manually
  - **Implementation**: JavaScript detects extension, pre-selects source format, allows override
  - **Files**: `web/templates/index.html` (format detection logic)
  - **Verification**: .md file auto-selects "Markdown" format
  - ✅ **Completed**: 2025-12-11

- [x] 8.5 **Story: Visual feedback for drag operations**
  - **As a** user, **I want** visual cues during drag, **So that** I know where to drop
  - **Implementation**: Added CSS class toggle on dragover/dragleave
  - **Files**: `web/templates/index.html` (CSS + JavaScript)
  - **Verification**: Drop zone highlights during drag
  - ✅ **Completed**: 2025-12-12

## Epic 9: UI Redesign: Material Web
**Status**: ✅ Completed (2025-12-15)

- [x] 9.1 **Story: Remove USWDS and Liquid Glass styles**
  - **As a** developer, **I want** to remove old design systems, **So that** UI is modern
  - **Implementation**: Removed legacy CSS imports and classes
  - **Files**: `web/templates/index.html` (removed old CSS links)
  - **Verification**: No USWDS references remain
  - ✅ **Completed**: 2025-12-13

- [x] 9.2-9.3 **Story: Integrate Material Web components**
  - **As a** user, **I want** modern UI components, **So that** interface looks professional
  - **Implementation**: Added @material/web via CDN, replaced form elements with Material components
  - **Files**: `web/templates/index.html` (Material Web integration)
  - **Verification**: Buttons and selects use Material Design 3 styling
  - ✅ **Completed**: 2025-12-14

- [x] 9.4-9.6 **Story: Update JavaScript for Web Components**
  - **As a** developer, **I want** compatible JavaScript, **So that** Material components work correctly
  - **Implementation**: Updated `.value` access for Web Component properties
  - **Files**: `web/templates/index.html` (JavaScript updates)
  - **Verification**: Form submission works with Material components
  - ✅ **Completed**: 2025-12-15

## Epic 10: Intelligent Theme Customization
**Status**: ✅ Completed (2025-12-17)

- [x] 10.1-10.5 **Story: Dark mode with system preference support**
  - **As a** user, **I want** dark mode, **So that** UI is comfortable in low light
  - **Implementation**: Theme toggle (System/Light/Dark), CSS custom properties, localStorage persistence, `prefers-color-scheme` listener
  - **Files**: `web/templates/index.html` (theme toggle JavaScript + CSS)
  - **Verification**: Theme persists across sessions, follows system preference
  - ✅ **Completed**: 2025-12-17

## Epic 11: Security Hardening
**Status**: ✅ Mostly Completed (2025-12-20)

- [x] 11.1 **Story: File size limits**
  - **As a** system, **I want** upload size limits, **So that** large files don't exhaust resources
  - **Implementation**: 100MB max upload via Flask config
  - **Files**: `web/app.py` (MAX_CONTENT_LENGTH)
  - **Verification**: 101MB file rejected with 413 error
  - ✅ **Completed**: 2025-12-18

- [x] 11.2-11.4 **Story: Security headers and rate limiting**
  - **As a** security engineer, **I want** protective headers, **So that** app resists common attacks
  - **Implementation**: CSP, X-Frame-Options, HSTS headers; rate limiting on `/convert`
  - **Files**: `web/app.py` (security middleware, Flask-Limiter)
  - **Verification**: Headers present in response, rate limit enforced
  - ✅ **Completed**: 2025-12-19

- [x] 11.5-11.7 **Story: Redis security and secrets management**
  - **As a** administrator, **I want** externalized secrets, **So that** production is secure
  - **Implementation**: SECRET_KEY via environment variable, Redis key pattern audit
  - **Files**: `web/app.py` (env var loading), `docker-compose.yml` (env config)
  - **Verification**: App fails to start if SECRET_KEY not set in production
  - ✅ **Completed**: 2025-12-20

- [ ] 11.8 **Story: GitHub branch protection**
  - **As a** repository owner, **I want** branch protection, **So that** main branch is stable
  - **Implementation**: GitHub ruleset to prevent force push
  - **Status**: 🔵 Planned

## Epic 12: Test Infrastructure
**Status**: ✅ Completed (2025-12-23)

- [x] 12.1-12.6 **Story: Comprehensive test suite with CI/CD**
  - **As a** developer, **I want** automated testing, **So that** changes don't break functionality
  - **Implementation**: pytest framework, unit tests for web/worker, negative tests, coverage reporting, GitHub Actions CI
  - **Files**: `tests/` directory, `.github/workflows/test.yml`
  - **Verification**: `pytest tests/ -v` runs all tests, GitHub Actions passes
  - ✅ **Completed**: 2025-12-23

## Epic 13: Observability & Monitoring
**Status**: 🟡 Partial (2025-12-28)

- [x] 13.1 **Story: Structured logging**
  - **As a** operator, **I want** JSON logs, **So that** logs are machine-parseable
  - **Implementation**: JSON log format with structured fields
  - **Files**: `web/app.py`, `worker/tasks.py` (logging config)
  - **Verification**: Logs output valid JSON
  - ✅ **Completed**: 2025-12-24

- [ ] 13.2 **Story: Health checks in docker-compose**
  - **Status**: 🔵 Planned

- [ ] 13.3-13.4 **Story: Prometheus metrics and Grafana dashboard**
  - **Status**: 🔵 Planned (covered in Epic 21.5)

- [x] 13.5 **Story: Disk space pre-checks**
  - **As a** system, **I want** to check disk space, **So that** conversions don't fail mid-process
  - **Implementation**: Pre-flight disk space check before large file processing
  - **Files**: `worker/tasks.py` (disk space check)
  - **Verification**: Low disk space triggers user-friendly error
  - ✅ **Completed**: 2025-12-28

- [ ] 13.6 **Story: Alerting rules**
  - **Status**: 🔵 Planned (covered in Epic 21.11)

## Epic 14: API & Documentation
**Status**: ✅ Completed (2025-12-30)

- [x] 14.1-14.5 **Story: Comprehensive documentation**
  - **As a** new contributor, **I want** complete documentation, **So that** I can understand and deploy the system
  - **Implementation**: OpenAPI/Swagger docs, deployment guide, format matrix, troubleshooting guide, Marker integration docs
  - **Files**: `docs/` directory (API.md, DEPLOYMENT.md, FORMATS.md, TROUBLESHOOTING.md, AI_INTEGRATION.md)
  - **Verification**: Documentation renders correctly, covers all topics
  - ✅ **Completed**: 2025-12-30

## Epic 15: UX Enhancements
**Status**: 🟡 Partial (2026-01-05)

- [x] 15.1 **Story: Determinate progress indication**
  - **As a** user, **I want** percentage progress, **So that** I know how long conversion will take
  - **Implementation**: Progress percentage displayed during conversion
  - **Files**: `web/templates/index.html` (progress bar)
  - **Verification**: Progress updates from 0% to 100%
  - ✅ **Completed**: 2026-01-02

- [x] 15.2-15.3 **Story: Batch upload and manual deletion**
  - **As a** user, **I want** batch operations, **So that** I can process multiple files efficiently
  - **Implementation**: Multi-file upload support, delete button before auto-cleanup
  - **Files**: `web/app.py` (batch endpoints), `web/templates/index.html` (UI)
  - **Verification**: Upload multiple files at once, delete individual jobs
  - ✅ **Completed**: 2026-01-04

- [x] 15.4 **Story: Format compatibility hints**
  - **As a** user, **I want** format hints, **So that** I choose compatible conversions
  - **Implementation**: Tooltip shows supported target formats for each source
  - **Files**: `web/templates/index.html` (format hints)
  - **Verification**: Hover shows compatible formats
  - ✅ **Completed**: 2026-01-05

- [ ] 15.5 **Story: Job status webhooks**
  - **Status**: 🔵 Planned

- [x] 15.6 **Story: Service availability indicator**
  - **As a** user, **I want** to see service status, **So that** I know if AI features are available
  - **Implementation**: Marker status banner in UI (linked to Epic 13.8)
  - **Files**: `web/templates/index.html` (status banner)
  - **Verification**: Banner shows "Initializing" or "Ready"
  - ✅ **Completed**: 2026-01-05

- [x] 15.7 **Story: Disk space error messages**
  - **As a** user, **I want** clear error messages, **So that** I understand why conversion failed
  - **Implementation**: User-friendly error when disk space check fails (linked to Epic 13.5)
  - **Files**: `web/templates/index.html` (error display)
  - **Verification**: Disk full shows clear error message
  - ✅ **Completed**: 2026-01-05

## Epic 16: Scalability & Performance
**Status**: 🟡 Partial (2026-01-08)

- [ ] 16.1 **Story: Configurable worker concurrency**
  - **Status**: 🔵 Planned

- [x] 16.2-16.3 **Story: WebSocket support and Redis pooling**
  - **As a** user, **I want** real-time updates, **So that** I see job progress instantly
  - **Implementation**: Socket.IO replaces polling, Redis connection pooling
  - **Files**: `web/app.py` (Socket.IO integration), `worker/tasks.py` (Redis pool)
  - **Verification**: WebSocket connection established, real-time job updates
  - ✅ **Completed**: 2026-01-07

- [ ] 16.4 **Story: Kubernetes/Helm manifests**
  - **Status**: 🔵 Planned

- [ ] 16.5 **Story: Load testing suite**
  - **Status**: 🔵 Planned

- [x] 16.6-16.7 **Story: Queue priority and Marker job queuing**
  - **As a** system, **I want** intelligent queueing, **So that** resources are used efficiently
  - **Implementation**: Priority levels for small files, Marker job queuing when API unavailable
  - **Files**: `worker/tasks.py` (priority logic)
  - **Verification**: Small files process faster, Marker jobs queue gracefully
  - ✅ **Completed**: 2026-01-08

## Epic 17: Marker API Migration (maximofn → adithya-s-k)
**Status**: ❌ Superseded by Epic 18 (2026-01-09)

*Goal was to switch API implementations, but moved to direct library integration instead.*

- [x] 17.1-17.3 **Story: Migrate Marker API implementation**
  - **Implementation**: Removed maximofn submodule, created standalone Dockerfile (later deleted in Epic 18)
  - ❌ **Superseded**: 2026-01-10 by Epic 18

## Epic 18: Migrate to datalab-to/marker
**Status**: ✅ Completed (2026-01-10)

**Goal**: Replace separate API service with direct library integration in the worker.

- [x] 18.1 **Story: Install Latest Marker in Worker**
  - **Goal**: Install marker-pdf library directly in worker container
  - **What Changed**:
    - Updated `worker/Dockerfile` to install `marker-pdf[full]` on CUDA 11.8 base
    - Added PyTorch, torchvision, and all Marker dependencies
    - Worker container now supports both Pandoc and Marker
  - **Files Modified**:
    - `worker/Dockerfile` - Added Marker installation (~40 lines)
    - `worker/requirements.txt` - Added marker-pdf dependency
  - **Verification**:
    ```bash
    docker-compose exec worker python -c "import marker; print(marker.__version__)"
    # Expected: marker version printed
    ```
  - ✅ **Completed**: 2026-01-09 | **Session**: epic-18-phase-1

- [x] 18.2 **Story: Refactor Marker Conversion Task (CLI)**
  - **Goal**: Use Marker CLI from worker instead of HTTP API
  - **What Changed**:
    - Modified `convert_with_marker` task to call `marker_single` CLI
    - Parsed stdout for progress updates
    - Handled output file copying from temp directory
  - **Files Modified**:
    - `worker/tasks.py` - Refactored Marker task with subprocess (~80 lines)
  - **Verification**:
    ```bash
    # Upload PDF, select "PDF (High Accuracy)"
    docker-compose logs worker | grep "marker_single"
    # Expected: CLI invocation logs
    ```
  - ✅ **Completed**: 2026-01-09 | **Session**: epic-18-phase-1

- [x] 18.3 **Story: Update Docker Compose for Marker Workers**
  - **Goal**: Remove separate marker-api service, give worker GPU access
  - **What Changed**:
    - Removed `marker-api` service from docker-compose.yml
    - Added GPU reservation to worker service
    - Updated health checks
  - **Files Modified**:
    - `docker-compose.yml` - Removed marker-api, added GPU config (~30 lines)
  - **Verification**:
    ```bash
    docker-compose config | grep -A 5 "worker"
    # Expected: GPU resources.reservations present
    ```
  - ✅ **Completed**: 2026-01-09 | **Session**: epic-18-phase-1

- [x] 18.4 **Story: Enhance Task with Marker Features**
  - **Goal**: Support Marker CLI flags for OCR and LLM
  - **What Changed**:
    - Added `--use_llm` and `--force_ocr` CLI flags
    - Extracted images to `data/outputs/job_id/images/`
    - Handled multi-file output (markdown + images)
  - **Files Modified**:
    - `worker/tasks.py` - Added CLI flags and image extraction (~50 lines)
  - **Verification**:
    ```bash
    # Upload PDF with complex layout
    # Expected: Markdown + images directory in output
    ```
  - ✅ **Completed**: 2026-01-09 | **Session**: epic-18-phase-1

- [x] 18.5 **Story: Update Tests and Verification**
  - **Goal**: Update tests to mock subprocess instead of HTTP requests
  - **What Changed**:
    - Modified `tests/unit/test_worker.py` to mock `subprocess.run`
    - Removed `tests/wait_for_marker.py` (no longer needed)
  - **Files Modified**:
    - `tests/unit/test_worker.py` - Updated mocks (~40 lines)
    - `tests/wait_for_marker.py` - Deleted
  - **Verification**:
    ```bash
    pytest tests/unit/test_worker.py -v
    # Expected: All tests pass
    ```
  - ✅ **Completed**: 2026-01-09 | **Session**: epic-18-phase-1

- [x] 18.6 **Story: Documentation and Rollback**
  - **Goal**: Update documentation to reflect new architecture
  - **What Changed**:
    - Removed marker-api references from README.md
    - Updated AI_INTEGRATION.md with direct library usage
  - **Files Modified**:
    - `README.md` - Removed API references (~20 lines)
    - `docs/AI_INTEGRATION.md` - Updated architecture section (~60 lines)
  - **Verification**:
    ```bash
    grep -r "marker-api" docs/
    # Expected: No results (all references removed)
    ```
  - ✅ **Completed**: 2026-01-10 | **Session**: epic-18-phase-1

- [x] 18.7 **Story: Optimize Marker Integration with Python API**
  - **Goal**: Replace subprocess CLI calls with direct Python API for better control
  - **What Changed**:
    - Imported `PdfConverter` class directly in `worker/tasks.py`
    - Replaced `subprocess.run(['marker_single', ...])` with `PdfConverter()(pdf_path)`
    - Added configuration via `artifact_dict` and config options
    - Eliminated subprocess deadlock issues
    - Improved error handling and progress tracking
  - **Files Modified**:
    - `worker/tasks.py` - Removed subprocess, added PdfConverter API (lines 145-180)
    - `worker/warmup.py` - Pre-load models into artifact_dict (lines 45-60)
  - **Verification**:
    ```bash
    # Upload PDF via UI, select "PDF (High Accuracy)"
    # Expected: Conversion succeeds without hanging
    docker-compose logs worker | grep "PdfConverter"
    # Expected: API usage logs, no subprocess calls
    ```
  - ✅ **Completed**: 2026-01-10 | **Session**: epic-18-phase-2

## Epic 19: Functional Enhancements
**Status**: ✅ Completed (2026-01-11)

**Goal**: Add features that increase value for the user.

- [x] 19.1 **Story: Download Multi-File Conversion Outputs as ZIP Archive**
  - **Goal**: Bundle Marker multi-file outputs (images + markdown) into a single ZIP download
  - **What Changed**:
    - Modified download endpoint to detect multi-file outputs
    - Created in-memory ZIP archive for jobs with multiple files
    - Updated UI to show "Download ZIP" button for multi-file jobs
    - Preserved original "Download" button for single-file jobs
  - **Files Modified**:
    - `web/app.py` - Added ZIP creation logic in download endpoint (~60 lines)
    - `web/templates/index.html` - Updated download button UI (~20 lines)
  - **Verification**:
    ```bash
    # Upload PDF, convert with Marker (produces markdown + images)
    # Expected: Download button shows "Download ZIP"
    # Expected: ZIP contains markdown + images/ directory
    unzip -l downloaded_file.zip
    ```
  - ✅ **Completed**: 2026-01-11 | **Session**: epic-19

## Epic 20: Pre-Caching & UI Status
**Status**: ✅ Completed (2026-01-11)

**Goal**: Improve user experience by pre-loading AI models and reporting service status.

- [x] 20.1 **Story: Pre-Cache Marker LLMs on Container Startup**
  - **Goal**: Eliminate cold start delays by pre-loading models during startup
  - **What Changed**:
    - Added build-time model download to `worker/Dockerfile`
    - Created `worker/warmup.py` entrypoint to verify/load models
    - Worker reports ready status to Redis when initialization completes
    - Updated Celery to use `solo` pool for stability with PyTorch
  - **Files Modified**:
    - `worker/Dockerfile` - Added model download step (~15 lines)
    - `worker/warmup.py` - Created warmup script (~80 lines)
    - `docker-compose.yml` - Updated worker command to run warmup first (~10 lines)
    - `worker/tasks.py` - Updated pool config (~5 lines)
  - **Verification**:
    ```bash
    docker-compose up worker
    # Expected: "Models loaded successfully" in logs
    # Expected: Redis key marker:status = "ready"
    docker-compose exec redis redis-cli GET marker:status
    ```
  - ✅ **Completed**: 2026-01-11 | **Session**: epic-20

- [x] 20.2 **Story: UI Service Status with LLM Download ETA**
  - **Goal**: Show users when Marker is initializing vs ready
  - **What Changed**:
    - Added `/api/status/services` endpoint to check Redis marker:status
    - Added UI banner in `index.html` to show "Initializing" or "Ready"
    - Disabled PDF (High Accuracy) option until service is ready
    - Banner polls status endpoint every 5 seconds until ready
  - **Files Modified**:
    - `web/app.py` - Added status endpoint (~30 lines)
    - `web/templates/index.html` - Added status banner component (~60 lines)
  - **Verification**:
    ```bash
    # Start services, open browser
    # Expected: Banner shows "Marker AI: Initializing..." during startup
    # Expected: Banner changes to "Marker AI: Ready" after warmup
    # Expected: PDF option disabled until ready
    ```
  - ✅ **Completed**: 2026-01-11 | **Session**: epic-20

## Epic 21: GPU Detection and Resource Optimization
**Status**: ✅ Completed (2026-01-16) | **Priority**: P0 - Critical | **Effort**: 8-10 days

**Originally Planned**: 2026-01-14 | **Embedded**: 2026-01-15 | **Completed**: 2026-01-16

**Goal**: Enable DocuFlux to run efficiently on both GPU and CPU-only infrastructure with intelligent detection and conditional builds.

### Stories Overview
- [x] 21.1: Build-time GPU detection and conditional Docker images ✅ (2026-01-15)
- [x] 21.2: Runtime GPU detection and graceful degradation ✅ (2026-01-15)
- [x] 21.3: Docker Compose profiles for deployment scenarios ✅ (2026-01-15)
- [x] 21.4: Memory footprint reduction ✅ (2026-01-15)
- [x] 21.5: Prometheus metrics endpoint ✅ (2026-01-16)
- [x] 21.6: Intelligent data retention ✅ (2026-01-16)
- [x] 21.7: Secrets management and rotation ✅ (2026-01-16)
- [x] 21.8: Container security hardening ✅ (2026-01-16)
- [x] 21.9: Input validation and sanitization ✅ (2026-01-16)
- [x] 21.10: Enhanced health checks ✅ (2026-01-16)
- [x] 21.11: Alerting rules ✅ (2026-01-16)
- [x] 21.12: Graceful shutdown and cleanup ✅ (2026-01-16)
- [x] 21.13: GPU/CPU visual indicator in UI ⭐ NEW ✅ (2026-01-15)

---

#### Story 21.1: Detect GPU Availability at Build Time
**As a** DevOps engineer
**I want** to build GPU or CPU-specific Docker images
**So that** image size and dependencies match the deployment environment

**Acceptance Criteria:**

```gherkin
Feature: Build-time GPU Detection
  As a container builder
  I need to detect GPU availability during build
  So that I can create optimized images

  Scenario: Build GPU image with CUDA dependencies
    Given the host has NVIDIA GPU available
    When building worker image with BUILD_GPU=true
    Then the Dockerfile should install CUDA 11.8
    And install PyTorch with GPU support
    And download Marker AI models
    And result in worker:gpu image tag

  Scenario: Build CPU-only image without GPU dependencies
    Given the host has no GPU
    When building worker image with BUILD_GPU=false
    Then the Dockerfile should skip CUDA installation
    And install PyTorch CPU-only version
    And skip Marker model downloads
    And result in worker:cpu image tag
    And image size should be <5GB (vs ~15GB for GPU)
```

**Technical Implementation:**
- Add `ARG BUILD_GPU=true` to `worker/Dockerfile`
- Use conditional RUN statements based on BUILD_GPU
- Create separate requirements files: `requirements-gpu.txt`, `requirements-cpu.txt`
- Build script: `scripts/build.sh` detects GPU via `nvidia-smi`

**Files to Modify:**
- `worker/Dockerfile` - Add ARG and conditional logic (~50 lines)
- `worker/requirements-gpu.txt` (new) - GPU dependencies
- `worker/requirements-cpu.txt` (new) - CPU dependencies
- `scripts/build.sh` (new) - GPU detection and build script (~80 lines)

**Dependencies:** None

**Definition of Done:**
- [ ] `./scripts/build.sh` detects GPU and builds appropriate image
- [ ] GPU image includes CUDA and full Marker dependencies
- [ ] CPU image excludes CUDA, uses PyTorch CPU-only
- [ ] Both images build successfully without errors
- [ ] Image sizes: GPU ~15GB, CPU <5GB

---

#### Story 21.2: Runtime GPU Detection and Graceful Degradation
**As a** worker service
**I want** to detect GPU at runtime and adapt behavior
**So that** the application works on both GPU and CPU-only hosts

**Acceptance Criteria:**

```gherkin
Feature: Runtime GPU Detection
  As a worker starting up
  I need to detect GPU availability
  So that I can configure tasks appropriately

  Scenario: Detect GPU and enable Marker
    Given the worker container starts on GPU-enabled host
    When warmup.py runs check_gpu_availability()
    Then it should detect GPU using torch.cuda.is_available()
    And store marker:gpu_status = "available" in Redis
    And store GPU model, VRAM info in marker:gpu_info
    And enable Marker conversion tasks

  Scenario: Detect CPU-only and disable Marker
    Given the worker container starts on CPU-only host
    When warmup.py runs check_gpu_availability()
    Then it should detect no GPU available
    And store marker:gpu_status = "unavailable" in Redis
    And disable Marker conversion tasks
    And log warning about CPU-only mode

  Scenario: GPU exception handling in Marker tasks
    Given a Marker conversion is submitted
    And GPU becomes unavailable mid-task
    When the task attempts GPU operations
    Then catch CUDA out-of-memory errors
    And return user-friendly error message
    And mark task as failed gracefully
```

**Technical Implementation:**
- Implement real `check_gpu_availability()` in `warmup.py` (replace placeholder)
- Use PyTorch APIs: `torch.cuda.is_available()`, `torch.cuda.get_device_properties()`
- Store GPU info in Redis: `marker:gpu_status`, `marker:gpu_info`
- Update Marker task to check GPU status before conversion

**Files to Modify:**
- `worker/warmup.py` - Implement GPU detection (~80 lines)
- `worker/tasks.py` - Add GPU status check in Marker task (~40 lines)
- `web/app.py` - Update `/api/status/services` to return GPU status (~20 lines)

**Dependencies:** None

**Definition of Done:**
- [ ] `check_gpu_availability()` detects GPU correctly on GPU hosts
- [ ] Redis keys populated with GPU status and info
- [ ] Marker tasks check GPU status before running
- [ ] CPU-only hosts gracefully disable Marker
- [ ] UI shows GPU unavailable message (via 21.13)

---

#### Story 21.3: Docker Compose Profiles for Deployment Scenarios
**As a** system administrator
**I want** Docker Compose profiles for GPU/CPU deployments
**So that** I can start only necessary services

**Acceptance Criteria:**

```gherkin
Feature: Docker Compose Deployment Profiles
  As a deployer
  I need profile-based service configuration
  So that I can optimize for GPU or CPU environments

  Scenario: Start GPU profile
    Given docker-compose.yml has GPU profile
    When running docker-compose --profile gpu up
    Then start web, redis, worker:gpu, beat services
    And worker has GPU access (deploy.resources.reservations)
    And MARKER_ENABLED=true environment variable

  Scenario: Start CPU profile
    Given docker-compose.yml has CPU profile
    When running docker-compose --profile cpu up
    Then start web, redis, worker:cpu, beat services
    And worker has no GPU configuration
    And MARKER_ENABLED=false environment variable

  Scenario: Mixed deployment
    Given cloud infrastructure has both GPU and CPU nodes
    When deploying with docker-compose
    Then worker:gpu runs on GPU nodes
    And worker:cpu runs on CPU nodes
    And both handle appropriate job types
```

**Technical Implementation:**
- Add profiles to `docker-compose.yml`: `gpu`, `cpu`
- Create override files: `docker-compose.gpu.yml`, `docker-compose.cpu.yml`
- Tag worker images: `worker:gpu`, `worker:cpu`

**Files to Modify:**
- `docker-compose.yml` - Add profiles (~40 lines)
- `docker-compose.gpu.yml` (new) - GPU-specific config (~60 lines)
- `docker-compose.cpu.yml` (new) - CPU-specific config (~60 lines)

**Dependencies:** 21.1 (build-time detection), 21.2 (runtime detection)

**Definition of Done:**
- [ ] `docker-compose --profile gpu up` starts GPU services
- [ ] `docker-compose --profile cpu up` starts CPU services
- [ ] Worker logs show correct profile active
- [ ] GPU profile worker uses GPU for Marker tasks
- [ ] CPU profile worker disables Marker tasks

---

#### Story 21.4: Reduce Worker Memory Footprint
**As a** infrastructure engineer
**I want** reduced memory usage in workers
**So that** I can run more workers per host

**Acceptance Criteria:**

```gherkin
Feature: Memory Optimization
  As a worker process
  I need to minimize memory usage
  So that I can handle more concurrent tasks

  Scenario: Lazy model loading
    Given worker starts up
    When models are not immediately needed
    Then delay loading until first Marker task
    And free model memory after task completes
    And reduce idle memory from 8GB to <1GB

  Scenario: Garbage collection after tasks
    Given a Marker conversion completes
    When task result is returned
    Then call gc.collect() to free Python objects
    And call torch.cuda.empty_cache() to free GPU memory
    And log memory freed

  Scenario: Memory limits per profile
    Given docker-compose profiles are configured
    When starting worker:cpu
    Then set memory limit to 2GB
    When starting worker:gpu
    Then set memory limit to 16GB (VRAM + system)
```

**Technical Implementation:**
- Implement lazy model loading in `warmup.py`
- Add memory cleanup in `tasks.py` after each task
- Configure memory limits in docker-compose profiles

**Files to Modify:**
- `worker/warmup.py` - Lazy loading (~40 lines)
- `worker/tasks.py` - Memory cleanup (~30 lines)
- `docker-compose.gpu.yml` - Memory limits (~10 lines)
- `docker-compose.cpu.yml` - Memory limits (~10 lines)

**Dependencies:** 21.3 (profiles)

**Definition of Done:**
- [ ] Idle worker memory <1GB (CPU profile)
- [ ] Memory freed after each task completion
- [ ] Docker memory limits enforced
- [ ] No out-of-memory crashes under load

---

#### Story 21.5: Prometheus Metrics Endpoint
**As a** SRE
**I want** Prometheus metrics for monitoring
**So that** I can track system performance

**Acceptance Criteria:**

```gherkin
Feature: Prometheus Metrics
  As a monitoring system
  I need to scrape metrics from worker
  So that I can track performance and alerts

  Scenario: Expose metrics endpoint
    Given worker has prometheus-client installed
    When metrics endpoint /metrics is accessed
    Then return metrics in Prometheus format
    And include task duration histogram
    And include queue depth gauge
    And include GPU utilization gauge (if available)

  Scenario: Track conversion metrics
    Given a conversion task completes
    When metrics are updated
    Then increment conversion_total counter
    And record conversion_duration_seconds histogram
    And update queue_depth gauge
```

**Technical Implementation:**
- Add `prometheus-client` to requirements
- Create `/metrics` endpoint on port 9090
- Track: task duration, queue depth, GPU utilization, conversion counts

**Files to Modify:**
- `worker/requirements.txt` - Add prometheus-client
- `worker/metrics.py` (new) - Metrics definitions (~100 lines)
- `worker/tasks.py` - Instrument tasks (~50 lines)
- `docker-compose.yml` - Expose port 9090 (~5 lines)

**Dependencies:** None

**Definition of Done:**
- [ ] `/metrics` endpoint returns Prometheus format
- [ ] Metrics update on task completion
- [ ] Grafana can scrape and display metrics
- [ ] GPU metrics included when available

---

#### Story 21.6: Intelligent Data Retention
**As a** system
**I want** smart cleanup prioritization
**So that** disk space is used efficiently

**Acceptance Criteria:**

```gherkin
Feature: Intelligent Cleanup
  As a cleanup task
  I need to prioritize file deletion
  So that I maximize available disk space

  Scenario: Prioritize large files for cleanup
    Given cleanup task runs
    When disk usage >80%
    Then delete largest files first
    And preserve recently viewed files
    And emergency cleanup if >95% full

  Scenario: Track last viewed timestamps
    Given a user downloads a file
    When download completes
    Then update job:last_viewed timestamp in Redis
    And prioritize unviewed files for cleanup
```

**Technical Implementation:**
- Modify cleanup task to sort by file size
- Track `last_viewed` timestamp on downloads
- Implement emergency cleanup threshold

**Files to Modify:**
- `worker/tasks.py` - Update cleanup_old_jobs (~60 lines)
- `web/app.py` - Track last_viewed on download (~10 lines)

**Dependencies:** None

**Definition of Done:**
- [ ] Cleanup prioritizes large files
- [ ] Recently viewed files preserved longer
- [ ] Emergency cleanup triggers at 95% disk usage
- [ ] Disk space maintained <80% under normal load

---

#### Story 21.7: Secrets Management and Rotation
**As a** security engineer
**I want** proper secrets management
**So that** credentials are protected

**Acceptance Criteria:**

```gherkin
Feature: Secrets Management
  As the application
  I need to load secrets securely
  So that credentials are not exposed

  Scenario: Load secrets from Docker secrets
    Given Docker Swarm secrets are configured
    When application starts
    Then load SECRET_KEY from /run/secrets/secret_key
    And fallback to environment variable
    And fail fast if default secret in production

  Scenario: Support secret rotation
    Given secrets need to be rotated
    When new secret is provided
    Then application reads new secret on restart
    And maintains backward compatibility during transition
```

**Technical Implementation:**
- Create secrets loading utility in `web/secrets.py`
- Support Docker secrets, env vars, file paths
- Validate secrets at startup (fail if default)

**Files to Modify:**
- `web/secrets.py` (new) - Secrets management (~100 lines)
- `worker/secrets.py` (new) - Same for worker (~100 lines)
- `web/app.py` - Use secrets module (~20 lines)
- `docker-compose.yml` - Document secrets config (~15 lines)

**Dependencies:** None

**Definition of Done:**
- [ ] Secrets loaded from Docker secrets
- [ ] Environment variable fallback works
- [ ] Production startup fails with default secrets
- [ ] Secret rotation documented

---

#### Story 21.8: Container Security Hardening
**As a** security engineer
**I want** hardened containers
**So that** attack surface is minimized

**Acceptance Criteria:**

```gherkin
Feature: Container Security
  As a container
  I should run with minimal privileges
  So that exploits have limited impact

  Scenario: Run containers as non-root
    Given Dockerfiles create non-root user
    When containers start
    Then processes run as user "appuser"
    And not as root (UID 0)

  Scenario: Enable read-only root filesystem
    Given containers don't need write access to /
    When containers start
    Then root filesystem is read-only
    And only /app/data is writable

  Scenario: Drop unnecessary capabilities
    Given containers don't need all Linux capabilities
    When containers start
    Then drop all capabilities except required ones
    And add only NET_BIND_SERVICE if needed
```

**Technical Implementation:**
- Add non-root users to Dockerfiles
- Enable `read_only: true` in docker-compose
- Drop capabilities via `cap_drop: ALL`

**Files to Modify:**
- `web/Dockerfile` - Add USER appuser (~10 lines)
- `worker/Dockerfile` - Add USER appuser (~10 lines)
- `docker-compose.yml` - Security settings (~30 lines)

**Dependencies:** None

**Definition of Done:**
- [ ] Containers run as non-root user
- [ ] Root filesystem is read-only
- [ ] Capabilities dropped to minimum
- [ ] Security scan shows no high/critical issues

---

#### Story 21.9: Input Validation and Sanitization
**As a** security engineer
**I want** comprehensive input validation
**So that** injection attacks are prevented

**Acceptance Criteria:**

```gherkin
Feature: Input Validation
  As the application
  I need to validate all user inputs
  So that malicious data is rejected

  Scenario: Validate UUID format for job IDs
    Given a job ID is provided
    When validating the job ID
    Then reject if not valid UUID format
    And return 400 Bad Request

  Scenario: Sanitize filenames
    Given a filename contains special characters
    When saving the file
    Then remove path traversal sequences (../)
    And replace special characters with safe alternatives
    And limit filename length to 255 characters
```

**Technical Implementation:**
- Create validation decorators for Flask routes
- Implement filename sanitization utility
- Add UUID validation for all job ID parameters

**Files to Modify:**
- `web/validation.py` (new) - Validation utilities (~150 lines)
- `web/app.py` - Apply validation decorators (~40 lines)
- `worker/tasks.py` - Input validation in tasks (~30 lines)

**Dependencies:** None

**Definition of Done:**
- [ ] All job ID parameters validated as UUIDs
- [ ] Filenames sanitized before saving
- [ ] Path traversal attempts rejected
- [ ] Security tests pass for validation

---

#### Story 21.10: Enhanced Health Checks
**As a** orchestrator (Kubernetes, Docker Swarm)
**I want** detailed health endpoints
**So that** I can route traffic appropriately

**Acceptance Criteria:**

```gherkin
Feature: Health Check Endpoints
  As a load balancer
  I need health endpoints to determine service status
  So that I only route to healthy instances

  Scenario: Liveness probe
    Given the application is running
    When /healthz is accessed
    Then return 200 if process is alive
    And return 500 if process is deadlocked

  Scenario: Readiness probe
    Given the application is starting
    When /readyz is accessed
    Then return 503 if still initializing
    And return 200 if ready to accept traffic

  Scenario: Detailed health status
    Given an operator needs diagnostics
    When /api/health is accessed
    Then return JSON with component status:
      - redis: connected/disconnected
      - disk: available space
      - gpu: available/unavailable
      - models: loaded/not loaded
```

**Technical Implementation:**
- Add `/healthz`, `/readyz`, `/livez` endpoints
- Check Redis connectivity, disk space, GPU, models
- Return detailed JSON for `/api/health`

**Files to Modify:**
- `web/app.py` - Health endpoints (~80 lines)
- `worker/health.py` (new) - Health check logic (~100 lines)

**Dependencies:** 21.2 (GPU detection)

**Definition of Done:**
- [ ] `/healthz` returns liveness status
- [ ] `/readyz` returns readiness status
- [ ] `/api/health` returns detailed component status
- [ ] Kubernetes probes configured to use endpoints

---

#### Story 21.11: Alerting Rules and Failure Notifications
**As a** SRE
**I want** alerting rules for critical failures
**So that** I'm notified of production issues

**Acceptance Criteria:**

```gherkin
Feature: Alerting Rules
  As a monitoring system
  I need alert rules for critical conditions
  So that operators are notified

  Scenario: High task failure rate
    Given task failure rate exceeds 10%
    When alert evaluation runs
    Then trigger alert "HighTaskFailureRate"
    And send notification to operators

  Scenario: Disk space critical
    Given disk usage exceeds 95%
    When alert evaluation runs
    Then trigger alert "DiskSpaceCritical"
    And notify immediately (P0)
```

**Technical Implementation:**
- Create Prometheus alerting rules file
- Define alerts: HighTaskFailureRate, DiskSpaceCritical, GPUUnavailable
- Configure alert routing (email, Slack, PagerDuty)

**Files to Modify:**
- `monitoring/alerts.yml` (new) - Prometheus alert rules (~150 lines)
- `docs/ALERTING.md` (new) - Alert documentation (~100 lines)

**Dependencies:** 21.5 (Prometheus metrics)

**Definition of Done:**
- [ ] Alert rules defined for critical conditions
- [ ] Alerts trigger correctly in test scenarios
- [ ] Alert routing configured
- [ ] Runbooks documented for each alert

---

#### Story 21.12: Graceful Shutdown and Task Cleanup
**As a** worker
**I want** graceful shutdown handling
**So that** tasks complete before container stops

**Acceptance Criteria:**

```gherkin
Feature: Graceful Shutdown
  As a worker receiving SIGTERM
  I need to complete current tasks
  So that no work is lost

  Scenario: Handle SIGTERM gracefully
    Given worker is processing a task
    When SIGTERM signal is received
    Then finish current task
    And reject new tasks
    And exit within 30 seconds

  Scenario: GPU memory cleanup on shutdown
    Given worker used GPU for tasks
    When shutdown is initiated
    Then free all GPU memory
    And call torch.cuda.empty_cache()
    And log cleanup status
```

**Technical Implementation:**
- Add signal handlers for SIGTERM, SIGINT
- Implement task timeout and state saving
- GPU memory cleanup in shutdown handler

**Files to Modify:**
- `worker/tasks.py` - Signal handlers (~60 lines)
- `worker/warmup.py` - Shutdown cleanup (~40 lines)

**Dependencies:** None

**Definition of Done:**
- [ ] Worker completes current task on SIGTERM
- [ ] New tasks rejected during shutdown
- [ ] GPU memory freed before exit
- [ ] Graceful shutdown completes within 30 seconds

---

#### Story 21.13: GPU/CPU Visual Indicator in UI ⭐ NEW
**As a** user
**I want** a visual indicator on the UI showing if the application is running on CPU or GPU
**So that** I understand the performance mode and know if AI features will be available

**Acceptance Criteria:**

```gherkin
Feature: GPU/CPU Mode Visual Indicator
  As a user accessing the web interface
  I need to see if the system is using GPU or CPU mode
  So that I can understand performance capabilities

  Scenario: Display GPU mode indicator when GPU is available
    Given the worker has detected GPU acceleration
    And GPU status is stored in Redis (marker:gpu_status = "available")
    When I load the web interface
    Then I should see a status indicator in the header
    And it should display "⚡ GPU Accelerated" with green color
    And show GPU model name (e.g., "NVIDIA RTX 4090")
    And show available VRAM (e.g., "16 GB available")

  Scenario: Display CPU mode indicator when no GPU detected
    Given the worker is running in CPU-only mode
    And GPU status is stored in Redis (marker:gpu_status = "unavailable")
    When I load the web interface
    Then I should see a status indicator in the header
    And it should display "🖥️ CPU Mode" with amber color
    And show a tooltip: "AI features may be slower or unavailable"
    And disable "PDF (High Accuracy)" conversion option

  Scenario: Display loading indicator during GPU detection
    Given the worker is still initializing
    And GPU status is not yet determined
    When I load the web interface
    Then I should see "⏳ Detecting GPU..." with neutral color
    And show a progress spinner
    And disable conversion form until status is determined

  Scenario: Real-time status updates via WebSocket
    Given I am viewing the web interface
    And GPU status changes (worker restart, GPU becomes available)
    When the worker updates Redis with new GPU status
    Then I should receive a WebSocket event
    And the UI indicator should update without page refresh
    And conversion options should enable/disable accordingly

  Scenario: Detailed GPU information in status modal
    Given I click on the GPU status indicator
    When the status modal opens
    Then I should see detailed information:
      - GPU status (Available/Unavailable)
      - GPU model and driver version
      - VRAM total and available
      - CUDA version
      - Current GPU utilization %
      - Number of active conversion jobs using GPU
      - GPU temperature (if available)
```

**Technical Implementation:**

**Backend Changes (web/app.py):**
- Extend `/api/status/services` endpoint to include GPU details:
  ```python
  {
    "marker_status": "ready",
    "gpu_status": "available",  # or "unavailable", "initializing"
    "gpu_info": {
      "model": "NVIDIA GeForce RTX 4090",
      "vram_total": 16,  # GB
      "vram_available": 14,  # GB
      "cuda_version": "11.8",
      "driver_version": "535.129.03",
      "utilization": 23  # percentage
    }
  }
  ```

**Worker Changes (worker/warmup.py):**
- Implement real `check_gpu_availability()` function (replace placeholder)
- Use `nvidia-smi` or PyTorch APIs to detect GPU:
  ```python
  import torch

  def check_gpu_availability():
      if torch.cuda.is_available():
          gpu_info = {
              "status": "available",
              "model": torch.cuda.get_device_name(0),
              "vram_total": torch.cuda.get_device_properties(0).total_memory / 1e9,
              "vram_available": (torch.cuda.get_device_properties(0).total_memory -
                                torch.cuda.memory_allocated(0)) / 1e9,
              "cuda_version": torch.version.cuda
          }
      else:
          gpu_info = {"status": "unavailable"}

      # Store in Redis
      redis_client.hset("marker:gpu_info", mapping=gpu_info)
      return gpu_info
  ```

**Frontend Changes (web/templates/index.html):**
- Add GPU status indicator component in header
- CSS styling for status states (green for GPU, amber for CPU)
- JavaScript to update indicator from API
- WebSocket listener for real-time updates
- GPU details modal on click

**Files to Modify:**
- `web/app.py` - Extend `/api/status/services` endpoint (~30 lines)
- `worker/warmup.py` - Implement `check_gpu_availability()` (~80 lines)
- `web/templates/index.html` - Add GPU status component (~100 lines)
- `web/static/styles.css` (if separate) - GPU indicator styles (~50 lines)

**Dependencies:**
- Story 21.2 (Runtime GPU Detection) must be completed first
- Requires Redis keys: `marker:gpu_info` hash with GPU details
- Requires WebSocket support (already implemented in Epic 16)

**Testing:**
```bash
# Test GPU mode
docker-compose up --build  # On GPU-enabled host
curl http://localhost:5000/api/status/services | jq '.gpu_status'
# Expected: "available"

# Test CPU mode
docker-compose up --build  # On CPU-only host
curl http://localhost:5000/api/status/services | jq '.gpu_status'
# Expected: "unavailable"

# Test UI indicator
open http://localhost:5000
# Expected: Visual indicator shows current mode
# Expected: Clicking opens detailed GPU info modal
```

**Definition of Done:**
- [ ] GPU detection stores detailed info in Redis
- [ ] `/api/status/services` returns GPU status and details
- [ ] UI header displays GPU/CPU indicator with appropriate icon and color
- [ ] Indicator updates in real-time via WebSocket
- [ ] Clicking indicator shows detailed GPU information modal
- [ ] PDF (High Accuracy) option disabled when GPU unavailable
- [ ] Visual indicator tested on both GPU and CPU-only systems
- [ ] Tooltip provides helpful context for CPU mode
- [ ] Browser console shows no errors

---

## Epic 22: HTTPS Support with Cloudflare Tunnel
**Status**: ✅ Completed (2026-01-16) | **Priority**: P1 - High | **Effort**: 1-2 days

**Originally Planned**: 2026-01-14, Session: velvet-dreaming-micali | **Embedded**: 2026-01-15 | **Completed**: 2026-01-16

**Goal**: Enable zero-touch HTTPS with automatic SSL certificate management via Cloudflare Tunnel.

#### Story 22.1: Cloudflare Tunnel Service Integration
**As a** DevOps engineer
**I want** to deploy Cloudflare Tunnel as a Docker service
**So that** the web UI is automatically accessible via HTTPS without manual certificate management

**Acceptance Criteria:**

```gherkin
Feature: Cloudflare Tunnel Service Deployment
  As a deployment engineer
  I need to run Cloudflare Tunnel in a container
  So that DocuFlux is accessible via HTTPS with automatic SSL

  Scenario: Initial tunnel setup with authentication
    Given I have a Cloudflare account with a domain
    And I have a Cloudflare Tunnel authentication token
    When I add the tunnel service to docker-compose.yml
    And I set CLOUDFLARE_TUNNEL_TOKEN environment variable
    And I run "docker-compose up cloudflare-tunnel"
    Then the tunnel container should start successfully
    And connect to Cloudflare's edge network
    And register as an active tunnel
    And log tunnel URL and connection status

  Scenario: Tunnel routes traffic to web service
    Given the Cloudflare Tunnel is running
    And the web service is running on port 5000
    When a user navigates to https://docuflux.example.com
    Then Cloudflare should terminate SSL/TLS
    And proxy the request to http://web:5000 internally
    And return the response over HTTPS
    And display valid SSL certificate in browser
```

**Technical Implementation:**
- Add `cloudflare-tunnel` service to `docker-compose.yml`
- Use official Cloudflare image: `cloudflare/cloudflared:latest`
- Command: `tunnel --no-autoupdate run --token ${CLOUDFLARE_TUNNEL_TOKEN}`
- Environment variable: `CLOUDFLARE_TUNNEL_TOKEN` (from tunnel creation)

**Files to Modify:**
- `docker-compose.yml` - Add cloudflare-tunnel service (~30 lines)
- `.env.example` - Document CLOUDFLARE_TUNNEL_TOKEN (~10 lines)
- `docs/CLOUDFLARE_TUNNEL_SETUP.md` (new) - Setup instructions (~200 lines)

**Dependencies:** Cloudflare account, domain, API token

**Definition of Done:**
- [x] Cloudflare Tunnel service in docker-compose.yml
- [x] Tunnel connects to Cloudflare edge network
- [x] Web UI accessible via HTTPS
- [x] SSL certificate valid in browser
- [x] Setup documentation complete

---

#### Story 22.2: Automatic Tunnel Configuration and DNS
**As a** system administrator
**I want** the tunnel to automatically configure DNS records
**So that** my domain points to the tunnel without manual DNS changes

**Files to Modify:**
- `cloudflare/config.yml` (new) - Tunnel ingress configuration (~20 lines)
- `cloudflare/setup.sh` (new) - Automated tunnel creation (~50 lines)
- `docs/CLOUDFLARE_TUNNEL_SETUP.md` - DNS configuration guide

---

#### Story 22.3: WebSocket Secure (WSS) Support Through Tunnel
**As a** developer
**I want** WebSocket connections to upgrade to WSS over HTTPS
**So that** real-time job updates work securely over encrypted connections

**Files to Modify:**
- `web/app.py` - Update CSP header to prioritize wss:// (~10 lines)
- `web/templates/index.html` - Socket.IO auto-detects protocol (no changes needed)
- `tests/integration/test_websocket_ssl.py` (new) - WebSocket SSL tests

---

#### Story 22.4: Session Cookie Security Updates
**As a** security engineer
**I want** session cookies to be secure and HTTPS-only
**So that** session tokens cannot be stolen over unencrypted connections

**Files to Modify:**
- `web/app.py` - Add ProxyFix middleware, update cookie config (~20 lines)
- `docker-compose.yml` - Set SESSION_COOKIE_SECURE=true for cloudflare profile (~5 lines)

---

## Epic 23: Application-Level Encryption at Rest
**Status**: 🔵 Planned | **Priority**: P2 - Medium | **Effort**: 4-5 days

**Originally Planned**: 2026-01-14, Session: velvet-dreaming-micali | **Embedded**: 2026-01-15

**Goal**: Implement AES-256-GCM encryption for all files and sensitive metadata with per-job encryption keys.

#### Story 23.1: File Encryption Service with AES-256-GCM
**As a** developer
**I want** a reusable encryption service for files
**So that** all uploaded and converted files are encrypted before writing to disk

**Files to Modify:**
- `web/encryption.py` (new) - AES-256-GCM encryption service (~200 lines)
- `worker/encryption.py` (new) - Same service for worker (~200 lines)
- `web/requirements.txt` - Add cryptography library (~1 line)
- `worker/requirements.txt` - Add cryptography library (~1 line)

---

#### Story 23.2: Per-Job Encryption Key Management
**As a** security engineer
**I want** each job to have a unique encryption key
**So that** compromising one key doesn't expose all files

**Files to Modify:**
- `web/key_manager.py` (new) - Per-job key management (~150 lines)
- `worker/key_manager.py` (new) - Same for worker (~150 lines)
- `web/app.py` - Integrate key generation on job creation (~30 lines)
- `worker/tasks.py` - Integrate key retrieval on file operations (~30 lines)

---

#### Story 23.3: Transparent Decryption on Download
**As a** user
**I want** to download converted files without manual decryption
**So that** encryption is transparent and doesn't affect usability

**Files to Modify:**
- `web/app.py` - Update download endpoint with decryption (~60 lines)
- `web/encryption.py` - Add streaming decryption methods (~50 lines)

---

#### Story 23.4: Redis Data Encryption for Sensitive Metadata
**As a** security engineer
**I want** to encrypt sensitive fields in Redis
**So that** job metadata doesn't expose user information

**Files to Modify:**
- `web/redis_client.py` (new) - Encrypted Redis client wrapper (~150 lines)
- `worker/redis_client.py` (new) - Same for worker (~150 lines)
- `web/app.py` - Use encrypted client for metadata operations (~40 lines)
- `worker/tasks.py` - Use encrypted client for status updates (~40 lines)

---

#### Story 23.5: Master Key and Secrets Management
**As a** security administrator
**I want** proper secrets management for encryption keys
**So that** master keys are protected and rotatable

**Files to Modify:**
- `web/secrets.py` (new) - Secrets and key management (~100 lines)
- `worker/secrets.py` (new) - Same for worker (~100 lines)
- `web/app.py` - Use secrets module on startup (~20 lines)
- `docker-compose.yml` - Document MASTER_ENCRYPTION_KEY (~15 lines)
- `.env.example` - Add master key configuration (~10 lines)
- `docs/SECRETS_MANAGEMENT.md` (new) - Key management guide (~200 lines)

---

## Epic 24: Encryption in Transit with Redis TLS
**Status**: 🔵 Planned | **Priority**: P1 - High | **Effort**: 2-3 days

**Originally Planned**: 2026-01-14, Session: velvet-dreaming-micali | **Embedded**: 2026-01-15

**Goal**: Secure all inter-service communication with Redis TLS and remove port exposure.

#### Story 24.1: Redis TLS Configuration with CA Certificates
**As a** security engineer
**I want** Redis to use TLS for all connections
**So that** inter-service communication is encrypted

**Files to Modify:**
- `docker-compose.yml` - Update Redis service with TLS config (~40 lines)
- `web/app.py` - Update Redis URLs to rediss:// (~10 lines)
- `worker/tasks.py` - Update Redis URLs to rediss:// (~10 lines)
- `certs/` (new directory) - Certificate storage

---

#### Story 24.2: Celery Task Message Encryption
**As a** security engineer
**I want** Celery task messages to be encrypted
**So that** task data is protected even if Redis is compromised

**Files to Modify:**
- `web/app.py` - Update Celery configuration (~20 lines)
- `worker/tasks.py` - Update Celery configuration (~20 lines)
- `docker-compose.yml` - Add CELERY_SIGNING_KEY environment variable (~5 lines)

---

#### Story 24.3: Remove Redis Port Exposure
**As a** security engineer
**I want** Redis to be accessible only from Docker internal network
**So that** external attackers cannot connect to Redis

**Files to Modify:**
- `docker-compose.yml` - Remove Redis ports, add optional Redis Commander (~30 lines)
- `docs/DEVELOPMENT.md` - Update Redis debugging instructions (~50 lines)

---

#### Story 24.4: Certificate Management for Redis TLS
**As a** DevOps engineer
**I want** automated certificate generation and renewal for Redis
**So that** TLS certificates don't expire unexpectedly

**Files to Modify:**
- `scripts/generate-certs.sh` (new) - Certificate generation for Redis (~80 lines)
- `scripts/renew-certs.sh` (new) - Certificate renewal automation (~60 lines)
- `scripts/reload-services.sh` (new) - Service reload after cert renewal (~40 lines)
- `docs/CERTIFICATE_MANAGEMENT.md` (new) - Certificate procedures (~250 lines)

---

## Epic 25: Certificate Management with Certbot & Cloudflare DNS
**Status**: 🔵 Planned | **Priority**: P1 - High | **Effort**: 2-3 days

**Originally Planned**: 2026-01-14, Session: velvet-dreaming-micali | **Embedded**: 2026-01-15

**Goal**: Automated certificate issuance and renewal via DNS-01 challenge for Redis TLS.

#### Story 25.1: Certbot Container with Cloudflare DNS Plugin
**As a** deployment engineer
**I want** Certbot running as a Docker service with Cloudflare DNS support
**So that** certificates can be obtained via DNS-01 challenge without exposing port 80

**Files to Modify:**
- `docker-compose.yml` - Add certbot service (~25 lines)
- `cloudflare/credentials.ini.example` (new) - API token template (~10 lines)
- `.gitignore` - Ignore credentials and private keys (~5 lines)
- `scripts/setup-certbot.sh` (new) - Initial Certbot setup (~70 lines)

---

#### Story 25.2: Automatic DNS-01 Challenge Completion
**As a** Certbot
**I want** to automatically create and delete DNS TXT records
**So that** ACME challenges complete without manual intervention

**Files to Modify:**
- `docs/CLOUDFLARE_API_SETUP.md` (new) - Token creation guide (~150 lines)
- `scripts/test-dns-challenge.sh` (new) - Test DNS propagation (~40 lines)

---

#### Story 25.3: Certificate Renewal Automation
**As a** system administrator
**I want** certificates to renew automatically before expiration
**So that** services don't experience TLS failures

**Files to Modify:**
- `scripts/renew-certs.sh` (new) - Renewal automation script (~60 lines)
- `scripts/reload-services.sh` (new) - Service reload script (~40 lines)
- `worker/tasks.py` - Add renew_certificates Celery task (~40 lines)
- `docker-compose.yml` - Mount scripts volume to services (~10 lines)

---

#### Story 25.4: Certificate Distribution to Redis and Services
**As a** certificate manager
**I want** certificates to be automatically distributed to services
**So that** all services use valid, up-to-date certificates

**Files to Modify:**
- `docker-compose.yml` - Add shared certificate volume (~20 lines)
- `scripts/deploy-certs.sh` (new) - Certificate deployment script (~60 lines)
- `scripts/reload-services.sh` - Service reload after cert deployment (~20 lines)

---

## Next Steps for Future Sessions

### IMMEDIATE PRIORITY: GPU Detection & UI Indicator (Epic 21)
**Recommended Starting Point**: Story 21.13 + 21.2 (Visual indicator + Runtime detection)

**Why Start Here:**
- High user value (immediate visibility into system mode)
- Unblocks Epic 21.2 implementation
- Simple frontend change, good warmup task
- Enables better UX for all users

**Implementation Order (Epic 21):**
1. **Phase 1** (Days 1-2): Stories 21.2 + 21.13 (Runtime GPU detection + UI indicator)
2. **Phase 2** (Days 3-4): Stories 21.1 + 21.3 (Build-time detection + Compose profiles)
3. **Phase 3** (Days 5-6): Stories 21.4 + 21.5 (Memory optimization + Metrics)
4. **Phase 4** (Days 7-8): Stories 21.7 + 21.8 + 21.9 (Security hardening)
5. **Phase 5** (Days 9-10): Stories 21.10 + 21.11 + 21.12 (Operational excellence)

### NEXT FOCUS: Encryption & HTTPS (Epics 22-25)
**After Epic 21 completion**, proceed with security enhancements:

**Implementation Order:**
1. **Phase 1** (Days 1-2): Epic 22 (Cloudflare Tunnel) - HTTPS foundation
2. **Phase 2** (Days 3-5): Epic 25 (Certbot) - Certificate infrastructure
3. **Phase 3** (Days 6-8): Epic 24 (Redis TLS) - Encryption in transit
4. **Phase 4** (Days 9-13): Epic 23 (Encryption at Rest) - File encryption

### FUTURE ENHANCEMENTS
- Epic 16.4: Kubernetes/Helm deployment manifests
- Epic 15.5: Job status webhooks/notifications
- New Epic: User authentication and tiered access
- New Epic: Conversion history and persistent storage

---

## Document Metadata

**Total Lines**: ~880
**Last Updated**: 2026-01-15
**Epics**: 25 (1-17 completed, 18-20 completed, 21-25 planned)
**Format Strategy**:
- Option A (Full BDD): Epics 21-25 (planned)
- Option B (Concise Story): Epics 18-20 (recent complex)
- Option C (Enhanced Checkbox): Epics 1-17 (completed simple)

**Self-Contained**: All epic details embedded inline, no external file dependencies
