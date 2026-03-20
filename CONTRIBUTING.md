# Contributing to DocuFlux

## Architecture

DocuFlux is a containerized document conversion service with this core pattern:

```
Browser → Flask (5000) → Redis → Celery Worker → Pandoc / Marker AI / SLM
```

| Service | Description |
|---------|-------------|
| **web** | Flask frontend: uploads, UI, REST API, WebSocket |
| **worker** | Celery worker: Pandoc, Marker AI, SLM (11 task types) |
| **redis** | Celery broker + job metadata store |
| **beat** | Celery Beat scheduler for cleanup and metrics |

Shared modules live in `shared/` (encryption, key management, secrets).

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
# All tests with coverage
pytest

# Specific test file
pytest tests/unit/test_web.py -v

# Integration tests (requires running services)
pytest tests/integration/ -v

# Syntax check
python3 -m py_compile web/app.py worker/tasks.py worker/warmup.py
```

Coverage threshold is 70%. The test suite uses `pytest.ini` for configuration.

## Project Structure

```
docuflux/
├── web/app.py              # Flask routes + REST API
├── web/templates/          # Material Design 3 UI
├── worker/tasks.py         # Celery tasks
├── worker/warmup.py        # GPU detection + SLM eager load
├── worker/metrics.py       # Prometheus metrics
├── shared/                 # Shared modules (encryption, secrets, key management)
├── config.py               # Pydantic Settings
├── deploy/                 # Infrastructure configs
│   ├── cloudflare/         # Cloudflare Tunnel config + setup
│   ├── certs/              # TLS certificates
│   ├── monitoring/         # Prometheus alert rules
│   └── k8s/                # Kubernetes manifests
├── tests/
│   ├── unit/               # Pytest unit tests
│   ├── integration/        # WebSocket + pipeline E2E tests
│   └── load/locustfile.py  # Locust load tests
├── scripts/build.sh        # Build wrapper (auto/gpu/cpu)
└── docker-compose*.yml     # Compose variants (base/gpu/cpu/tls/cloudflare)
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
