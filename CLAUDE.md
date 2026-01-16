# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**DocuFlux** is a containerized document conversion service combining Pandoc (universal converter) with Marker AI (deep learning PDF processor). It uses a microservices architecture with asynchronous task processing.

**Core Pattern**: Web UI (Flask) → Task Queue (Redis/Celery) → Worker (Pandoc + Marker AI) → Shared Volume Storage

## Essential Commands

### Development Workflow

```bash
# Build and start all services (GPU mode - default)
docker-compose up --build

# GPU-optimized build (auto-detects GPU)
./scripts/build.sh auto
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up

# CPU-only build (no GPU required, 5x smaller image)
./scripts/build.sh cpu
docker-compose -f docker-compose.yml -f docker-compose.cpu.yml up

# View logs for specific service
docker-compose logs -f web      # Flask backend
docker-compose logs -f worker   # Celery worker (Pandoc + Marker)
docker-compose logs -f redis    # Message broker + metadata store
docker-compose logs -f beat     # Scheduler (cleanup tasks)

# Stop all services
docker-compose down

# Rebuild specific service
docker-compose up --build worker
```

### Testing

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/unit/test_web.py -v

# Run single test
pytest tests/unit/test_web.py::test_index -v

# Coverage report
pytest --cov=web --cov=worker --cov-report=term-missing

# Run verification script
python3 tests/verify_phase8.py
```

### Code Validation

```bash
# Validate Python syntax
python3 -m py_compile web/app.py
python3 -m py_compile worker/tasks.py worker/warmup.py

# Check Docker builds
docker build -f worker/Dockerfile --build-arg BUILD_GPU=true worker/
docker build -f web/Dockerfile web/
```

## Architecture

### Service Communication Flow

```
User Browser
    ↓ HTTP (upload file)
Flask Web (port 5000)
    ↓ enqueue task
Redis (Celery broker, port 6379)
    ↓ task pickup
Celery Worker
    ↓ runs Pandoc or Marker
Shared Volume (data/)
    ↓ file ready
Flask Web
    ↓ HTTP download or WebSocket update
User Browser
```

### Data Flow for Conversions

1. **Upload**: File saved to `data/uploads/{job_id}/{filename}`
2. **Metadata**: Job info stored in Redis `job:{job_id}` hash (status, progress, timestamps)
3. **Queue**: Task dispatched to `high_priority` queue (file >5MB) or `default` queue
4. **Processing**: Worker runs Pandoc or Marker, saves to `data/outputs/{job_id}/`
5. **Download**: User downloads from outputs, `downloaded_at` timestamp recorded
6. **Cleanup**: Beat scheduler deletes old files every 5 minutes based on retention policy

### Critical Architecture Details

**Worker Pool Configuration**:
- Uses `--pool=solo` (NOT gevent) to support PyTorch/CUDA synchronous operations
- Gevent monkey-patching removed from worker to prevent deadlocks with Marker AI

**Marker Integration**:
- Marker AI runs **directly in worker** (not external service)
- Uses Python API: `from marker.converters.pdf import PdfConverter`
- Models pre-cached during Docker build, loaded lazily at runtime
- `warmup.py` runs on worker startup to verify models and detect GPU

**GPU Detection (Epics 21.2, 21.13)**:
- Runtime detection via `check_gpu_availability()` in `worker/warmup.py`
- Stores GPU status in Redis: `marker:gpu_status`, `marker:gpu_info`
- UI displays real-time GPU/CPU indicator in header
- Gracefully disables Marker when GPU unavailable

**Memory Optimization (Epic 21.4)**:
- Lazy loading: Models loaded on first Marker task, not at startup
- Memory cleanup: `gc.collect()` and `torch.cuda.empty_cache()` after each task
- Reduces idle worker memory from 8GB to <1GB

**Multi-file Handling**:
- Marker outputs markdown + images in separate `images/` subdirectory
- Automatic ZIP bundling if output contains multiple files
- ZIP detection: checks for `images/` folder or multiple files in output directory

### Redis Key Structure

```
# Job metadata (DB 1)
job:{uuid}                  # Hash with status, filename, from, to, progress, timestamps

