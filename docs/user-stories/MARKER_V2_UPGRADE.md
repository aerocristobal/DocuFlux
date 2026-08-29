# DocuFlux — Marker AI v1 → v2 Upgrade Epic

**Status:** In progress (waves 1–2 merged; two follow-ups open) · **Last updated:** 2026-08-29
**Related docs:** [BACKLOG.md](BACKLOG.md) · [SPRINT-BACKLOG.md](SPRINT-BACKLOG.md) · [PRD.md](../PRD.md) · [ARCHITECTURE.md](../ARCHITECTURE.md) · [AI_INTEGRATION.md](../AI_INTEGRATION.md) · [benchmark report](../benchmarks/marker_v2/REPORT.md)

Stories use BDD framing per the project's BDD conventions. Each story carries the acceptance criteria it was (or must be) validated against, the files it touches, and the tests that pin it. Stories M2.1–M2.4 and M2.7 are merged; M2.5–M2.6 are open follow-ups for a future dispatch.

---

## Epic M2 — Marker AI v1 → v2 upgrade `@conversion-quality`

> **Vision:** Move Marker from marker-pdf 1.10.x (in-process PyTorch models, ~72 s model load, per-worker VRAM) to marker-pdf 2.0.0 (thin clients attached to a shared Surya VLM inference server, ~1 s attach, GPU owned once) without regressing any conversion feature or its test coverage, and with the one observed v2 degeneration mode bounded before unguarded rollout.

```gherkin
Feature: Marker AI v2 upgrade
  In order to get faster, cheaper Marker conversions that scale across workers
  As the DocuFlux team operating GPU workers
  I want marker-pdf upgraded to 2.0.0 with a shared inference server
  And every existing Marker feature and its regression net preserved

  Scenario: The one where a cold worker converts its first PDF
    Given a freshly started GPU worker attached to the Surya VLM server
    When the first pdf_marker conversion is submitted
    Then it does not pay a per-worker model-loading penalty

  Scenario: The one where the VLM degenerates into a repetition loop
    Given a marker 2 conversion producing far more output than the source pages could hold
    When the quality scorer runs on that output
    Then the job is flagged poor with the excess_output and/or repetitive_output reason
    And the job never ships as a silent plain success
```

