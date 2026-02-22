from datetime import timedelta
from typing import Optional, List, Literal
import json

from pydantic import Field, SecretStr, validator
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
    upload_folder: str = Field("data/uploads", validation_alias="UPLOAD_FOLDER")
    output_folder: str = Field("data/outputs", validation_alias="OUTPUT_FOLDER")

    # --- Redis/Celery URLs ---
    redis_metadata_url: str = Field("redis://redis:6379/1", validation_alias="REDIS_METADATA_URL")
    celery_broker_url: str = Field("redis://redis:6379/0", validation_alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field("redis://redis:6379/0", validation_alias="CELERY_RESULT_BACKEND")

    # --- Flask/Session Settings ---
    flask_debug: bool = Field(False, validation_alias="FLASK_DEBUG")
    max_content_length: int = Field(100 * 1024 * 1024, validation_alias="MAX_CONTENT_LENGTH")  # 100MB default
    min_free_space: int = Field(500 * 1024 * 1024, validation_alias="MIN_FREE_SPACE")  # 500MB default
    session_cookie_secure: bool = Field(False, validation_alias="SESSION_COOKIE_SECURE")
    permanent_session_lifetime_days: int = Field(30, validation_alias="PERMANENT_SESSION_LIFETIME_DAYS")
    # Derived property for timedelta
    @property
    def permanent_session_lifetime(self) -> timedelta:
        return timedelta(days=self.permanent_session_lifetime_days)

    # --- Cloudflare ProxyFix Settings ---
    behind_proxy: bool = Field(False, validation_alias="BEHIND_PROXY")

    # --- Redis TLS Config ---
    redis_tls_ca_certs: Optional[str] = Field(None, validation_alias="REDIS_TLS_CA_CERTS")
    redis_tls_certfile: Optional[str] = Field(None, validation_alias="REDIS_TLS_CERTFILE")
    redis_tls_keyfile: Optional[str] = Field(None, validation_alias="REDIS_TLS_KEYFILE")

    # --- MCP Server URL ---
    mcp_server_url: str = Field("http://mcp-server:8080/execute", validation_alias="MCP_SERVER_URL")

    # --- Marker/SLM Specific ---
    max_marker_pages: int = Field(300, validation_alias="MAX_MARKER_PAGES")
    max_slm_context: int = Field(2000, validation_alias="MAX_SLM_CONTEXT")  # Example token limit
    slm_model_path: Optional[str] = Field(None, validation_alias="SLM_MODEL_PATH")  # No default, as it might be dynamically loaded
    marker_enabled: bool = Field(False, validation_alias="MARKER_ENABLED") # Default to False if not specified
    build_gpu: bool = Field(False, validation_alias="BUILD_GPU") # Default to False if not specified

    @validator('build_gpu', pre=True)
    def build_gpu_auto(cls, v):
        if isinstance(v, str) and v.lower() == 'auto':
            return False
        return v


    # --- Browser Extension Capture Settings ---
    capture_session_ttl: int = Field(86400, validation_alias="CAPTURE_SESSION_TTL")
    max_capture_pages: int = Field(500, validation_alias="MAX_CAPTURE_PAGES")
    capture_allowed_origins: List[str] = Field(
        ["chrome-extension://*", "moz-extension://*"],
        validation_alias="CAPTURE_ALLOWED_ORIGINS"
    )

    # --- Prometheus Metrics ---
    metrics_port: int = Field(9090, validation_alias="METRICS_PORT")

    # --- Flask-Limiter Defaults ---
    # Pydantic can parse JSON strings into Python types
    default_limits: List[str] = Field(["1000 per day", "200 per hour"], validation_alias="FLASK_LIMITER_DEFAULT_LIMITS")
    storage_uri: Optional[str] = Field(None, validation_alias="FLASK_LIMITER_STORAGE_URI") # Defaults to REDIS_METADATA_URL if None, handled below in settings instance creation

    # --- Flask-SocketIO Settings ---
    socketio_async_mode: Literal["eventlet", "gevent", "threading", "fork"] = Field("eventlet", validation_alias="SOCKETIO_ASYNC_MODE")
    socketio_message_queue: Optional[str] = Field(None, validation_alias="SOCKETIO_MESSAGE_QUEUE") # Defaults to REDIS_METADATA_URL if None, handled below in settings instance creation
    socketio_cors_allowed_origins: Optional[str] = Field(None, validation_alias="SOCKETIO_CORS_ALLOWED_ORIGINS")

    # --- Secrets (handled by secrets_manager or directly if available) ---
    # These are usually handled by secrets_manager, but defining them here ensures Pydantic validation
    # and allows direct loading from env if secrets_manager is bypassed or for testing.
    secret_key: Optional[SecretStr] = Field(None, validation_alias="SECRET_KEY")
    master_encryption_key: Optional[SecretStr] = Field(None, validation_alias="MASTER_ENCRYPTION_KEY")
    cloudflare_tunnel_token: Optional[SecretStr] = Field(None, validation_alias="CLOUDFLARE_TUNNEL_TOKEN")
    celery_signing_key: Optional[SecretStr] = Field(None, validation_alias="CELERY_SIGNING_KEY")

settings = Settings()

# Post-initialization logic for defaults that depend on other settings
if settings.storage_uri is None:
    settings.storage_uri = settings.redis_metadata_url
if settings.socketio_message_queue is None:
    settings.socketio_message_queue = settings.redis_metadata_url