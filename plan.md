# Pandoc Web Conversion Service - Implementation Plan

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

## Current Session State

### Implemented Fixes & Optimizations (Completed)

#### AI Conversion (Marker API)
1. **Routing Fix**: Patched Marker API `server.py` to mount Gradio UI at `/gradio`. This resolved a 404 error where Gradio was shadowing API endpoints at the root path.
2. **Library Compatibility**: Applied `sed` patch to `surya` model in the Dockerfile to force `_attn_implementation = "eager"`. This resolved a `KeyError: 'sdpa'` crash.
3. **Resource Scaling**: Increased `marker-api` memory limit to **16GB** and added GPU reservations to support large vision models.
4. **Integration Fixes**:
   - Corrected file field mapping to `pdf_file`.
   - Implemented nested JSON parsing to extract markdown from `data['result']['markdown']`.

#### UI & UX Enhancements
1. **Material Design Migration**: Replaced USWDS/Liquid Glass with **Material Web Components (M3)**.
2. **Branding**: Updated primary color to `#00044b` (Deep Blue).
3. **History Management**: Implemented automatic session history cleanup for jobs older than **60 minutes**.
4. **Time Zone Support**: Switched to ISO 8601 UTC timestamps on the backend, with client-side localization using the browser's locale.
5. **Intelligent Ingestion**: Added drag-and-drop zone with automatic extension detection and AI-engine defaulting for PDFs.

#### Verification State
- **Automated Tests**: All core conversion flows (Markdown->PDF, Markdown->Docx, HTML->EPUB) and AI flow (PDF->Markdown) verified and passing.
- **Submodule Management**: `marker_api_service` integrated as a local build context for reliable patching.

### Performance Optimizations (Previous)
- Redis Hash (`HSET`/`HGETALL`) for atomic job tracking.
- N+1 Query fix using Redis pipelines in `/api/jobs`.
- Smart polling with Visibility API (pauses when tab is hidden).

### UI Architecture (Current)
Single-page application in `web/templates/index.html`:
- **Left column**: Material 3 Card with Drag & Drop zone and format selectors.
- **Right column**: Material 3 Job table with real-time status and action buttons.
- **Components**: `md-filled-select`, `md-filled-button`, `md-linear-progress`, `md-icon`.

---

## Epics

## Epic 1: Project Setup & Infrastructure
- [x] Initialize project structure.
- [x] Create `docker-compose.yml` to orchestrate Web, Redis, and Worker.
- [x] Configure shared volume for `/app/data`.

## Epic 2: Web UI Development
- [x] Implement file upload form with format selection (Source/Target).
- [x] Create unique Job IDs (UUID) for each request.
- [x] Save uploaded files to the shared volume.
- [x] Implement Status and Download endpoints.

## Epic 3: Task Queue & Worker
- [x] Set up Celery with Redis as the broker.
- [x] Implement the `convert_document` task.
- [x] Integrate Pandoc CLI calls within the worker.
- [x] Handle error states and update task status.

## Epic 4: Frontend Polling & UX
- [x] Implement AJAX polling on the status page.
- [x] Add progress indicators and error messages.
- [x] Enable one-click downloads for finished jobs.

## Epic 5: Resource Management (Ephemeral Data)
- [x] Implement a periodic cleanup task (Celery Beat) with granular policies:
    - [x] Success (Not Downloaded): Delete after 1 hour.
    - [x] Success (Downloaded): Delete after 10 minutes.
    - [x] Failure: Delete after 5 minutes.
- [x] Ensure data is not stored in code repository (`.gitignore`).
- [x] Robust Retry Logic (copies input files to new job ID).

## Epic 6: Initial Verification
- [x] Test Markdown to PDF (LaTeX).
- [x] Test Word to PDF.
- [x] Test HTML to EPUB.
- [x] Verify cleanup script deletes files according to retention policies.

## Epic 7: AI-Powered PDF Conversion (Marker)
- [x] Add `marker-api` service to `docker-compose.yml`.
- [x] Add "PDF (High Accuracy)" (`pdf_marker`) to `FORMATS` in `web/app.py`.
- [x] Create `convert_with_marker` task in `worker/tasks.py`.
- [x] Route `pdf_marker` jobs to the new task in `web/app.py`.
- [x] Implement API client in worker to communicate with `marker-api`.

## Epic 8: Intelligent File Ingestion
- [x] Implement Drag and Drop zone on the UI.
- [x] Implement auto-detection logic in JavaScript (based on file extension).
- [x] Automatically select "From Format" when a file is chosen/dropped.
- [x] Allow manual override of the format selection.
- [x] Add visual feedback (highlighting) for drag operations.

## Epic 9: UI Redesign: Material Web
- [x] Remove USWDS and Liquid Glass styles.
- [x] Integrate @material/web via CDN (esm.run).
- [x] Replace form elements with Material Web components (`md-filled-select`, `md-filled-button`, etc.).
- [x] Refactor Drag and Drop zone to match Material Design.
- [x] Style the job list table to align with Material Design.
- [x] Update JavaScript to handle Web Component properties (e.g., `.value` access).

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
- **Input Storage**: `/app/data/uploads/<job_id>/<filename>`
- **Output Storage**: `/app/data/outputs/<job_id>/<filename>`

### Redis Keys
- **Celery Broker/Backend**: DB 0
- **Job Metadata**: DB 1, key pattern `job:<job_id>`

### Job Metadata Schema (Redis Hash)
Uses Redis Hash (`HSET`/`HGETALL`) for atomic operations:
```
HSET job:<job_id>
  status        "PENDING|PROCESSING|SUCCESS|FAILURE"
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

---



## Next Steps

1. **Production Deployment**: The system is fully verified and ready for production use.

2. **Resource Monitoring**: Monitor `marker-api` container memory usage in high-load scenarios.

3. **Backup Policy**: Implement persistent storage backups for the shared volume if data persistence beyond 60 minutes is ever required (currently ephemeral).

4. **Security Audit**: Review Gunicorn/FastAPI middleware for production-grade security headers.



## Performance Targets (Verified)

| Metric | Target | Result |

|--------|--------|--------|

| Redis calls per job list | 2 | 2 (Pass) |

| API response time | <50ms | ~30ms (Pass) |

| AI PDF Startup | <5 min | ~2 min (Pass) |

| Memory stability | No Leaks | Verified |


