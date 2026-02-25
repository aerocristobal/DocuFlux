import sys
import os
import subprocess
import time
import shutil
import redis
import requests
import logging
from urllib.parse import urlparse
from celery import Celery
from celery.schedules import crontab
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename
from warmup import get_slm_model  # Epic 26: SLM model getter
from PIL import Image  # Epic 28: For image processing in analyze_screenshot_layout

from config import settings
from secrets_manager import load_all_secrets
from encryption import EncryptionService
from key_manager import create_key_manager
from metrics import (
    worker_tasks_active,
    conversion_total,
    conversion_failures_total,
    conversion_duration_seconds,
    update_queue_metrics,
)

# Configure Structured Logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s", "message": "%(message)s"}'))
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

# Load secrets and settings
try:
    loaded_secrets = load_all_secrets()
    settings_override_data = {
        k.lower(): v for k, v in loaded_secrets.items() if v is not None
    }
    app_settings = settings.model_copy(update=settings_override_data)
    
    if app_settings.storage_uri is None:
        app_settings.storage_uri = app_settings.redis_metadata_url
    if app_settings.socketio_message_queue is None:
        app_settings.socketio_message_queue = app_settings.redis_metadata_url

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
)

# Redis metadata client (DB 1)
redis_client = redis.Redis.from_url(
    app_settings.redis_metadata_url,
    max_connections=20,
    decode_responses=True
)

# WebSocket emitter (standalone, no Flask app)
socketio = SocketIO(message_queue=app_settings.socketio_message_queue)


def is_valid_uuid(val):
    """Return True if val is a valid UUID string, False otherwise."""
    try:
        import uuid
        uuid.UUID(str(val))
        return True
    except (ValueError, ImportError):
        return False


def update_job_metadata(job_id, data):
    """Update job metadata hash in Redis and broadcast a WebSocket event."""
    redis_client.hset(f"job:{job_id}", mapping=data)
    try:
        socketio.emit('job_update', {'id': job_id, **data}, namespace='/')
    except Exception as e:
        logging.error(f"WebSocket emit failed for job {job_id}: {e}")


def get_job_metadata(job_id):
    """Retrieve job metadata from Redis."""
    try:
        metadata = redis_client.hgetall(f"job:{job_id}")
        if not metadata:
            return None
        return {k.decode('utf-8') if isinstance(k, bytes) else k:
                v.decode('utf-8') if isinstance(v, bytes) else v
                for k, v in metadata.items()}
    except Exception as e:
        logging.error(f"Error retrieving metadata for job {job_id}: {e}")
        return None


def call_mcp_server(action, args):
    """
    Helper function to send commands to the MCP server.
    """
    payload = {'action': action, 'args': args}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(MCP_SERVER_URL, json=payload, headers=headers, timeout=60)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling MCP server for action '{action}': {e}")
        raise

@celery.task(
    name='tasks.convert_document',
    time_limit=600,           # Hard limit: 10 minutes
    soft_time_limit=540,      # Soft limit: 9 minutes
    acks_late=True,           # Re-queue if worker dies
    reject_on_worker_lost=True
)
def convert_document(job_id, input_filename, output_filename, from_format, to_format):
    """Convert a document using Pandoc.

    Args:
        job_id: UUID identifying this conversion job.
        input_filename: Name of the uploaded source file.
        output_filename: Desired name for the converted output file.
        from_format: Pandoc source format key (e.g. 'docx', 'markdown').
        to_format: Pandoc target format key (e.g. 'html', 'pdf').

    Side Effects:
        Updates Redis job metadata with progress and final status.
        Writes output to data/outputs/{job_id}/.
        Emits WebSocket events to connected clients via update_job_metadata.
        Increments/decrements Prometheus worker_tasks_active gauge.

    Returns:
        dict: {'status': 'success', 'output_file': filename} on success.

    Raises:
        FileNotFoundError: If input file does not exist.
        Exception: Wraps Pandoc subprocess errors and timeouts.
    """
    # Epic 21.5: Track active tasks
    worker_tasks_active.inc()
    start_time = time.time()

    try:
        if not is_valid_uuid(job_id):
            logging.error(f"Invalid job_id received: {job_id}")
            return {"status": "error", "message": "Invalid job ID"}

        # Abort if startup recovery already marked this job as failed.
        current_status = redis_client.hget(f"job:{job_id}", 'status')
        if current_status in ('FAILURE', 'REVOKED'):
            logging.warning(f"Skipping re-queued task for already-{current_status} job {job_id}")
            worker_tasks_active.dec()
            return {"status": "skipped", "reason": current_status}

        safe_job_id = secure_filename(job_id)
        safe_input_filename = secure_filename(input_filename)
        safe_output_filename = secure_filename(output_filename)

        input_path = os.path.join(UPLOAD_FOLDER, safe_job_id, safe_input_filename)
        output_path = os.path.join(OUTPUT_FOLDER, safe_job_id, safe_output_filename)

        logging.info(f"Starting conversion for job {job_id}: {from_format} -> {to_format}")
        update_job_metadata(job_id, {
            'status': 'PROCESSING',
            'started_at': str(time.time()),
            'progress': '10'
        })
    except Exception:
        worker_tasks_active.dec()
        raise
    
    if not os.path.exists(input_path):
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': 'Input file missing'})
        raise FileNotFoundError(f"Input file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    update_job_metadata(job_id, {'progress': '20'})

    cmd = [
        'pandoc',
        '-f', from_format,
        '-t', to_format if to_format != 'pdf' else 'pdf',
        input_path,
        '-o', output_path
    ]

    # Add PDF-specific options for CJK support
    if to_format == 'pdf':
        cmd.extend([
            '--pdf-engine=xelatex',
            '--variable', 'mainfont=Noto Sans CJK SC',
            '--variable', 'CJKmainfont=Noto Sans CJK SC',
            '--variable', 'monofont=DejaVu Sans Mono',
            '--variable', 'geometry:margin=1in',
        ])

    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=500)
        logging.info(f"Conversion successful: {output_path}")

        # Epic 30.2: file_count=1 for single Pandoc output (enables list_jobs() cache)
        update_job_metadata(job_id, {
            'status': 'SUCCESS',
            'completed_at': str(time.time()),
            'progress': '100',
            'encrypted': 'false',
            'file_count': '1'
        })
        redis_client.expire(f"job:{job_id}", 7200)

        # Epic 21.5: Record success metrics
        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='success').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        # Epic 21.4: Memory cleanup after Pandoc task
        import gc
        gc.collect()

        return {"status": "success", "output_file": os.path.basename(output_path)}
    except subprocess.TimeoutExpired:
        error_msg = "Conversion timed out after 500 seconds"
        logging.error(f"Timeout for job {job_id}: {error_msg}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': error_msg, 'progress': '0'})
        redis_client.expire(f"job:{job_id}", 600)

        # Epic 21.5: Record failure metrics
        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='timeout').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        raise Exception(error_msg)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr or e.stdout or "Unknown error"
        logging.error(f"Pandoc error for job {job_id}: {error_msg}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(error_msg)[:500], 'progress': '0'})
        redis_client.expire(f"job:{job_id}", 600)

        # Epic 21.5: Record failure metrics
        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='pandoc_error').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        raise Exception(f"Pandoc failed: {error_msg}")
    except Exception as e:
        logging.error(f"Unexpected error for job {job_id}: {str(e)}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500], 'progress': '0'})
        redis_client.expire(f"job:{job_id}", 600)

        # Epic 21.5: Record failure metrics
        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='unknown').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        raise

