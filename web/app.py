import sys
import eventlet
eventlet.monkey_patch()

import os
import uuid
import time
import shutil
import logging
import zipfile
import io
import json
from flask import Flask, render_template, request, send_from_directory, jsonify, session, send_file, g, Response
from urllib.parse import urlparse
try:
    from prometheus_flask_exporter import PrometheusMetrics
    _has_prometheus = True
except ImportError:
    _has_prometheus = False
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from celery import Celery
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_socketio import SocketIO
from flask_cors import CORS
from flask_compress import Compress
from datetime import datetime

from config import settings
from settings_loader import load_settings
from encryption import EncryptionService
from key_manager import create_key_manager
from formats import FORMATS, detect_format_from_extension
from pandoc_options import PANDOC_OPTIONS_SCHEMA, validate_pandoc_options
from storage import create_storage_backend
import tempfile

# Configure Structured Logging with request-ID correlation
class _RequestIdFilter(logging.Filter):
    """Inject the current Flask request_id into every log record."""
    def filter(self, record):
        try:
            record.request_id = getattr(g, 'request_id', '-')
        except RuntimeError:
            record.request_id = '-'
        return True

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(
    '{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s",'
    ' "request_id": "%(request_id)s", "message": "%(message)s"}'
))
handler.addFilter(_RequestIdFilter())
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

# Load secrets and settings
try:
    app_settings = load_settings(settings)
except ValueError as e:
    logging.error(f"Failed to load secrets: {e}")
    sys.exit(1)

app = Flask(__name__)
if not app_settings.secret_key:
    logging.error("FATAL: SECRET_KEY is not set. Refusing to start.")
    sys.exit(1)
app.secret_key = app_settings.secret_key.get_secret_value() if hasattr(app_settings.secret_key, 'get_secret_value') else app_settings.secret_key

if app_settings.behind_proxy:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1, x_proto=1, x_host=1, x_prefix=1
    )
    logging.info("ProxyFix middleware enabled - trusting proxy headers")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=app_settings.permanent_session_lifetime,
    SESSION_COOKIE_SECURE=app_settings.session_cookie_secure,
    UPLOAD_FOLDER=app_settings.upload_folder,
    OUTPUT_FOLDER=app_settings.output_folder,
    MAX_CONTENT_LENGTH=app_settings.max_content_length
)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=app_settings.default_limits,
    storage_uri=app_settings.storage_uri,
    strategy="fixed-window",
)
csrf = CSRFProtect(app)
socketio = SocketIO(
    app,
    async_mode=app_settings.socketio_async_mode,
    message_queue=app_settings.socketio_message_queue,
    cors_allowed_origins=app_settings.socketio_cors_allowed_origins
)
CORS(app, resources={r"/api/v1/capture/*": {
    "origins": app_settings.capture_allowed_origins,
    "supports_credentials": False,
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Content-Type", "X-Client-ID"]
}})
Compress(app)
if _has_prometheus:
    metrics = PrometheusMetrics(app)
    metrics.info('app_info', 'DocuFlux web service', version='1.0')

UPLOAD_FOLDER = app_settings.upload_folder
OUTPUT_FOLDER = app_settings.output_folder
MIN_FREE_SPACE = app_settings.min_free_space

storage = create_storage_backend(app_settings)
storage.ensure_directories()

@app.errorhandler(413)
def request_entity_too_large(error):
    # 413 fires before @before_request, so g.request_id may not exist
    rid = getattr(g, 'request_id', None) or request.headers.get('X-Request-ID', str(uuid.uuid4())[:8])
    return jsonify({
        'error': 'File too large',
        'message': f'Maximum size is {app_settings.max_content_length / (1024 * 1024):.0f}MB.',
        'request_id': rid,
    }), 413

@app.before_request
def _assign_request_id():
    """Generate or propagate a correlation ID for structured log tracing."""
    g.request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())[:8]


@app.after_request
def _echo_request_id(response):
    """Return the correlation ID in the response so callers can correlate logs."""
    response.headers['X-Request-ID'] = getattr(g, 'request_id', '-')
    return response


