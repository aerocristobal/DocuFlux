from datetime import timedelta
from typing import Optional, List, Literal
import json

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralized configuration management for the DocuFlux application using Pydantic Settings.

    Loads settings from environment variables, .env files, and provides type conversions and validation.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  # Ignore extra environment variables not defined in the model
        case_sensitive=False,  # Environment variables are case-insensitive by default
    )

    # --- General Paths ---
    upload_folder: str = Field("data/uploads", env="UPLOAD_FOLDER")
    output_folder: str = Field("data/outputs", env="OUTPUT_FOLDER")

    # --- Redis/Celery URLs ---
    redis_metadata_url: str = Field("redis://redis:6379/1", env="REDIS_METADATA_URL")
    celery_broker_url: str = Field("redis://redis:6379/0", env="CELERY_BROKER_URL")
    celery_result_backend: str = Field("redis://redis:6379/0", env="CELERY_RESULT_BACKEND")

    # --- Flask/Session Settings ---
    flask_debug: bool = Field(False, env="FLASK_DEBUG")
    max_content_length: int = Field(100 * 1024 * 1024, env="MAX_CONTENT_LENGTH")  # 100MB default
    min_free_space: int = Field(500 * 1024 * 1024, env="MIN_FREE_SPACE")  # 500MB default
    session_cookie_secure: bool = Field(False, env="SESSION_COOKIE_SECURE")
    permanent_session_lifetime_days: int = Field(30, env="PERMANENT_SESSION_LIFETIME_DAYS")
    # Derived property for timedelta
    @property
    def permanent_session_lifetime(self) -> timedelta:
        return timedelta(days=self.permanent_session_lifetime_days)

    # --- Cloudflare ProxyFix Settings ---
    behind_proxy: bool = Field(False, env="BEHIND_PROXY")

    # --- Redis TLS Config ---
    redis_tls_ca_certs: Optional[str] = Field(None, env="REDIS_TLS_CA_CERTS")
    redis_tls_certfile: Optional[str] = Field(None, env="REDIS_TLS_CERTFILE")
    redis_tls_keyfile: Optional[str] = Field(None, env="REDIS_TLS_KEYFILE")

    # --- MCP Server URL ---
    mcp_server_url: str = Field("http://mcp-server:8080/execute", env="MCP_SERVER_URL")

    # --- Marker/SLM Specific ---
    max_marker_pages: int = Field(300, env="MAX_MARKER_PAGES")
    max_slm_context: int = Field(2000, env="MAX_SLM_CONTEXT")  # Example token limit
    slm_model_path: Optional[str] = Field(None, env="SLM_MODEL_PATH")  # No default, as it might be dynamically loaded
    marker_enabled: bool = Field(False, env="MARKER_ENABLED") # Default to False if not specified
    build_gpu: bool = Field(False, env="BUILD_GPU") # Default to False if not specified

    # --- Prometheus Metrics ---
    metrics_port: int = Field(9090, env="METRICS_PORT")

    # --- Flask-Limiter Defaults ---
    # Pydantic can parse JSON strings into Python types
    default_limits: List[str] = Field(["1000 per day", "200 per hour"], env="FLASK_LIMITER_DEFAULT_LIMITS")
    storage_uri: str = Field(None, env="FLASK_LIMITER_STORAGE_URI") # Defaults to REDIS_METADATA_URL if None, handled below in settings instance creation

    # --- Flask-SocketIO Settings ---
    socketio_async_mode: Literal["eventlet", "gevent", "threading", "fork"] = Field("eventlet", env="SOCKETIO_ASYNC_MODE")
    socketio_message_queue: Optional[str] = Field(None, env="SOCKETIO_MESSAGE_QUEUE") # Defaults to REDIS_METADATA_URL if None, handled below in settings instance creation
    socketio_cors_allowed_origins: str = Field("*", env="SOCKETIO_CORS_ALLOWED_ORIGINS")

    # --- Secrets (handled by secrets_manager or directly if available) ---
    # These are usually handled by secrets_manager, but defining them here ensures Pydantic validation
    # and allows direct loading from env if secrets_manager is bypassed or for testing.
    secret_key: Optional[SecretStr] = Field(None, env="SECRET_KEY")
    master_encryption_key: Optional[SecretStr] = Field(None, env="MASTER_ENCRYPTION_KEY")
    cloudflare_tunnel_token: Optional[SecretStr] = Field(None, env="CLOUDFLARE_TUNNEL_TOKEN")
    
# Create a singleton instance of settings
settings = Settings()

# Post-initialization logic for defaults that depend on other settings
if settings.storage_uri is None:
    settings.storage_uri = settings.redis_metadata_url
if settings.socketio_message_queue is None:
    settings.socketio_message_queue = settings.redis_metadata_url