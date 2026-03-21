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
from redis_client import create_redis_client, create_sentinel_client, parse_sentinel_hosts
from job_metadata import update_job_metadata as _shared_update, get_job_metadata as _shared_get, fire_webhook as _shared_fire_webhook
from uuid_validation import validate_uuid
from pandoc_options import PANDOC_OPTIONS_SCHEMA, PDF_DEFAULTS, build_pandoc_cmd
from storage import create_storage_backend

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

storage = create_storage_backend(app_settings)

# Celery configuration — Sentinel-aware when REDIS_SENTINEL_HOSTS is set
if app_settings.redis_sentinel_hosts:
    _sentinels = parse_sentinel_hosts(app_settings.redis_sentinel_hosts)
    _broker_urls = ';'.join(f'sentinel://{h}:{p}' for h, p in _sentinels)
    _broker_url = f'{_broker_urls}/{app_settings.redis_sentinel_db_broker}'
    _sentinel_pw = app_settings.redis_sentinel_password
    _sentinel_pw_val = (_sentinel_pw.get_secret_value() if hasattr(_sentinel_pw, 'get_secret_value') else _sentinel_pw) if _sentinel_pw else None
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

# Epic 7.3: Dead letter queue — capture permanently failed tasks
from celery.signals import task_failure as _task_failure_signal
import json as _json

@_task_failure_signal.connect
def _handle_task_failure(sender=None, task_id=None, exception=None,
                         args=None, kwargs=None, traceback=None,
                         einfo=None, **kw):
    """Capture failed tasks to a dead letter queue in Redis."""
    try:
        entry = {
            'task_id': task_id,
            'task_name': sender.name if sender else 'unknown',
            'args': _json.dumps(args) if args else '[]',
            'kwargs': _json.dumps(kwargs) if kwargs else '{}',
            'exception': str(exception)[:1000],
            'failed_at': str(time.time()),
        }
        redis_client.lpush('dlq:tasks', _json.dumps(entry))
        redis_client.ltrim('dlq:tasks', 0, 999)
        from metrics import dlq_total
        dlq_total.inc()
        logging.info("Task %s (%s) added to DLQ", task_id, entry['task_name'])
    except Exception as e:
        logging.error("Failed to write to DLQ: %s", e)

# Redis metadata client (DB 1) — Sentinel-aware when REDIS_SENTINEL_HOSTS is set
if app_settings.redis_sentinel_hosts:
    redis_client = create_sentinel_client(
        sentinel_hosts_str=app_settings.redis_sentinel_hosts,
        service_name=app_settings.redis_sentinel_service,
        db=app_settings.redis_sentinel_db_metadata,
        password=_sentinel_pw_val if app_settings.redis_sentinel_hosts else None,
        socket_connect_timeout=5,
        socket_timeout=10,
    )
else:
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
    mcp_secret = os.environ.get('MCP_SECRET', '')
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {mcp_secret}'}
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
from tasks.maintenance import cleanup_old_files, migrate_filesystem_jobs, update_metrics
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