@app.after_request
def add_security_headers(response):
    # Epic 22.3: Updated CSP to support both ws:// and wss:// WebSocket connections
    # When behind proxy (HTTPS), Socket.IO auto-upgrades to wss://
    csp = (
        "default-src 'self' https://esm.run https://fonts.googleapis.com https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline' https://esm.run https://cdn.jsdelivr.net https://cdn.socket.io; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self' https://esm.run https://cdn.jsdelivr.net;"
    )
    response.headers['Content-Security-Policy'] = csp
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(), payment=()'

    # Epic 22.4: Enable HSTS when running behind HTTPS proxy
    if app_settings.behind_proxy or not app.debug:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    return response

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        'error': 'Rate limit exceeded',
        'message': str(e.description),
        'request_id': getattr(g, 'request_id', '-'),
    }), 429


@app.errorhandler(500)
def internal_server_error(error):
    rid = getattr(g, 'request_id', '-')
    logging.exception("Unhandled exception [request_id=%s]", rid)
    return jsonify({'error': 'Internal server error', 'request_id': rid}), 500


@app.errorhandler(Exception)
def handle_unhandled_exception(error):
    rid = getattr(g, 'request_id', '-')
    logging.exception("Unhandled exception [request_id=%s]: %s", rid, error)
    return jsonify({'error': 'Internal server error', 'request_id': rid}), 500

from redis_client import create_redis_client, create_sentinel_client
from job_metadata import update_job_metadata as _shared_update, get_job_metadata as _shared_get

# Metadata Redis client (DB 1) — Sentinel-aware when REDIS_SENTINEL_HOSTS is set
if app_settings.redis_sentinel_hosts:
    _sentinel_pw = app_settings.redis_sentinel_password
    _sentinel_pw_val = (_sentinel_pw.get_secret_value() if hasattr(_sentinel_pw, 'get_secret_value') else _sentinel_pw) if _sentinel_pw else None
    redis_client = create_sentinel_client(
        sentinel_hosts_str=app_settings.redis_sentinel_hosts,
        service_name=app_settings.redis_sentinel_service,
        db=app_settings.redis_sentinel_db_metadata,
        password=_sentinel_pw_val,
        socket_connect_timeout=5,
        socket_timeout=10,
    )
else:
    redis_client = create_redis_client(
        url=app_settings.redis_metadata_url,
        app_settings=app_settings,
        max_connections=50,
        socket_connect_timeout=5,
        socket_timeout=10,
    )

# Celery configuration — Sentinel-aware when REDIS_SENTINEL_HOSTS is set
if app_settings.redis_sentinel_hosts:
    from redis_client import parse_sentinel_hosts
    _sentinels = parse_sentinel_hosts(app_settings.redis_sentinel_hosts)
    _broker_urls = ';'.join(f'sentinel://{h}:{p}' for h, p in _sentinels)
    _broker_url = f'{_broker_urls}/{app_settings.redis_sentinel_db_broker}'
    celery = Celery('tasks', broker=_broker_url, backend=_broker_url)
    _transport_opts = {'master_name': app_settings.redis_sentinel_service}
    if _sentinel_pw_val:
        _transport_opts['sentinel_kwargs'] = {'password': _sentinel_pw_val}
    celery.conf.broker_transport_options = _transport_opts
    celery.conf.result_backend_transport_options = _transport_opts
else:
    celery = Celery(
        'tasks',
        broker=app_settings.celery_broker_url,
        backend=app_settings.celery_result_backend
    )
celery.conf.task_routes = {
    'tasks.convert_document': {'queue': 'default'},
    'tasks.convert_with_marker': {'queue': 'gpu'},
    'tasks.convert_with_marker_slm': {'queue': 'gpu'},
    'tasks.convert_with_hybrid': {'queue': 'gpu'},
    'tasks.assemble_capture_session': {'queue': 'default'},
}

# Epic 24.2: Celery Task Message Encryption
# Enable message signing for task integrity and authentication
_cskey = app_settings.celery_signing_key
celery_signing_key = (_cskey.get_secret_value() if hasattr(_cskey, 'get_secret_value') else _cskey) if _cskey else None
if celery_signing_key:
    celery.conf.task_serializer = 'auth'
    celery.conf.result_serializer = 'json'
    celery.conf.accept_content = ['auth', 'application/json']
    celery.conf.security_key = celery_signing_key
    celery.conf.security_certificate = None  # Using symmetric key, not certificates
    celery.conf.security_digest = 'sha256'
    logging.info("Celery message signing enabled (task_serializer=auth)")
