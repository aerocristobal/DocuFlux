# DocuFlux — Sprint Backlog: PR #108 Follow-ups

**Status:** Proposed · **Last updated:** 2026-07-12
**Related docs:** [BACKLOG.md](BACKLOG.md) · [PRD.md](../PRD.md) · [ARCHITECTURE.md](../ARCHITECTURE.md)

Source: code review of PR #108 (`feat/p0-implementation`, the [BACKLOG.md](BACKLOG.md) P0/P1 wave). The review's correctness bugs and CI blockers were fixed directly on that PR; the items below are the cleanup, efficiency, and operational-risk findings that were deliberately left out of that PR to keep it from growing further, plus one API-contract change it introduced that needs a consumer audit. Stories use BDD framing per the project's BDD conventions.

**Suggested sprint scope:** S1–S4 (hot-path efficiency) and S10 (routing consolidation) are the highest-leverage items — S10 in particular already caused one bug in PR #108 (a missing `convert_with_ocr` queue-routing entry) and will cause another the next time an engine is added if left unfixed.

---

## Epic 7 — Post-P0 Cleanup `@tech-debt` — **P2**

> **Vision:** Close the efficiency, duplication, and operational-risk gaps surfaced while reviewing the P0/P1 wave, before they compound into the next feature's bugs the way the queue-routing duplication already did once.

```gherkin
Feature: Post-P0 cleanup
  In order to keep the P0/P1 wave's velocity from decaying into review debt
  As the team building on top of feat/p0-implementation
  I want the duplication, hot-path inefficiencies, and operational gaps it left behind closed out
```

| # | Story | Priority | Theme | Depends on |
|---|-------|----------|-------|------------|
| 7.1 | Throttle per-page OCR progress updates | P2 | Efficiency | — |
| 7.2 | Stream file-content validation instead of full in-memory read | P2 | Efficiency | — |
| 7.3 | Throttle `last_used_at` write on every authenticated request | P2 | Efficiency | — |
| 7.4 | Parallelize per-page Tesseract OCR | P2 | Efficiency | 7.1 |
| 7.5 | Consolidate the worker healthcheck command | P2 | Reuse | — |
| 7.6 | Single source of truth for expected upload MIME types | P2 | Reuse | — |
| 7.7 | Unify Markdown table-detection logic | P2 | Reuse | — |
| 7.8 | `worker/warmup.py`: adopt shared JSON logging | P2 | Reuse | — |
| 7.9 | Remove `convert_with_marker_slm`'s redundant disk write | P3 | Simplification | — |
| 7.10 | Single canonical engine→queue routing mapping | P1 | Altitude | — |
| 7.11 | Add `QualityReport.from_metadata()` deserializer | P2 | Altitude | — |
| 7.12 | Worker-light as a single point of failure for CPU queues | P1 | Ops | — |
| 7.13 | Audit consumers of the `completed-with-warnings` status | P1 | Compat | — |

---

### Story 7.1 — Throttle per-page OCR progress updates

```gherkin
In order to keep Tesseract OCR CPU-bound instead of network-bound
As a self-hoster running the CPU-only OCR path
I want per-page progress updates batched instead of firing on every page
```

**Problem:** `convert_with_ocr` (`worker/tasks/conversion.py`) calls `update_job_metadata` — a blocking Redis `HSET` plus a `socketio.emit` — once per rasterized page inside the OCR loop. A 50–300 page scanned document serializes that many Redis round trips and WebSocket emits directly into the CPU-bound Tesseract critical path.

**Acceptance criteria**
- Progress updates emit at a fixed cadence (e.g. every 5% of pages, or every 2 seconds), not once per page.
- The final page's completion still updates progress to 100% before the SUCCESS write.
- Existing OCR progress tests updated to assert the throttled cadence, not a per-page call count.

**Files:** `worker/tasks/conversion.py` (`convert_with_ocr`), `tests/unit/test_worker.py`.

---

### Story 7.2 — Stream file-content validation instead of full in-memory read

