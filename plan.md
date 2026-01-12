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

**Last Updated**: 2026-01-11

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
| AI Conversion | **Working** | Marker Python API integration with options (OCR, LLM) |
| Web UI | **Working** | Material Design 3, drag-drop, real-time updates, Marker status banner |
| Security | **Working** | CSRF, rate limiting, input validation, headers |
| Testing | **Partial** | pytest suite exists, but needs updates for recent Marker API changes |
| Observability | **Partial** | Logging done; Prometheus/Grafana not implemented |
| Deployment | **Partial** | Docker Compose ready; K8s manifests missing |

### Recent Changes
- **Epic 18 (Marker Migration)**: Completed migration to direct library usage.
- **Epic 19.1 (ZIP Download)**: Implemented automatic ZIP bundling for multi-file outputs (images + markdown).
- **Startup Optimization**: Added build-time model download and runtime `warmup.py` to prevent cold start delays.
- **Status Reporting**: Added real-time Marker status polling (Initialization/Ready) to the UI.
- **Bug Fixes**: Resolved worker hang by switching to `solo` pool and removing Gevent patching in worker. Fixed `TypeError` in `PdfConverter` call.

---

## Epics

## Epic 1: Project Setup & Infrastructure
- [x] 1.1 Initialize project structure.
- [x] 1.2 Create `docker-compose.yml` to orchestrate Web, Redis, and Worker.
- [x] 1.3 Configure shared volume for `/app/data`.

## Epic 2: Web UI Development
- [x] 2.1 Implement file upload form with format selection (Source/Target).
- [x] 2.2 Create unique Job IDs (UUID) for each request.
- [x] 2.3 Save uploaded files to the shared volume.
- [x] 2.4 Implement Status and Download endpoints.

## Epic 3: Task Queue & Worker
- [x] 3.1 Set up Celery with Redis as the broker.
- [x] 3.2 Implement the `convert_document` task.
- [x] 3.3 Integrate Pandoc CLI calls within the worker.
- [x] 3.4 Handle error states and update task status.

## Epic 4: Frontend Polling & UX
- [x] 4.1 Implement AJAX polling on the status page.
- [x] 4.2 Add progress indicators and error messages.
- [x] 4.3 Enable one-click downloads for finished jobs.

## Epic 5: Resource Management (Ephemeral Data)
- [x] 5.1 Implement a periodic cleanup task (Celery Beat) with granular policies:
    - [x] 5.1.1 Success (Not Downloaded): Delete after 1 hour.
    - [x] 5.1.2 Success (Downloaded): Delete after 10 minutes.
    - [x] 5.1.3 Failure: Delete after 5 minutes.
- [x] 5.2 Ensure data is not stored in code repository (`.gitignore`).
- [x] 5.3 Robust Retry Logic (copies input files to new job ID).

## Epic 6: Initial Verification
- [x] 6.1 Test Markdown to PDF (LaTeX).
- [x] 6.2 Test Word to PDF.
- [x] 6.3 Test HTML to EPUB.
- [x] 6.4 Verify cleanup script deletes files according to retention policies.

## Epic 7: AI-Powered PDF Conversion (Marker) - LEGACY
*Superseded by Epic 18. Original implementation used a separate API service.*
- [x] 7.1 Add `marker-api` service to `docker-compose.yml`.
- [x] 7.2 Add "PDF (High Accuracy)" (`pdf_marker`) to `FORMATS` in `web/app.py`.
- [x] 7.3 Create `convert_with_marker` task in `worker/tasks.py`.
- [x] 7.4 Route `pdf_marker` jobs to the new task in `web/app.py`.
- [x] 7.5 Implement API client in worker to communicate with `marker-api`.

## Epic 8: Intelligent File Ingestion
- [x] 8.1 Implement Drag and Drop zone on the UI.
- [x] 8.2 Implement auto-detection logic in JavaScript (based on file extension).
- [x] 8.3 Automatically select "From Format" when a file is chosen/dropped.
- [x] 8.4 Allow manual override of the format selection.
- [x] 8.5 Add visual feedback (highlighting) for drag operations.

