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
- **Frontend**: HTML5, USWDS 3.7.1, Vanilla JavaScript.
- **Infrastructure**: Docker, Docker Compose, Redis.
- **Conversion Engine**: Pandoc (via `pandoc/latex` image).

---

## Current Session State

### Performance Optimizations (Completed)
The following performance and resource optimizations have been implemented:

#### Backend Optimizations
1. **Redis Schema Migration** (`web/app.py`, `worker/tasks.py`):
   - Replaced JSON serialization with Redis Hash (`HSET`/`HGETALL`)
   - Atomic operations eliminate race conditions
   - Connection pooling with `max_connections=10`

2. **N+1 Query Fix** (`web/app.py:147-177`):
   - Batch fetch all job metadata using Redis pipeline
   - Reduced Redis calls from 40-60 per request to 2

3. **Celery Task Timeouts** (`worker/tasks.py:44-51`):
   - Hard limit: 10 minutes (`time_limit=600`)
   - Soft limit: 9 minutes (`soft_time_limit=540`)
   - Subprocess timeout: 500 seconds
   - `acks_late=True` for worker crash recovery

4. **Cleanup Frequency** (`worker/tasks.py:22`):
   - Reduced from every minute to every 5 minutes

#### Infrastructure Optimizations
1. **Docker Resource Limits** (`docker-compose.yml`):
   - Redis: 300MB memory, persistence enabled
   - Web: 1 CPU, 512MB memory
   - Worker: 2 CPUs, 2GB memory
   - Beat: 256MB memory

2. **Health Checks** (`docker-compose.yml`):
   - Redis: `redis-cli ping` every 10s
   - Web: HTTP check on `/` every 30s
   - Services wait for dependencies with `condition: service_healthy`

3. **Production Web Server** (`web/Dockerfile`):
   - Gunicorn with 2 workers, 4 threads each
   - Access logging enabled

4. **Pinned Dependencies**:
   - `flask==3.0.0`, `celery==5.3.4`, `redis==5.0.1`, `gunicorn==21.2.0`
   - `pandoc/latex:3.1` (pinned from `latest`)

#### Frontend Optimizations
1. **Smart Polling** (`web/templates/index.html`):
   - Active jobs: 5-second interval
   - Idle state: 15-second interval
   - Visibility API pauses polling when tab is hidden
   - Request deduplication with AbortController

2. **Event Delegation**:
   - Single event listener for all job actions
   - Data attributes instead of inline onclick handlers

3. **Asset Loading**:
   - Preconnect hint for CDN
   - Deferred USWDS init script

### Previous Uncommitted Changes
1. **`web/app.py`** - SPA architecture + performance optimizations
2. **`worker/tasks.py`** - Task timeouts + Redis Hash + cleanup optimization
3. **`web/templates/status.html`** - DELETED (replaced by SPA)
4. **`.gitignore`** - Ignores `data/`, `__pycache__/`, etc.
5. **`docker-compose.yml`** - Resource limits, health checks, volumes
6. **`web/Dockerfile`** - Gunicorn production server
7. **`worker/Dockerfile`** - Pinned pandoc version, concurrency limit
8. **`web/requirements.txt`** - Pinned versions + gunicorn
9. **`worker/requirements.txt`** - Pinned versions

### UI Architecture (Current)
Single-page application in `web/templates/index.html`:
- **Left column**: Conversion form (file upload, format selectors)
- **Right column**: Jobs table with real-time status updates (adaptive polling)
- **USWDS Components**: Header, Forms, Select, Buttons, Tables, Alerts, Tags
- **JavaScript Features**:
  - Smart polling with Visibility API (pauses when tab hidden)
  - Event delegation for job actions (cancel/retry)
  - Request deduplication (AbortController)
  - Auto-dismissing alerts with cleanup

---

## Implementation Phases

