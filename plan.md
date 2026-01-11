# DocuFlux - Implementation Plan

## Architecture Overview
This project implements a containerized document conversion service using a Task Queue pattern.

- **Web UI (Flask)**: Handles file uploads, status polling, and downloads.
- **Task Queue (Redis)**: Manages communication between the web app and workers.
- **Pandoc Worker (Celery)**: Executes Pandoc commands to convert files.
- **Shared Volume**: A shared storage space for input and output files.
- **Ephemeral Data Store (Redis)**: Tracks job metadata to enforce strict data retention policies.

## Tech Stack
- **Backend**: Python 3.11, Flask, Celery.
- **Frontend**: HTML5, Material Web Components (@material/web), Vanilla JavaScript.
- **Infrastructure**: Docker, Docker Compose, Redis, NVIDIA Container Toolkit (for AI).
- **Conversion Engines**: Pandoc 3.1, Marker (AI-powered PDF engine).

---

## Quick Start for New Sessions

**Last Updated**: 2026-01-10

### Critical Files to Understand
| File | Purpose | Lines |
|------|---------|-------|
| `web/app.py` | Flask backend - routes, security, Redis integration | ~326 |
| `worker/tasks.py` | Celery tasks - Pandoc & Marker conversions, cleanup | ~308 |
| `web/templates/index.html` | Material Design 3 UI - SPA with Socket.IO | ~274 |
| `docker-compose.yml` | Service orchestration (web, worker, beat, redis, marker-api) | ~150 |

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
3. Upload a PDF, select "PDF (High Accuracy)" as source - uses Marker AI (requires GPU for best performance)
4. Check `/api/status/services` endpoint for health status

### Common Issues
| Issue | Solution |
|-------|----------|
| Marker API 503 errors | Service starting up (takes 1-2 min with GPU, longer on CPU) |
| Redis connection refused | Ensure redis container is running: `docker-compose up redis` |
| Permission denied on data/ | `chmod -R 777 data/` or fix volume ownership |
| CSRF token missing | Clear browser cookies, reload page |

