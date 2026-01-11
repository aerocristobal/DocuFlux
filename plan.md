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

## Current Session State

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

---

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
**Rate Limiting (Epic 11.3)**: Implement `Flask-Limiter` to protect the `/convert` endpoint from abuse.
**Metrics (Epic 13.3)**: Implement Prometheus metrics for better operational insights.
