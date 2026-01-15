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
| Security | **Partial** | CSRF, rate limiting, headers; needs secrets mgmt, container hardening, enhanced validation |
| Testing | **Partial** | pytest suite exists, but needs updates for recent Marker API changes |
| Observability | **Partial** | Logging done; Prometheus/Grafana not implemented, GPU monitoring placeholder only |
| Deployment | **Partial** | Docker Compose ready; no GPU detection, no profiles, K8s manifests missing |
| Resource Efficiency | **Needs Work** | No GPU detection, hardcoded 16GB VRAM, no conditional builds, ~15GB worker image |

### Recent Changes
- **2026-01-14 (Epics 22-25 Planning)**: Completed comprehensive planning for HTTPS support via Cloudflare Tunnel, application-level encryption at rest, Redis TLS with CA certificates, and automated certificate management with Certbot + Cloudflare DNS. Created detailed BDD user stories covering all security domains. See `/home/chris/.claude/plans/velvet-dreaming-micali.md`.
- **2026-01-14 (Epic 21 Planning)**: Completed comprehensive planning for GPU detection, resource optimization, security hardening, and operational excellence. Created detailed implementation plan with BDD user stories. See previous plan session.
- **Epic 18 (Marker Migration)**: Completed migration to direct library usage.
- **Epic 19.1 (ZIP Download)**: Implemented automatic ZIP bundling for multi-file outputs (images + markdown).
- **Startup Optimization**: Added build-time model download and runtime `warmup.py` to prevent cold start delays.
- **Status Reporting**: Added real-time Marker status polling (Initialization/Ready) to the UI.
- **Bug Fixes**: Resolved worker hang by switching to `solo` pool and removing Gevent patching in worker. Fixed `TypeError` in `PdfConverter` call.

### Known Gaps Identified in Planning

**GPU & Resource Management (Epic 21):**
- **No GPU detection**: Worker always assumes GPU available, fails on CPU-only hosts
- **Hardcoded GPU assumptions**: INFERENCE_RAM=16 hardcoded, no runtime adaptation
- **Large image size**: Worker image ~15GB due to unconditional CUDA/PyTorch/models
- **Incomplete GPU monitoring**: `check_gpu_memory()` in warmup.py is a placeholder
- **No deployment profiles**: All services start unconditionally, no GPU/CPU profiles

**Security & Encryption (Epics 22-25):**
- **No HTTPS support**: Web service runs on plain HTTP port 5000, no TLS termination
- **No encryption at rest**: Files stored in plaintext (~53MB in data/), 777 permissions
- **Redis exposed**: Port 6379 exposed on 0.0.0.0 (critical security vulnerability)
- **No encryption in transit**: All Redis connections unencrypted, Celery messages in plaintext
- **No certificate infrastructure**: No PKI, no certificate management, no renewal automation
- **Insecure cookies**: SESSION_COOKIE_SECURE=false, vulnerable to session hijacking
- **WebSocket unencrypted**: Uses ws:// protocol, no wss:// support
- **Default secrets**: SECRET_KEY hardcoded to default value, no validation

**Observability & Operations:**
- **Missing observability**: No Prometheus metrics, no alerting, no detailed health checks
- **Limited input validation**: Basic validation exists but needs enhancement
- **Container security**: Containers run as root, no read-only filesystems

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

## Epic 21: GPU Detection and Resource Optimization (Planned)
**Goal**: Enable DocuFlux to run efficiently on both GPU and CPU-only infrastructure with intelligent detection and conditional builds.

**Status**: Planning completed 2026-01-14. See detailed plan at `/home/chris/.claude/plans/velvet-dreaming-micali.md`

- [ ] 21.1 **Story: Detect GPU Availability at Build Time**
  - Add build arguments and conditional Dockerfile logic
  - Create GPU detection script (`detect_gpu.sh`)
  - Support building `worker:gpu` and `worker:cpu` images
  - Create separate requirements files for GPU and CPU builds

- [ ] 21.2 **Story: Runtime GPU Detection and Graceful Degradation**
  - Implement real `check_gpu_availability()` in `warmup.py` (replace placeholder)
  - Store GPU status and VRAM info in Redis
  - Add GPU exception handling in Marker conversion tasks
  - Update UI to disable Marker option when GPU unavailable

- [ ] 21.3 **Story: Docker Compose Profiles for Deployment Scenarios**
  - Add GPU and CPU profiles to docker-compose
  - Create `docker-compose.gpu.yml` and `docker-compose.cpu.yml` overrides
  - Support mixed infrastructure deployments

- [ ] 21.4 **Story: Reduce Worker Memory Footprint**
  - Implement lazy model loading
  - Add memory cleanup after tasks (`gc.collect()`)
  - Adjust memory limits per deployment profile

- [ ] 21.5 **Story: Prometheus Metrics Endpoint**
  - Add `prometheus-client` dependency
  - Expose `/metrics` endpoint on port 9090
  - Track task duration, queue depth, GPU utilization

