# Executable capability specs

Gherkin specs describing what DocuFlux does, bound to step definitions and run as part of
the test suite. They are **not** documentation of intent — a scenario here fails when the
behaviour it describes breaks.

```
tests/features/
├── capabilities/          # executable, hand-maintained — the regression net
│   ├── conversion/
│   ├── capture/
│   ├── api_v1/
│   ├── auth_and_keys/
│   ├── security/
│   ├── reliability/
│   └── storage_retention/
├── steps/                 # step definitions (*_steps.py)
└── epic5_supply_chain/    # retired kanban export — see "History" below
```

## Running them

```bash
pytest -m bdd                    # every scenario
pytest -m "bdd and p0"           # the high-priority subset
pytest -m "bdd and security"     # one capability
```

Bindings live in `tests/bdd/`, one module per capability directory, each a single
`scenarios('<capability>')` call. Binding at directory level means a `.feature` added
under `capabilities/` is collected automatically and cannot sit silently unbound. A
scenario whose steps are unimplemented fails with `StepDefinitionNotFound` rather than
being skipped.

## Adding a scenario

1. Write it in the relevant `capabilities/<capability>/*.feature`, in the language of the
   user rather than the implementation — no status codes or function names in the prose.
2. Add any new steps to `tests/features/steps/<capability>_steps.py`.
3. Run `pytest -m bdd`. If a step is missing you will be told which one.

Tags become pytest markers, so every tag must be registered in `pytest.ini` —
`--strict-markers` enforces that. Keep the vocabulary closed: one capability tag and one
priority tag (`@p0`/`@p1`/`@p2`) per feature.

Step definitions drive the Flask test client against mocked Redis and Celery. They verify
what the app *does*, not that a real broker accepts it.

## History: the retired kanban export

This directory used to hold 22 `.feature` files exported from a kanban board, one per
backlog story, with no runner and no step definitions — the previous README said so
outright. They were story-shaped rather than capability-shaped (several described CI
configuration rather than product behaviour), so they could not become a regression net
as written.

Twenty were retired once their behaviour was covered elsewhere. The mapping, recorded so
the deletion is auditable:

| Story | Now covered by |
|---|---|
| 1.1 Quality scoring | `tests/unit/test_quality.py`, `capabilities/conversion/conversion_outcomes.feature` |
| 1.5 Inline extracted images | `tests/characterization/test_capture_assembly.py` |
| 1.6 Chunked SLM metadata | `tests/unit/test_metadata_task.py` |
| 2.1a Tesseract language packs | `tests/unit/test_packaging.py` (Dockerfile apt contract) |
| 3.1 Pandoc empty output | `tests/unit/test_worker.py::TestEmptyOutputDetection` |
| 3.2 Worker healthcheck | implemented in `docker-compose.yml`; infrastructure, no test |
| 3.3 Timeout-safe cleanup | `tests/characterization/test_engine_guards.py`, `test_retention_decisions.py` |
| 3.4 SLM JSON repair retry | `tests/unit/test_metadata_task.py` |
| 3.5 Structured JSON logging | `tests/unit/test_logging_config.py` |
| 4.1a Cert generation / TLS | implemented in `scripts/` + compose overlay; infrastructure, no test |
| 4.2 Rate limit on convert | `tests/unit/test_api_v1.py` |
| 4.5 MCP non-root + healthcheck | implemented in `mcp_server/Dockerfile`; infrastructure, no test |
| 4.6 Remove CSP unsafe-inline | `tests/unit/test_web.py` (nonce tests) |
| 5.1a Test infra + coverage floor | `scripts/check_coverage_floors.py`, `tests/unit/test_coverage_floors.py` |
| 5.2 Pin base images by digest | implemented in `worker/Dockerfile`; infrastructure, no test |
| 5.3b JS lint gate | `.github/workflows/ci.yml` (`lint-js`, blocking) |
| 5.4c SBOM generation | `.github/workflows/ci.yml` (`sast`) |
| 6.2 Eager Marker warmup | `tests/unit/test_worker.py::TestEagerMarkerWarmup` |
| 6.4a Split convert routes | `tests/unit/test_conversion_route_helpers.py` |
| 6.4b Shared metadata builder | `tests/unit/test_job_metadata_builder.py`, `test_job_metadata_shared.py` |

Four of those are infrastructure with no automated test. They are marked as such rather
than credited to a test that does not exist.

Two features are **retained because the work is not done**, and remain inert specs:

- `5_3a_python_lint_type_gate_ruff_mypy.feature` — `ruff` and `mypy` run with `|| true`,
  a deliberate staged adoption. Delete this file when the gate becomes blocking.
- `5_4a_sast_gate_bandit_semgrep.feature` — `bandit -lll` is blocking; semgrep was never
  added. Delete this file when it is.

**The export is retired.** `tests/features/epic*/` is no longer a generated directory —
do not regenerate into it. If the board still exports Gherkin, point it at
`docs/user-stories/generated/`, well away from `testpaths`.
