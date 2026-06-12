# DocuFlux — Product Requirements Document

**Status:** Living document · **Last updated:** 2026-06-11
**Related docs:** [ARCHITECTURE.md](ARCHITECTURE.md) · [BACKLOG.md](BACKLOG.md) · [API.md](API.md) · [FORMATS.md](FORMATS.md) · [CONFIGURATION.md](CONFIGURATION.md) · [AI_INTEGRATION.md](AI_INTEGRATION.md)

---

## 1. Product Overview

### 1.1 Pitch

DocuFlux is a **self-hostable, privacy-first document conversion service**. It combines three conversion engines — Pandoc (universal, 20+ formats), Marker AI (deep-learning PDF→Markdown with layout awareness), and a local small language model (SLM) for metadata extraction — behind a REST API, a Material Design web UI, browser extensions for authenticated web capture, and a Model Context Protocol (MCP) server for AI-agent integration.

### 1.2 Problem Statement

- **Cloud converters leak documents.** Sending contracts, medical records, or internal documentation to a SaaS converter is unacceptable for many individuals and organizations.
- **Local tools lack quality AI conversion.** Pandoc alone produces poor results on scanned or layout-heavy PDFs; high-fidelity PDF→Markdown historically required cloud APIs.
- **Automation surfaces are missing.** Most local converters are CLIs or desktop apps with no job queue, no webhooks, no API keys, and no agent integration.

DocuFlux solves all three: documents never leave the deployment, AI conversion runs on local hardware (GPU-accelerated when available), and every capability is exposed through a versioned API.

### 1.3 Product Principles

1. **Data never leaves the deployment.** No external API calls for conversion or metadata extraction. Models run locally (Marker, TinyLlama via llama-cpp-python).
2. **Encrypted by default.** Files at rest use AES-256-GCM (`shared/encryption.py`); job metadata in Redis is encrypted (`shared/redis_encryption.py`).
3. **Degrades gracefully across hardware.** Full capability on a single NVIDIA GPU; CPU-only deployments retain Pandoc-based conversion (see §9 for the scanned-PDF gap).
4. **Transient by design.** Outputs auto-expire (10 min after download / 1 hour undownloaded / 5 min for failures). DocuFlux is a converter, not a document store.
5. **API-first.** Every UI action maps to a documented REST endpoint ([openapi.yaml](openapi.yaml)).

---

## 2. Goals and Non-Goals

### 2.1 Goals

| Goal | Mechanism |
|------|-----------|
| High-fidelity document conversion, especially PDF→Markdown | Marker AI + hybrid Pandoc/Marker routing |
| API-first automation | REST API v1, `dk_` API keys, webhooks, WebSocket progress |
| Agent integration | MCP server (Playwright-backed vision capture), machine-readable API docs |
| Authenticated web content capture | Chrome/Firefox extensions with batch session assembly |
| Compliance-ready security posture | NIST SP 800-53 mappings in `oscal/` (component definition + SSP) |
| Runs on commodity hardware | CPU and GPU Docker Compose profiles; Kubernetes manifests with HPA |

### 2.2 Non-Goals

- **Multi-tenant SaaS.** DocuFlux is single-deployment, single-trust-domain software. API keys gate access; there is no tenant isolation model.
- **Document storage/management.** Retention is deliberately transient. No folders, search, or versioning.
- **Real-time collaborative editing.** Output is downloaded, not edited in place.
- **Format authoring.** DocuFlux converts; it does not provide editors for the formats it supports.

---

## 3. Personas

### 3.1 Self-Hoster "Sam"

Runs DocuFlux on a homelab or small-org server via `docker-compose.gpu.yml` or `docker-compose.cpu.yml`.

- **Cares about:** setup friction, hardware requirements, upgrade safety, TLS, resource limits.
- **Pains (current):** Marker requires a GPU — scanned PDFs are effectively unsupported on CPU-only hardware; Redis TLS is disabled pending certificate generation; first Marker conversion is slow (~30 s lazy model load).

### 3.2 API Integrator "Ines"

Calls `POST /api/v1/convert` from backend pipelines using `dk_`-prefixed API keys.

- **Cares about:** predictable JSON contracts, webhook reliability, rate limits, error semantics, job latency.
- **Pains (current):** no quality signal in responses — a degraded conversion looks identical to a good one; partial Pandoc output can complete "successfully"; API keys have no expiration or audit trail.

### 3.3 Extension User "Eva"