model_dict = None


def get_model_dict():
    """Lazily load and cache Marker AI model artifacts.

    Returns:
        dict: Model artifact dictionary for use with PdfConverter.
    """
    global model_dict
    if model_dict is None:
        # Set env vars for marker to optimize for 16GB VRAM
        os.environ["INFERENCE_RAM"] = "16"
        from marker.models import create_model_dict
        logging.info("Initializing Marker models...")
        model_dict = create_model_dict()
        logging.info("Marker models initialized.")
    return model_dict


def _check_pdf_page_limit(job_id, input_path, max_pages):
    """Check that a PDF does not exceed the page limit for Marker AI.

    Args:
        job_id: Job UUID for metadata updates on rejection.
        input_path: Path to the PDF file.
        max_pages: Maximum allowed page count.

    Returns:
        dict with {'status': 'error', 'message': ...} if limit exceeded, else None.
    """
    try:
        import pypdfium2 as pdfium
        pdf_doc = pdfium.PdfDocument(input_path)
        page_count = len(pdf_doc)
        pdf_doc.close()
        if page_count > max_pages:
            error_msg = (
                f"PDF has {page_count} pages, which exceeds the {max_pages}-page "
                f"limit for AI conversion. Split the document into smaller parts."
            )
            update_job_metadata(job_id, {
                'status': 'FAILURE', 'completed_at': str(time.time()),
                'error': error_msg, 'progress': '0'
            })
            return {"status": "error", "message": error_msg}
        logging.info(f"PDF page count: {page_count} (limit: {max_pages})")
    except Exception as e:
        logging.warning(f"Could not check PDF page count: {e}")
    return None


def _run_marker(input_path, options):
    """Load Marker models and run PDF conversion.

    Args:
        input_path: Path to the input PDF.
        options: Marker config dict (e.g. {'force_ocr': True}).

    Returns:
        tuple: (converter, rendered) Marker objects.
    """
    from marker.converters.pdf import PdfConverter
    artifacts = get_model_dict()
    converter = PdfConverter(artifact_dict=artifacts, config=options)
    logging.info(f"Running Marker conversion on {input_path}")
    rendered = converter(input_path)
    return converter, rendered


def _save_marker_output(rendered, output_path, images_dir):
    """Extract text and images from a Marker result and write to disk.

    Args:
        rendered: Marker rendered output object.
        output_path: Path to write the output Markdown file.
        images_dir: Directory to save extracted images.

    Returns:
        tuple: (text, images, saved_images_count, file_count)
            file_count = output.md + metadata.json + N images
    """
    import json
    from marker.output import text_from_rendered

    text, _, images = text_from_rendered(rendered)

    saved_images_count = 0
    for filename, image in images.items():
        image.save(os.path.join(images_dir, filename))
        saved_images_count += 1
        text = text.replace(f"({filename})", f"(images/{filename})")
    logging.info(f"Saved {saved_images_count} images to {images_dir}")

    with open(output_path, "w", encoding='utf-8') as f:
        f.write(text)

    metadata_path = os.path.join(os.path.dirname(output_path), "metadata.json")
    with open(metadata_path, "w", encoding='utf-8') as f:
        json.dump(rendered.metadata, f, indent=2, default=str)

    # output.md + metadata.json + N images
    file_count = 2 + saved_images_count
    return text, images, saved_images_count, file_count


def _cleanup_marker_memory(*objects):
    """Free GPU/CPU memory after a Marker task.

    Args:
        *objects: Python objects to delete before running gc.collect().
    """
    import gc
    for obj in objects:
        try:
            del obj
        except Exception:
            pass
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            mem_freed = torch.cuda.memory_reserved(0) - torch.cuda.memory_allocated(0)
            logging.info(f"Memory cleanup complete. GPU memory freed: {mem_freed / 1e9:.2f} GB")
        else:
            logging.info("Memory cleanup complete (CPU mode)")
    except Exception as e:
        logging.warning(f"Memory cleanup failed: {e}")


@celery.task(
    name='tasks.convert_with_marker',
    bind=True,                # Enable access to self (for retry)
    time_limit=1200,          # Marker is slower (GPU/CPU heavy) - 20 mins
    soft_time_limit=1140,     # 19 mins
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3
)
def convert_with_marker(self, job_id, input_filename, output_filename, from_format, to_format, options=None):
    """Convert a PDF to Markdown using Marker AI (GPU-accelerated deep learning).

    Args:
        self: Celery task instance (bind=True).
        job_id: UUID identifying this conversion job.
        input_filename: Name of the uploaded PDF file.
        output_filename: Desired name for the output Markdown file.
        from_format: Source format ('pdf_marker').
        to_format: Target format (typically 'markdown').
        options: Optional dict with Marker config, e.g. {'force_ocr': True, 'use_llm': False}.

    Side Effects:
        Updates Redis job metadata with progress and final status.
        Writes output Markdown + images to data/outputs/{job_id}/.
        Triggers extract_slm_metadata task on success.
        Performs GPU/CPU memory cleanup after each run.

    Returns:
        dict: {'status': 'success', 'output_file': filename} on success.

    Raises:
        FileNotFoundError: If input file does not exist.
        Exception: Wraps Marker conversion errors; supports Celery retry up to max_retries.
    """
    worker_tasks_active.inc()
    start_time = time.time()

    if not is_valid_uuid(job_id):
        logging.error(f"Invalid job_id received: {job_id}")
        worker_tasks_active.dec()
        return {"status": "error", "message": "Invalid job ID"}

    # Abort if startup recovery already marked this job as failed.
    # reject_on_worker_lost re-queues the task, but recover_orphaned_jobs()
    # marks it FAILURE on startup — skip to break the crash-loop.
    current_status = redis_client.hget(f"job:{job_id}", 'status')
    if current_status in ('FAILURE', 'REVOKED'):
        logging.warning(f"Skipping re-queued task for already-{current_status} job {job_id}")
        worker_tasks_active.dec()
        return {"status": "skipped", "reason": current_status}

    safe_job_id = secure_filename(job_id)
    input_path = os.path.join(UPLOAD_FOLDER, safe_job_id, secure_filename(input_filename))
    output_dir = os.path.join(OUTPUT_FOLDER, safe_job_id)
    output_path = os.path.join(output_dir, secure_filename(output_filename))

    logging.info(f"Starting Marker conversion for job {job_id} (Attempt {self.request.retries + 1}) with options: {options}")
    update_job_metadata(job_id, {'status': 'PROCESSING', 'started_at': str(time.time()), 'progress': '5'})

    if not os.path.exists(input_path):
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': 'Input file missing'})
        raise FileNotFoundError(f"Input file not found: {input_path}")

    page_limit_error = _check_pdf_page_limit(job_id, input_path, app_settings.max_marker_pages)
    if page_limit_error:
        worker_tasks_active.dec()
        return page_limit_error

    os.makedirs(output_dir, exist_ok=True)
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    converter = rendered = text = images = None
    try:
        update_job_metadata(job_id, {'progress': '15'})
        converter, rendered = _run_marker(input_path, options or {})

        update_job_metadata(job_id, {'progress': '80'})
        text, images, _, file_count = _save_marker_output(rendered, output_path, images_dir)

        update_job_metadata(job_id, {'progress': '90', 'file_count': str(file_count)})
        logging.info(f"Marker conversion successful: {output_path}")

        update_job_metadata(job_id, {
            'status': 'SUCCESS', 'completed_at': str(time.time()),
            'progress': '100', 'encrypted': 'false'
        })
        redis_client.expire(f"job:{job_id}", 7200)
        extract_slm_metadata.delay(job_id, output_path)

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='success').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        _cleanup_marker_memory(converter, rendered, text, images)
        return {"status": "success", "output_file": os.path.basename(output_path)}

    except Exception as e:
        logging.error(f"Marker error for job {job_id}: {e}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500], 'progress': '0'})
        redis_client.expire(f"job:{job_id}", 600)

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='marker_error').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        _cleanup_marker_memory(converter, rendered, text, images)
        raise