else:
    # No signing key: use JSON serializer (matches worker default, prevents pickle mismatch)
    celery.conf.update(
        task_serializer='json',
        result_serializer='json',
        accept_content=['json'],
    )
    logging.warning("Celery signing key not set - messages not authenticated")

# Epic 23.3: Initialize encryption components (lazily)
_encryption_service = None
_key_manager = None

def get_encryption_service():
    """Get or create encryption service instance."""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service

def get_key_manager():
    """Get or create key manager instance."""
    global _key_manager
    if _key_manager is None:
        _key_manager = create_key_manager(redis_client)
    return _key_manager

def decrypt_file_to_temp(encrypted_path, job_id):
    """
    Decrypt an encrypted file to a temporary location.

    Epic 23.3: Transparent decryption on download

    Args:
        encrypted_path: Path to encrypted file
        job_id: Job identifier for key retrieval

    Returns:
        Path to decrypted temporary file, or None if decryption failed
    """
    try:
        encryption_service = get_encryption_service()
        key_manager = get_key_manager()

        # Get DEK for this job
        dek = key_manager.get_job_key(job_id)
        if dek is None:
            logging.error(f"No decryption key found for job {job_id}")
            return None

        # Create temporary file for decrypted content
        temp_fd, temp_path = tempfile.mkstemp(suffix=os.path.splitext(encrypted_path)[1])
        os.close(temp_fd)

        # Decrypt file
        encryption_service.decrypt_file(
            input_path=encrypted_path,
            output_path=temp_path,
            key=dek,
            associated_data=job_id
        )

        logging.info(f"Decrypted file for download: {encrypted_path} -> {temp_path}")
        return temp_path

    except Exception as e:
        logging.error(f"Decryption failed for {encrypted_path}: {e}")
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.remove(temp_path)
        return None

def check_disk_space():
    """
    Checks if there is sufficient free disk space.

    Returns:
        bool: True if free disk space is greater than or equal to MIN_FREE_SPACE,
              False otherwise. Returns True for non-local backends or on error.
    """
    usage = storage.disk_usage()
    if usage is None:
        return True  # S3 or other backends without disk limits
    try:
        total, used, free = usage
        return free >= MIN_FREE_SPACE
    except Exception:
        return True

def update_job_metadata(job_id, updates):
    """Write updates to a job's Redis metadata hash and broadcast a WebSocket event."""
    _shared_update(redis_client, socketio, job_id, updates)

def get_job_metadata(job_id):
    """Retrieve job metadata from Redis."""
    return _shared_get(redis_client, job_id)

# ============================================================================
# API Key Authentication
# ============================================================================

import secrets
from functools import wraps

APIKEY_PREFIX = 'apikey:'


def _generate_api_key():
    """Return a new random API key with 'dk_' prefix."""
    return 'dk_' + secrets.token_urlsafe(32)


def _validate_api_key(key):
    """Return key metadata dict if valid, None otherwise."""
    if not key or not key.startswith('dk_'):
        return None
    try:
        data = redis_client.hgetall(f"{APIKEY_PREFIX}{key}")
        return {k.decode() if isinstance(k, bytes) else k:
                v.decode() if isinstance(v, bytes) else v
                for k, v in data.items()} if data else None
    except Exception:
        return None


def require_api_key(f):
    """Decorator: require valid X-API-Key header, return 401/403 otherwise."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key', '').strip()
        if not key:
            return jsonify({'error': 'API key required. Provide X-API-Key header.'}), 401
        if not _validate_api_key(key):
            return jsonify({'error': 'Invalid or revoked API key'}), 403
        return f(*args, **kwargs)
    return decorated


from uuid_validation import validate_uuid as is_valid_uuid  # used by web.routes via _app_mod

# Register route blueprints
from web.routes import register_blueprints
register_blueprints(app)


if __name__ == '__main__':
    debug_mode = app_settings.flask_debug
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
