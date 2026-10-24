# Deployment Guide

## Prerequisites
- Docker Engine & Docker Compose
- NVIDIA Container Toolkit (Optional, for GPU acceleration in the worker)
- 16GB+ RAM (Recommended if using AI conversion)
- 10GB+ Disk Space

## Configuration
Environment variables are managed via `docker-compose.yml`. For production, it is recommended to create a `.env` file (though direct editing of compose args works too).

### Core Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret. **Required**: the web app refuses to start without it, and in production the placeholders `change-me-in-production`, `insecure-default` and `dev-only` are rejected | none |
| `MASTER_ENCRYPTION_KEY`, `CELERY_SIGNING_KEY` | Encryption-at-rest and Celery signing keys. Required when `FLASK_ENV=production` (the default); ephemeral keys are generated otherwise | none |
| `REDIS_PASSWORD`, `MCP_SECRET` | Redis `requirepass` and MCP bearer token, consumed by `docker-compose.yml` | none (set in `.env`) |
| `MAX_CONTENT_LENGTH` | Max file upload size (Bytes), from `config.py` | `209715200` (200 MB) |
| `OCR_MODE` | `on_demand` or `cpu_only` (see step 3) | `on_demand` |
| `REDIS_METADATA_URL` | Redis DB for job tracking | `redis://redis:6379/1` |
| `CELERY_BROKER_URL` | Redis DB for task queue | `redis://redis:6379/0` |

## Production Deployment Steps

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/aerocristobal/DocuFlux.git
    cd DocuFlux
    ```

2.  **Set Secure Secrets**
    Copy `.env.example` to `.env` and set real values (do not leave the `change-me-*` placeholders). Alternatively, edit the `web` service in `docker-compose.yml`:
    ```yaml
    environment:
      - SECRET_KEY=your-super-secure-random-string
    ```

3.  **GPU Configuration (Optional but Recommended)**
    To enable fast PDF conversions with Marker, ensure NVIDIA drivers are installed and the container runtime is configured. Under Marker v2 the worker is a thin client: model weights live in the shared `surya-vlm` inference server, which owns the GPU. The worker attaches via `SURYA_INFERENCE_URL`. See [BUILD.md](../BUILD.md) for GPU vs CPU build profiles.

    By default (`OCR_MODE=on_demand`) the `surya-controller` starts `surya-vlm` when a Marker job needs it and stops it after `SURYA_IDLE_TIMEOUT_MINUTES` (default 15) of inactivity, so an `exited` `surya-vlm` is normal. Set `OCR_MODE=cpu_only` to never load it, and set `ADMIN_API_SECRET` to use the manual load/unload admin API. See [CONFIGURATION.md](CONFIGURATION.md) and ARCHITECTURE §7.3. The lifecycle is Compose-only; Kubernetes keeps `surya-vlm` always-on.
    ```yaml
    # In docker-compose.gpu.yml — the surya-vlm service owns the GPU;
    # the worker also reserves a device for local PDF/SLM processing.
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    ```

4.  **Start Services**
    ```bash
    docker-compose up -d --build
    ```
    The base file is the GPU topology: it starts `redis`, `web`, `worker` (heavy lane, `gpu` queue), `worker-light` (Pandoc, OCR, metadata, maintenance), `beat`, `mcp-server`, `surya-vlm`, and the Surya lifecycle services `surya-controller` and `docker-socket-proxy`. On a CPU-only host add the CPU overlay and set `OCR_MODE=cpu_only`:
    ```bash
    docker-compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build
    ```
    The CPU overlay moves `surya-vlm`, `surya-controller` and `docker-socket-proxy` to a never-activated profile, so nothing on a CPU host touches the Docker socket or a GPU.

5.  **Scaling Workers**
    To handle more concurrent jobs, you can scale the worker container:
    ```bash
    docker-compose up -d --scale worker=3
    ```
    *Note: each scaled worker is a thin Marker client, so worker memory stays modest — but all workers share the single `surya-vlm` inference server, so inference throughput and VRAM are the bottleneck for concurrent AI jobs. Stick to 1 GPU worker for AI tasks or size the server accordingly.*

## Security Considerations
- **Network**: The service listens on port `5000` by default. It is recommended to put this behind a reverse proxy (Nginx/Caddy) with SSL termination. The `docker-compose.cloudflare.yml` tunnel overlay was removed; if you use Cloudflare Tunnel, run `cloudflared` yourself (see `deploy/cloudflare/` and the deprecated [archive/CLOUDFLARE_TUNNEL_SETUP.md](archive/CLOUDFLARE_TUNNEL_SETUP.md)). Set `SESSION_COOKIE_SECURE=true` and `BEHIND_PROXY=true` behind HTTPS.
- **Redis transport**: Redis is on the internal Docker network only, password-protected, plaintext by default. For TLS use the opt-in `docker-compose.tls.yml` overlay (see [CERTIFICATE_MANAGEMENT.md](CERTIFICATE_MANAGEMENT.md)); note it currently covers `web`, `worker` and `beat` but not `worker-light` or `surya-controller`.
- **Docker socket**: only `docker-socket-proxy` mounts `/var/run/docker.sock` (read-only), on an isolated `internal: true` network shared with `surya-controller`. Its HAProxy allowlist forwards only list and start/stop calls; check it with `deploy/docker-socket-proxy/verify.sh`. It cannot be scoped to a single container, so treat a compromised controller as able to stop or start any container on the host.
- **Data Retention**: Files are automatically cleaned up.
    - Success (Not Downloaded): 1 hour
    - Success (Downloaded): 10 minutes
    - Failure: 5 minutes
- **Isolation**: The web and worker images run as a non-root `appuser`, the MCP server as `pwuser` and the controller as UID 10001; services drop all capabilities and set `no-new-privileges`.

## OSCAL Compliance

NIST SP 800-53 Rev 5 compliance artifacts are in `oscal/`:
- `component-definition.json` — Control mappings (AC-2, AC-3, AU-2, SC-8, SC-28)
- `ssp.json` — System Security Plan with implementation evidence

## Health Checks
- **Web UI**: `GET /` (Port 5000)
- **Liveness**: `GET /healthz` — returns `OK` if the process is alive
- **Readiness**: `GET /readyz` — checks Redis connectivity
- **Detailed Health**: `GET /api/health` — component-level status (Redis, disk, GPU, workers)
- **Service Status**: `GET /api/status/services` (JSON)
- **Build Profiles**: See [BUILD.md](../BUILD.md) for GPU/CPU build configuration
