# Deployment Guide

## Prerequisites
- Docker Engine & Docker Compose
- NVIDIA Container Toolkit (Optional, for GPU acceleration of Marker API)
- 16GB+ RAM (Recommended if using AI conversion)
- 10GB+ Disk Space

## Configuration
Environment variables are managed via `docker-compose.yml`. For production, it is recommended to create a `.env` file (though direct editing of compose args works too).

### Core Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret (Change this for prod!) | `dev-secret-key` |
| `MAX_CONTENT_LENGTH` | Max file upload size (Bytes) | 100MB (Hardcoded in app.py config, but good to know) |
| `REDIS_METADATA_URL` | Redis DB for job tracking | `redis://redis:6379/1` |
| `CELERY_BROKER_URL` | Redis DB for task queue | `redis://redis:6379/0` |
| `MARKER_API_URL` | Internal URL for AI service | `http://marker-api:8000` |

## Production Deployment Steps

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/your-repo/pandoc-web.git
    cd pandoc-web
    ```

2.  **Set Secure Secrets**
    Update the `web` service in `docker-compose.yml` or use a `.env` file:
    ```yaml
    environment:
      - SECRET_KEY=your-super-secure-random-string
    ```

3.  **GPU Configuration (Optional but Recommended)**
    To enable fast PDF conversions with Marker, ensure NVIDIA drivers are installed and the container runtime is configured.
    The `docker-compose.yml` is already set up to use the GPU if available:
    ```yaml
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

5.  **Scaling Workers**
    To handle more concurrent jobs, you can scale the worker container:
    ```bash
    docker-compose up -d --scale worker=3
    ```
    *Note: Marker API is resource-heavy. If scaling workers, ensure your hardware can support multiple concurrent AI jobs or stick to 1 worker for AI tasks.*

## Security Considerations
- **Network**: The service listens on port `5000` by default. It is recommended to put this behind a reverse proxy (Nginx/Caddy) with SSL termination.
- **Data Retention**: Files are automatically cleaned up.
    - Success (Not Downloaded): 1 hour
    - Success (Downloaded): 10 minutes
    - Failure: 5 minutes
- **Isolation**: The application runs as root inside the container in the current dev configuration. For strict production environments, consider refactoring Dockerfiles to use a non-root user.

## Health Checks
- **Web UI**: `GET /` (Port 5000)
- **Service Status**: `GET /api/status/services` (JSON)
- **Marker API**: `GET /health` (Port 8000 on internal network)