def _get_disk_usage_percent(path='/app/data'):
    """
    Get disk usage percentage for the given path.

    Epic 21.6: Disk usage monitoring for intelligent cleanup
    """
    try:
        total, used, free = shutil.disk_usage(path)
        return (used / total) * 100
    except Exception as e:
        logging.error(f"Error getting disk usage: {e}")
        return 0.0


def _get_directory_size(path):
    """
    Get total size of a directory in bytes.

    Epic 21.6: File size calculation for prioritized cleanup
    """
    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                if os.path.exists(filepath):
                    total_size += os.path.getsize(filepath)
    except Exception as e:
        logging.error(f"Error calculating directory size for {path}: {e}")
    return total_size


def _job_retention_decision(
    job_id, meta, now, upload_dir, output_dir,
    retention_failure, retention_downloaded, retention_no_download, retention_orphan,
    emergency_cleanup
):
    """Decide whether a job should be deleted and return (should_delete, reason, priority).

    Args:
        job_id: Job UUID string.
        meta: Job metadata dict from Redis, or None if missing.
        now: Current timestamp (float).
        upload_dir: Path to uploads root.
        output_dir: Path to outputs root.
        retention_failure: Seconds to retain failed jobs.
        retention_downloaded: Seconds to retain downloaded/viewed jobs.
        retention_no_download: Seconds to retain completed-but-not-downloaded jobs.
        retention_orphan: Seconds to retain jobs with no Redis metadata.
        emergency_cleanup: If True, mark all jobs for immediate deletion.

    Returns:
        tuple: (should_delete: bool, reason: str, priority: int)
            Higher priority = deleted first during space-constrained runs.
    """
    should_delete = False
    reason = ""
    priority = 0

    if meta:
        status = meta.get('status')
        completed_at = float(meta['completed_at']) if meta.get('completed_at') else None
        downloaded_at = float(meta['downloaded_at']) if meta.get('downloaded_at') else None
        last_viewed = float(meta['last_viewed']) if meta.get('last_viewed') else None
        started_at = float(meta['started_at']) if meta.get('started_at') else None

        if status == 'FAILURE':
            if completed_at and now > completed_at + retention_failure:
                should_delete, reason, priority = True, "Failed job expired (5m)", 10
        elif status == 'SUCCESS':
            reference_time = last_viewed or downloaded_at or completed_at
            if downloaded_at or last_viewed:
                if now > reference_time + retention_downloaded:
                    should_delete, reason, priority = True, "Downloaded/viewed job expired (10m since last access)", 5
            elif completed_at and now > completed_at + retention_no_download:
                should_delete, reason, priority = True, "Completed job (not downloaded) expired (1h)", 3

        if not completed_at and started_at and now > started_at + 7200:
            should_delete, reason, priority = True, "Stale processing job (2h)", 8
    else:
        check_path = os.path.join(upload_dir, job_id)
        if not os.path.exists(check_path):
            check_path = os.path.join(output_dir, job_id)
        if os.path.exists(check_path):
            mtime = os.path.getmtime(check_path)
            if now > mtime + retention_orphan:
                should_delete, reason, priority = True, "Orphaned job expired (1h fallback)", 7

    if emergency_cleanup:
        should_delete = True
        priority = 15
        reason = f"EMERGENCY: {reason}" if reason else "EMERGENCY: Disk >95% full"

    return should_delete, reason, priority