| # | Story | Status | Theme | Depends on |
|---|-------|--------|-------|------------|
| M2.1 | Pin marker-pdf 2.0.0 and add the Surya VLM inference service | ✅ Done (PR #140, #141) | Dependency + topology | — |
| M2.2 | Migrate Marker call sites to the v2 client/server model | ✅ Done (PR #142) | Migration | M2.1 |
| M2.3 | Benchmark v1 vs v2 and decide rollout | ✅ Done (PR #143, #144) | Evidence | M2.2 |
| M2.4 | Bound v2 degeneration in the quality scorer + re-tune GPU concurrency | ✅ Done (PR #145) | Hardening | M2.3 |
| M2.5 | Decide inference-server recycling for long-lived workers | ⏳ Open | Ops | M2.4 |
| M2.6 | Make the k8s Surya VLM model cache writable by the container UID | ⏳ Open | Ops | M2.1 |
| M2.7 | Refresh v1-era docs for the v2 topology | ✅ Done (this PR) | Docs | M2.1–M2.4 |

---

### Story M2.1 — Pin marker-pdf 2.0.0 and add the Surya VLM inference service

```gherkin
In order to run the marker 2 client/server architecture
As the worker image maintainer
I want marker-pdf pinned to 2.0.0 and a dedicated vLLM service serving the Surya VLM
```

**Problem:** marker-pdf 1.10.2 loaded every model in-process on every worker, paying a ~72 s model load and holding the GPU per worker. Marker 2 moves the VLM into a shared inference server that workers attach to over HTTP, so the deployment needs a server for workers to attach to.

**Acceptance criteria**
- `worker/requirements-true.txt` pins `marker-pdf[full]==2.0.0`; torch/transformers are left unpinned for marker to manage (GPU image only; CPU image unchanged).
- A `surya-vlm` service (vLLM, `datalab-to/surya-ocr-2`) exists in `docker-compose.gpu.yml` and `deploy/k8s/surya-vlm.yaml`, reserving the GPU.
- The k8s NetworkPolicy admits worker → `surya-vlm:8000` ingress and `surya-vlm` → Redis/DNS egress.
- Deployment topologies set `SURYA_INFERENCE_URL` and `SURYA_INFERENCE_AUTOSTART=false` so workers attach and never spawn their own server.

**Validated by**
- `tests/unit/test_packaging.py` (requirements contract; CI `contracts` job).
- The benchmark run (REPORT.md §Reproducing) attached a v2 worker to the compose `surya-vlm` service successfully.
- Static audit: surya `settings.py` defines `SURYA_MODEL_CHECKPOINT = "datalab-to/surya-ocr-2"` and the `SURYA_INFERENCE_*` settings used here; `attach_or_spawn()` attaches to a reachable external URL and refuses to spawn when `SURYA_INFERENCE_AUTOSTART=false`.

**Files:** `worker/requirements-true.txt`, `docker-compose.gpu.yml`, `deploy/k8s/surya-vlm.yaml`, `deploy/k8s/worker.yaml`, `deploy/k8s/network-policies.yaml`.

---

### Story M2.2 — Migrate Marker call sites to the v2 client/server model

```gherkin
In order to keep every Marker feature working on marker 2
As a worker task author
I want all Marker call sites using the v2 API and the new lifecycle
```

**Problem:** marker 2 changes where model weights live (server, not worker) and what per-task cleanup may touch. Every call site — `convert_with_marker`, `convert_with_marker_slm`, `convert_with_hybrid`, capture batch OCR — had to keep working while dropping v1-only behavior (`INFERENCE_RAM`, per-task `torch.cuda.empty_cache()`).

**Acceptance criteria**
- `PdfConverter(artifact_dict=..., config=...)` + `converter(path)` + `text_from_rendered(rendered)` → `(text, _, images)` is used at every Marker call site (conversion + capture).
- `get_model_dict()` no longer sets `INFERENCE_RAM` (devices are set server-side) and lazily builds a thin artifact dict.
- `_cleanup_marker_memory()` releases only local rendered/image objects; it never calls `torch.cuda.empty_cache()` (would free nothing on the server) and never stops the shared server between jobs.
- A `shutdown_marker_models()` helper is bound to Celery `worker_shutdown` (not per-task), calling `marker.models.shutdown_models(model_dict)` best-effort.
- Every Marker engine still scores quality, saves images/metadata, and honors `include_images` exactly as on v1.

**Validated by**
- `tests/unit/test_worker.py`: `TestConvertWithMarker` (PdfConverter + text_from_rendered used; images saved; quality persisted; failure paths), `TestShutdownMarkerModels` (no-op when nothing loaded; stops server and clears cache; cache cleared even when shutdown raises), `TestMarkerTaskCleanup` (per-task cleanup must NOT call `shutdown_models` on success or failure).
- `tests/unit/test_capture_api.py` / capture characterization tests (capture batch OCR path unchanged).
- Static audit against marker-pdf 2.0.0 docs: `PdfConverter.__init__(artifact_dict, config=None, ...)`, `text_from_rendered` triple-return, `marker.models.shutdown_models(model_dict)` all match the v2 API; `get_model_dict()` no longer sets `INFERENCE_RAM` in the conversion path (`worker/warmup.py` still sets it in its own sidecar process, which has no effect on the Celery worker).
- All touched modules pass `python -m py_compile`.

**Files:** `worker/tasks/conversion.py`, `worker/tasks/__init__.py`, `worker/tasks/capture.py`, `tests/unit/test_worker.py`.

---

### Story M2.3 — Benchmark v1 vs v2 and decide rollout

```gherkin
In order to upgrade with evidence rather than faith
As the team shipping the Marker upgrade
I want a reproducible v1-vs-v2 benchmark over a representative corpus
```

**Problem:** An upgrade with no comparison can silently trade one failure mode for another. The benchmark had to measure throughput, model-load time, extraction volume, CJK recovery, and table health on both versions over the same documents.

**Acceptance criteria**
- `benchmarks/marker_v2/` contains a reproducible runner (`run.sh`), corpus fetcher/deriver, and comparison script over 10 documents / 213 pages.
- Results committed (`REPORT.md`, `report-raw.txt`, `v1.json`, `v2.json`) with a go/no-go recommendation.
- The report quantifies: model load (v1 72.2 s vs v2 0.8 s), warmup-corrected throughput (+73%), CJK recovery (6/6 both), and any regressions found.

**Validated by**
- The committed report: no category lost extraction quality; CJK 6/6; `tables_unrepairable` 0; one silent v2 degeneration event (a repetition loop, 14× output) which did not reproduce in isolation and was not caught by the then-current scorer — handed to M2.4.

**Files:** `benchmarks/marker_v2/` (run.sh, corpus.py, fetch_corpus.py, build_derived.py, run_bench.py, compare.py, REPORT.md, report-raw.txt, v1.json, v2.json).

---

### Story M2.4 — Bound v2 degeneration in the quality scorer + re-tune GPU concurrency

```gherkin
In order to never ship a marker 2 degeneration as a silent success
As the team rolling v2 out to production traffic
I want the quality scorer to flag excess/repetitive output
And the GPU worker concurrency re-tuned for the shared-server model
```

**Problem:** The benchmark found marker 2 can degenerate once into a repetition loop producing ~74k chars/page with the scorer rating it 100. Before unguarded rollout the scorer had to bound output, and the worker had to exploit the fact that the VLM server (not the worker) owns the GPU.

**Acceptance criteria**
- `score_markdown()` flags `excess_output` when words-per-page > 2000 or chars-per-page > 10000, and `repetitive_output` when the most common bigram covers > 30% of bigram positions; both force a `poor` grade (never `good`), and `excess_output` is only ever emitted once.
- Optional `per_page_word_counts` enables empty-page detection on real page boundaries (used by capture), with a mismatched-length fallback to the old chunking.
- The GPU worker runs at `--concurrency=2` consuming `gpu,default,high_priority` (k8s + compose), so parallel Marker jobs share the one inference server.
- `shared/quality.py` keeps its 100% per-module coverage floor.

**Validated by**
- `tests/unit/test_quality.py`: excess-output word and char ceilings, single-reason-code behavior, `empty_output` suppression, repetition above/below threshold, short-document guard, `per_page_word_counts` incl. mismatched-length fallback.
- `scripts/check_coverage_floors.py` (CI `test` job) enforces the `shared/quality.py = 100` floor.
- Manual probe against the merged scorer: degenerate repetition → `poor`, reasons `['no_headings', 'repetitive_output']`; 25k words on 1 page → `poor`, `excess_output` present; clean doc → `good`, score 100; short doc → no `repetitive_output` false positive.

**Files:** `shared/quality.py`, `tests/unit/test_quality.py`, `deploy/k8s/worker.yaml`, `docker-compose.gpu.yml`.

---

### Story M2.5 — Decide inference-server recycling for long-lived workers (open)

```gherkin
In order to eliminate the silent degeneration mode observed in the benchmark
As an operator running long-lived GPU workers
I want a decision on whether the shared VLM server should recycle periodically
```

**Problem:** The benchmark's single degeneration event happened on the tenth document of a sequential batch, never in isolation, pointing at state accumulating in the long-lived shared vLLM server. The scorer now catches the symptom (M2.4) but does not cure the cause; a document that degenerates still consumes its full task budget before being flagged.

**Acceptance criteria (needs an ops decision — starts as a discussion)**
- Decide between: (a) periodically recycle the `surya-vlm` server (e.g. `SURYA_INFERENCE_KEEP_ALIVE` semantics or a cron restart) so accumulated state cannot produce a loop, or (b) accept the scorer guard as sufficient and document the residual risk.
- If recycling is chosen, add it to compose/k8s manifests and note the failure mode + mitigation in `docs/ALERTING.md`.
- Consider a mid-conversion abort (hard per-page output cap in the task, not only post-hoc scoring) so a degenerate job fails fast instead of exhausting its time limit.

**Files:** `docker-compose.gpu.yml`, `deploy/k8s/surya-vlm.yaml`, `worker/tasks/conversion.py` (if aborting), `docs/ALERTING.md`.

---

### Story M2.6 — Make the k8s Surya VLM model cache writable by the container UID (open)

```gherkin
In order to let the vLLM server actually load the model on a k8s GPU node
As the k8s operator rolling out surya-vlm
I want the model cache path writable by the non-root container user
```

**Problem:** `deploy/k8s/surya-vlm.yaml` runs vLLM as `runAsUser: 1000` but mounts an `emptyDir` at `/root/.cache/huggingface`. vLLM's `HOME` stays `/root`, so Hugging Face downloads target `/root/.cache/huggingface` — owned by root on the emptyDir, not writable by UID 1000; and an emptyDir mount shadows any model baked into the image at that path. The compose path works (root user, no mount), so this only bites k8s deployments.

**Acceptance criteria**
- vLLM's model-cache directory is writable by the container's `runAsUser` (set `HF_HOME`/`HOME` to a writable path, or pre-populate the emptyDir with the right ownership), verified by a `surya-vlm` pod reaching `Ready` on a GPU node and completing a conversion.
- A regression note (or a packaging test where feasible) prevents the mount path/UID from drifting apart again.

**Files:** `deploy/k8s/surya-vlm.yaml`.

---

### Story M2.7 — Refresh v1-era docs for the v2 topology

```gherkin
In order to keep the docs describing the system that actually runs
As an operator or new contributor reading the architecture docs
I want no v1-only claims (in-process models, ~30 s load, per-task empty_cache) left behind
```

**Problem:** After M2.1–M2.4, several docs still described v1: in-process model loading with a ~30 s first-conversion penalty, per-task `torch.cuda.empty_cache()`, the 50-words/page hybrid heuristic as the "only quality signal", and the marker pin "1.10.x".

**Acceptance criteria**
- `ARCHITECTURE.md` describes the shared `surya-vlm` server, the thin worker client, the new ADR (002a), and the corrected model-lifecycle section; the engine-selection diagram no longer shows `cuda empty_cache` after Marker tasks.
- `PRD.md` performance row, FR-2.2, quality-bar section, and the open-risks row (marker pin) reflect marker 2 and the quality-scorer-driven hybrid threshold.
- `AI_INTEGRATION.md` and `ALERTING.md` GPU sections describe the client/server topology and point at the `surya-vlm` container.

**Validated by** this PR; doc-only, no behavior change.

---

## Validation summary (bead acceptance check)

The upgrade was validated against its acceptance criteria as follows:

| Check | Result | Evidence |
|---|---|---|
| No feature regression (pdf_marker / pdf_marker_slm / pdf_hybrid / capture OCR) | ✅ | All four call sites use the v2 API; unit + characterization tests cover success, failure, quality persistence, image saving, and include_images semantics |
| No test-coverage regression | ✅ | `shared/quality.py` floor 100 enforced by CI; new tests for shutdown lifecycle and scorer bounds; coverage floor config unchanged since before the upgrade |
| v2 API usage is correct | ✅ | `PdfConverter(artifact_dict=..., config=...)`, `text_from_rendered`, `create_model_dict()`, `shutdown_models()` match marker-pdf 2.0.0; `SURYA_INFERENCE_URL` / `SURYA_INFERENCE_AUTOSTART` / `datalab-to/surya-ocr-2` match surya settings |
| Degeneration mode is bounded | ✅ (post-hoc) | `excess_output` + `repetitive_output` force `poor`; verified by unit tests and manual probes |
| Docs reflect v2 | ✅ | M2.7 (this PR) |
| Full suite green on CI | ⏳ CI runs on the PR | `pytest` (all tiers + coverage floors), `pytest -m bdd`, `pytest -m packaging`, ruff/mypy (non-blocking), bandit, eslint, vitest |

Open items for future beads: **M2.5** (server recycling decision) and **M2.6** (k8s model-cache writability).