# Marker service status (DB 1)
service:marker:status       # "initializing" | "ready" | "error"
service:marker:eta          # Download ETA string
marker:gpu_status           # "available" | "unavailable" | "initializing"
marker:gpu_info             # Hash with model, vram_total, vram_available, cuda_version, etc.

# Celery tasks (DB 0)
celery-task-meta-*          # Celery task results
_kombu.*                    # Celery broker messages
```

### File Structure Patterns

**Input files**: `data/uploads/{job_id}/{original_filename}`
**Output files**: `data/outputs/{job_id}/{converted_filename}`
**Output images**: `data/outputs/{job_id}/images/{image_filename}`
**Metadata**: `data/outputs/{job_id}/metadata.json` (Marker metadata)
**ZIP bundles**: `data/outputs/{job_id}/{job_id}.zip`

### Retention Policy

| Status | Downloaded | Retention |
|--------|-----------|-----------|
| SUCCESS | Yes | 10 minutes after download |
| SUCCESS | No | 1 hour after completion |
| FAILURE | - | 5 minutes after failure |
| Orphaned | - | 1 hour fallback |

Enforced by `cleanup_old_files()` task in `worker/tasks.py`, scheduled every 5 minutes via Celery Beat.

## Build System (Epic 21.1, 21.3)

### Conditional Builds

The Dockerfile supports GPU or CPU-only builds:

```bash
# Auto-detect (recommended)
./scripts/build.sh auto

# Force GPU build (~15GB image)
./scripts/build.sh gpu
docker build --build-arg BUILD_GPU=true -t worker:gpu worker/

# Force CPU build (~3GB image)
./scripts/build.sh cpu
docker build --build-arg BUILD_GPU=false -t worker:cpu worker/
```

**How it works**:
- `ARG BUILD_GPU` controls base image: CUDA 11.8 (GPU) or Ubuntu 22.04 (CPU)
- Conditional PyTorch installation: GPU wheel vs CPU-only wheel
- Conditional Marker installation: `requirements-gpu.txt` vs `requirements-cpu.txt`
- CPU build skips Marker entirely, disabling AI PDF conversion

### Deployment Profiles

```bash
# GPU profile (18GB memory, GPU reservations)
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up

# CPU profile (2GB memory, no GPU)
docker-compose -f docker-compose.yml -f docker-compose.cpu.yml up
```

## Key Files and Their Purposes

### Backend

**`web/app.py`** (~408 lines):
- Flask routes: `/`, `/convert`, `/download/{job_id}`, `/download_zip/{job_id}`, `/api/status/services`
- CSRF protection (Flask-WTF)
- Rate limiting (1000/day, 200/hour per IP)
- Security headers (CSP, HSTS, X-Frame-Options)
- Redis job metadata management
- ZIP bundling for multi-file outputs
- WebSocket server for real-time updates

**`worker/tasks.py`** (~319 lines):
- Celery tasks: `convert_document` (Pandoc), `convert_with_marker` (AI PDF), `cleanup_old_files`
- Queue routing: `high_priority` for large files (>5MB), `default` otherwise
- Marker Python API integration: `PdfConverter(artifact_dict=models, config=options)`
- Progress tracking: updates Redis with 0-100% progress
- Memory cleanup: gc.collect() and torch.cuda.empty_cache() after tasks
- Image path fixing: updates markdown to use `images/` relative paths

**`worker/warmup.py`** (~154 lines):
- GPU detection: `check_gpu_availability()` using `torch.cuda.is_available()` and `nvidia-smi`
- Model cache verification (lazy loading - doesn't load models into memory)
- Health check server on port 8080: `/healthz` endpoint
- Redis status updates: `service:marker:status`, `marker:gpu_status`, `marker:gpu_info`

### Frontend

**`web/templates/index.html`** (~478 lines):
- Material Design 3 components from `@material/web` via ESM
- Drag-and-drop file upload with visual feedback
- Format selection with 17+ supported formats
- Real-time job status via Socket.IO WebSocket
- Theme switcher (system/light/dark) with localStorage persistence
- Marker service status banner (shows initialization progress)
- GPU/CPU status indicator chip (Epic 21.13) - clickable modal with GPU details
- Job list with progress bars, download buttons, retry/cancel/delete actions

### Infrastructure

**`docker-compose.yml`** (99 lines):
- Base configuration for all services
- Shared volume: `./data` mounted to `/app/data` in web and worker
- Health checks for all services (redis, web, worker)
- Resource limits: web (512MB), worker (16GB), redis (300MB)

**`docker-compose.gpu.yml`** (31 lines):
- GPU profile override: worker uses `worker:gpu` image
- GPU reservations: NVIDIA driver, 1 GPU, GPU capabilities
- Memory: 18GB (16GB VRAM + 2GB system)
- Environment: `MARKER_ENABLED=true`, `BUILD_GPU=true`

**`docker-compose.cpu.yml`** (26 lines):
- CPU profile override: worker uses `worker:cpu` image
- No GPU reservations
- Memory: 2GB
- Environment: `MARKER_ENABLED=false`, `BUILD_GPU=false`

**`worker/Dockerfile`** (74 lines):
- Multi-stage build with conditional base image selection
- Stage 1: GPU (CUDA 11.8) or CPU (Ubuntu 22.04)
- Conditional PyTorch installation (GPU vs CPU wheel)
- Conditional requirements: `requirements-${BUILD_GPU}.txt`
- Conditional Marker model pre-caching (GPU only)
- Pandoc, LaTeX, Git-LFS, OpenCV dependencies

## Testing Patterns

### Test Structure

```
tests/
├── conftest.py              # Fixtures: app, client, redis mocks
├── unit/
│   ├── test_web.py         # Flask route tests, job management
│   └── test_worker.py      # Celery task tests, conversions
└── verify_phase8.py        # Integration verification script
```

### Writing Tests

**Flask route tests**:
```python
def test_route(client):
    response = client.get('/')
    assert response.status_code == 200
