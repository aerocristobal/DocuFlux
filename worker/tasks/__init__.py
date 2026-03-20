import sys
import os
import subprocess
import time
import requests
import logging
from celery import Celery
from flask_socketio import SocketIO

from config import settings
from settings_loader import load_settings
from redis_client import create_redis_client
from job_metadata import update_job_metadata as _shared_update, get_job_metadata as _shared_get, fire_webhook as _shared_fire_webhook
from uuid_validation import validate_uuid
from pandoc_options import PANDOC_OPTIONS_SCHEMA, PDF_DEFAULTS, build_pandoc_cmd

# Configure Structured Logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s", "message": "%(message)s"}'))
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

# Load secrets and settings
try:
    app_settings = load_settings(settings)
except ValueError as e:
    logging.error(f"Failed to load secrets: {e}")
    sys.exit(1)

UPLOAD_FOLDER = app_settings.upload_folder
OUTPUT_FOLDER = app_settings.output_folder
MCP_SERVER_URL = app_settings.mcp_server_url

celery = Celery(
    'tasks',
    broker=app_settings.celery_broker_url,
    backend=app_settings.celery_result_backend
)
celery.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    result_expires=3600,
    worker_max_tasks_per_child=50,
)

# Fallback task routing — web routes set queue= explicitly, but this catches programmatic dispatches
celery.conf.task_routes = {
    'tasks.convert_with_marker': {'queue': 'gpu'},
    'tasks.convert_with_marker_slm': {'queue': 'gpu'},
    'tasks.convert_with_hybrid': {'queue': 'gpu'},
}

# Redis metadata client (DB 1) — supports TLS via rediss:// URL + cert env vars
redis_client = create_redis_client(
    url=app_settings.redis_metadata_url,
    app_settings=app_settings,
    max_connections=20,
    socket_connect_timeout=5,
    socket_timeout=10,
)

# WebSocket emitter (standalone, no Flask app)
socketio = SocketIO(message_queue=app_settings.socketio_message_queue)


is_valid_uuid = validate_uuid  # alias for backward compat


def update_job_metadata(job_id, data):
    """Update job metadata hash in Redis and broadcast a WebSocket event."""
    _shared_update(redis_client, socketio, job_id, data)


def get_job_metadata(job_id):
    """Retrieve job metadata from Redis."""
    return _shared_get(redis_client, job_id)


def fire_webhook(job_id, status, extra=None):
    """Fire webhook POST if a URL is registered for this job."""
    _shared_fire_webhook(redis_client, job_id, status, extra)


def call_mcp_server(action, args):
    """Helper function to send commands to the MCP server."""
    payload = {'action': action, 'args': args}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(MCP_SERVER_URL, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling MCP server for action '{action}': {e}")
        raise


# Import sub-modules to trigger @celery.task registration
from tasks import conversion  # noqa: E402, F401
from tasks import capture     # noqa: E402, F401
from tasks import maintenance  # noqa: E402, F401
from tasks import metadata    # noqa: E402, F401

# Re-export task functions for backward compatibility
from tasks.conversion import convert_document, convert_with_marker, convert_with_marker_slm, convert_with_hybrid
from tasks.capture import analyze_screenshot_layout, agentic_page_turner, process_capture_batch, assemble_capture_session
from tasks.maintenance import cleanup_old_files, update_metrics
from tasks.metadata import extract_slm_metadata, test_amazon_session

# Re-export helpers so @patch('tasks.xxx') in tests still works
from tasks.conversion import (
    get_model_dict, model_dict, _check_pdf_page_limit, _run_marker,
    _save_marker_output, _cleanup_marker_memory, _slm_refine_markdown,
    _assess_pandoc_quality, PageLimitExceeded,
)
from tasks.maintenance import (
    _get_disk_usage_percent, _get_directory_size, _job_retention_decision,
)

# Beat schedule
celery.conf.beat_schedule = {
    'cleanup-every-5-minutes': {
        'task': 'tasks.cleanup_old_files',
        'schedule': 120.0,
    },
    'update-queue-metrics': {
        'task': 'tasks.update_metrics',
        'schedule': 120.0,
    },
}