```gherkin
In order to keep large uploads from blocking the web process
As an API integrator uploading large PDFs
I want content-type validation to avoid loading the entire file into memory
```

**Problem:** `validate_file_content_type` (`web/validation.py`, Story 4.4) reads the entire uploaded file into memory (`content = file.read()`) synchronously in the Flask request handler, for every upload on `/convert` and `/api/v1/convert`. The prior 8-byte magic-header check didn't have this cost. For large PDFs this adds meaningful per-request latency and peak memory versus before.

**Acceptance criteria**
- PDF header/`%%EOF`/polyglot checks read only the byte ranges they need (header, trailing window) rather than the whole file.
- The ZIP-based format checks (docx/odt/epub), which already need `zipfile` to open the archive, are scoped to avoid an extra full read where the archive API can operate on the stream directly.
- A large-file benchmark or regression test confirms peak memory no longer scales with upload size for the header/trailer checks.

**Files:** `web/validation.py`, `tests/unit/test_validation.py`.

---

### Story 7.3 — Throttle `last_used_at` write on every authenticated request

```gherkin
In order to keep API-key auth from doubling Redis round trips per request
As a self-hoster serving API traffic through require_api_key
I want the last-used timestamp updated on a cadence, not every single call
```

**Problem:** `require_api_key` (`web/app.py`, Story 4.3) does a synchronous Redis `HSET` (`last_used_at`) plus two `logging.info` audit calls on every authenticated request — not just key creation/validation. This doubles Redis round trips per request just to update a timestamp that doesn't need per-request freshness.

**Acceptance criteria**
- `last_used_at` is only written when stale by more than a configurable window (e.g. 5 minutes), or the write is pipelined with the existing `hgetall` used for validation.
- Audit-log semantics (every attempt logged) are unchanged — only the bookkeeping write is throttled.
- Existing audit-log tests (`test_audit_log_never_contains_raw_key`, `test_valid_auth_updates_last_used_at`) still pass under the new cadence.

**Files:** `web/app.py` (`require_api_key`), `tests/unit/test_web.py`.

---

### Story 7.4 — Parallelize per-page Tesseract OCR

```gherkin
In order to convert large scanned PDFs in reasonable wall-clock time
As a self-hoster processing multi-hundred-page scanned documents
I want per-page OCR to use more than one CPU core
```

**Problem:** `convert_with_ocr` runs Tesseract strictly sequentially over each rasterized page in a single-threaded loop, even though per-page OCR is embarrassingly parallel. A 100-page scanned document OCRs one page at a time on one core, multiplying wall-clock time roughly linearly with page count and holding the Celery worker slot (`soft_time_limit=840s`) for the whole duration.

**Acceptance criteria**
- Page-level OCR runs across a bounded thread or process pool (sized to available CPU count / a config value), not one page at a time.
- Output page ordering is preserved regardless of completion order.
- A benchmark on a representative multi-page sample shows wall-clock improvement proportional to available cores, without exceeding the existing memory ceiling.