## Epic 9: UI Redesign: Material Web
- [x] 9.1 Remove USWDS and Liquid Glass styles.
- [x] 9.2 Integrate @material/web via CDN (esm.run).
- [x] 9.3 Replace form elements with Material Web components (`md-filled-select`, `md-filled-button`, etc.).
- [x] 9.4 Refactor Drag and Drop zone to match Material Design.
- [x] 9.5 Style the job list table to align with Material Design.
- [x] 9.6 Update JavaScript to handle Web Component properties (e.g., `.value` access).

## Epic 10: Intelligent Theme Customization
- [x] 10.1 Implement theme toggle in the header (System/Light/Dark).
- [x] 10.2 Create Dark Mode color tokens (Material Design 3 dark scheme).
- [x] 10.3 Implement JavaScript logic for System/Manual theme switching.
- [x] 10.4 Persist user preference in LocalStorage.
- [x] 10.5 Listen for system preference changes (`prefers-color-scheme`).

## Epic 11: Security Hardening
- [x] 11.1 Implement file size limits (100MB max upload).
- [x] 11.2 Add MIME type validation (whitelist approach, not extension-only).
- [x] 11.3 Add rate limiting on `/convert` endpoint (Flask-Limiter).
- [x] 11.4 Add security headers (CSP, X-Frame-Options, HSTS, etc.).
- [x] 11.5 Audit Redis key patterns for injection risks.
- [x] 11.6 Externalize `SECRET_KEY` with documented env var requirement.
- [x] 11.7 Review Gunicorn/FastAPI middleware for production-grade security headers.
- [ ] 11.8 Add GitHub ruleset to protect branch from forced push.

## Epic 12: Test Infrastructure
- [x] 12.1 Adopt pytest framework with proper test discovery.
- [x] 12.2 Create unit tests for `web/app.py` core functions.
- [x] 12.3 Create unit tests for `worker/tasks.py` task logic.
- [x] 12.4 Add negative test cases (invalid formats, corrupted files, missing services).
- [x] 12.5 Implement code coverage reporting (pytest-cov).
- [x] 12.6 Add GitHub Actions CI/CD pipeline for automated testing.

## Epic 13: Observability & Monitoring
- [x] 13.1 Implement structured logging (JSON format for parsing).
- [ ] 13.2 Add health checks for Worker and Beat services in `docker-compose.yml`.
- [ ] 13.3 Add Prometheus metrics endpoint (conversion counts, durations, queue depth).
- [ ] 13.4 Create Grafana dashboard template.
- [x] 13.5 Add disk space pre-checks before processing large files.
- [ ] 13.6 Implement alerting rules for critical failures.
- [x] 13.7 Monitor `marker-api` container memory usage in high-load scenarios (Obsolete with Epic 18).
- [x] 13.8 Monitor `marker-api` availability (Obsolete with Epic 18).

## Epic 14: API & Documentation
- [x] 14.1 Generate OpenAPI/Swagger documentation for REST endpoints.
- [x] 14.2 Create production deployment guide (env vars, scaling, secrets).
- [x] 14.3 Document format compatibility matrix (which formats can convert to which).
- [x] 14.4 Add troubleshooting guide for common issues.
- [x] 14.5 Document Marker API integration and fallback behavior (Updated for Epic 18).

## Epic 15: UX Enhancements
- [x] 15.1 Add determinate progress indication (percentage completion).
- [x] 15.2 Implement batch/bulk file upload support.
- [x] 15.3 Add manual job deletion from UI (before auto-cleanup).
- [x] 15.4 Display format compatibility hints when selecting formats.
- [ ] 15.5 Add job status webhooks/notifications.
- [x] 15.6 Add an 'available' or 'unavailable' status indicator (Epic 13.8).
- [x] 15.7 Add an error message if a user tried to convert a file and the disk space pre-checks fail (Epic 13.5).