### Architecture Notes
- **Marker API**: Uses standalone `marker/Dockerfile` that directly clones [adithya-s-k/marker-api](https://github.com/adithya-s-k/marker-api). No submodule dependency.

---

## Current Session State

### Status Summary
| Category | Status | Notes |
|----------|--------|-------|
| Core Conversion | **Working** | Pandoc conversions (17 formats) fully functional |
| AI Conversion | **Working** | Marker API integration with retry logic |
| Web UI | **Working** | Material Design 3, drag-drop, real-time updates |
| Security | **Working** | CSRF, rate limiting, input validation, headers |
| Testing | **Working** | pytest suite with CI/CD pipeline |
| Observability | **Partial** | Logging done; Prometheus/Grafana not implemented |
| Deployment | **Partial** | Docker Compose ready; K8s manifests missing |

### Implemented Fixes & Optimizations

#### AI Conversion (Marker API)
**Routing Fix**: Patched Marker API `server.py` to mount Gradio UI at `/gradio`. This resolved a 404 error where Gradio was shadowing API endpoints at the root path.
**Library Compatibility**: Applied `sed` patch to `surya` model in the Dockerfile to force `_attn_implementation = "eager"`. This resolved a `KeyError: 'sdpa'` crash.
**Resource Scaling**: Increased `marker-api` memory limit to **16GB** and added GPU reservations to support large vision models.
**Integration Fixes**:
   - Corrected file field mapping to `pdf_file`.
   - Implemented nested JSON parsing to extract markdown from `data['result']['markdown']`.

#### UI & UX Enhancements
**Material Design Migration**: Replaced USWDS/Liquid Glass with **Material Web Components (M3)**..
**History Management**: Implemented automatic session history cleanup for jobs older than **60 minutes**.
**Time Zone Support**: Switched to ISO 8601 UTC timestamps on the backend, with client-side localization using the browser's locale.
**Intelligent Ingestion**: Added drag-and-drop zone with automatic extension detection and AI-engine defaulting for PDFs.
...

...
#### UI Architecture
Single-page application in `web/templates/index.html`:
- **Left column**: Material 3 Surface Card with Drag & Drop zone and format selectors.
- **Right column**: Material 3 Surface Card with `md-list` for job tracking.
- **Components**: `md-filled-select`, `md-filled-button`, `md-linear-progress` (determinate), `md-icon`, `md-list`, `md-assist-chip`, `md-dialog`.
- **Interactions**: WebSocket-based real-time updates (Socket.IO), Material 3 Dialogs, theme persistence.

### Security Hardening
...
4. **CI/CD Fixes**: Resolved submodule configuration error and fixed test collection failures by adding `pythonpath` to `pytest.ini`, creating `__init__.py` files, and moving module imports into test fixtures to ensure proper environment initialization.
5. **Security Remediations**: 
    - Hardened against path traversal by sanitizing filenames with `secure_filename` and strictly validating Job UUIDs.
    - Implemented **CSRF protection** using `Flask-WTF` and updated frontend to handle tokens.
    - Added **Rate Limiting** via `Flask-Limiter` to mitigate brute-force and DoS risks.
    - Hardened session cookies (`HttpOnly`, `Secure`, `SameSite`) and enabled **HSTS**.
    - Fixed **Cross-site Scripting (XSS)** vulnerabilities in the frontend by escaping all user-controlled data before rendering.
    - Disabled Flask debug mode by default to prevent info exposure and RCE risks.
    - Fixed application crash caused by malformed logging format string in `web/app.py`.
    - Migrated async stack from **eventlet to gevent** to resolve Redis connection timeouts and DNS lookup issues in the containerized environment.
4. **CI/CD Fixes**: Resolved submodule configuration error by adding `.gitmodules` and enabling recursive submodule checkout in GitHub Actions.

### UX & Observability
1. **Service Status Monitoring**: Added real-time checks for `marker-api` availability and server disk space. The UI warns users if the AI service is down or storage is low.
2. **Structured Logging**: Implemented JSON-formatted logging for better observability.
3. **Disk Space Pre-Check**: Implemented a 500MB free space safety check before accepting uploads (Error 507).
4. **Manual Job Deletion**: Added a "Trash" action to the job list, allowing users to remove jobs and associated files immediately.
5. **Smart Format Hints**: Updated UI to dynamically filter target formats and warn about service availability.
6. **Resilient AI Queueing**: Implemented automatic retry logic (exponential backoff) for AI conversion jobs. If `marker-api` is unavailable, jobs are now queued and retried instead of failing immediately.

### Verification State
- **Automated Tests**: All core conversion flows (Markdown->PDF, Markdown->Docx, HTML->EPUB) and AI flow (PDF->Markdown) verified and passing.
- **Submodule Management**: `marker_api_service` integrated as a local build context for reliable patching.
- **Documentation**: Comprehensive guides in `docs/` updated.

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

## Epic 7: AI-Powered PDF Conversion (Marker)
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
- [x] 13.7 Monitor `marker-api` container memory usage in high-load scenarios.
- [x] 13.8 Monitor `marker-api` availability.

## Epic 14: API & Documentation
- [x] 14.1 Generate OpenAPI/Swagger documentation for REST endpoints.
- [x] 14.2 Create production deployment guide (env vars, scaling, secrets).
- [x] 14.3 Document format compatibility matrix (which formats can convert to which).
- [x] 14.4 Add troubleshooting guide for common issues.
- [x] 14.5 Document Marker API integration and fallback behavior.

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

**Goal**: Remove dependency on `maximofn/marker_api_docker` submodule and use `adithya-s-k/marker-api` directly.

**Background**:
- Current: `marker_api_service/` is a git submodule of `maximofn/marker_api_docker`
- The Dockerfile inside already clones `adithya-s-k/marker-api` at build time
- This creates an unnecessary indirection and dependency on an external repo's Docker setup

**Key Differences** (current vs adithya-s-k/marker-api direct):
| Aspect | Current (via maximofn) | adithya-s-k/marker-api |
|--------|------------------------|------------------------|
| Port | 8000 | 8080 (default) |
| Health endpoint | `/health` | `/health` |
| Convert endpoint | `/convert` | `/convert` |
| Request field | `pdf_file` | `pdf_file` |
| Response | `{"result":{"markdown":"..."}}` | `{"status":"Success","result":{...}}` |
| Gradio path | `/gradio` (patched) | `/` (default) |

### Tasks
- [x] 17.1 Remove `maximofn/marker_api_docker` git submodule
  - `git submodule deinit marker_api_service`
  - `git rm marker_api_service`
  - Remove from `.gitmodules`
- [x] 17.2 Create new `marker/` directory with standalone Dockerfile
  - Base: `nvcr.io/nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04`
  - Clone `adithya-s-k/marker-api` directly
  - Apply Surya attention patch (if still needed)
  - Apply Gradio path patch (mount at `/gradio`)
  - Expose port 8000 (keep consistent with current setup)
- [x] 17.3 Update `docker-compose.yml` marker-api service
  - Change build context from `./marker_api_service` to `./marker`
  - Verify port mapping remains 8000:8000
- [x] 17.4 Verify response parsing in `worker/tasks.py`
  - Check if `result.markdown` path still works
  - Add fallback for different response structures
- [x] 17.5 Update health check in `web/app.py`
  - Verify `/health` endpoint works
- [x] 17.6 Update tests for new Marker API structure
  - `tests/unit/test_worker.py` - mock response format
  - `tests/wait_for_marker.py` - health check endpoint
- [x] 17.7 Update documentation
  - `docs/AI_INTEGRATION.md` - new setup instructions
  - `plan.md` - architecture notes
- [ ] 17.8 Verify end-to-end PDF conversion flow
  - Manual test: Upload PDF, convert to Markdown
  - Verify retry logic still works on 503

### Files to Modify
| File | Changes |
|------|---------|
| `.gitmodules` | Remove marker_api_service entry |
| `docker-compose.yml` | Update build context to `./marker` |
| `marker/Dockerfile` | New file (standalone, no submodule) |
| `worker/tasks.py` | Verify/update response parsing |
| `web/app.py` | Verify health check works |
| `tests/unit/test_worker.py` | Update mock responses |
| `tests/wait_for_marker.py` | Verify health endpoint |
| `docs/AI_INTEGRATION.md` | Update setup instructions |

### Rollback Plan
If issues arise, revert to submodule approach:
1. `git submodule add https://github.com/maximofn/marker_api_docker marker_api_service`
2. Restore docker-compose.yml build context
3. Delete `marker/` directory

## Epic 18: Epic: Migrate to datalab-to/marker
**As a developer, I want to replace adithya-s-k/marker-api with datalab-to/marker so that the application uses the latest, feature-rich conversion engine.**

- [ ] 18.1 ## Story: Install Latest Marker in Worker
**As a Docker builder, I want the Celery worker container to install marker-pdf[full] so that it supports PDF/images/PPTX/DOCX/XLSX/HTML/EPUB conversions.**
**Acceptance Criteria:**
- Dockerfile installs `marker-pdf[full]` on CUDA 12.1 base image[1]
- Container runs `marker_single --help` successfully with GPU detection
- `docker-compose up --build worker` succeeds without pip errors
- Test: `docker-compose run worker marker_single --version` outputs latest (post-0.3.2)[2]

- [ ] 18.2 ## Story: Refactor Marker Conversion Task
**As a backend developer, I want the `convertwithmarker` Celery task to invoke `marker_single` CLI instead of HTTP API so that conversions use datalab-to/marker directly.**
**Acceptance Criteria:**
- `workertasks.py` replaces requests.post with subprocess.run(["marker_single", input_path, output_dir, "--output_format", "markdown"])[1]
- Handles stdout parsing for markdown; stores in `data/outputs/job_id/output.md`
- Supports `--force_ocr` for PDFs; falls back to Pandoc if CLI fails
- Task updates Redis status: PENDING -> PROCESSING -> SUCCESS/FAILURE
- Logs GPU usage and OOM errors with `--batch_multiplier 1` default

- [ ] 18.3 ## Story: Update Docker Compose for Marker Workers
**As a DevOps engineer, I want docker-compose.yml to remove marker-api service and scale Celery workers with GPU so that Marker tasks run distributed without separate API.**
**Acceptance Criteria:**
- Remove `marker` service/build context from docker-compose.yml[1]
- Add `deploy.resources.reservations.devices` for `--gpus all` on worker service
- Use separate Celery queue (`marker_queue`) via `CELERY_TASK_ROUTES`
- `docker-compose up --scale worker=3` processes 3 parallel PDFs
- Healthcheck verifies `marker_single` availability in worker

- [ ] 18.4 ## Story: Enhance Task with Marker Features
**As an application user, I want PDF conversions to leverage datalab-to/marker's advanced options so that accuracy improves for tables/equations/images.**
**Acceptance Criteria:**
- CLI flags: `--use_llm` toggle (env `MARKER_USE_LLM=true`), `--force_ocr`, `--output_format json` option[3][1]
- Extract images to `data/outputs/job_id/images/`; link in markdown
- UI selects "High Accuracy LLM" for `--use_llm`; warns on no Ollama/Gemini key
- Job metadata in Redis includes `marker_flags` for audit

- [ ] 18.5 ## Story: Update Tests and Verification
**As a QA engineer, I want updated pytest suite to cover new Marker CLI integration so that migration doesn't regress PDF conversions.**
**Acceptance Criteria:**
- `tests/unittestworker.py` mocks `subprocess.run` for `marker_single`[1]
- `tests/waitformarker.py` tests end-to-end: upload PDF -> SUCCESS -> download.md
- Coverage >90% on `workertasks.py`; includes OOM retry scenarios
- `pytest tests -v --cov` passes; GitHub Actions green[1]

- [ ] 18.6 ## Story: Documentation and Rollback
**As a maintainer, I want updated docs and rollback plan so that the migration is reversible and reproducible.**
**Acceptance Criteria:**
- `docs/AI_INTEGRATION.md` details new CLI setup, flags, GPU tuning[1]
- `plan.md` Epic 18: Marker Migration with verification checklist
- Rollback: Restore marker service Dockerfile, revert task to HTTP
- README.md notes datalab-to/marker version pinning (`pip install marker-pdf==latest`)
[1](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/29358965/9e56848c-debb-4c89-9deb-aff1776080c1/plan.md)
[2](https://pypi.org/project/marker-pdf/0.3.2/)
[3](https://github.com/datalab-to/marker)

- [ ] 18.7 ## Story: Optimize Marker Integration with Python API
**As a performance engineer, I want Celery tasks to use datalab-to/marker's Python PdfConverter API instead of CLI subprocess so that conversion overhead is eliminated and GPU memory is tuned dynamically.**
**Acceptance Criteria:**
- `workertasks.py` imports `from marker.converters.pdf import PdfConverter; from marker.models import create_model_dict; from marker.output import text_from_rendered` and invokes `converter("input.pdf")` directly[1][2]
- Sets `os.environ["INFERENCE_RAM"] = "16"` (or env-var tunable) before import to match GPU VRAM; falls back to "8" on smaller GPUs[1]
- Extracts `markdown, metadata, images` via `text_from_rendered`; saves images to `data/outputs/job_id/images/`, embeds links in markdown
- Task completes 2x faster than subprocess (benchmark: <30s avg PDF on A6000); no shell spawn logs in Celery output
- Handles OOM by retrying with reduced `VRAM_PER_TASK=8` env and `--max_pages 50`; updates Redis `error: "GPU OOM, retrying low-mem"`
- Test: `pytest tests/test_marker_api.py` mocks `PdfConverter`, asserts markdown len >0, images dict non-empty; e2e GPU test passes
[1](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/29358965/9e56848c-debb-4c89-9deb-aff1776080c1/plan.md)
[2](https://github.com/datalab-to/marker)

## Epic 19: Functional Enhancements
**As a user, I want functionality that increase the value I received from the application.**
- [ ] 19.1## Story: Download Multi-File Conversion Outputs as ZIP Archive
**As a user, when I convert a file from one format to another format and the output has multiple files, such as a markdown formatted file and multiple images, I want to download all the files in one archive from the web UI so that I can easily access the complete conversion result without multiple downloads.**
**Acceptance Criteria:**
- PDF→Markdown via Marker generates `output.md` + `image-001.png`, `image-002.png`, etc. in `data/outputs/job_id/`[1][2]
- Download button shows "📦 Download All (ZIP)" when `job.status == SUCCESS` and directory contains >1 file; otherwise "📥 Download" for single file
- `webapp.py` adds `/download_zip/<job_id>` endpoint: scans `data/outputs/job_id/`, zips all files (`*.md`, `*.png`, `*.jpg`, `metadata.json`), streams response with `Content-Type: application/zip; Content-Disposition: attachment`
- ZIP contains flat structure: `output.md`, `images/image-001.png`, `images/image-002.png`, `metadata.json` (Marker metadata with page_stats, toc)
- Frontend `index.html` updates JS: `fetch('/download_zip/${jobId}')` triggers download; Material md-circular-progress during zip creation (>5s)
- Size limit: Reject jobs >500MB total output; Redis `error: "Output too large for ZIP"` with partial download option
- Test: `pytest tests/test_download_zip.py` creates mock job with 3 images + md, verifies ZIP extracts correctly with `zipfile` assertions
- Cleanup: ZIP streamed, not stored; respects existing retention (delete after download/1hr)[1]
**UI Mockup:**
```
Job List Item:
[Markdown ✓] sample.pdf → md    📦 Download All (4 files, 2.3MB)
                          12:34 PM  ✅ Success  28s
```
[1](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/29358965/9e56848c-debb-4c89-9deb-aff1776080c1/plan.md)
[2](https://github.com/datalab-to/marker)

## Technical Reference

### File Locations
| Component | Path |
|-----------|------|
| Flask App | `web/app.py` |
| Celery Worker | `worker/tasks.py` |
| Main Template | `web/templates/index.html` |
| Docker Config | `docker-compose.yml` |

### Storage Paths (Inside Containers)
- **Input Storage**: `data/uploads/<job_id>/<filename>` (Relative to app root)
- **Output Storage**: `data/outputs/<job_id>/<filename>` (Relative to app root)

### Redis Keys
- **Celery Broker/Backend**: DB 0
- **Job Metadata**: DB 1, key pattern `job:<job_id>`

### Job Metadata Schema (Redis Hash)
Uses Redis Hash (`HSET`/`HGETALL`) for atomic operations:
```
HSET job:<job_id>
  status        "PENDING|PROCESSING|SUCCESS|FAILURE|REVOKED"
  created_at    "1704672000.0"    # String timestamp
  started_at    "1704672001.0"
  completed_at  "1704672005.0"
  downloaded_at "1704672100.0"
  filename      "document.md"
  error         "Error message (max 500 chars)"
  is_retry      "true"
  original_job_id "uuid-of-original"
```

### Supported Formats
17 formats defined in `web/app.py:FORMATS`:
- **Markdown**: markdown, gfm
- **Web**: html5, ipynb
- **Office**: docx, pptx (output only), odt, rtf
- **E-Books**: epub3, epub2
- **Technical**: latex, pdf (output only), asciidoc, rst, bibtex
- **Wiki**: mediawiki, jira

### API Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Main page with conversion form and job list |
| POST | `/convert` | Submit new conversion job |
| GET | `/api/jobs` | List all jobs for current session |
| POST | `/api/cancel/<job_id>` | Cancel a queued job |
| POST | `/api/retry/<job_id>` | Retry a failed job (creates new job) |
| GET | `/download/<job_id>` | Download converted file |
| GET | `/api/status/services` | Check status of dependent services (Marker API, Disk Space) |

## Next Steps for Future Sessions

### ~~Priority 0: Marker API Migration (Epic 17)~~ COMPLETED
| Task | Epic | Status | Description |
|------|------|--------|-------------|
| Remove submodule | 17.1 | Done | Removed maximofn/marker_api_docker submodule |
| Create standalone Dockerfile | 17.2 | Done | Created `marker/Dockerfile` |
| Update docker-compose | 17.3 | Done | Changed build context to `./marker` |
| Verify response parsing | 17.4 | Done | Parsing already handles multiple formats |
| Update tests | 17.6 | Done | Updated `wait_for_marker.py` to use `/health` |
| End-to-end verification | 17.8 | **Pending** | Requires manual test with `docker-compose up` |

### Priority 1: Observability (Production Readiness)
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Prometheus metrics | 13.3 | Medium | Add `/metrics` endpoint with conversion counts, durations, queue depth |
| Health checks | 13.2 | Low | Add healthcheck directives for worker/beat in docker-compose.yml |
| Grafana dashboard | 13.4 | Medium | Create dashboard template for the Prometheus metrics |
| Alerting rules | 13.6 | Medium | Configure alerts for queue backlog, failed jobs, disk space |

### Priority 2: DevOps & Infrastructure
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| GitHub branch protection | 11.8 | Low | Configure ruleset to prevent force push to main |
| Worker concurrency env var | 16.1 | Low | Make `--concurrency=N` configurable via WORKER_CONCURRENCY env |
| Kubernetes manifests | 16.4 | High | Create Helm chart for K8s deployment |
| Load testing | 16.5 | Medium | Add locust/k6 test suite for performance validation |

### Priority 3: Advanced Features
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Job webhooks | 15.5 | Medium | POST callback to user-defined URL on job completion/failure |

### Quick Wins (< 1 hour each)
1. **Epic 11.8** - GitHub branch protection: Settings → Branches → Add ruleset
2. **Epic 13.2** - Health checks: Add `healthcheck` to worker/beat in docker-compose.yml
3. **Epic 16.1** - Worker concurrency: Replace hardcoded `--concurrency=2` with `${WORKER_CONCURRENCY:-2}`

### Implementation Notes for Future Sessions
- **Prometheus metrics**: Use `prometheus_flask_exporter` library; expose at `/metrics`
- **Kubernetes**: Consider Kompose for initial conversion, then customize
- **Load testing**: Focus on `/convert` endpoint with various file sizes
- **Webhooks**: Add `callback_url` parameter to `/convert`, call on task completion