```

**Celery task tests** (note: tests need updating for Marker API):
```python
@patch('tasks.subprocess.run')  # OLD - tests mock subprocess
def test_convert_document(mock_run):
    # But actual code uses PdfConverter class now
    # Tests need updating to mock PdfConverter instead
    pass
```

**Current test status**: Pytest suite exists but worker tests are outdated. They mock `subprocess.run` for Marker, but the actual implementation uses `PdfConverter` Python API directly.

## Common Development Scenarios

### Adding a New Conversion Format

1. Add format to `FORMATS` list in `web/app.py` (line ~153)
2. Ensure Pandoc supports it (check with `pandoc --list-input-formats`)
3. Add MIME type detection if needed
4. No worker changes needed - Pandoc handles it

### Modifying Marker Options

1. Update options in `web/app.py` route handler (line ~200+)
2. Pass options to `convert_with_marker.delay(job_id, ..., options={})`
3. Worker receives options in `worker/tasks.py::convert_with_marker()`
4. Options passed to `PdfConverter(config=options)`

### GPU Detection Changes

1. Modify `check_gpu_availability()` in `worker/warmup.py`
2. Update Redis keys: `marker:gpu_status`, `marker:gpu_info`
3. Frontend automatically picks up changes via `/api/status/services`
4. UI updates via `updateGPUStatus()` JavaScript function

### Adding WebSocket Events

1. Emit from worker: `socketio.emit('event_name', data, room=job_id)`
2. Emit from web: `socketio.emit('event_name', data, room=job_id)`
3. Listen in frontend: `socket.on('event_name', (data) => { ... })`
4. Example: GPU status updates use `socket.on('gpu_status_update', updateGPUStatus)`

## Important Patterns and Conventions

### Error Handling in Tasks

Always update job metadata on failure:
```python
try:
    # conversion logic
    update_job_metadata(job_id, {'status': 'SUCCESS', 'completed_at': str(time.time())})
except Exception as e:
    update_job_metadata(job_id, {'status': 'FAILURE', 'error': str(e)[:500]})
    raise  # Re-raise for Celery retry logic
```

### Memory Management

After Marker tasks, always cleanup:
```python
del converter, rendered, text, images
import gc
gc.collect()
if torch.cuda.is_available():
    torch.cuda.empty_cache()