**Files:** `worker/tasks/conversion.py` (`convert_with_ocr`), `config.py`.
**Depends on:** 7.1 (throttled progress reporting should land first so parallel workers aren't all trying to report progress independently).

---

### Story 7.5 — Consolidate the worker healthcheck command

```gherkin
In order to change the worker liveness probe in one place
As the engineer who next needs to adjust the Celery healthcheck
I want a single healthcheck script instead of five duplicated copies
```

**Problem:** The exact `celery -A tasks inspect ping ...` shell command is duplicated five times in the P0 wave: `worker/Dockerfile`, `docker-compose.yml` (both the `worker` and the new `worker-light` services), and `deploy/k8s/worker.yaml` (both the GPU and CPU worker specs) — with the k8s copies already using slightly different flags than the Dockerfile/compose copies. A future change (e.g. adding TLS args) requires editing all five in lockstep; missing one silently leaves stale liveness behavior in that deployment target.

**Acceptance criteria**
- A single `worker/healthcheck.sh` (or equivalent) is `COPY`ed into the worker image and invoked identically from the Dockerfile `HEALTHCHECK`, the compose `healthcheck:` blocks, and the k8s `exec.command`.
- No behavioral change to the probe itself — this is a pure consolidation.
- Compose and k8s worker/worker-light services still report healthy after the change (verified via `docker compose up` / a cluster dry-run).

**Files:** `worker/Dockerfile`, `docker-compose.yml`, `deploy/k8s/worker.yaml`, new `worker/healthcheck.sh`.

---

### Story 7.6 — Single source of truth for expected upload MIME types

```gherkin
In order to add a new upload format without silently under-validating it
As the engineer adding format support
I want the content-type validator to read expected MIME types from the same place the rest of the app does
```

**Problem:** `web/validation.py`'s `_EXPECTED_MIME_TYPES` (Story 4.4) re-hardcodes MIME-type-per-extension mappings that already live in `shared/formats.py`'s `FORMATS` list (each format already declares its `mime_types`). Adding a new format to `FORMATS` without a matching update to `_EXPECTED_MIME_TYPES` silently drops it from the content-sniffing check with no test failure to flag it — this exact PR added `pdf_ocr` to `FORMATS` without updating the validator's dict.

**Acceptance criteria**
- `validate_file_content_type` derives its expected-MIME-types lookup from `shared/formats.py` instead of a parallel dict.
- A test asserts every extension in `FORMATS` with a declared `mime_types` list is covered by the validator (so a future addition without a corresponding validator update fails CI, not silently ships).

**Files:** `web/validation.py`, `shared/formats.py`, `tests/unit/test_validation.py`.

---

### Story 7.7 — Unify Markdown table-detection logic

```gherkin
In order to keep the quality scorer and the table-repair pass from disagreeing
As a maintainer changing what counts as a malformed Markdown table
I want one shared table-boundary scanner instead of two independent implementations
```

**Problem:** `shared/quality.py` (`_malformed_table_count`/`_col_count`) and `shared/table_postprocess.py` (`normalize_tables`/`_split_row`) each independently re-implement "walk lines and locate a Markdown table via its separator row," using different iteration direction and different cell-splitting helpers. `table_postprocess.py`'s own docstring claims it follows "the same pattern" as `quality.py`, but nothing enforces that beyond sharing one regex constant. A future edit to what counts as a table applied to only one scanner would make `normalize_tables` "repair" a table that `_malformed_table_count` still flags as malformed, silently breaking the quality score's consistency with the repair pass.

**Acceptance criteria**
- One shared table-boundary scanner (e.g. in `shared/quality.py` or a new `shared/markdown_tables.py`) is used by both the quality scorer and the post-processor.
- Existing table-related test fixtures in `tests/unit/test_quality.py` and `tests/unit/test_table_postprocess.py` still pass unchanged.
- A new test asserts that any table the post-processor successfully repairs is no longer counted as malformed by the scorer, and vice versa (the two can no longer disagree).

**Files:** `shared/quality.py`, `shared/table_postprocess.py`, `tests/unit/test_quality.py`, `tests/unit/test_table_postprocess.py`.

---

### Story 7.8 — `worker/warmup.py`: adopt shared JSON logging

```gherkin
In order to correlate warmup-sidecar logs with the rest of the worker tier
As an operator querying aggregated logs by request_id/job_id/task_id
I want warmup.py's logs in the same structured JSON format as the rest of the fleet
```

**Problem:** `shared/logging_config.py` (Story 3.5) exists specifically so web and worker tiers share one structured JSON log format with common correlation fields. `worker/tasks/__init__.py` was switched to call `configure_json_logging()`, but `worker/warmup.py` — part of the same worker container, touched in this same PR — still calls `logging.basicConfig(...)` with a plain-text format. Its log lines stay unparseable by JSON-based log aggregation and lack `request_id`/`job_id`/`task_id` fields, defeating Story 3.5's stated goal for this process.

**Acceptance criteria**
- `worker/warmup.py` calls `configure_json_logging()` instead of `logging.basicConfig()`.
- Warmup log output is valid JSON matching the shared schema, verified by a smoke test or log-format assertion.

**Files:** `worker/warmup.py`, `shared/logging_config.py`.

---

### Story 7.9 — Remove `convert_with_marker_slm`'s redundant disk write

```gherkin
In order to avoid doing table-repair and disk I/O work that's immediately discarded
As the worker process running the Marker+SLM engine
I want the pre-SLM text written to disk exactly once, not written then overwritten
```

**Problem:** `convert_with_marker_slm` calls `_save_marker_output(...)`, which postprocesses tables and writes `output_path` to disk — then a few lines later the SLM-refined text is postprocessed again and unconditionally overwrites the same file. The first write and its accompanying table-repair pass are pure waste on every `marker_slm` job: the file is discarded before any consumer could see it.

**Acceptance criteria**
- `_save_marker_output` gains a way to skip the disk write (e.g. an `include_write=False` parameter) for callers that know they'll immediately overwrite it, or the marker_slm path is restructured to keep text in memory until the final write.
- Behavior for `convert_with_marker` and `convert_with_hybrid` (which do want `_save_marker_output`'s write) is unchanged.
- A test confirms the pre-SLM table-repair pass no longer runs for `convert_with_marker_slm`.

**Files:** `worker/tasks/conversion.py` (`_save_marker_output`, `convert_with_marker_slm`), `tests/unit/test_worker.py`.

---

### Story 7.10 — Single canonical engine→queue routing mapping

```gherkin
In order to add a new conversion engine without re-deriving its queue routing in four places
As the engineer who adds the next conversion engine after pdf_ocr
I want one {task_name: queue} mapping used everywhere routing decisions are made
```

**Problem:** GPU-vs-CPU queue routing is re-implemented as ad hoc `task_name`/`internal_from_format` membership conditionals at two call sites in `web/routes/conversion.py` (`/convert` and `/api/v1/convert`), duplicated again in two static `celery.conf.task_routes` fallback dicts (`web/app.py`, `worker/tasks/__init__.py`) — four places that must be kept in sync by hand. This exact PR needed a follow-up fix (this review) because `tasks.convert_with_ocr` was added to both inline routing call sites but initially omitted from both fallback dicts; any future dispatch path relying on default Celery routing instead of an explicit `queue=` kwarg would have silently sent OCR jobs to a queue nothing consumes.

**Acceptance criteria**
- A single canonical mapping (e.g. `ENGINE_QUEUES = {"pdf_marker": "gpu", "pdf_hybrid": "gpu", "pdf_marker_slm": "gpu", "pdf_ocr": SIZE_BASED, ...}`) drives both the explicit `send_task(..., queue=...)` calls and the `task_routes` fallback config, so a new engine's routing is declared once.
- Both `/convert` and `/api/v1/convert` use the shared mapping instead of independent conditionals.
- A test asserts every task name dispatched anywhere in `web/routes/conversion.py` has a corresponding `task_routes` entry (so this class of gap fails CI instead of shipping silently, the way it did this time).

**Files:** `web/routes/conversion.py`, `web/app.py`, `worker/tasks/__init__.py`, `tests/unit/test_web.py`.

---

### Story 7.11 — Add `QualityReport.from_metadata()` deserializer

```gherkin
In order to keep the API's quality response in sync with how quality is stored
As the engineer who next changes the quality metadata schema
I want one shared deserializer instead of a hand-rolled field-by-field reconstruction
```

**Problem:** `QualityReport` (`shared/quality.py`) only has a one-way `to_metadata()` serializer. `api_v1_status` (`web/routes/conversion.py`) hand-reconstructs the same shape field-by-field from raw Redis strings (`quality_grade`/`quality_score`/`quality_reasons`/`quality_metrics`) instead of a shared deserializer paired with `to_metadata()`. It's the only place in the codebase reconstructing this shape, so a future metadata schema change (e.g. a new reason format) could silently drop or misread a field there with nothing else exercising the code path to catch it.

**Acceptance criteria**
- `QualityReport.from_metadata(dict) -> QualityReport` is added as the inverse of `to_metadata()`.
- `api_v1_status` uses `from_metadata()` instead of hand-parsing Redis fields.
- A round-trip test (`to_metadata()` → `from_metadata()` → equal `QualityReport`) exists in `tests/unit/test_quality.py`.

**Files:** `shared/quality.py`, `web/routes/conversion.py`, `tests/unit/test_quality.py`.

---

### Story 7.12 — Worker-light as a single point of failure for CPU queues

```gherkin
In order to avoid CPU-routed jobs stalling indefinitely
As a self-hoster running the split worker/worker-light topology
I want a fallback consumer for high_priority/default when worker-light is unavailable
```

**Problem:** Story 6.3 narrowed the GPU worker's queue consumption from `gpu,high_priority,default` to `gpu` only, moving `high_priority`/`default` to a new `worker-light` service. Before this split, a single worker process consumed all three queues, so `worker-light` crashing or OOMing (plausible under heavy Tesseract OCR load) while the GPU worker stays healthy and idle now means Pandoc/OCR jobs queued to `high_priority`/`default` sit unprocessed indefinitely — the GPU worker no longer drains them as it would have before.

**Acceptance criteria (needs a product/ops decision — this story starts as a discussion, not a code change)**
- Decide between: (a) `worker-light` runs with multiple replicas / horizontal autoscaling so a single-instance crash isn't fatal, or (b) the GPU worker also subscribes to `high_priority` as an emergency fallback queue (at lower Celery priority than `gpu`), restoring the pre-6.3 fallback behavior without giving up the lean CPU-only image for the common case.
- Whichever direction is chosen, `docker-compose.yml` and `deploy/k8s/worker.yaml` are updated accordingly, and an incident runbook note is added to `docs/ALERTING.md` or `docs/DEPLOYMENT.md` describing the failure mode and mitigation.

**Files:** `docker-compose.yml`, `deploy/k8s/worker.yaml`, `docs/ALERTING.md` or `docs/DEPLOYMENT.md`.

---

### Story 7.13 — Audit consumers of the `completed-with-warnings` status

```gherkin
In order to avoid silently breaking existing integrations
As a maintainer of first-party consumers of the DocuFlux API
I want every consumer checking job status audited against the new status value
```

**Problem:** `api_v1_status` (Story 1.3) now returns `completed-with-warnings` instead of `success` for successfully-converted-but-poor-quality jobs — a new member of what was previously a fixed status enum (`pending`/`processing`/`success`/`failure`/`revoked`/`capturing`). `openapi.yaml`/`API.md` were updated to document the new value, but nothing in the P0 PR audited downstream consumers. Any integration doing a strict `status == 'success'` check (the job succeeded and `download_url` is present, but the consumer's check never matches) will look stuck rather than complete.

**Acceptance criteria**
- `mcp_server/` and any other first-party consumer of `/api/v1/status` is audited and updated to treat `completed-with-warnings` as a completion state (alongside `success`), not just checked for exact-match `success`.
- A changelog or migration note documents the new status value for third-party integrators, with a suggested check (`status in ('success', 'completed-with-warnings')` or "status not in the pending/processing set") rather than strict equality.

**Files:** `mcp_server/`, `docs/API.md`, `docs/openapi.yaml`, a new changelog entry.

---

## Execution Notes

- **7.10** is the one item in this backlog with a concrete "this already caused a bug" history — prioritize it over the other P2 cleanup items even though it's more invasive, since the failure mode (a new engine silently landing in an unconsumed queue) has no visible error, only a job stuck in `PENDING` forever.
- **7.12** and **7.13** are not pure code changes — they need a product/ops decision and a consumer audit respectively before implementation. Schedule them as spikes/discussions first.
- **7.1 → 7.4** are ordered (7.1 first) because parallelizing OCR while progress reporting is still per-page would multiply the Redis/WebSocket load this backlog is trying to reduce.
