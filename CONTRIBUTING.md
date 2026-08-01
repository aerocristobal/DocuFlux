# Contributing to DocuFlux

## Architecture

DocuFlux is a containerized document conversion service with this core pattern:

```
Browser → Flask (5000) → Redis → Celery Worker → Pandoc / Marker AI / SLM
```

| Service | Description |
|---------|-------------|
| **web** | Flask frontend: uploads, UI, REST API, WebSocket |
| **worker** | Celery worker: Pandoc, Marker AI, SLM, capture assembly |
| **mcp-server** | Playwright server for vision-based and agentic extraction |
| **redis** | Celery broker + job metadata store |
| **beat** | Celery Beat scheduler for cleanup and metrics |

Shared modules live in `shared/` (encryption, storage, key management, formats, secrets).

## Development Setup

```bash
git clone https://github.com/aerocristobal/docuflux.git
cd docuflux
cp .env.example .env   # edit SECRET_KEY and optional settings
```

### Running Services

```bash
# Auto-detect GPU
./scripts/build.sh auto
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up

# CPU-only
./scripts/build.sh cpu
docker-compose -f docker-compose.yml -f docker-compose.cpu.yml up
```

### Rebuilding a Single Service

```bash
docker-compose up --build worker
```

## Testing

```bash
# Everything, with coverage
pytest

# One tier
pytest -m unit
pytest -m bdd                  # executable capability specs
pytest -m characterization     # tests that pin current behaviour

# One capability, or one priority band
pytest -m "bdd and security"
pytest -m "bdd and p0"

# One file
pytest tests/unit/test_web.py -v
```

Tier markers (`unit`, `integration`, `ui`, `bdd`, `characterization`) are applied
automatically by directory — you do not decorate tests with them. Every other marker must
be registered in `pytest.ini`; `--strict-markers` rejects typos.

The suite is fully mocked and needs no running services, apart from the handful of tests
marked `@pytest.mark.docker`, which CI deselects with `-m "not docker"`.

### Coverage

Coverage is enforced per module, not as one project-wide average, because a large
well-covered module can otherwise hide an entirely untested one. Floors live in
`pyproject.toml` and are checked by:

```bash
pytest && python scripts/check_coverage_floors.py
```

It only enforces against a complete, passing run — a filtered run (`pytest -m unit`)
covers less of the tree by construction, and the checker refuses rather than reporting
breaches that are not real.

After adding tests, raise the floors deliberately:

```bash
pytest && python scripts/check_coverage_floors.py --emit-floors
```

and paste the result into `pyproject.toml`. Lowering a floor should be as visible in
review as raising one. Anything unlisted is held to `default_floor`, so a new module has
to arrive with tests.

### The three kinds of test here

- **`tests/unit/`, `tests/integration/`, `tests/ui/`** — ordinary tests. Add freely.
- **`tests/features/capabilities/`** — executable specs of product behaviour, written in
  the language of the user. See `tests/features/README.md` for how to add a scenario.
- **`tests/characterization/`** — these pin behaviour *as it is today*, including
  behaviour that may be undesirable. Do not "fix" a failing one to make it pass. If you
  changed the behaviour on purpose, change the test in the same commit and say why in the
  message. `@pytest.mark.provisional` marks behaviour known to be incomplete.

### Packaging contracts

`pytest -m packaging` parses the Dockerfiles and requirements files instead of building
an image. It catches a dependency an image imports but does not declare, and a system
package a Python dependency shells out to. When you add a dependency, this is the test
that tells you which requirements file needs it.

## Project Structure

```
docuflux/
├── web/
│   ├── app.py              # Flask app, middleware, auth
│   ├── routes/             # 5 route blueprints (auth, capture, conversion, health, webhooks)
│   ├── validation.py       # Input validation (MIME, UUID, SSRF, filename)
│   └── templates/          # Material Design 3 UI
├── worker/
│   ├── tasks/              # Celery tasks (capture, conversion, maintenance, metadata)
│   ├── warmup.py           # GPU detection + SLM eager load
│   └── metrics.py          # Prometheus metrics
├── shared/                 # Shared modules (encryption, storage, formats, config, keys, secrets)
├── extension-src/          # Chrome/Firefox browser extension source
├── mcp_server/             # Playwright MCP server for vision extraction
├── oscal/                  # NIST SP 800-53 compliance artifacts
├── deploy/                 # Infrastructure configs
│   ├── cloudflare/         # Cloudflare Tunnel config + setup
│   ├── certs/              # TLS certificates
│   ├── monitoring/         # Prometheus alert rules
│   └── k8s/                # Kubernetes manifests
├── tests/
│   ├── unit/               # Pytest unit tests
│   ├── integration/        # E2E + encryption pipeline tests
│   └── load/locustfile.py  # Locust load tests
├── scripts/build.sh        # Build wrapper (auto/gpu/cpu)
└── docker-compose*.yml     # 5 Compose variants (base/gpu/cpu/tls/cloudflare)
```

## Code Style

- Follow existing patterns in the codebase
- Use type hints for function signatures
- Configuration via Pydantic Settings (`config.py`) — add new env vars there
- Secrets via `shared/secrets_manager.py` — never hardcode credentials

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes with clear, focused commits
3. Ensure all tests pass: `pytest`
4. Ensure syntax is clean: `python3 -m py_compile <changed-files>`
5. Open a PR with a description of what changed and why
