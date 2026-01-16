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
- Includes: CUDA 11.8, PyTorch GPU, Marker AI models
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
- Allocates 18GB memory (16GB VRAM + 2GB system)
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

**Lazy Model Loading:**
- Models pre-cached during Docker build (GPU images)
- Not loaded into memory until first Marker task
- Reduces idle worker memory from 8GB to <1GB

**Automatic Cleanup:**
- `gc.collect()` after every task completion
- `torch.cuda.empty_cache()` for GPU memory
- Logs memory freed for monitoring

## Files Structure

```
.
├── scripts/
│   └── build.sh              # GPU detection & build automation
├── worker/
│   ├── Dockerfile            # Conditional multi-stage build
│   ├── requirements-gpu.txt  # GPU dependencies
│   ├── requirements-cpu.txt  # CPU-only dependencies
│   ├── warmup.py             # Lazy loading + GPU detection
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
| `INFERENCE_RAM` | `16` (auto-detected) | `4` | VRAM allocation for Marker |

## Memory Limits

| Service | GPU Profile | CPU Profile |
|---------|-------------|-------------|
| Worker | 18GB | 2GB |
| Web | 512MB | 512MB |
| Redis | 300MB | 300MB |
| Beat | 256MB | 256MB |

## Troubleshooting

### GPU Not Detected
```bash
# Check GPU availability
nvidia-smi

# Verify Docker can access GPU
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```

### Build Fails with ARG Error
```bash
# Ensure requirements files exist
ls worker/requirements-*.txt

# Should show:
# worker/requirements-gpu.txt
# worker/requirements-cpu.txt
```

### Worker Memory Issues
```bash
# Check worker logs for memory cleanup
docker-compose logs worker | grep "Memory cleanup"

# Expected output:
# "Memory cleanup complete. GPU memory freed: X.XX GB"
```

## Performance Comparison

| Metric | GPU Build | CPU Build | Improvement |
|--------|-----------|-----------|-------------|
| Image Size | ~15GB | ~3GB | **5x smaller** |
| Idle Memory | <1GB | <500MB | **Lazy loading** |
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