```

### Job Metadata Updates

Use `update_job_metadata()` helper, never direct Redis calls:
```python
update_job_metadata(job_id, {
    'status': 'PROCESSING',
    'progress': '50',
    'custom_field': 'value'
})
```

### WebSocket Broadcasting

Always use room for job-specific updates:
```python
socketio.emit('job_update', {'status': 'SUCCESS'}, room=job_id)
```

### UUID Validation

Always validate UUIDs from user input:
```python
if not is_valid_uuid(job_id):
    return {"status": "error", "message": "Invalid job ID"}
```

## Known Issues and Workarounds

### Gevent + PyTorch Incompatibility

**Issue**: Worker hangs during Marker conversion if using gevent pool
**Solution**: Use `--pool=solo` in Celery worker command
**Location**: `docker-compose.yml` line 55

### Marker Test Mocking

**Issue**: Tests mock `subprocess.run` but code uses `PdfConverter` class
**Solution**: Update `tests/unit/test_worker.py` to mock `PdfConverter` instead
**Status**: Known gap, tests need updating

### GPU Memory Leaks

**Issue**: GPU memory not freed between tasks
**Solution**: Epic 21.4 implemented cleanup - `gc.collect()` and `torch.cuda.empty_cache()`
**Location**: `worker/tasks.py` lines 239-250, 259-269

### Redis Port Exposure

**Issue**: Redis port 6379 exposed on 0.0.0.0 (security vulnerability)
**Solution**: Planned in Epic 22-25 (network isolation, TLS)
**Status**: Known security gap

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | `change-me-in-production` | Flask session encryption |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery message broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | Celery result storage |
| `REDIS_METADATA_URL` | `redis://redis:6379/1` | Job metadata storage |
| `UPLOAD_FOLDER` | `data/uploads` | Input file storage |
| `OUTPUT_FOLDER` | `data/outputs` | Output file storage |
| `BUILD_GPU` | `true` | Controls Docker build mode |
| `MARKER_ENABLED` | (auto-detected) | Runtime feature flag |
| `INFERENCE_RAM` | (auto-detected) | VRAM allocation for Marker |
| `SESSION_COOKIE_SECURE` | `false` | HTTPS-only cookies |

## Plan.md Structure

The `plan.md` file (1804 lines) is the project's master planning document:

- **Lines 1-120**: Quick start guide, architecture, critical files, running instructions
- **Lines 121-680**: Status summary, epics 1-20 (completed), BDD user stories
- **Lines 681-1476**: Epics 21-25 (planned) - GPU detection, HTTPS, encryption, security
- **Lines 1477+**: Next steps, implementation phases, roadmap

**Epic tracking**: Each epic has BDD user stories with Gherkin scenarios (Given/When/Then)
**Status format**: ✅ Completed | 🔵 Planned | 🟡 In Progress | ⚠️ Deferred | ❌ Cancelled

## Verification and Troubleshooting

### Quick Health Check

```bash
# Check all services running
docker-compose ps

# Check web is responding
curl http://localhost:5000/

# Check API status
curl http://localhost:5000/api/status/services | jq

# Expected output:
# {
#   "disk_space": "ok",
#   "marker": "ready",
#   "marker_status": "ready",
#   "models_cached": true,
#   "gpu_status": "available",  # or "unavailable"
#   "gpu_info": { ... }
# }
```

### Common Issues

**"Redis connection refused"**: Redis container not running or health check failing
```bash
docker-compose up redis
docker-compose logs redis
```

**"Permission denied on data/"**: Volume ownership issues
```bash
chmod -R 777 data/
```

**"Worker hanging on PDF conversion"**: Gevent conflict with PyTorch
- Check `docker-compose.yml` worker command uses `--pool=solo`
- Check worker code doesn't have `monkey.patch_all()`

**"GPU not detected"**: NVIDIA Container Toolkit not installed or configured
```bash
nvidia-smi  # Should show GPU on host
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi  # Should work in Docker
```

**"Models downloading on every startup"**: Cache not persisted or build didn't pre-cache
- Check Dockerfile runs model pre-cache step (line 59-64)
- Check if using CPU build (models not cached for CPU builds)

## References

- **README.md**: User-facing documentation, feature overview
- **BUILD.md**: Build system documentation, GPU/CPU profiles, deployment
- **plan.md**: Master implementation plan with BDD user stories
- **docs/**: Additional documentation (deployment, API, formats, troubleshooting)
