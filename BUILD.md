# DocuFlux Build & Deployment Guide

This guide explains how to build and deploy DocuFlux with GPU or CPU-only configurations.

## Quick Start

### Auto-Detection Build (Recommended)
```bash
./scripts/build.sh auto
```
This automatically detects GPU availability and builds the appropriate image.

### Manual Build

**GPU Build:**
```bash
./scripts/build.sh gpu
```
- Image size: ~15GB
- Includes: CUDA 12.2 (`nvcr.io/nvidia/cuda:12.2.2-cudnn8-devel-ubuntu22.04` base), PyTorch GPU, `marker-pdf[full]==2.0.0` (thin client — model weights live in the `surya-vlm` inference server, not in this image)
- Requires: NVIDIA GPU with 16GB+ VRAM

**CPU Build:**
```bash
./scripts/build.sh cpu
```
- Image size: ~3GB
- Includes: PyTorch CPU-only, no Marker dependencies
- Requires: No special hardware

## Deployment Profiles

### GPU Profile (Default)
```bash
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up
```
- Uses worker:gpu image
- Worker: 18GB memory (16GB VRAM + 2GB system, `docker-compose.gpu.yml`) + NVIDIA device reservation (local PDF/SLM processing)
- Starts the shared **`surya-vlm`** inference server (vLLM image, 24GB memory, `VLLM_GPU_MEMORY_UTILIZATION=0.85`) that the worker attaches to via `SURYA_INFERENCE_URL=http://surya-vlm:8000`
- Enables Marker AI PDF conversion
- Requires GPU with CUDA support

### CPU Profile
```bash
docker-compose -f docker-compose.yml -f docker-compose.cpu.yml up
```
- Uses worker:cpu image
- Allocates 2GB memory
- Disables Marker AI features
- Runs on any host (no GPU needed)

## Architecture

### Build-time vs Runtime Detection

**Build-time Detection (Epic 21.1):**
- `scripts/build.sh` detects GPU during image build
- Creates optimized images: `worker:gpu` or `worker:cpu`
- Conditional Dockerfile installs appropriate dependencies

**Runtime Detection (Epic 21.2):**
- Worker detects GPU on startup via `check_gpu_availability()`
- Stores GPU info in Redis for UI display
- Gracefully disables Marker if GPU unavailable

### Memory Optimization (Epic 21.4)

**Marker v2 thin client:**
- Model weights live in the `surya-vlm` inference server process, not in the worker — the worker never allocates VRAM for Marker at all
- The worker only builds a small client-side artifact dict, lazily on first Marker task
- Worker idle memory stays below 1GB regardless of conversion volume

**Automatic Cleanup:**
- `_cleanup_marker_memory()` releases the task's local objects (`gc.collect()`) after every conversion
- GPU memory belongs to the `surya-vlm` server, sized server-side by `VLLM_GPU_MEMORY_UTILIZATION`
- The deployment recycles the server on a schedule (see [ARCHITECTURE.md](docs/ARCHITECTURE.md#inference-server-recycling)) — never the worker

## Files Structure

```
.
├── scripts/
│   └── build.sh              # GPU detection & build automation
├── worker/
│   ├── Dockerfile            # Conditional multi-stage build
│   ├── requirements-true.txt   # GPU dependencies (BUILD_GPU=true)
│   ├── requirements-false.txt  # CPU-only dependencies (BUILD_GPU=false)
│   ├── warmup.py             # GPU detection, SLM eager load, inference-server probe
│   └── tasks.py              # Memory cleanup
├── docker-compose.yml        # Base configuration
├── docker-compose.gpu.yml    # GPU profile overrides
└── docker-compose.cpu.yml    # CPU profile overrides
```

## Environment Variables

| Variable | GPU Profile | CPU Profile | Description |
|----------|-------------|-------------|-------------|
| `BUILD_GPU` | `true` | `false` | Controls build-time dependencies |
| `MARKER_ENABLED` | `true` | `false` | Runtime feature flag |
| `SURYA_INFERENCE_URL` | `http://surya-vlm:8000` | — (unset) | Shared Surya VLM inference server the worker attaches to |
| `SURYA_INFERENCE_AUTOSTART` | `false` | — | Deployment owns the server; the worker never spawns its own |

GPU-side sizing knobs live on the `surya-vlm` service itself: `SURYA_MODEL` (default `datalab-to/surya-ocr-2`) and `VLLM_GPU_MEMORY_UTILIZATION` (default `0.85`).

## Memory Limits

| Service | GPU Profile | CPU Profile |
|---------|-------------|-------------|
| Worker | 18GB | 2GB |
| surya-vlm | 24GB | n/a (not deployed) |
| Web | 512MB | 512MB |
| Redis | 300MB | 300MB |
| Beat | 256MB | 256MB |

## Troubleshooting

### GPU Not Detected
```bash
# Check GPU availability
nvidia-smi

# Verify Docker can access GPU
docker run --rm --gpus all nvidia/cuda:12.2.2-base-ubuntu22.04 nvidia-smi
```

### Build Fails with ARG Error
```bash
# Ensure requirements files exist
ls worker/requirements-*.txt

# Should show:
# worker/requirements-true.txt
# worker/requirements-false.txt
```

### Worker Memory Issues
```bash
# Check worker logs for cleanup after Marker tasks
docker-compose logs worker | grep "cleanup"

# Expected output:
# "Marker task cleanup complete (local objects released; model weights live in the inference server process)"

# If the worker cannot reach the inference server, check its health
# (port 8000 is internal-only, so query it from inside the Docker network):
docker compose exec worker python -c "import urllib.request; print(urllib.request.urlopen('http://surya-vlm:8000/health', timeout=5).status)"
# 503 while the surya-ocr-2 model loads, 200 when ready
```

## Performance Comparison

| Metric | GPU Build | CPU Build | Improvement |
|--------|-----------|-----------|-------------|
| Image Size | ~15GB | ~3GB | **5x smaller** |
| Idle Memory | <1GB | <500MB | **Thin client (weights in surya-vlm)** |
| Build Time | ~15 min | ~5 min | **3x faster** |
| PDF Conversion | Yes (GPU) | No | **GPU required** |

## Migration Guide

### From Legacy Build
```bash
# Old way (always GPU)
docker-compose up --build

# New way (auto-detect)
./scripts/build.sh auto
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up
```

### Switching Profiles
```bash
# Stop current deployment
docker-compose down

# Rebuild for different profile
./scripts/build.sh cpu
docker-compose -f docker-compose.yml -f docker-compose.cpu.yml up
```

## CI/CD Integration

```yaml
# GitHub Actions example
- name: Build DocuFlux
  run: |
    if [ "${{ matrix.profile }}" == "gpu" ]; then
      ./scripts/build.sh gpu
    else
      ./scripts/build.sh cpu
    fi

# Matrix strategy
strategy:
  matrix:
    profile: [gpu, cpu]
```

## Epics Implemented

- **Epic 21.1:** Build-time GPU Detection (conditional Docker builds)
- **Epic 21.2:** Runtime GPU Detection (already implemented)
- **Epic 21.3:** Docker Compose Profiles (GPU/CPU deployment modes)
- **Epic 21.4:** Memory Footprint Reduction (lazy loading + cleanup)
- **Epic 21.13:** GPU/CPU Visual Indicator (already implemented)

---

For questions or issues, see [GitHub Issues](https://github.com/your-repo/pandoc-web/issues)