- [ ] 21.6 **Story: Intelligent Data Retention**
  - Prioritize cleanup of large files
  - Track last viewed timestamps
  - Implement emergency cleanup triggers

- [ ] 21.7 **Story: Secrets Management and Rotation**
  - Create secrets loading utility
  - Validate secrets at startup (fail fast if default)
  - Support Docker Swarm secrets and rotation

- [ ] 21.8 **Story: Container Security Hardening**
  - Add non-root users to Dockerfiles
  - Enable read-only root filesystems
  - Drop unnecessary Linux capabilities

- [ ] 21.9 **Story: Input Validation and Sanitization**
  - Add UUID validation decorators
  - Implement comprehensive input validation
  - Create validation utilities module

- [ ] 21.10 **Story: Enhanced Health Checks**
  - Add `/healthz`, `/readyz`, `/livez` endpoints
  - Check Redis, disk, GPU, and model availability
  - Return detailed component status

- [ ] 21.11 **Story: Alerting Rules and Failure Notifications**
  - Create Prometheus alerting rules
  - Configure alert routing
  - Add critical logging for alert triggers

- [ ] 21.12 **Story: Graceful Shutdown and Task Cleanup**
  - Add SIGTERM/SIGINT signal handlers
  - Implement task timeout and state saving
  - Add GPU memory cleanup on shutdown

## Epic 22: HTTPS Support with Cloudflare Tunnel (Planned)
**Goal**: Enable zero-touch HTTPS with automatic SSL certificate management via Cloudflare Tunnel.

**Status**: Planning completed 2026-01-14. See detailed plan at `/home/chris/.claude/plans/velvet-dreaming-micali.md`

- [ ] 22.1 **Story: Cloudflare Tunnel Service Integration**
  - Add cloudflare-tunnel Docker service
  - Configure CLOUDFLARE_TUNNEL_TOKEN environment variable
  - Setup automatic connection to Cloudflare edge network
  - Health monitoring for tunnel status

- [ ] 22.2 **Story: Automatic Tunnel Configuration and DNS**
  - Create tunnel in Cloudflare dashboard
  - Configure ingress rules for domain routing
  - Automatic CNAME record creation
  - Support multiple service routing

- [ ] 22.3 **Story: WebSocket Secure (WSS) Support Through Tunnel**
  - Enable wss:// protocol for Socket.IO
  - Update CSP headers for WebSocket security
  - Test real-time updates over encrypted connection

- [ ] 22.4 **Story: Session Cookie Security Updates**
  - Set SESSION_COOKIE_SECURE=true for HTTPS
  - Enable ProxyFix middleware for X-Forwarded-Proto
  - Always apply HSTS headers in production
  - Test secure cookie flags in browser

## Epic 23: Application-Level Encryption at Rest (Planned)
**Goal**: Implement AES-256-GCM encryption for all files and sensitive metadata with per-job encryption keys.

**Status**: Planning completed 2026-01-14. See detailed plan at `/home/chris/.claude/plans/velvet-dreaming-micali.md`

- [ ] 23.1 **Story: File Encryption Service with AES-256-GCM**
  - Create EncryptionService class in web and worker
  - Implement streaming encryption for large files
  - Encrypt uploads before writing to disk
  - Encrypt conversion outputs

- [ ] 23.2 **Story: Per-Job Encryption Key Management**
  - Generate unique 256-bit key per job
  - Encrypt job keys with master key
  - Store encrypted keys in Redis with TTL
  - Support key rotation and dual-key mode

- [ ] 23.3 **Story: Transparent Decryption on Download**
  - Stream decrypted files to HTTP response
  - Handle ZIP archives with multiple encrypted files
  - Verify GCM authentication tags
  - Never write plaintext to disk

- [ ] 23.4 **Story: Redis Data Encryption for Sensitive Metadata**
  - Create EncryptedRedisClient wrapper
  - Encrypt filename and error message fields
  - Keep indexed fields plaintext for queries
  - Transparent encryption/decryption

- [ ] 23.5 **Story: Master Key and Secrets Management**
  - Load master key from environment or Docker secrets
  - Derive master key from SECRET_KEY using HKDF
  - Refuse to start with default secrets in production
  - Document key backup and recovery procedures

## Epic 24: Encryption in Transit with Redis TLS (Planned)
**Goal**: Secure all inter-service communication with Redis TLS and remove port exposure.

**Status**: Planning completed 2026-01-14. See detailed plan at `/home/chris/.claude/plans/velvet-dreaming-micali.md`

- [ ] 24.1 **Story: Redis TLS Configuration with CA Certificates**
  - Generate certificates for Redis server and clients
  - Configure Redis with TLS-only mode
  - Update connection URLs to rediss://
  - Client certificate authentication (mTLS)