@celery.task(name='tasks.cleanup_old_files')
def cleanup_old_files():
    """
    Performs intelligent cleanup of old job files based on retention policies, size, and recency.

    This task is designed to manage disk space by:
    - Prioritizing the deletion of larger files.
    - Preserving recently viewed or downloaded files.
    - Implementing emergency cleanup procedures when disk usage exceeds 95%.

    It processes job files from both UPLOAD_FOLDER and OUTPUT_FOLDER.
    Jobs are categorized by status (FAILURE, SUCCESS, PENDING) and retention
    periods are applied accordingly. Orphaned jobs (files without metadata)
    are also handled.

    The cleanup process sorts deletion candidates by priority (e.g., failed jobs,
    then un-downloaded successful jobs) and then by size (largest first),
    stopping if disk usage returns to a healthy level.

    Returns:
        None: This function does not return any value directly. It logs its actions.
    """
    RETENTION_SUCCESS_NO_DOWNLOAD = 3600  # 1 hour
    RETENTION_SUCCESS_DOWNLOADED = 600    # 10 minutes
    RETENTION_FAILURE = 300               # 5 minutes
    RETENTION_ORPHAN = 3600               # 1 hour (fallback)

    upload_dir = os.environ.get('UPLOAD_FOLDER', 'data/uploads')
    output_dir = os.environ.get('OUTPUT_FOLDER', 'data/outputs')

    job_ids = set()
    if os.path.exists(upload_dir):
        job_ids.update(os.listdir(upload_dir))
    if os.path.exists(output_dir):
        job_ids.update(os.listdir(output_dir))

    disk_usage_percent = _get_disk_usage_percent(upload_dir)
    emergency_cleanup = disk_usage_percent > 95

    if emergency_cleanup:
        logging.warning(f"EMERGENCY CLEANUP: Disk usage at {disk_usage_percent:.1f}%")
    elif disk_usage_percent > 80:
        logging.info(f"Aggressive cleanup: Disk usage at {disk_usage_percent:.1f}%")

    logging.info(f"Running cleanup. Found {len(job_ids)} jobs on disk.")

    now = time.time()
    deletion_candidates = []

    for job_id in job_ids:
        if not is_valid_uuid(job_id):
            continue

        meta = get_job_metadata(job_id)
        should_delete, reason, priority = _job_retention_decision(
            job_id, meta, now, upload_dir, output_dir,
            RETENTION_FAILURE, RETENTION_SUCCESS_DOWNLOADED,
            RETENTION_SUCCESS_NO_DOWNLOAD, RETENTION_ORPHAN,
            emergency_cleanup
        )

        if should_delete:
            upload_path = os.path.join(upload_dir, job_id)
            output_path = os.path.join(output_dir, job_id)
            total_size = (
                (_get_directory_size(upload_path) if os.path.exists(upload_path) else 0) +
                (_get_directory_size(output_path) if os.path.exists(output_path) else 0)
            )
            deletion_candidates.append({
                'job_id': job_id, 'reason': reason,
                'priority': priority, 'size_bytes': total_size,
                'key': f"job:{job_id}"
            })

    deletion_candidates.sort(key=lambda x: (x['priority'], x['size_bytes']), reverse=True)
    logging.info(f"Found {len(deletion_candidates)} jobs eligible for deletion")

    total_freed = 0
    for candidate in deletion_candidates:
        job_id = candidate['job_id']
        size_mb = candidate['size_bytes'] / (1024 * 1024)
        logging.info(f"Deleting job {job_id} ({size_mb:.2f} MB). Reason: {candidate['reason']}")

        for base in [upload_dir, output_dir]:
            p = os.path.join(base, job_id)
            if os.path.exists(p):
                try:
                    shutil.rmtree(p)
                    total_freed += candidate['size_bytes']
                except Exception as e:
                    logging.error(f"Error deleting {p}: {e}")

        redis_client.delete(candidate['key'])


    logging.info(f"Cleanup complete. Freed {total_freed / (1024 * 1024):.2f} MB")

    # Clean up orphaned capture session keys (safety net; Redis TTL handles normal expiry)
    try:
        for key in redis_client.scan_iter("capture:session:*"):
            if redis_client.ttl(key) == -1:
                redis_client.delete(key)
                logging.info(f"Deleted orphaned capture session key: {key}")
    except Exception as e:
        logging.warning(f"Error cleaning up capture session keys: {e}")


@celery.task(name='tasks.update_metrics')
def update_metrics():
    """
    Periodic task to update queue metrics.

    This task runs every 30 seconds to keep queue depth metrics current.
    Epic 21.5: Prometheus Metrics
    """
    try:
        update_queue_metrics(redis_client)
    except Exception as e:
        logging.error(f"Error updating metrics: {e}")