Uses the Chrome/Firefox extension (`extension-src/`) to capture authenticated web content (e.g., Percipio/Skillsoft readers) page-by-page into a single Markdown document.

- **Cares about:** one-click capture, batch reliability across hundreds of pages, fidelity of the assembled document.
- **Pains (current):** capture quality depends on Marker OCR of screenshots; no per-page quality feedback; sessions cap at 500 pages.

### 3.4 Agent/MCP Consumer "Astra"

An LLM agent using the MCP server (`mcp_server/server.js`, Playwright-backed) and the REST API for autonomous document acquisition and conversion, often feeding downstream RAG pipelines.

- **Cares about:** structured outputs, reliable tool contracts, metadata quality (title/summary/tags) for indexing.
- **Pains (current):** SLM metadata is truncated at 2,000 tokens — long documents get poor titles/summaries; no machine-readable engine-capability discovery (the agent cannot ask "is OCR available here?").

---

## 4. User Journeys

### 4.1 Sam: Install → First Conversion

1. Clone repo, run `./scripts/build.sh auto` (GPU auto-detection).
2. `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`.
3. Open `http://localhost:5000`, upload a PDF, select "PDF (High Accuracy)".
4. Watch real-time progress via WebSocket; download Markdown + extracted images as a zip.
5. (Optional) Configure Cloudflare Tunnel or Redis TLS per [CLOUDFLARE_TUNNEL_SETUP.md](CLOUDFLARE_TUNNEL_SETUP.md) / [CERTIFICATE_MANAGEMENT.md](CERTIFICATE_MANAGEMENT.md).

### 4.2 Ines: Key → Convert → Webhook → Download

1. Admin issues a key: `POST /api/v1/auth/keys` (Bearer `ADMIN_API_SECRET`).
2. Pipeline uploads: `POST /api/v1/convert` (multipart, `engine=pdf_hybrid`), receives `job_id`.
3. Registers callback: `POST /api/v1/webhooks` with `job_id` + HTTPS URL (SSRF-validated).
4. On completion, webhook fires with status + download URL; pipeline fetches `GET /api/v1/download/{job_id}`.
5. Output auto-expires per retention policy.

### 4.3 Eva: Extension Capture

1. Installs extension; configures DocuFlux server URL and API key in the popup.
2. Opens a paywalled/authenticated reader; clicks "Capture Pages".
3. Extension creates a capture session (`POST /api/v1/capture/sessions`), then streams pages (HTML + screenshot) in batches of 50.
4. On "Finish", the worker assembles batches: screenshots → PDF → Marker (force-OCR) → merged Markdown with YAML front matter.
5. Eva downloads the assembled document.

### 4.4 Astra: MCP Tool Call → Conversion → Metadata

1. Agent invokes an MCP tool; the MCP server drives Playwright to render and capture target content.
2. Captured content enters the same capture/conversion pipeline.
3. Worker runs Marker, then queues `tasks.extract_slm_metadata` — TinyLlama extracts title, summary, tags.
4. Agent polls status, retrieves Markdown + metadata JSON for downstream indexing.

---

## 5. Functional Requirements

### 5.1 Format Conversion (source of truth: `shared/formats.py`, [FORMATS.md](FORMATS.md))

- **FR-1.1** Convert between 20+ formats via Pandoc: Markdown/GFM, HTML, DOCX, ODT, RTF, EPUB 2/3, LaTeX, reStructuredText, AsciiDoc, BibTeX, MediaWiki, Jira, and others.
- **FR-1.2** Validate uploads by magic bytes (PDF `%PDF`, ZIP-based `PK`) and text encoding, not extension alone (`web/validation.py`).
- **FR-1.3** Enforce limits: 200 MB max upload (`MAX_CONTENT_LENGTH`), 500 MB minimum free disk (`MIN_FREE_SPACE`).

### 5.2 AI PDF→Markdown

- **FR-2.1** `pdf_marker`: Marker AI conversion with layout awareness, table detection, and image extraction. Page limit 600 (`MAX_MARKER_PAGES`); optional `force_ocr` and `use_llm` flags.
- **FR-2.2** `pdf_hybrid`: try Pandoc first; if output quality is below threshold (currently 50 words/page — see Backlog Epic 1 for the planned quality-score replacement), fall back to Marker.
- **FR-2.3** `pdf_marker_slm`: Marker followed by SLM refinement of OCR artifacts in 600-word chunks.
- **FR-2.4** Extracted images are written to an `images/` subdirectory with Markdown references rewritten accordingly; multi-file outputs are zipped on download.