- [ ] 24.2 **Story: Celery Task Message Encryption**
  - Enable Celery message signing
  - Configure encrypted task serializer
  - Reject unsigned/tampered messages
  - Log authentication failures

- [ ] 24.3 **Story: Remove Redis Port Exposure**
  - Remove ports section from Redis service
  - Isolate Redis to Docker internal network
  - Add optional Redis Commander for debugging
  - Update development documentation

- [ ] 24.4 **Story: Certificate Management for Redis TLS**
  - Automate certificate generation and renewal
  - Implement certificate reload without downtime
  - Monitor certificate expiration
  - Validate certificates before deployment

## Epic 25: Certificate Management with Certbot & Cloudflare DNS (Planned)
**Goal**: Automated certificate issuance and renewal via DNS-01 challenge for Redis TLS.

**Status**: Planning completed 2026-01-14. See detailed plan at `/home/chris/.claude/plans/velvet-dreaming-micali.md`

- [ ] 25.1 **Story: Certbot Container with Cloudflare DNS Plugin**
  - Add certbot Docker service with dns-cloudflare plugin
  - Configure Cloudflare API credentials
  - Issue certificates via DNS-01 challenge
  - Support wildcard certificates

- [ ] 25.2 **Story: Automatic DNS-01 Challenge Completion**
  - Automated TXT record creation/deletion
  - Handle DNS propagation delays
  - Retry with exponential backoff
  - Cloudflare API token permissions

- [ ] 25.3 **Story: Certificate Renewal Automation**
  - Daily renewal checks via cron/Celery Beat
  - Renew certificates within 30 days of expiration
  - Deploy hook to reload services
  - Handle renewal failures gracefully

- [ ] 25.4 **Story: Certificate Distribution to Redis and Services**
  - Distribute certificates to all services via volume
  - Atomic certificate replacement
  - Validate certificates before deployment
  - Send reload signals to services

## Next Steps for Future Sessions

### CURRENT FOCUS: Encryption & HTTPS Implementation (Epics 22-25)
**Detailed Plan**: See `/home/chris/.claude/plans/velvet-dreaming-micali.md` for comprehensive BDD user stories

**Implementation Order (Recommended):**
1. **Phase 1**: Epic 22 (Cloudflare Tunnel) - 1-2 days - HIGHEST PRIORITY
2. **Phase 2**: Epic 25 (Certbot & Cloudflare DNS) - 2-3 days - HIGH PRIORITY
3. **Phase 3**: Epic 24 (Redis TLS) - 2-3 days - HIGH PRIORITY
4. **Phase 4**: Epic 23 (Encryption at Rest) - 4-5 days - MEDIUM PRIORITY

**Previous Planning**: Epic 21 (GPU Detection) - See earlier plan session

### Priority 1: GPU Detection and Conditional Builds (Epic 21.1-21.3)
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Build-time GPU detection | 21.1 | High | Modify Dockerfile with ARG and conditional builds for GPU vs CPU |
| Runtime GPU detection | 21.2 | High | Implement real GPU checking in warmup.py and tasks.py |
| Docker Compose profiles | 21.3 | Medium | Create profiles for GPU/CPU/mixed deployments |

### Priority 2: Resource Optimization (Epic 21.4-21.6)
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Memory optimization | 21.4 | Medium | Lazy loading, cleanup, adjusted limits |
| Prometheus metrics | 21.5 | Medium | Add `/metrics` endpoint for monitoring |
| Intelligent cleanup | 21.6 | Low | Size-based prioritization, emergency cleanup |

### Priority 3: Security Hardening (Epic 21.7-21.9)
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Secrets management | 21.7 | Medium | Proper loading, validation, rotation support |
| Container hardening | 21.8 | Medium | Non-root users, read-only fs, capability dropping |
| Input validation | 21.9 | Medium | UUID validation, sanitization, whitelisting |

### Priority 4: Operational Excellence (Epic 21.10-21.12)
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Health endpoints | 21.10 | Medium | Detailed healthz/readyz/livez with component checks |
| Alerting | 21.11 | Medium | Prometheus rules and notification routing |
| Graceful shutdown | 21.12 | Low | Signal handlers, GPU cleanup, state preservation |

### Priority 5: Stabilization & Testing
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Update Unit Tests | 12 | Medium | Update `tests/unit/test_worker.py` to match the new `PdfConverter` API usage (mocking `PdfConverter` class instead of `subprocess`). |
| Load Testing | 16.5 | Medium | Validate behavior under concurrent load with `solo` pool (verify queueing works). |
| GPU Detection Tests | 21 | High | End-to-end verification of GPU detection and fallback scenarios |

### Priority 6: Future Enhancements
| Task | Epic | Effort | Description |
|------|------|--------|-------------|
| Kubernetes Manifests | 16.4 | High | Prepare Helm charts for production deployment. |
| User tier-based priority | New | High | Premium users, authentication, tiered queues |
| Conversion history | New | High | Persistent storage, user accounts, history UI |