## Epic 16: Scalability & Performance
- [ ] 16.1 Make worker concurrency configurable via env var.
- [x] 16.2 Implement WebSocket support to replace polling.
- [x] 16.3 Add Redis connection pooling optimization.
- [ ] 16.4 Create Kubernetes/Helm deployment manifests.
- [ ] 16.5 Add load testing suite (locust/k6).
- [x] 16.6 Implement queue priority levels (small files faster).
- [x] 16.7 Implement queueing for marker-api jobs when api is not available.

## Epic 17: Marker API Migration (maximofn → adithya-s-k)
**STATUS: SUPERCEDED** by Epic 18.
The goal was to switch API implementations, but we decided to move the engine directly into the worker (Epic 18) for better performance and simpler architecture.
- [x] 17.1 Remove `maximofn/marker_api_docker` git submodule.
- [x] 17.2 Create new `marker/` directory with standalone Dockerfile (Deleted in Epic 18).
- [x] 17.3 Update `docker-compose.yml` marker-api service (Removed in Epic 18).

## Epic 18: Migrate to datalab-to/marker
**Goal**: Replace separate API service with direct library integration in the worker.

- [x] 18.1 **Story: Install Latest Marker in Worker**
  - Dockerfile installs `marker-pdf[full]` on CUDA 11.8 base image.
  - Worker container supports `marker_single` CLI.

- [x] 18.2 **Story: Refactor Marker Conversion Task**
  - `worker/tasks.py` invokes `marker_single` CLI via `subprocess`.
  - Handles stdout parsing and output copying.

- [x] 18.3 **Story: Update Docker Compose for Marker Workers**
  - `marker-api` service removed.
  - `worker` service given GPU access (`deploy.resources.reservations`).

- [x] 18.4 **Story: Enhance Task with Marker Features**
  - Implement CLI flags: `--use_llm` toggle, `--force_ocr`.
  - Extract images to `data/outputs/job_id/images/`.

- [x] 18.5 **Story: Update Tests and Verification**
  - Update `tests/unit/test_worker.py` to mock `subprocess.run` instead of `requests.post`.
  - Update `tests/wait_for_marker.py` (or remove if no longer needed).

- [x] 18.6 **Story: Documentation and Rollback**
  - Update `README.md` (remove marker-api references).
  - Verify `docs/AI_INTEGRATION.md`.

- [x] 18.7 **Story: Optimize Marker Integration with Python API**
  - Use `from marker.converters.pdf import PdfConverter` instead of CLI for better control.

## Epic 19: Functional Enhancements
**Goal**: Add features that increase value for the user.

- [x] 19.1 **Story: Download Multi-File Conversion Outputs as ZIP Archive**
  - When Marker produces multiple files (images + markdown), bundle them into a ZIP.
  - Update UI to show "Download ZIP" for these jobs.

## Epic 20: Pre-Caching & UI Status (New)
**Goal**: Improve user experience by pre-loading AI models and reporting service status.

- [x] 20.1 **Story: Pre-Cache Marker LLMs on Container Startup**
  - Add build-time download to Dockerfile.
  - Create `warmup.py` entrypoint to verify/load models and report ready status.
  - Update `worker` to use `solo` pool for stability.

- [x] 20.2 **Story: UI Service Status with LLM Download ETA**
  - Add `/api/status/services` logic to check Redis keys.
  - Add UI banner in `index.html` to show "Initializing" status.
  - Disable PDF conversion option until service is ready.

## Next Steps for Future Sessions

### Priority 1: Stabilization & Testing
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Update Unit Tests | 12 | Medium | Update `tests/unit/test_worker.py` to match the new `PdfConverter` API usage (mocking `PdfConverter` class instead of `subprocess`). |
| Load Testing | 16.5 | Medium | Validate behavior under concurrent load with `solo` pool (verify queueing works). |

### Priority 2: DevOps
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Prometheus metrics | 13.3 | Medium | Add `/metrics` endpoint to worker for monitoring queue depth and GPU usage. |
| Kubernetes Manifests | 16.4 | High | Prepare Helm charts for production deployment. |