### 5.3 Metadata Extraction (SLM)

- **FR-3.1** After Marker/hybrid success, automatically extract title, summary, and tags via llama-cpp-python (TinyLlama-1.1B by default; configurable via `SLM_MODEL_PATH`).
- **FR-3.2** Metadata is stored in job metadata and included in webhook payloads.
- **Known limitation:** context truncated at 2,000 tokens (`MAX_SLM_CONTEXT`) — see Backlog Story 1.6.

### 5.4 Browser Capture Pipeline

- **FR-4.1** Session lifecycle: create → add pages (≤500/session, batches of 50) → finish → assembled document. Session TTL 24 h.
- **FR-4.2** Each page carries URL, title, text, HTML, and a base64 screenshot; assembly converts screenshot batches to PDF and runs Marker with forced OCR.
- **FR-4.3** CORS restricted to `chrome-extension://` and `moz-extension://` origins on capture endpoints only.

### 5.5 MCP Server

- **FR-5.1** Internal Node.js service (port 8080) exposing Playwright-driven browser actions, authenticated by `MCP_SECRET` bearer token; reachable only from the worker on the internal network.

### 5.6 Job Lifecycle

- **FR-6.1** Every conversion is an async job (UUID v4) with statuses queued → in_progress → completed/failed, progress percentage, and stage description.
- **FR-6.2** Real-time updates via Flask-SocketIO; polling via `GET /api/v1/status/{job_id}`.
- **FR-6.3** Webhooks: registered per-job, fired on completion/failure, 3 retries, SSRF-guarded URL validation, optional HTTPS enforcement (`WEBHOOK_REQUIRE_HTTPS`).
- **FR-6.4** Retention: outputs deleted 10 min after download, 1 h if never downloaded, 5 min after failure (Celery Beat, `worker/tasks/maintenance.py`).
- **FR-6.5** Permanently failed tasks land in a Redis dead-letter queue (`dlq:tasks`).

### 5.7 Administration

- **FR-7.1** API key management: create/revoke `dk_` keys via `/api/v1/auth/keys` (admin bearer auth); keys stored hashed (`shared/key_manager.py`).
- **FR-7.2** Configuration via Pydantic Settings (`config.py`): env vars → `.env` → Docker secrets, with `SecretStr` for sensitive values.
- **FR-7.3** Health endpoints: `/healthz` (liveness), `/readyz` (readiness), `/api/health` (detailed: Redis, disk, GPU, workers, model cache).

---

## 6. Non-Functional Requirements

### 6.1 Performance

| Metric | Target | Current state |
|--------|--------|---------------|
| Pandoc conversion (typical doc) | < 30 s | Met; 500 s subprocess timeout |
| Marker PDF→Markdown | < 20 min hard limit | 1200 s task limit; throughput unmeasured (Backlog 6.1) |
| Time-to-first-conversion (cold worker) | < 60 s | ~30 s Marker lazy model load on first job (Backlog 6.2) |
| Status endpoint latency | p95 < 200 ms | Per `tests/load/locustfile.py` SLA |
| Conversion request handling | p95 < 2 s (enqueue) | Per locustfile SLA |
| Error rate under load | < 1% | Per locustfile SLA |
| Concurrency | 1 conversion at a time per worker | `pool=solo` — head-of-line blocking (Backlog 6.3) |

### 6.2 Security & Privacy

- AES-256-GCM encryption at rest for uploads, outputs, and Redis job metadata.
- No outbound calls during conversion (model weights fetched at build/startup only).
- Non-root containers, dropped capabilities, `no-new-privileges`, noexec tmpfs.
- CSRF protection, rate limiting (1000/day, 200/hour defaults), SSRF guards on webhooks.
- **Known gaps** (tracked in [BACKLOG.md](BACKLOG.md) Epic 4): Redis TLS disabled, no API key expiration/audit, 8-byte magic validation, MCP container runs as root.

### 6.3 Deployability

- Docker Compose profiles: base, `cpu` (≤3 GB image, no Marker), `gpu` (CUDA 11.8, ~15 GB), `tls`.
- Kubernetes: namespace, Redis StatefulSet, web/worker Deployments with HPA (1–10 replicas), NetworkPolicies (`deploy/k8s/`).
- Optional Cloudflare Tunnel ingress for zero-config HTTPS.

### 6.4 Compliance

- NIST SP 800-53 control mappings (AC-2, AC-3, AU-2, SC-8, SC-28) maintained as OSCAL artifacts (`oscal/component-definition.json`, `oscal/ssp.json`), schema-validated in CI (`.github/workflows/oscal-validate.yml`).

