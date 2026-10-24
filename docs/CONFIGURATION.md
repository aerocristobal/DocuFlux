# Configuration Management

DocuFlux uses [Pydantic Settings](https://pydantic-settings.readthedocs.io/) for managing its configuration. This approach provides a robust, type-safe, and hierarchical way to handle application settings across different environments.

## How Configuration is Loaded

Pydantic Settings loads configuration parameters from several sources, with earlier sources taking precedence over later ones:

1.  **Environment Variables**: The primary method for configuring DocuFlux, especially in production or containerized environments (e.g., Docker, Kubernetes).
2.  **`.env` files**: For local development, settings can be defined in a `.env` file in the project root. This file is automatically loaded by Pydantic Settings.
3.  **Default Values**: Each setting has a default value defined directly in the `Settings` class in `config.py`.

On top of that, `shared/settings_loader.py` overlays four secrets loaded by `secrets_manager.py` (`SECRET_KEY`, `MASTER_ENCRYPTION_KEY`, `CELERY_SIGNING_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`): it first looks for Docker secrets (files in `/run/secrets/`) and falls back to the environment variable, so a Docker secret wins over the environment for those four values only.

## The `Settings` Class

All application configuration is defined in the `config.py` file within the `Settings` class. This class inherits from Pydantic's `BaseSettings` and uses type hints for all configuration parameters, ensuring type validation and better code readability.

**Example from `config.py`:**

```python
from datetime import timedelta
from typing import Optional, List, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ... (other settings) ...

    # --- General Paths ---
    upload_folder: str = Field("data/uploads", validation_alias="UPLOAD_FOLDER")
    output_folder: str = Field("data/outputs", validation_alias="OUTPUT_FOLDER")

    # --- Redis/Celery URLs ---
    redis_metadata_url: str = Field("redis://redis:6379/1", validation_alias="REDIS_METADATA_URL")

    # --- Flask/Session Settings ---
    flask_debug: bool = Field(False, validation_alias="FLASK_DEBUG")
    secret_key: Optional[SecretStr] = Field(None, validation_alias="SECRET_KEY")

    # ... (other settings) ...
```

-   Each field in the `Settings` class corresponds to a configuration parameter.
-   The `Field` function is used to specify:
    -   A default value if the environment variable is not set.
    -   The `validation_alias` argument (e.g., `validation_alias="UPLOAD_FOLDER"`) explicitly links the Python attribute to an environment variable name.
-   `SecretStr` is used for sensitive fields (like `secret_key`) to prevent accidental logging or exposure.

## On-Demand OCR Settings

These settings control the Surya (Marker) lifecycle on Docker Compose (see [ARCHITECTURE.md](ARCHITECTURE.md) §7.3). They seed `settings:ocr` in Redis; an admin can override mode and timeouts at runtime via `PUT /api/v1/admin/ocr-settings`.

| Setting (env var) | Default | Meaning |
|---|---|---|
| `ocr_mode` (`OCR_MODE`) | `on_demand` | `on_demand`: Surya is loaded when a Marker job needs it and unloaded when idle. `cpu_only`: Surya is never started; scanned PDFs use Tesseract. |
| `surya_idle_timeout_minutes` (`SURYA_IDLE_TIMEOUT_MINUTES`) | `15` | Minutes with no Marker work before the controller unloads Surya (a manual load pins it and skips this). Minimum 1. |
| `surya_cold_start_timeout_seconds` (`SURYA_COLD_START_TIMEOUT_SECONDS`) | `600` | How long a job waits for Surya to become healthy after a load request before failing. Minimum 1. |
| `ADMIN_API_SECRET` | none | Bearer secret required for `POST /api/v1/admin/surya` (manual load/unload), `PUT /api/v1/admin/ocr-settings`, and key management. The `GET` variants of both admin endpoints are read-only and need no secret. |

On a host without a GPU of at least 16 GB VRAM, a manual load returns `409`; in `cpu_only` mode both a manual load and any Marker conversion request (`pdf_marker`, `pdf_hybrid`, `pdf_marker_slm`) return `409`.

## Settings Reference

Every field of the `Settings` class in `config.py`, by environment variable. Names are case-insensitive; unknown variables are ignored. Variables that are read directly by Compose, the controller or other containers (not by `Settings`) are listed in the last table.

### Paths and storage

| Env var | Default | Meaning |
|---|---|---|
| `UPLOAD_FOLDER` | `data/uploads` | Upload directory |
| `OUTPUT_FOLDER` | `data/outputs` | Converted output directory |
| `STORAGE_BACKEND` | `local` | `local` or `s3` |
| `S3_BUCKET`, `S3_ENDPOINT_URL` | none | S3-compatible bucket and endpoint |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | none | S3 credentials (`SecretStr`) |
| `S3_REGION` | `us-east-1` | S3 region |
| `S3_SSE_ALGORITHM` | none | Server-side encryption algorithm, e.g. `AES256` |

### Redis, Celery and Sentinel

| Env var | Default | Meaning |
|---|---|---|
| `REDIS_METADATA_URL` | `redis://redis:6379/1` | Job metadata, capture sessions, API keys, rate-limit counters |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | Celery result backend |
| `REDIS_SENTINEL_HOSTS` | none | Sentinel hosts (enables Sentinel mode) |
| `REDIS_SENTINEL_SERVICE` | `mymaster` | Sentinel service name |
| `REDIS_SENTINEL_PASSWORD` | none | Sentinel password (`SecretStr`) |
| `REDIS_SENTINEL_DB_METADATA` / `REDIS_SENTINEL_DB_BROKER` | `1` / `0` | Sentinel DB indexes |
| `REDIS_TLS_CA_CERTS`, `REDIS_TLS_CERTFILE`, `REDIS_TLS_KEYFILE` | none | TLS material for `rediss://` URLs; set by `docker-compose.tls.yml` (CA only). See [CERTIFICATE_MANAGEMENT.md](CERTIFICATE_MANAGEMENT.md) |

### Web, session and limits

| Env var | Default | Meaning |
|---|---|---|
| `FLASK_DEBUG` | `false` | Flask debug mode; never enable in production |
| `MAX_CONTENT_LENGTH` | `209715200` (200 MB) | Maximum upload size in bytes |
| `MIN_FREE_SPACE` | `524288000` (500 MB) | Minimum free disk before uploads are refused (HTTP 507) |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `PERMANENT_SESSION_LIFETIME_DAYS` | `30` | Session lifetime |
| `BEHIND_PROXY` | `false` | Enable `ProxyFix` to trust proxy headers |
| `FLASK_LIMITER_DEFAULT_LIMITS` | `["1000 per day", "200 per hour"]` | JSON list of default rate limits |
| `FLASK_LIMITER_STORAGE_URI` | `REDIS_METADATA_URL` | Limiter storage |
| `CONVERT_RATE_LIMIT` | `200 per hour` | Rate limit for `/convert` and `/api/v1/convert` |
| `API_KEY_DEFAULT_TTL_DAYS` | `90` | Default lifetime of new API keys (override per key with `expires_in_days`) |
| `SOCKETIO_ASYNC_MODE` | `eventlet` | `eventlet`, `gevent`, `threading` or `fork` |
| `SOCKETIO_MESSAGE_QUEUE` | `REDIS_METADATA_URL` | SocketIO message queue |
| `SOCKETIO_CORS_ALLOWED_ORIGINS` | `*` | SocketIO CORS origins |
| `METRICS_PORT` | `9090` | Worker Prometheus metrics port |
| `MCP_SERVER_URL` | `http://mcp-server:8080/execute` | Worker to MCP endpoint |

### Conversion engines

| Env var | Default | Meaning |
|---|---|---|
| `MARKER_ENABLED` | `false` | Marker available on this worker (the GPU overlay sets `true`) |
| `BUILD_GPU` | `false` | Build/runtime GPU flag (`auto` is treated as `false`; `scripts/build.sh auto` detects the GPU) |
| `MAX_MARKER_PAGES` | `600` | Page limit for Marker jobs |
| `HYBRID_QUALITY_THRESHOLD` | `60` | Minimum quality score (0-100) Pandoc output needs in `pdf_hybrid`, else fall back to Marker |
| `EAGER_MARKER_WARMUP` | `false` | Build the Marker client artifact dict at worker start instead of on the first conversion |
| `MAX_SLM_CONTEXT` | `2000` | SLM context window (tokens) before sampling |
| `SLM_MODEL_PATH` | none | GGUF model path (default TinyLlama location when unset) |

### Browser capture

| Env var | Default | Meaning |
|---|---|---|
| `CAPTURE_SESSION_TTL` | `86400` | Capture session lifetime in seconds |
| `CAPTURE_LIST_SCOPE` | `owner` | `owner` scopes `GET /api/captures` to the caller's identities; `global` returns every capture (single-user self-host only) |
| `MAX_CAPTURE_PAGES` | `500` | Maximum pages per session |
| `CAPTURE_BATCH_SIZE` | `50` | Pages per processing batch |
| `CAPTURE_ALLOWED_ORIGINS` | `["chrome-extension://*", "moz-extension://*"]` | CORS origins for capture endpoints |

### Webhooks

| Env var | Default | Meaning |
|---|---|---|
| `WEBHOOK_REQUIRE_HTTPS` | `false` | Reject non-HTTPS webhook URLs |
| `WEBHOOK_URL_ALLOWLIST` / `WEBHOOK_URL_BLOCKLIST` | none | Extra SSRF allow/block entries |

### Secrets

| Env var | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | none, **required** | Flask session secret. The web app refuses to start without it, and in production (`FLASK_ENV=production`, the default) the placeholder values `change-me-in-production`, `insecure-default` and `dev-only` are rejected |
| `MASTER_ENCRYPTION_KEY` | none | Required in production; an ephemeral key is generated when `FLASK_ENV` is not `production` |
| `CELERY_SIGNING_KEY` | none | Celery task signing; required in production, ephemeral otherwise |
| `ADMIN_API_SECRET` | none | Bearer secret for `/api/v1/auth/keys` and the Surya admin endpoints. Admin endpoints return 503 while unset |
| `CLOUDFLARE_TUNNEL_TOKEN` | none | Optional tunnel token; only used by operator-managed `cloudflared` |

### Read outside `Settings`

| Env var | Used by | Meaning |
|---|---|---|
| `REDIS_PASSWORD` | `docker-compose.yml` | Redis `requirepass` and the password embedded in the Redis URLs |
| `FLASK_ENV` | `shared/secrets_manager.py` | `production` (default) enforces required secrets |
| `MCP_SECRET`, `MCP_ALLOWED_DOMAINS` | worker, `mcp-server` | MCP bearer token and navigable-domain allowlist (default `read.amazon.com,percipio.com`) |
| `SURYA_INFERENCE_URL` | worker | Inference server base URL, `http://surya-vlm:8000/v1` (the `/v1` is required) |
| `SURYA_INFERENCE_AUTOSTART` | worker | `false`: the deployment owns the server |
| `SURYA_MODEL`, `VLLM_GPU_MEMORY_UTILIZATION` | `surya-vlm` | Model (`datalab-to/surya-ocr-2`) and VRAM fraction (`0.85`) |
| `WORKER_CONCURRENCY` | worker | Celery concurrency (default `1`) |
| `DOCKER_HOST_URL`, `COMPOSE_PROJECT_NAME`, `RECONCILE_INTERVAL_SECONDS`, `CONTROLLER_HEARTBEAT_FILE` | `surya-controller` | Socket-proxy URL, Compose project used to find `surya-vlm`, reconcile interval, healthcheck heartbeat file |

## Overriding Settings

You can override any setting by:

1.  **Environment Variables**: Set an environment variable with the name given in the `validation_alias` of the `Field` (e.g., `UPLOAD_FOLDER=/new/path`).
2.  **`.env` File**: Create a `.env` file in the project root and define your settings there (e.g., `UPLOAD_FOLDER=/custom/uploads`).
3.  **Docker Compose**: Define environment variables directly in your `docker-compose.yml` file for specific services. These take precedence over defaults.

## Sensitive Information (Secrets)

DocuFlux uses `secrets_manager.py` to handle sensitive information (`SECRET_KEY`, `MASTER_ENCRYPTION_KEY`, `CELERY_SIGNING_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`) with a specific loading priority:

1.  **Docker Swarm Secrets**: If running in Docker Swarm, secrets files located in `/run/secrets/` will be prioritized.
2.  **Environment Variables**: If Docker Swarm secrets are not used, these values will be read from standard environment variables.
3.  **Dynamic Generation (Development)**: For `MASTER_ENCRYPTION_KEY` and `CELERY_SIGNING_KEY` whenever `FLASK_ENV` is not `production`, a key is ephemerally generated if not explicitly provided, improving developer convenience (job data does not survive a restart). In production (the default), both keys are *required*.

This ensures that sensitive data is handled securely and isolated from general configuration.

## Best Practices

*   **Never commit `.env` files** to version control, especially in production. Use `.env.example` as a template.
*   **Use Docker Swarm Secrets** or a dedicated secret management solution in production for sensitive data.
*   **Avoid hardcoding** configuration values directly in the application code. Always use the `settings` object.
*   **Review `config.py`** for available options and their default values.

By following this centralized approach, managing DocuFlux's configuration becomes more predictable, secure, and easier to maintain.