## Phase 1: Project Setup & Infrastructure
- [x] Initialize project structure.
- [x] Create `docker-compose.yml` to orchestrate Web, Redis, and Worker.
- [x] Configure shared volume for `/app/data`.

## Phase 2: Web UI Development
- [x] Implement file upload form with format selection (Source/Target).
- [x] Create unique Job IDs (UUID) for each request.
- [x] Save uploaded files to the shared volume.
- [x] Implement Status and Download endpoints.

## Phase 3: Task Queue & Worker
- [x] Set up Celery with Redis as the broker.
- [x] Implement the `convert_document` task.
- [x] Integrate Pandoc CLI calls within the worker.
- [x] Handle error states and update task status.

## Phase 4: Frontend Polling & UX
- [x] Implement AJAX polling on the status page.
- [x] Add progress indicators and error messages.
- [x] Enable one-click downloads for finished jobs.

## Phase 5: Resource Management (Ephemeral Data)
- [x] Implement a periodic cleanup task (Celery Beat) with granular policies:
    - [x] Success (Not Downloaded): Delete after 1 hour.
    - [x] Success (Downloaded): Delete after 10 minutes.
    - [x] Failure: Delete after 5 minutes.
- [x] Ensure data is not stored in code repository (`.gitignore`).
- [x] Robust Retry Logic (copies input files to new job ID).

## Phase 6: UI/UX Modernization (USWDS)
- [x] Replace Bootstrap with USWDS.
- [x] Implement USWDS components (Banner, Header, Forms, Tables, Alerts).
- [x] Ensure accessibility compliance (skip nav link, proper labels, semantic HTML).

## Phase 7: UI Redesign: Apple Liquid Glass
- [x] Implement "Liquid Glass" / Glassmorphism visual style.
    - [x] Add dynamic/colorful background (mesh gradient).
    - [x] Apply translucency and blur (`backdrop-filter`) to containers.
    - [x] Update border-radius and shadows for depth.
- [x] Modernize typography (System UI fonts).
- [x] Refine inputs and buttons to match the aesthetic.

## Phase 8: Final Verification
- [ ] Test Markdown to PDF (LaTeX).
- [ ] Test Word to PDF.
- [ ] Test HTML to EPUB.
- [ ] Verify cleanup script deletes files according to retention policies.

## Phase 9: AI-Powered PDF Conversion (Marker)
- [x] Add `marker-api` service to `docker-compose.yml`.
- [x] Add "PDF (High Accuracy)" (`pdf_marker`) to `FORMATS` in `web/app.py`.
- [x] Create `convert_with_marker` task in `worker/tasks.py`.
- [x] Route `pdf_marker` jobs to the new task in `web/app.py`.
- [x] Implement API client in worker to communicate with `marker-api`.

## Phase 10: Intelligent File Ingestion
- [x] Implement Drag and Drop zone on the UI.
- [x] Implement auto-detection logic in JavaScript (based on file extension).
- [x] Automatically select "From Format" when a file is chosen/dropped.
- [x] Allow manual override of the format selection.
- [x] Add visual feedback (highlighting) for drag operations.

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
1. Commit all changes (performance optimizations complete)
2. Rebuild Docker images: `docker-compose build`
3. Start services: `docker-compose up -d`
4. Run Phase 7 verification tests:
   - Test Markdown to PDF (LaTeX)
   - Test Word to PDF
   - Test HTML to EPUB
   - Verify cleanup runs every 5 minutes
5. Verify performance improvements:
   - Check `/api/jobs` response time (<50ms target)
   - Monitor container resources: `docker stats`
   - Verify Redis memory: `docker exec <redis> redis-cli info memory`

## Performance Targets (Expected)
| Metric | Before | After |
|--------|--------|-------|
| Redis calls per job list | 40-60 | 2 |
| Polling requests/minute | 20 | 4-12 |
| API response time | ~200ms | <50ms |
| Memory leaks | Present | None |
| Task timeout protection | None | 10 min |