### 6.5 Observability

- Prometheus metrics: conversion counts/durations/failures by format, GPU utilization, queue depth (`worker/metrics.py`); Grafana dashboard ([grafana-dashboard.json](grafana-dashboard.json)); alert rules ([ALERTING.md](ALERTING.md)).
- Structured JSON logging with request-ID correlation in the web tier. **Gap:** worker logs are unstructured (Backlog 3.5).

---

## 7. Quality Bar: Definition of "Good Conversion"

A conversion is *good* when the output preserves: readable body text (no garbage-character runs), document structure (headings at correct levels), tables (well-formed Markdown pipe tables), images (extracted and referenced), and ordering (no shuffled or dropped pages).

**Current state: this bar is not measurable.** The only quality signal in the system is the hybrid engine's 50-words/page heuristic, applied solely to decide Pandoc→Marker fallback. No job carries a quality grade; a degraded output and an excellent output return identical API responses. Making this section enforceable is the purpose of **Backlog Epic 1** (quality scoring in `shared/quality.py`, surfaced through the API).

---

## 8. Success Metrics

| Metric | Definition | Baseline | Target |
|--------|-----------|----------|--------|
| Conversion success rate | completed / (completed + failed) | Trackable via `docuflux_conversion_total` | > 98% |
| Silent-degradation rate | jobs marked completed whose output scores "poor" | **Unmeasurable today** (needs Epic 1) | < 2% |
| Scanned-PDF support on CPU | scanned PDFs producing readable output on CPU profile | ~0% (no OCR fallback) | > 90% (Epic 2) |
| p95 job latency by engine | enqueue → completed | Unmeasured (needs Backlog 6.1) | Establish, then improve |
| Capture session success rate | sessions assembled without page loss | Untracked | > 99% |
| Security gate coverage | CI gates: lint, SAST, container scan, SBOM | 0 of 4 | 4 of 4 (Epic 5) |

---

## 9. Current State vs Roadmap

### 9.1 Shipped Capabilities

| Capability | Status |
|------------|--------|
| Pandoc conversion, 20+ formats | ✅ Shipped |
| Marker AI PDF→Markdown (GPU) | ✅ Shipped |
| Hybrid Pandoc→Marker routing | ✅ Shipped (crude heuristic) |
| SLM metadata extraction | ✅ Shipped (2,000-token window) |
| Browser extension capture | ✅ Shipped |
| MCP server (Playwright) | ✅ Shipped |
| API keys, webhooks, WebSocket progress | ✅ Shipped |
| Encryption at rest | ✅ Shipped |
| Prometheus/Grafana observability | ✅ Shipped (web tier structured; worker logs not) |
| K8s + Compose deployment | ✅ Shipped |

### 9.2 Known Limitations

1. **No OCR on CPU-only deployments** — scanned PDFs are unsupported without a GPU.
2. **No conversion quality signal** — silent degradation is invisible to clients.
3. **Single-task worker** (`pool=solo`) — a long Marker job blocks all other work.
4. **SLM metadata truncation** at 2,000 tokens.
5. **Redis TLS disabled**; several security-critical modules excluded from test coverage.
6. **Memory-bound I/O** — downloads and zip generation buffer whole files in RAM.

### 9.3 Roadmap

The prioritized improvement backlog — 6 epics, 31 stories across conversion quality, OCR capability, reliability, hardening, supply-chain, and performance — lives in **[BACKLOG.md](BACKLOG.md)**.

---

## 10. Open Questions & Risks

| # | Question / Risk | Impact |
|---|-----------------|--------|
| 1 | CUDA 11.8 approaching EOL — when to migrate the GPU image to CUDA 12.x? | Marker/PyTorch compatibility work |
| 2 | Should TinyLlama be replaced by a stronger small model (e.g., Qwen/Phi class) for metadata? | Metadata quality vs CPU/VRAM budget |
| 3 | Is the 600-page Marker limit right? Large books/manuals exceed it. | Capability ceiling for key use cases |
| 4 | Marker license/upstream cadence — pinned at 1.10.x; upgrade policy undefined. | Supply-chain and quality drift |
| 5 | Eventlet is in maintenance mode upstream; Flask-SocketIO async-mode migration may be needed. | Web tier rearchitecture risk |
| 6 | No SLO ownership: metrics exist but no alert thresholds are enforced as commitments. | Operational ambiguity |
