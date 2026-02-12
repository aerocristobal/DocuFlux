# Configuration Management

DocuFlux uses [Pydantic Settings](https://pydantic-settings.readthedocs.io/) for managing its configuration. This approach provides a robust, type-safe, and hierarchical way to handle application settings across different environments.

## How Configuration is Loaded

Pydantic Settings loads configuration parameters from several sources, with earlier sources taking precedence over later ones:

1.  **Environment Variables**: The primary method for configuring DocuFlux, especially in production or containerized environments (e.g., Docker, Kubernetes).
2.  **`.env` files**: For local development, settings can be defined in a `.env` file in the project root. This file is automatically loaded by Pydantic Settings.
3.  **`secrets_manager.py`**: For sensitive values like `SECRET_KEY`, `MASTER_ENCRYPTION_KEY`, and `CLOUDFLARE_TUNNEL_TOKEN`, the `secrets_manager.py` module first attempts to load these from Docker Swarm secrets (files in `/run/secrets/`). If not found there, it falls back to environment variables.
4.  **Default Values**: Each setting has a default value defined directly in the `Settings` class in `config.py`.

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
    upload_folder: str = Field("data/uploads", env="UPLOAD_FOLDER")
    output_folder: str = Field("data/outputs", env="OUTPUT_FOLDER")

    # --- Redis/Celery URLs ---
    redis_metadata_url: str = Field("redis://redis:6379/1", env="REDIS_METADATA_URL")

    # --- Flask/Session Settings ---
    flask_debug: bool = Field(False, env="FLASK_DEBUG")
    secret_key: Optional[SecretStr] = Field(None, env="SECRET_KEY")

    # ... (other settings) ...
```

-   Each field in the `Settings` class corresponds to a configuration parameter.
-   The `Field` function is used to specify:
    -   A default value if the environment variable is not set.
    -   The `env` argument (e.g., `env="UPLOAD_FOLDER"`) explicitly links the Python attribute to an environment variable name.
-   `SecretStr` is used for sensitive fields (like `secret_key`) to prevent accidental logging or exposure.

## Overriding Settings

You can override any setting by:

1.  **Environment Variables**: Set an environment variable with the exact name specified in the `env` argument of the `Field` (e.g., `UPLOAD_FOLDER=/new/path`).
2.  **`.env` File**: Create a `.env` file in the project root and define your settings there (e.g., `UPLOAD_FOLDER=/custom/uploads`).
3.  **Docker Compose**: Define environment variables directly in your `docker-compose.yml` file for specific services. These take precedence over defaults.

## Sensitive Information (Secrets)

DocuFlux uses `secrets_manager.py` to handle sensitive information (e.g., `SECRET_KEY`, `MASTER_ENCRYPTION_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`) with a specific loading priority:

1.  **Docker Swarm Secrets**: If running in Docker Swarm, secrets files located in `/run/secrets/` will be prioritized.
2.  **Environment Variables**: If Docker Swarm secrets are not used, these values will be read from standard environment variables.
3.  **Dynamic Generation (Development)**: For `MASTER_ENCRYPTION_KEY` in non-production environments (`FLASK_ENV=testing` or `development`), a key will be ephemerally generated if not explicitly provided, improving developer convenience. In production, this key is *required*.

This ensures that sensitive data is handled securely and isolated from general configuration.

## Best Practices

*   **Never commit `.env` files** to version control, especially in production. Use `.env.example` as a template.
*   **Use Docker Swarm Secrets** or a dedicated secret management solution in production for sensitive data.
*   **Avoid hardcoding** configuration values directly in the application code. Always use the `settings` object.
*   **Review `config.py`** for available options and their default values.

By following this centralized approach, managing DocuFlux's configuration becomes more predictable, secure, and easier to maintain.
