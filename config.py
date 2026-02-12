import os
import ast
from datetime import timedelta

class Config:
    """
    Centralized configuration management for the DocuFlux application.

    Loads settings from environment variables with sensible defaults and handles type conversions.
    """

    # --- General Paths ---
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'data/uploads')
    OUTPUT_FOLDER = os.environ.get('OUTPUT_FOLDER', 'data/outputs')

    # --- Redis/Celery URLs ---
    REDIS_METADATA_URL = os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1')
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/0')
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

    # --- Flask/Session Settings ---
    FLASK_DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', str(100 * 1024 * 1024))) # 100MB default
    MIN_FREE_SPACE = int(os.environ.get('MIN_FREE_SPACE', str(500 * 1024 * 1024))) # 500MB default
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
    PERMANENT_SESSION_LIFETIME = timedelta(days=int(os.environ.get('PERMANENT_SESSION_LIFETIME_DAYS', '30')))

    # --- Cloudflare ProxyFix Settings ---
    BEHIND_PROXY = os.environ.get('BEHIND_PROXY', 'false').lower() == 'true'

    # --- Redis TLS Config ---
    REDIS_TLS_CA_CERTS = os.environ.get('REDIS_TLS_CA_CERTS')
    REDIS_TLS_CERTFILE = os.environ.get('REDIS_TLS_CERTFILE')
    REDIS_TLS_KEYFILE = os.environ.get('REDIS_TLS_KEYFILE')

    # --- MCP Server URL ---
    MCP_SERVER_URL = os.environ.get('MCP_SERVER_URL', 'http://mcp-server:8080/execute')

    # --- Marker/SLM Specific ---
    MAX_MARKER_PAGES = int(os.environ.get('MAX_MARKER_PAGES', '300'))
    MAX_SLM_CONTEXT = int(os.environ.get('MAX_SLM_CONTEXT', '2000')) # Example token limit
    SLM_MODEL_PATH = os.environ.get("SLM_MODEL_PATH") # No default, as it might be dynamically loaded

    # --- Prometheus Metrics ---
    METRICS_PORT = int(os.environ.get('METRICS_PORT', '9090'))

    # --- Flask-Limiter Defaults ---
    # Using a literal_eval for complex defaults like lists or tuples
    DEFAULT_LIMITS_STR = os.environ.get('FLASK_LIMITER_DEFAULT_LIMITS', '["1000 per day", "200 per hour"]')
    DEFAULT_LIMITS = ast.literal_eval(DEFAULT_LIMITS_STR) if isinstance(DEFAULT_LIMITS_STR, str) else DEFAULT_LIMITS_STR
    STORAGE_URI = os.environ.get('FLASK_LIMITER_STORAGE_URI', REDIS_METADATA_URL) # Defaults to REDIS_METADATA_URL

    # --- Flask-SocketIO Settings ---
    SOCKETIO_ASYNC_MODE = os.environ.get('SOCKETIO_ASYNC_MODE', 'eventlet')
    SOCKETIO_MESSAGE_QUEUE = os.environ.get('SOCKETIO_MESSAGE_QUEUE', REDIS_METADATA_URL) # Defaults to REDIS_METADATA_URL
    SOCKETIO_CORS_ALLOWED_ORIGINS = os.environ.get('SOCKETIO_CORS_ALLOWED_ORIGINS', '*')