@celery.task(
    name='tasks.extract_slm_metadata',
    time_limit=300, # 5 minutes for SLM inference
    soft_time_limit=240, # 4 minutes
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=1 # Only one retry as SLM inference can be resource intensive
)
def extract_slm_metadata(job_id, markdown_file_path):
    """
    Extracts semantic metadata (title, tags, summary) from Markdown content
    using a local Small Language Model (SLM).
    """
    logging.info(f"Starting SLM metadata extraction for job {job_id} from {markdown_file_path}")
    update_job_metadata(job_id, {'slm_status': 'PROCESSING', 'slm_started_at': str(time.time())})

    try:
        slm = get_slm_model()
        if slm is None:
            logging.warning(f"SLM model not loaded for job {job_id}. Skipping metadata extraction.")
            update_job_metadata(job_id, {'slm_status': 'SKIPPED', 'slm_error': 'SLM model not available'})
            return {"status": "skipped", "message": "SLM model not available"}

        if not os.path.exists(markdown_file_path):
            logging.error(f"Markdown file not found for SLM extraction: {markdown_file_path}")
            update_job_metadata(job_id, {'slm_status': 'FAILURE', 'slm_error': 'Markdown file missing'})
            return {"status": "failure", "message": "Markdown file missing"}

        with open(markdown_file_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()

        # Truncate content if too long for SLM (adjust based on model context window)
        MAX_SLM_CONTEXT = app_settings.max_slm_context # Example token limit, adjust as needed
        if len(markdown_content.split()) > MAX_SLM_CONTEXT:
            markdown_content = " ".join(markdown_content.split()[:MAX_SLM_CONTEXT])
            logging.warning(f"Truncated markdown content for SLM inference for job {job_id}")

        prompt = (
            "You are a helpful assistant that extracts structured information from documents. "
            "Given the following Markdown content, extract a concise title, relevant tags (up to 5), "
            "and a brief summary (1-2 sentences). "
            "Respond ONLY with a JSON object. Ensure the output is valid JSON.\n\n"
            "Markdown Content:\n"
            f"{markdown_content}\n\n"
            "JSON Output Structure:\n"
            "```json\n"
            "{\n"
            '  "title": "Concise document title",\n'
            '  "tags": ["tag1", "tag2"],\n'
            '  "summary": "Brief summary of the document."\n'
            "}\n"
            "```\n"
            "JSON Output:\n"
        )

        logging.info(f"Sending prompt to SLM for job {job_id}...")
        
        # Use the loaded slm_model to perform inference
        output = slm.create_completion(
            prompt,
            max_tokens=512, # Max tokens for the completion
            temperature=0.1,
            top_p=0.9,
            stop=["```"], # Stop generation when it encounters ```
        )
        
        generated_text = output['choices'][0]['text'].strip()
        logging.info(f"SLM generated raw text for job {job_id}:\n{generated_text}")

        json_start = generated_text.find('{')
        json_end = generated_text.rfind('}')
        if json_start != -1 and json_end != -1:
            json_str = generated_text[json_start:json_end+1]
        else:
            raise ValueError("No valid JSON found in SLM output.")

        metadata = json.loads(json_str)

        if not all(k in metadata for k in ["title", "tags", "summary"]):
            raise ValueError("Invalid metadata structure returned by SLM.")
        if not isinstance(metadata["tags"], list):
            metadata["tags"] = [str(metadata["tags"])] # Ensure tags is a list

        update_job_metadata(job_id, {
            'slm_status': 'SUCCESS',
            'slm_completed_at': str(time.time()),
            'slm_title': metadata.get('title', ''),
            'slm_tags': json.dumps(metadata.get('tags', [])), # Store as JSON string in Redis
            'slm_summary': metadata.get('summary', '')
        })
        logging.info(f"SLM metadata extracted and stored for job {job_id}")
        return {"status": "success", "metadata": metadata}

    except Exception as e:
        error_msg = f"SLM metadata extraction failed for job {job_id}: {str(e)}"
        logging.error(error_msg)
        update_job_metadata(job_id, {'slm_status': 'FAILURE', 'slm_error': error_msg})
        raise


@celery.task(
    name='tasks.test_amazon_session',
    time_limit=120, # 2 minutes for browser interaction
    soft_time_limit=90, # 1.5 minutes
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0 # No retries, session state might be invalid
)
def test_amazon_session(job_id, encrypted_session_file_path):
    """
    Tests an Amazon session by launching Playwright with the provided session state
    and verifying access to read.amazon.com.
    """
    logging.info(f"Starting Amazon session test for job {job_id}")
    update_job_metadata(job_id, {'amazon_session_status': 'TESTING', 'amazon_session_started_at': str(time.time())})

    decrypted_session_file = None
    try:
        encryption_service = get_encryption_service()
        key_manager = get_key_manager()

        # Decrypt the session file
        session_dek = key_manager.get_job_key(job_id)
        if not session_dek:
            raise ValueError(f"No decryption key found for job {job_id}")

        decrypted_session_file = encrypted_session_file_path.replace(".enc", ".json")
        encryption_service.decrypt_file(
            input_path=encrypted_session_file_path,
            output_path=decrypted_session_file,
            key=session_dek,
            associated_data=job_id
        )

        with open(decrypted_session_file, 'r', encoding='utf-8') as f:
            storage_state_json = json.load(f) # MCP server expects JSON object

        logging.info(f"Decrypted session file for job {job_id}. Calling MCP server...")

        # Call MCP server to launch browser with session state and navigate
        # Target URL to check for login (e.g., a specific book or the library page)
        target_url = "https://read.amazon.com/kp/notebook"
        mcp_response = call_mcp_server(
            'create_context_and_goto',
            {'url': target_url, 'storageState': storage_state_json}
        )

        if mcp_response.get('success'):
            final_url = mcp_response.get('url', '')
            parsed_url = urlparse(final_url)
            hostname = parsed_url.hostname

            if hostname and (
                hostname in ('signin.amazon.com', 'kindle.amazon.com') or
                hostname.endswith('.signin.amazon.com') or
                hostname.endswith('.kindle.amazon.com')
            ):
                logging.warning(f"Amazon session for job {job_id} is invalid: redirected to {hostname}.")
                update_job_metadata(job_id, {
                    'amazon_session_status': 'INVALID',
                    'amazon_session_completed_at': str(time.time()),
                    'amazon_session_error': f'Redirected to {hostname}'
                })
                return {"status": "invalid", "message": f"Session invalid: redirected to {hostname}."}
            else:
                logging.info(f"Amazon session for job {job_id} is VALID: successfully accessed {target_url}.")
                update_job_metadata(job_id, {
                    'amazon_session_status': 'VALID',
                    'amazon_session_completed_at': str(time.time()),
                    'amazon_session_error': ''
                })
                return {"status": "valid", "message": "Session is valid."}
        else:
            error_msg = mcp_response.get('error', 'Unknown MCP error')
            logging.error(f"MCP server failed for job {job_id}: {error_msg}")
            update_job_metadata(job_id, {
                'amazon_session_status': 'FAILURE',
                'amazon_session_completed_at': str(time.time()),
                'amazon_session_error': f"MCP server error: {error_msg}"
            })
            return {"status": "failure", "message": f"MCP server error: {error_msg}"}

    except Exception as e:
        error_msg = f"Amazon session test failed for job {job_id}: {str(e)}"
        logging.error(error_msg)
        update_job_metadata(job_id, {'amazon_session_status': 'FAILURE', 'amazon_session_error': error_msg})
        raise
    finally:
        # Purge sensitive session data
        if os.path.exists(encrypted_session_file_path):
            os.remove(encrypted_session_file_path)
            logging.info(f"Purged encrypted session file: {encrypted_session_file_path}")
        if decrypted_session_file and os.path.exists(decrypted_session_file):
            os.remove(decrypted_session_file)
            logging.info(f"Purged decrypted session file: {decrypted_session_file}")
        key_manager.delete_job_key(job_id) # Delete DEK from Redis
        logging.info(f"Purged session key for job {job_id}")


@celery.task(
    name='tasks.analyze_screenshot_layout',
    time_limit=300, # 5 minutes for layout analysis
    soft_time_limit=240, # 4 minutes
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0
)
def analyze_screenshot_layout(job_id, url, storage_state_json=None):
    """
    Analyzes the layout of a screenshot from a given URL, identifying text and visual regions.
    Uses MCP server to capture the screenshot and Marker (or a placeholder) for analysis.
    """
    logging.info(f"Starting screenshot layout analysis for job {job_id} on URL: {url}")
    update_job_metadata(job_id, {'layout_analysis_status': 'PROCESSING', 'layout_analysis_started_at': str(time.time())})

    temp_screenshot_path = None
    try:
        # Create a temporary path for the screenshot in the shared volume
        job_output_dir = os.path.join(OUTPUT_FOLDER, job_id)
        os.makedirs(job_output_dir, exist_ok=True)
        temp_screenshot_path = os.path.join(job_output_dir, f"screenshot_{job_id}.png")

        logging.info(f"Capturing screenshot for job {job_id} to {temp_screenshot_path}...")
        script = [
            {'action': 'goto', 'args': {'url': url}},
            {'action': 'screenshot', 'args': {'path': temp_screenshot_path}},
        ]
        mcp_args = {'script': script}
        if storage_state_json:
            mcp_args['storageState'] = storage_state_json
        mcp_response = call_mcp_server('execute_script', mcp_args)
        if not mcp_response.get('success'):
            raise Exception(f"Failed to navigate and capture screenshot: {mcp_response.get('error', 'Unknown error')}")
        results = mcp_response.get('script_execution_results', [])
        screenshot_result = next((r for r in results if r.get('action') == 'screenshot'), None)
        if not screenshot_result or not screenshot_result.get('success'):
            raise Exception(f"Screenshot step failed in MCP script")
        logging.info(f"Screenshot captured and saved to {temp_screenshot_path}")


        # --- Layout Analysis using Marker (Placeholder) ---
        # Assuming Marker provides an API for image-based layout analysis.
        # If not, this part would need a dedicated image processing library.
        # For now, we'll simulate output.
        logging.info(f"Performing layout analysis on {temp_screenshot_path} using Marker...")

        # In a real scenario, this would involve Marker's internal segmenter or similar.
        # We will simulate output based on image dimensions.
        with Image.open(temp_screenshot_path) as img:
            width, height = img.size

        # Simulated layout regions
        layout_results = {
            "text_regions": [
                {"bbox": [0, 0, width, height * 0.7], "content": "Simulated text content from OCR"},
                {"bbox": [0, height * 0.75, width * 0.5, height], "content": "More simulated text"}
            ],
            "visual_regions": [
                {"bbox": [width * 0.7, 0, width, height * 0.3], "type": "chart", "description": "Simulated chart region"},
                {"bbox": [width * 0.55, height * 0.75, width, height], "type": "image", "description": "Simulated image region"}
            ]
        }
        
        logging.info(f"Layout analysis completed for job {job_id}. Results: {layout_results}")

        update_job_metadata(job_id, {
            'layout_analysis_status': 'SUCCESS',
            'layout_analysis_completed_at': str(time.time()),
            'layout_results': json.dumps(layout_results)
        })
        return {"status": "success", "layout_results": layout_results}

    except Exception as e:
        error_msg = f"Screenshot layout analysis failed for job {job_id}: {str(e)}"
        logging.error(error_msg)
        update_job_metadata(job_id, {'layout_analysis_status': 'FAILURE', 'layout_analysis_error': error_msg})
        raise
    finally:
        if temp_screenshot_path and os.path.exists(temp_screenshot_path):
            os.remove(temp_screenshot_path)
            logging.info(f"Cleaned up temporary screenshot: {temp_screenshot_path}")


@celery.task(
    name='tasks.agentic_page_turner',
    time_limit=1800, # 30 minutes for multi-page extraction
    soft_time_limit=1740, # 29 minutes
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0
)
def agentic_page_turner(job_id, start_url, next_button_selector,
                        end_condition_selector=None, max_pages=10,
                        storage_state_json=None):
    """
    Performs agentic page turning and extraction using the MCP server.
    """
    logging.info(f"Starting agentic page turner for job {job_id} on URL: {start_url}")
    update_job_metadata(job_id, {'page_turner_status': 'PROCESSING', 'page_turner_started_at': str(time.time())})

    current_page_num = 0
    extracted_data = []
    try:
        # Initial script to navigate to the start URL
        script = [
            {'action': 'goto', 'args': {'url': start_url}},
        ]
        if storage_state_json:
            script[0]['args']['storageState'] = storage_state_json

        # Execute initial navigation
        mcp_response = call_mcp_server('execute_script', {'script': script})
        if not mcp_response.get('success'):
            raise Exception(f"Initial navigation failed: {mcp_response.get('error', 'Unknown error')}")
        
        # Get content from the first page
        page_content = mcp_response['script_execution_results'][0].get('content', '')
        extracted_data.append({'page_num': current_page_num + 1, 'content': page_content})
        logging.info(f"Page {current_page_num + 1} extracted.")
        update_job_metadata(job_id, {'page_turner_progress': f"{current_page_num + 1}/{max_pages}", 'current_page_url': start_url})


        while current_page_num < max_pages:
            current_page_num += 1
            logging.info(f"Attempting to turn page {current_page_num + 1} for job {job_id}")

            # Script for page turning: detect next button, click, wait for navigation, extract content
            page_turn_script = [
                {'action': 'wait_for_selector', 'args': {'selector': next_button_selector, 'timeout': 10000}},
                {'action': 'click_element', 'args': {'selector': next_button_selector}},
                {'action': 'get_content'} # Get content of the new page
            ]

            # Execute page turning script
            mcp_response = call_mcp_server('execute_script', {'script': page_turn_script, 'storageState': storage_state_json})

            if not mcp_response.get('success'):
                logging.warning(f"Failed to turn page {current_page_num + 1}: {mcp_response.get('error', 'Unknown error')}. Ending extraction.")
                break # Exit loop if cannot turn page

            # Check for end condition
            if end_condition_selector:
                check_end_script = [
                    {'action': 'get_element_bounding_box', 'args': {'selector': end_condition_selector}}
                ]
                end_response = call_mcp_server('execute_script', {'script': check_end_script, 'storageState': storage_state_json})
                if end_response.get('success') and end_response['script_execution_results'][0]['bbox']:
                    logging.info(f"End condition met: '{end_condition_selector}' found on page {current_page_num + 1}.")
                    break # Exit loop

            page_content = mcp_response['script_execution_results'][-1].get('content', '')
            extracted_data.append({'page_num': current_page_num + 1, 'content': page_content})
            logging.info(f"Page {current_page_num + 1} extracted.")
            update_job_metadata(job_id, {'page_turner_progress': f"{current_page_num + 1}/{max_pages}"})

        update_job_metadata(job_id, {
            'page_turner_status': 'SUCCESS',
            'page_turner_completed_at': str(time.time()),
            'extracted_pages': json.dumps(extracted_data) # Store extracted content
        })
        logging.info(f"Agentic page turning completed for job {job_id}. Extracted {len(extracted_data)} pages.")
        return {"status": "success", "extracted_pages_count": len(extracted_data)}

    except Exception as e:
        error_msg = f"Agentic page turning failed for job {job_id}: {str(e)}"
        logging.error(error_msg)
        update_job_metadata(job_id, {'page_turner_status': 'FAILURE', 'page_turner_error': error_msg})
        raise


@celery.task(
    name='tasks.process_capture_batch',
    time_limit=900,
    soft_time_limit=840,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_capture_batch(session_id, job_id, batch_index, page_start, page_end):
    """
    Process a batch of captured pages through Marker OCR.

    Reads pages[page_start:page_end] from Redis, converts screenshots to PDF,
    runs Marker, and writes batch.md + images to the batch staging directory.
    Image filenames use a globally unique counter to avoid collisions on merge.
    """
    import base64
    import io as io_module
    import json as json_module
    import gc
    import re as re_module

    batch_key = f"capture:batch:{session_id}:{batch_index}"
    session_key = f"capture:session:{session_id}"
    batch_dir = os.path.join(OUTPUT_FOLDER, job_id, 'batches', f'batch_{batch_index}')
    batch_images_dir = os.path.join(batch_dir, 'images')
    os.makedirs(batch_images_dir, exist_ok=True)

    logging.info(f"Processing capture batch {batch_index}: session={session_id}, pages={page_start}-{page_end}")

    redis_client.hset(batch_key, mapping={
        'status': 'processing',
        'started_at': str(time.time()),
    })

    try:
        pages_raw = redis_client.lrange(f"capture:session:{session_id}:pages", page_start, page_end - 1)
        pages = [json_module.loads(p) for p in pages_raw]
        pages.sort(key=lambda p: p.get('page_hint', 0))

        # Collect one screenshot per page
        ocr_images_b64 = []
        for page in pages:
            page_imgs = page.get('images', [])
            chosen = next((i for i in page_imgs if i.get('is_screenshot') and i.get('b64')), None)
            if chosen is None:
                chosen = next((i for i in page_imgs if i.get('b64')), None)
            if chosen:
                ocr_images_b64.append(chosen['b64'])

        if not ocr_images_b64:
            # No images — write empty batch and mark done
            with open(os.path.join(batch_dir, 'batch.md'), 'w', encoding='utf-8') as f:
                f.write('')
            redis_client.hset(batch_key, mapping={
                'status': 'done',
                'completed_at': str(time.time()),
                'image_count': '0',
            })
            redis_client.hincrby(session_key, 'batches_done', 1)
            logging.info(f"Batch {batch_index} done (no images): session={session_id}")
            return {'status': 'success', 'images': 0}

        # Decode and build PIL images
        from PIL import Image as PILImage
        pil_images = []
        for img_b64 in ocr_images_b64:
            try:
                if ',' in img_b64:
                    img_b64 = img_b64.split(',', 1)[1]
                pil_img = PILImage.open(io_module.BytesIO(base64.b64decode(img_b64))).convert('RGB')
                pil_images.append(pil_img)
            except Exception as e:
                logging.warning(f"Batch {batch_index}: failed to decode screenshot: {e}")

        if not pil_images:
            with open(os.path.join(batch_dir, 'batch.md'), 'w', encoding='utf-8') as f:
                f.write('')
            redis_client.hset(batch_key, mapping={
                'status': 'done',
                'completed_at': str(time.time()),
                'image_count': '0',
            })
            redis_client.hincrby(session_key, 'batches_done', 1)
            return {'status': 'success', 'images': 0}

        # Build in-memory PDF
        pdf_buf = io_module.BytesIO()
        pil_images[0].save(pdf_buf, format='PDF', save_all=True, append_images=pil_images[1:], resolution=150)
        pdf_buf.seek(0)

        # Run Marker
        artifacts = get_model_dict()
        from marker.converters.pdf import PdfConverter
        from marker.output import text_from_rendered

        converter = PdfConverter(artifact_dict=artifacts, config={'force_ocr': True})
        rendered = converter(pdf_buf)
        markdown_text, _, marker_images = text_from_rendered(rendered)

        # Claim globally-unique image offsets atomically
        img_count_key = f"capture:session:{session_id}:image_counter"
        num_images = len(marker_images or {})
        if num_images > 0:
            new_total = redis_client.incrby(img_count_key, num_images)
            redis_client.expire(img_count_key, app_settings.capture_session_ttl)
            base_offset = new_total - num_images
        else:
            base_offset = 0

        # Save images and rewrite markdown refs to final path
        image_count = 0
        for i, (img_name, img_obj) in enumerate(sorted((marker_images or {}).items())):
            global_idx = base_offset + i
            final_name = f"img_{global_idx:05d}.png"
            img_obj.save(os.path.join(batch_images_dir, final_name))
            markdown_text = re_module.sub(
                re_module.escape(f"({img_name})"),
                f"(images/{final_name})",
                markdown_text,
            )
            image_count += 1

        with open(os.path.join(batch_dir, 'batch.md'), 'w', encoding='utf-8') as f:
            f.write(markdown_text)

        redis_client.hset(batch_key, mapping={
            'status': 'done',
            'completed_at': str(time.time()),
            'image_count': str(image_count),
        })
        redis_client.hincrby(session_key, 'batches_done', 1)

        # Update job progress: batches account for 0–75%
        batches_queued = int(redis_client.hget(session_key, 'batches_queued') or 1)
        batches_done = int(redis_client.hget(session_key, 'batches_done') or 1)
        progress = int((batches_done / batches_queued) * 75)
        update_job_metadata(job_id, {'progress': str(progress)})

        logging.info(f"Batch {batch_index} done: session={session_id}, images={image_count}")

        _cleanup_marker_memory(converter, rendered)
        gc.collect()
        return {'status': 'success', 'images': image_count}

    except Exception as e:
        error_msg = str(e)
        logging.error(f"Batch {batch_index} failed: session={session_id}, error={error_msg}")
        redis_client.hset(batch_key, mapping={
            'status': 'failed',
            'error': error_msg[:500],
        })
        redis_client.hincrby(session_key, 'batches_failed', 1)
        # Do NOT mark the job as FAILURE — the assembly step handles partial failures
        raise


@celery.task(
    name='tasks.assemble_capture_session',
    time_limit=600,
    soft_time_limit=540,
    acks_late=True,
    reject_on_worker_lost=True,
)
def assemble_capture_session(session_id, job_id):
    """
    Assembles captured browser extension pages into a single Markdown document.

    Reads pages from capture:session:{session_id}:pages Redis list, sorts by
    page_hint, saves images, rewrites image refs, and writes YAML front matter
    + merged Markdown to the output directory. If to_format is not markdown,
    runs Pandoc to convert to the target format.
    """
    import base64
    import json as json_module
    import gc
    import shutil

    logging.info(f"Starting capture assembly: session={session_id}, job={job_id}")
    update_job_metadata(job_id, {
        'status': 'PROCESSING',
        'started_at': str(time.time()),
        'progress': '10',
    })

    try:
        session_key = f"capture:session:{session_id}"
        session_meta = redis_client.hgetall(session_key)
        title = session_meta.get('title', 'Captured Document')
        to_format = session_meta.get('to_format', 'markdown')
        source_url = session_meta.get('source_url', '')

        pages_raw = redis_client.lrange(f"capture:session:{session_id}:pages", 0, -1)
        if not pages_raw:
            raise ValueError("No pages found in capture session")

        pages = [json_module.loads(p) for p in pages_raw]
        pages.sort(key=lambda p: p.get('page_hint', 0))

        update_job_metadata(job_id, {'progress': '20'})

        output_dir = os.path.join(OUTPUT_FOLDER, job_id)
        images_dir = os.path.join(output_dir, 'images')
        os.makedirs(images_dir, exist_ok=True)

        safe_title = secure_filename(title) or f"capture_{job_id}"
        force_ocr = session_meta.get('force_ocr', 'false').lower() == 'true'
        batches_queued = int(session_meta.get('batches_queued', 0))

        if force_ocr and batches_queued > 0:
            # Batch merge path: stitch together pre-processed batch outputs
            update_job_metadata(job_id, {'progress': '78'})
            all_markdown_parts = []
            total_images = 0
            batches_failed = int(session_meta.get('batches_failed', 0))

            for i in range(batches_queued):
                batch_key = f"capture:batch:{session_id}:{i}"
                batch_status = redis_client.hget(batch_key, 'status') or 'unknown'
                batch_dir = os.path.join(output_dir, 'batches', f'batch_{i}')

                if batch_status == 'failed':
                    all_markdown_parts.append(
                        f"\n\n> **⚠ Batch {i} failed to process — these pages may be missing.**\n\n"
                    )
                    logging.warning(f"Batch {i} failed for session {session_id}; inserting tombstone")
                    continue

                md_path = os.path.join(batch_dir, 'batch.md')
                if os.path.exists(md_path):
                    with open(md_path, 'r', encoding='utf-8') as f:
                        all_markdown_parts.append(f.read())

                batch_images_dir = os.path.join(batch_dir, 'images')
                if os.path.exists(batch_images_dir):
                    for img_name in sorted(os.listdir(batch_images_dir)):
                        shutil.copy2(
                            os.path.join(batch_images_dir, img_name),
                            os.path.join(images_dir, img_name),
                        )
                        total_images += 1

            front_matter = f"---\ntitle: {title}\nsource: {source_url}\npages: {len(pages)}\n---\n\n"
            merged_content = front_matter + "\n\n---\n\n".join(all_markdown_parts)
            image_count = total_images

            # Clean up staging area
            shutil.rmtree(os.path.join(output_dir, 'batches'), ignore_errors=True)

        elif force_ocr:
            # Fallback: force_ocr session with no pre-processed batches (very small session)
            import io as io_module
            from PIL import Image as PILImage

            # Collect one image per page (prefer screenshots, fall back to first image).
            ocr_images_b64 = []
            for page in pages:
                page_imgs = page.get('images', [])
                chosen = next((i for i in page_imgs if i.get('is_screenshot') and i.get('b64')), None)
                if chosen is None:
                    chosen = next((i for i in page_imgs if i.get('b64')), None)
                if chosen:
                    ocr_images_b64.append(chosen['b64'])

            if not ocr_images_b64:
                raise ValueError("No valid page images found for OCR")

            logging.info(f"OCR fallback path: assembling {len(ocr_images_b64)} page images via Marker for job {job_id}")
            update_job_metadata(job_id, {'progress': '30'})

            pil_images = []
            for img_b64 in ocr_images_b64:
                try:
                    if ',' in img_b64:
                        img_b64 = img_b64.split(',', 1)[1]
                    pil_img = PILImage.open(io_module.BytesIO(base64.b64decode(img_b64))).convert('RGB')
                    pil_images.append(pil_img)
                except Exception as e:
                    logging.warning(f"Failed to decode screenshot: {e}")

            if not pil_images:
                raise ValueError("No valid page images found for OCR")

            pdf_buf = io_module.BytesIO()
            pil_images[0].save(pdf_buf, format='PDF', save_all=True, append_images=pil_images[1:], resolution=150)
            pdf_buf.seek(0)

            update_job_metadata(job_id, {'progress': '45'})

            artifacts = get_model_dict()
            from marker.converters.pdf import PdfConverter
            from marker.output import text_from_rendered

            converter = PdfConverter(artifact_dict=artifacts, config={'force_ocr': True})
            rendered = converter(pdf_buf)

            update_job_metadata(job_id, {'progress': '80'})

            markdown_text, _, marker_images = text_from_rendered(rendered)

            image_count = 0
            for img_name, img_obj in (marker_images or {}).items():
                safe_img_name = secure_filename(img_name) or f"image_{image_count}.png"
                img_obj.save(os.path.join(images_dir, safe_img_name))
                markdown_text = markdown_text.replace(f"({img_name})", f"(images/{safe_img_name})")
                image_count += 1

            front_matter = f"---\ntitle: {title}\nsource: {source_url}\npages: {len(pages)}\n---\n\n"
            merged_content = front_matter + markdown_text
            batches_failed = 0

        else:
            # Text assembly path: merge DOM-extracted markdown from each page
            all_markdown_parts = []
            image_count = 0

            for page in pages:
                page_text = page.get('text', '')
                page_images = page.get('images', [])

                for img_info in page_images:
                    if img_info.get('is_screenshot'):
                        continue  # Skip screenshots in text path
                    img_filename = img_info.get('filename', f'image_{image_count}.png')
                    img_b64 = img_info.get('b64', '')

                    if img_b64:
                        try:
                            if ',' in img_b64:
                                img_b64 = img_b64.split(',', 1)[1]
                            img_data = base64.b64decode(img_b64)
                            safe_img_filename = secure_filename(img_filename) or f"image_{image_count}.png"
                            img_save_path = os.path.join(images_dir, safe_img_filename)
                            with open(img_save_path, 'wb') as f:
                                f.write(img_data)
                            page_text = page_text.replace(f"({img_filename})", f"(images/{safe_img_filename})")
                            image_count += 1
                        except Exception as e:
                            logging.warning(f"Failed to save image {img_filename}: {e}")

                # Strip blob: and absolute URL image references — they won't resolve
                # in the downloaded file (Kindle renders pages as blob: img elements)
                import re as re_module
                page_text = re_module.sub(r'!\[[^\]]*\]\((blob:[^)]+|https?://[^)]+)\)', '', page_text)
                all_markdown_parts.append(page_text)

            front_matter = f"---\ntitle: {title}\nsource: {source_url}\npages: {len(pages)}\n---\n\n"
            merged_content = front_matter + "\n\n---\n\n".join(all_markdown_parts)
            batches_failed = 0

        update_job_metadata(job_id, {'progress': '88'})

        output_filename = f"{safe_title}.md"
        output_path = os.path.join(output_dir, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(merged_content)

        # Free large base64 data from Redis immediately
        redis_client.delete(f"capture:session:{session_id}:pages")

        update_job_metadata(job_id, {'progress': '85'})

        if to_format not in ('markdown', 'gfm'):
            format_extensions = {
                'docx': 'docx', 'epub3': 'epub', 'epub2': 'epub',
                'html': 'html', 'pdf': 'pdf', 'rst': 'rst',
                'latex': 'tex', 'odt': 'odt', 'rtf': 'rtf',
            }
            out_ext = format_extensions.get(to_format, to_format)
            converted_filename = f"{safe_title}.{out_ext}"
            converted_path = os.path.join(output_dir, converted_filename)

            cmd = ['pandoc', '-f', 'markdown', '-t', to_format, output_path, '-o', converted_path]
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
            os.remove(output_path)  # Remove intermediate markdown
            file_count = 1 + image_count
        else:
            file_count = 1 + image_count

        # Clean up empty images dir
        if image_count == 0 and os.path.exists(images_dir):
            os.rmdir(images_dir)
            file_count = 1

        success_meta = {
            'status': 'SUCCESS',
            'completed_at': str(time.time()),
            'progress': '100',
            'file_count': str(file_count),
            'encrypted': 'false',
        }
        if batches_failed > 0:
            success_meta['batch_warnings'] = f"{batches_failed} batch(es) had failures — some pages may be missing"
        update_job_metadata(job_id, success_meta)
        redis_client.expire(f"job:{job_id}", 7200)

        logging.info(f"Capture assembly complete: job={job_id}, pages={len(pages)}, images={image_count}")
        gc.collect()
        return {"status": "success", "pages": len(pages), "images": image_count}

    except Exception as e:
        error_msg = f"Capture assembly failed: {str(e)}"
        logging.error(f"Error assembling capture session {session_id}: {error_msg}")
        update_job_metadata(job_id, {
            'status': 'FAILURE',
            'completed_at': str(time.time()),
            'error': error_msg[:500],
            'progress': '0',
        })
        redis_client.expire(f"job:{job_id}", 600)
        raise


celery.conf.beat_schedule = {
    'cleanup-every-5-minutes': {
        'task': 'tasks.cleanup_old_files',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
    'update-queue-metrics': {
        'task': 'tasks.update_metrics',
        'schedule': 30.0,  # Every 30 seconds
    },
}
