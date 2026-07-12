import sys
import os
import subprocess
import time
import requests
import logging
from celery import Celery
from celery.signals import task_prerun, task_postrun
from flask_socketio import SocketIO

from config import settings
from settings_loader import load_settings
from redis_client import create_redis_client, create_sentinel_client, parse_sentinel_hosts
from job_metadata import update_job_metadata as _shared_update, get_job_metadata as _shared_get, fire_webhook as _shared_fire_webhook
from uuid_validation import validate_uuid
from pandoc_options import PANDOC_OPTIONS_SCHEMA, PDF_DEFAULTS, build_pandoc_cmd
from storage import create_storage_backend
from logging_config import configure_json_logging, set_job_context

# Story 3.5: shared JSON log format with the web tier (shared/logging_config.py).
configure_json_logging()


@task_prerun.connect
def _set_job_log_context(task_id=None, args=None, **_kwargs):
    """Correlate every log line for a task's execution with its job_id/task_id.

    Every task in this module takes job_id as its first positional arg, so
    this one signal handler covers all of them without touching each task.
    """
    job_id = args[0] if args else None
    set_job_context(job_id=job_id, task_id=task_id)


@task_postrun.connect
def _clear_job_log_context(**_kwargs):
    set_job_context()

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
    'tasks.convert_with_ocr': {'queue': 'default'},
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
from tasks.conversion import convert_document, convert_with_marker, convert_with_marker_slm, convert_with_hybrid, convert_with_ocr
from tasks.capture import analyze_screenshot_layout, agentic_page_turner, process_capture_batch, assemble_capture_session
from tasks.maintenance import cleanup_old_files, migrate_filesystem_jobs, update_metrics, sweep_orphaned_temp_files
from tasks.metadata import extract_slm_metadata, test_amazon_session, _sample_for_slm_context

# Re-export helpers so @patch('tasks.xxx') in tests still works
from tasks.conversion import (
    get_model_dict, model_dict, _check_pdf_page_limit, _run_marker,
    _save_marker_output, _cleanup_marker_memory, _slm_refine_markdown,
    _assess_pandoc_quality, _postprocess_tables, PageLimitExceeded,
)
from tasks.maintenance import (
    _get_disk_usage_percent, _get_directory_size, _job_retention_decision,
)

def _eager_marker_warmup():
    """Story 6.2: preload Marker models in this Celery worker process at
    startup instead of on the first PDF conversion.

    warmup.py (the health-check sidecar) runs as a *separate process* from
    this worker (see worker/entrypoint.sh — `python3 warmup.py &` then
    `exec celery ...`), so loading models there would warm the wrong
    process. This runs in the worker process itself, at import time — the
    same point every task will later call get_model_dict() from — so a
    real conversion never pays the model-load penalty on its first
    request. Opt-in and best-effort: a failure here just falls back to
    today's lazy-loading behavior, never blocks the worker from starting.
    The Redis key is left absent when the feature is off, so consumers
    should treat "missing" the same as "false" (cold/lazy).
    """
    if not app_settings.eager_marker_warmup:
        return
    try:
        logging.info("Story 6.2: eager Marker warmup starting...")
        conversion.get_model_dict()
        redis_client.set('marker:model_warm', 'true')
        logging.info("Story 6.2: eager Marker warmup complete.")
    except Exception as e:
        logging.warning(f"Story 6.2: eager Marker warmup failed, falling back to lazy loading: {e}")
        try:
            redis_client.set('marker:model_warm', 'false')
        except Exception:
            pass  # Redis unreachable — don't crash worker startup over a status flag


_eager_marker_warmup()

# Beat schedule
celery.conf.beat_schedule = {
    'cleanup-every-5-minutes': {
        'task': 'tasks.cleanup_old_files',
        'schedule': 120.0,
        'options': {'queue': 'default'},
    },
    'update-queue-metrics': {
        'task': 'tasks.update_metrics',
        'schedule': 120.0,
        'options': {'queue': 'default'},
    },
    'sweep-orphaned-temp-files': {
        'task': 'tasks.sweep_orphaned_temp_files',
        'schedule': 900.0,
        'options': {'queue': 'default'},
    },
}
