import os
import subprocess
import time
import shutil
import redis
import requests
import logging
import sys
import threading
import signal
import atexit
from urllib.parse import urlparse
import celery as celery_module  # Import module to get version
from celery import Celery
from celery.schedules import crontab
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename
from warmup import get_slm_model # Epic 26: SLM model getter
from PIL import Image # Epic 28: For image processing

# Epic 21.5: Import Prometheus metrics
from metrics import (
    conversion_total,
    conversion_duration_seconds,
    conversion_failures_total,
    worker_tasks_active,
    worker_info,
    update_queue_metrics,
    start_metrics_server
)

# Epic 23.3: Import encryption modules
from encryption import EncryptionService
from key_manager import create_key_manager

# Epic 24.2: Import secrets management for Celery signing key
from secrets_manager import validate_secrets_at_startup

# Configure Structured Logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s", "message": "%(message)s"}'))
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

# Epic 24.2: Validate secrets at startup
try:
    worker_secrets = validate_secrets_at_startup()
except ValueError as e:
    logging.error(f"Failed to load secrets: {e}")
    sys.exit(1)

UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'data/uploads')
OUTPUT_FOLDER = os.environ.get('OUTPUT_FOLDER', 'data/outputs')

def is_valid_uuid(val):
    try:
        import uuid
        uuid.UUID(str(val))
        return True
    except (ValueError, ImportError):
        return False

# WebSocket Emitter (Standalone for worker)
socketio = SocketIO(message_queue=os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1'))

# Epic 24.1: Redis TLS Configuration
# Metadata Redis client (DB 1) with connection pooling optimization and TLS support
redis_url = os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1')

# Configure TLS parameters if using rediss://
redis_kwargs = {
    'max_connections': 20,
    'decode_responses': True
}

if redis_url.startswith('rediss://'):
    # Enable TLS
    redis_kwargs['ssl'] = True
    redis_kwargs['ssl_cert_reqs'] = 'required'

    # Certificate paths from environment
    ca_certs = os.environ.get('REDIS_TLS_CA_CERTS')
    certfile = os.environ.get('REDIS_TLS_CERTFILE')
    keyfile = os.environ.get('REDIS_TLS_KEYFILE')

    if ca_certs:
        redis_kwargs['ssl_ca_certs'] = ca_certs
    if certfile:
        redis_kwargs['ssl_certfile'] = certfile
    if keyfile:
        redis_kwargs['ssl_keyfile'] = keyfile

    logging.info(f"Redis TLS enabled with CA: {ca_certs}")

redis_client = redis.Redis.from_url(redis_url, **redis_kwargs)

celery = Celery(
    'tasks',
    broker=os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/0'),
    backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
)

# Epic 24.2: Celery Task Message Encryption
# Epic 24.2: Celery message signing configuration
# Note: 'auth' serializer requires celery[auth] extra and proper configuration
# For now, using standard JSON serialization. Message authentication can be
# implemented using Celery's built-in security features or message signing.
celery_signing_key = worker_secrets.get('CELERY_SIGNING_KEY')
if celery_signing_key:
    celery.conf.task_serializer = 'json'
    celery.conf.result_serializer = 'json'
    celery.conf.accept_content = ['json', 'application/json']
    # TODO: Implement proper message signing/authentication
    # celery.conf.security_key = celery_signing_key
    logging.info("Celery signing key loaded (authentication implementation pending)")
else:
    celery.conf.task_serializer = 'json'
    celery.conf.result_serializer = 'json'
    celery.conf.accept_content = ['json', 'application/json']
    logging.warning("Celery signing key not set - messages not authenticated")

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

# Epic 21.5: Start Prometheus metrics server in background thread
def _start_metrics_background():
    """Start metrics server in a daemon thread."""
    try:
        logging.info("Starting Prometheus metrics server on port 9090")
        start_metrics_server(port=9090, host='0.0.0.0')
    except Exception as e:
        logging.error(f"Failed to start metrics server: {e}")

metrics_thread = threading.Thread(target=_start_metrics_background, daemon=True)
metrics_thread.start()

# Epic 21.5: Set worker info
worker_info.info({
    'version': '1.0.0',
    'python_version': sys.version.split()[0],
    'celery_version': celery_module.__version__
})

# Epic 21.12: Graceful Shutdown Handling
shutdown_requested = False

def cleanup_on_shutdown():
    """
    Cleanup handler called on shutdown.

    Epic 21.12: GPU memory cleanup and graceful shutdown
    """
    global shutdown_requested
    if shutdown_requested:
        return  # Already handling shutdown

    shutdown_requested = True
    logging.info("Shutdown requested - performing cleanup...")

    try:
        # Epic 21.12: GPU memory cleanup
        try:
            import torch
            import gc

            gc.collect()

            if torch.cuda.is_available():
                logging.info("Freeing GPU memory...")
                torch.cuda.empty_cache()
                torch.cuda.synchronize()

                # Log final GPU memory state
                allocated = torch.cuda.memory_allocated(0) / 1e9
                reserved = torch.cuda.memory_reserved(0) / 1e9
                logging.info(f"GPU memory freed. Allocated: {allocated:.2f}GB, Reserved: {reserved:.2f}GB")
            else:
                logging.info("CPU-only mode - no GPU cleanup needed")

        except Exception as e:
            logging.error(f"Error during GPU cleanup: {e}")

        # Log shutdown completion
        logging.info("Shutdown cleanup complete")

    except Exception as e:
        logging.error(f"Error during shutdown cleanup: {e}")


def signal_handler(signum, frame):
    """
    Handle SIGTERM and SIGINT signals for graceful shutdown.

    Epic 21.12: Graceful shutdown on signal
    """
    signal_name = 'SIGTERM' if signum == signal.SIGTERM else 'SIGINT'
    logging.info(f"Received {signal_name} - initiating graceful shutdown...")

    # Run cleanup
    cleanup_on_shutdown()

    # Exit gracefully (Celery will finish current task first due to acks_late)
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Register cleanup for normal exit
atexit.register(cleanup_on_shutdown)

logging.info("Graceful shutdown handlers registered (SIGTERM, SIGINT, atexit)")


def recover_orphaned_jobs():
    """Mark any PROCESSING jobs as FAILED on worker startup.

    Jobs stuck in PROCESSING state after a worker restart will never complete.
    This function recovers them so users can see the failure and retry.
    """
    try:
        keys = redis_client.keys("job:*")
        recovered = 0
        for key in keys:
            meta = redis_client.hgetall(key)
            if meta.get('status') == 'PROCESSING':
                job_id = key.split(':', 1)[1] if ':' in key else key
                redis_client.hset(key, mapping={
                    'status': 'FAILURE',
                    'error': 'Worker restarted during conversion. Please retry.',
                    'completed_at': str(time.time()),
                    'progress': '0',
                })
                logging.warning(f"Recovered orphaned PROCESSING job: {job_id}")
                recovered += 1
        if recovered:
            logging.info(f"Startup recovery: marked {recovered} orphaned job(s) as FAILED")
    except Exception as e:
        logging.error(f"Error during orphaned job recovery: {e}")


recover_orphaned_jobs()

# Epic 23.3: Initialize encryption components (lazily to avoid startup delays)
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

def encrypt_output_files(job_id, output_dir):
    """
    Encrypt all output files for a job after conversion completes.

    Epic 23.3: Transparent file encryption

    Args:
        job_id: Job identifier
        output_dir: Directory containing output files

    Returns:
        True if encryption succeeded, False otherwise
    """
    try:
        encryption_service = get_encryption_service()
        key_manager = get_key_manager()

        # Generate DEK for this job
        dek = key_manager.generate_job_key(job_id, metadata={
            'created_at': str(time.time()),
            'job_id': job_id
        })

        # Encrypt all files in output directory
        encrypted_count = 0
        for root, dirs, files in os.walk(output_dir):
            for filename in files:
                file_path = os.path.join(root, filename)

                # Skip already encrypted files
                if file_path.endswith('.enc'):
                    continue

                # Encrypt file
                encrypted_path = file_path + '.enc'
                try:
                    encryption_service.encrypt_file(
                        input_path=file_path,
                        output_path=encrypted_path,
                        key=dek,
                        associated_data=job_id
                    )

                    # Remove plaintext file
                    os.remove(file_path)

                    # Rename encrypted file to original name
                    os.rename(encrypted_path, file_path)

                    encrypted_count += 1
                    logging.info(f"Encrypted file: {file_path}")

                except Exception as e:
                    logging.error(f"Failed to encrypt {file_path}: {e}")
                    # Clean up partial encryption
                    if os.path.exists(encrypted_path):
                        os.remove(encrypted_path)
                    return False

        logging.info(f"Encrypted {encrypted_count} files for job {job_id}")
        return True

    except Exception as e:
        logging.error(f"Encryption failed for job {job_id}: {e}")
        return False


def update_job_metadata(job_id, updates):
    """Update job metadata using Redis Hash (atomic operation) and broadcast via WebSocket."""
    key = f"job:{job_id}"
    try:
        # Epic 30.3: Pipeline hset + hgetall into one round trip instead of two
        pipe = redis_client.pipeline()
        pipe.hset(key, mapping=updates)
        pipe.hgetall(key)
        _, full_meta = pipe.execute()
        full_meta['id'] = job_id
        socketio.emit('job_update', full_meta, namespace='/')
    except Exception as e:
        logging.error(f"Error updating metadata for {job_id}: {e}")


def get_job_metadata(job_id):
    """Get all job metadata as a dictionary."""
    key = f"job:{job_id}"
    return redis_client.hgetall(key)


MCP_SERVER_URL = os.environ.get('MCP_SERVER_URL', 'http://mcp-server:8080/execute')

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

        # Epic 23.3: Encrypt output files
        # DISABLED: Encryption requires shared master key between web and worker
        # TODO: Configure shared MASTER_ENCRYPTION_KEY or disable encryption
        # output_dir = os.path.dirname(output_path)
        # if not encrypt_output_files(job_id, output_dir):
        #     error_msg = "File encryption failed"
        #     logging.error(f"Encryption failed for job {job_id}")
        #     update_job_metadata(job_id, {
        #         'status': 'FAILURE',
        #         'completed_at': str(time.time()),
        #         'error': error_msg,
        #         'progress': '0'
        #     })
        #
        #     duration = time.time() - start_time
        #     conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        #     conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='encryption_error').inc()
        #     conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        #     worker_tasks_active.dec()
        #
        #     raise Exception(error_msg)

        # Epic 30.2: file_count=1 for single Pandoc output (enables list_jobs() cache)
        update_job_metadata(job_id, {
            'status': 'SUCCESS',
            'completed_at': str(time.time()),
            'progress': '100',
            'encrypted': 'false',
            'file_count': '1'
        })

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

        # Epic 21.5: Record failure metrics
        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='unknown').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        raise

model_dict = None

def get_model_dict():
    global model_dict
    if model_dict is None:
        # Set env vars for marker to optimize for 16GB VRAM
        os.environ["INFERENCE_RAM"] = "16"
        # Import here to avoid loading at top level
        from marker.models import create_model_dict
        
        logging.info("Initializing Marker models...")
        model_dict = create_model_dict()
        logging.info("Marker models initialized.")
    return model_dict

@celery.task(
    name='tasks.convert_with_marker',
    bind=True,                # Enable access to self (for retry)
    time_limit=1200,          # Marker is slower (GPU/CPU heavy) - 20 mins
    soft_time_limit=1140,     # 19 mins
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3             # Reduced retries since no external service dependency
)
def convert_with_marker(self, job_id, input_filename, output_filename, from_format, to_format, options=None):
    # Epic 21.5: Track active tasks and start time
    worker_tasks_active.inc()
    start_time = time.time()

    if not is_valid_uuid(job_id):
        logging.error(f"Invalid job_id received: {job_id}")
        worker_tasks_active.dec()
        return {"status": "error", "message": "Invalid job ID"}

    # Abort if the job was already marked as FAILURE or REVOKED by startup recovery.
    # This breaks the crash-loop: reject_on_worker_lost re-queues the task, but
    # recover_orphaned_jobs() marks it FAILURE on startup, so we skip re-processing.
    current_status = redis_client.hget(f"job:{job_id}", 'status')
    if current_status in ('FAILURE', 'REVOKED'):
        logging.warning(f"Skipping re-queued task for already-{current_status} job {job_id}")
        worker_tasks_active.dec()
        return {"status": "skipped", "reason": current_status}

    safe_job_id = secure_filename(job_id)
    safe_input_filename = secure_filename(input_filename)
    safe_output_filename = secure_filename(output_filename)

    if options is None:
        options = {}

    input_path = os.path.join(UPLOAD_FOLDER, safe_job_id, safe_input_filename)
    output_dir = os.path.join(OUTPUT_FOLDER, safe_job_id)
    output_path = os.path.join(output_dir, safe_output_filename)

    logging.info(f"Starting Marker conversion for job {job_id} (Attempt {self.request.retries + 1}) with options: {options}")
    update_job_metadata(job_id, {
        'status': 'PROCESSING',
        'started_at': str(time.time()),
        'progress': '5'
    })

    if not os.path.exists(input_path):
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': 'Input file missing'})
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Reject PDFs that are too large for Marker AI to process reliably.
    # Marker takes ~20-30 seconds per page on GPU; 300 pages ≈ 2 hours max.
    MAX_MARKER_PAGES = int(os.environ.get('MAX_MARKER_PAGES', '300'))
    try:
        import pypdfium2 as pdfium
        pdf_doc = pdfium.PdfDocument(input_path)
        page_count = len(pdf_doc)
        pdf_doc.close()
        if page_count > MAX_MARKER_PAGES:
            error_msg = (
                f"PDF has {page_count} pages, which exceeds the {MAX_MARKER_PAGES}-page "
                f"limit for AI conversion. Split the document into smaller parts."
            )
            update_job_metadata(job_id, {
                'status': 'FAILURE', 'completed_at': str(time.time()),
                'error': error_msg, 'progress': '0'
            })
            worker_tasks_active.dec()
            return {"status": "error", "message": error_msg}
        logging.info(f"PDF page count: {page_count} (limit: {MAX_MARKER_PAGES})")
    except Exception as e:
        logging.warning(f"Could not check PDF page count: {e}")

    os.makedirs(output_dir, exist_ok=True)

    # Create images subdirectory
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    try:
        update_job_metadata(job_id, {'progress': '15'})
        
        # Get cached models
        artifacts = get_model_dict()
        
        # Initialize converter with task-specific configuration
        from marker.converters.pdf import PdfConverter
        converter = PdfConverter(artifact_dict=artifacts, config=options)
        
        update_job_metadata(job_id, {'progress': '20'})
        
        logging.info(f"Running Marker conversion on {input_path}")
        
        # Run conversion
        rendered = converter(input_path)
        
        update_job_metadata(job_id, {'progress': '80'})
        
        # Extract results
        from marker.output import text_from_rendered
        import json
        
        text, _, images = text_from_rendered(rendered)
        
        # Save images and update markdown links
        saved_images_count = 0
        for filename, image in images.items():
            image_save_path = os.path.join(images_dir, filename)
            image.save(image_save_path)
            saved_images_count += 1
            text = text.replace(f"({filename})", f"(images/{filename})")
            
        logging.info(f"Saved {saved_images_count} images to {images_dir}")

        # Save markdown
        with open(output_path, "w", encoding='utf-8') as f:
            f.write(text)
            
        # Save metadata
        metadata_path = os.path.join(output_dir, "metadata.json")
        with open(metadata_path, "w", encoding='utf-8') as f:
            json.dump(rendered.metadata, f, indent=2, default=str)

        # Epic 30.2: Cache file_count in metadata so list_jobs() skips os.walk()
        # Count: markdown file + metadata.json + images
        file_count = 2 + saved_images_count  # output.md + metadata.json + N images
        update_job_metadata(job_id, {'progress': '90', 'file_count': str(file_count)})
        logging.info(f"Marker conversion successful: {output_path}")

        # Epic 23.3: Encrypt output files
        # DISABLED: Encryption requires shared master key between web and worker
        # TODO: Configure shared MASTER_ENCRYPTION_KEY or disable encryption
        # if not encrypt_output_files(job_id, output_dir):
        #     error_msg = "File encryption failed"
        #     logging.error(f"Encryption failed for job {job_id}")
        #     update_job_metadata(job_id, {
        #         'status': 'FAILURE',
        #         'completed_at': str(time.time()),
        #         'error': error_msg,
        #         'progress': '0'
        #     })
        #
        #     duration = time.time() - start_time
        #     conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        #     conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='encryption_error').inc()
        #     conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        #     worker_tasks_active.dec()
        #
        #     raise Exception(error_msg)

        update_job_metadata(job_id, {
            'status': 'SUCCESS',
            'completed_at': str(time.time()),
            'progress': '100',
            'encrypted': 'false'  # Encryption disabled in development
        })

        # Epic 26: Trigger SLM metadata extraction after successful Marker conversion
        # This will run asynchronously in a separate task
        extract_slm_metadata.delay(job_id, output_path)

        # Epic 21.5: Record success metrics
        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='success').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        # Epic 21.4: Memory cleanup after successful task
        logging.info("Performing memory cleanup after Marker task completion...")
        del converter, rendered, text, images
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            mem_freed = torch.cuda.memory_reserved(0) - torch.cuda.memory_allocated(0)
            logging.info(f"Memory cleanup complete. GPU memory freed: {mem_freed / 1e9:.2f} GB")
        else:
            logging.info("Memory cleanup complete (CPU mode)")

        return {"status": "success", "output_file": os.path.basename(output_path)}

    except Exception as e:
        error_msg = f"Marker conversion failed: {str(e)}"
        logging.error(f"Error for job {job_id}: {error_msg}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500], 'progress': '0'})

        # Epic 21.5: Record failure metrics
        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='marker_error').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        # Epic 21.4: Memory cleanup even after failure
        logging.info("Performing memory cleanup after task failure...")
        import gc
        try:
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logging.info("Memory cleanup complete after failure")
        except Exception as cleanup_error:
            logging.warning(f"Memory cleanup failed: {cleanup_error}")

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


@celery.task(name='tasks.cleanup_old_files')
def cleanup_old_files():
    """
    Intelligent cleanup with prioritization by file size and recency.

    Epic 21.6: Intelligent Data Retention
    - Prioritizes large files for deletion
    - Preserves recently viewed files
    - Implements emergency cleanup at >95% disk usage
    """
    now = time.time()

    RETENTION_SUCCESS_NO_DOWNLOAD = 3600 # 1 hour
    RETENTION_SUCCESS_DOWNLOADED = 600   # 10 minutes
    RETENTION_FAILURE = 300              # 5 minutes
    RETENTION_ORPHAN = 3600              # 1 hour (fallback)

    upload_dir = os.environ.get('UPLOAD_FOLDER', 'data/uploads')
    output_dir = os.environ.get('OUTPUT_FOLDER', 'data/outputs')

    job_ids = set()
    if os.path.exists(upload_dir):
        job_ids.update(os.listdir(upload_dir))
    if os.path.exists(output_dir):
        job_ids.update(os.listdir(output_dir))

    # Epic 21.6: Check disk usage for emergency cleanup
    disk_usage_percent = _get_disk_usage_percent(upload_dir)
    emergency_cleanup = disk_usage_percent > 95
    aggressive_cleanup = disk_usage_percent > 80

    if emergency_cleanup:
        logging.warning(f"EMERGENCY CLEANUP: Disk usage at {disk_usage_percent:.1f}%")
    elif aggressive_cleanup:
        logging.info(f"Aggressive cleanup: Disk usage at {disk_usage_percent:.1f}%")

    logging.info(f"Running cleanup. Found {len(job_ids)} jobs on disk.")

    # Epic 21.6: Collect deletion candidates with size and priority
    deletion_candidates = []

    for job_id in job_ids:
        if not is_valid_uuid(job_id):
            continue

        key = f"job:{job_id}"
        meta = get_job_metadata(job_id)

        should_delete = False
        reason = ""
        priority = 0  # Higher priority = delete first

        if meta:
            status = meta.get('status')
            completed_at = float(meta.get('completed_at', 0)) if meta.get('completed_at') else None
            downloaded_at = float(meta.get('downloaded_at', 0)) if meta.get('downloaded_at') else None
            last_viewed = float(meta.get('last_viewed', 0)) if meta.get('last_viewed') else None
            started_at = float(meta.get('started_at', 0)) if meta.get('started_at') else None

            if status == 'FAILURE':
                if completed_at and now > completed_at + RETENTION_FAILURE:
                    should_delete = True
                    reason = "Failed job expired (5m)"
                    priority = 10  # High priority for failures
            elif status == 'SUCCESS':
                # Epic 21.6: Respect last_viewed timestamp
                reference_time = last_viewed or downloaded_at or completed_at

                if downloaded_at or last_viewed:
                    if now > reference_time + RETENTION_SUCCESS_DOWNLOADED:
                        should_delete = True
                        reason = f"Downloaded/viewed job expired (10m since last access)"
                        priority = 5  # Medium priority for old downloads
                elif completed_at:
                    if now > completed_at + RETENTION_SUCCESS_NO_DOWNLOAD:
                        should_delete = True
                        reason = "Completed job (not downloaded) expired (1h)"
                        priority = 3  # Lower priority for never-downloaded
            if not completed_at and started_at and now > started_at + 7200:
                should_delete = True
                reason = "Stale processing job (2h)"
                priority = 8  # High priority for stale jobs
        else:
            check_path = os.path.join(upload_dir, job_id)
            if not os.path.exists(check_path):
                 check_path = os.path.join(output_dir, job_id)
            if os.path.exists(check_path):
                 mtime = os.path.getmtime(check_path)
                 if now > mtime + RETENTION_ORPHAN:
                     should_delete = True
                     reason = "Orphaned job expired (1h fallback)"
                     priority = 7  # Medium-high priority for orphans

        # Epic 21.6: Emergency cleanup - delete everything eligible
        if emergency_cleanup:
            should_delete = True
            priority = 15
            reason = f"EMERGENCY: {reason}" if reason else "EMERGENCY: Disk >95% full"

        if should_delete:
            # Calculate total size for this job
            upload_path = os.path.join(upload_dir, job_id)
            output_path = os.path.join(output_dir, job_id)
            total_size = 0
            if os.path.exists(upload_path):
                total_size += _get_directory_size(upload_path)
            if os.path.exists(output_path):
                total_size += _get_directory_size(output_path)

            deletion_candidates.append({
                'job_id': job_id,
                'reason': reason,
                'priority': priority,
                'size_bytes': total_size,
                'key': key
            })

    # Epic 21.6: Sort by priority (descending), then by size (descending)
    deletion_candidates.sort(key=lambda x: (x['priority'], x['size_bytes']), reverse=True)

    logging.info(f"Found {len(deletion_candidates)} jobs eligible for deletion")

    # Epic 21.6: Delete in priority order
    total_freed = 0
    for candidate in deletion_candidates:
        job_id = candidate['job_id']
        reason = candidate['reason']
        size_mb = candidate['size_bytes'] / (1024 * 1024)

        logging.info(f"Deleting job {job_id} ({size_mb:.2f} MB). Reason: {reason}")

        for base in [upload_dir, output_dir]:
            p = os.path.join(base, job_id)
            if os.path.exists(p):
                try:
                    shutil.rmtree(p)
                    total_freed += candidate['size_bytes']
                except Exception as e:
                    logging.error(f"Error deleting {p}: {e}")

        redis_client.delete(candidate['key'])

        # Epic 21.6: Stop cleanup if disk usage back to normal (unless emergency)
        if not emergency_cleanup:
            current_usage = _get_disk_usage_percent(upload_dir)
            if current_usage < 70:
                logging.info(f"Disk usage now at {current_usage:.1f}%, stopping cleanup")
                break

    logging.info(f"Cleanup complete. Freed {total_freed / (1024 * 1024):.2f} MB")


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


@celery.task(name='tasks.renew_certificates')
def renew_certificates():
    """
    Celery task to trigger Certbot certificate renewal and reload services if successful.
    """
    logging.info("Initiating certificate renewal process...")

    try:
        # Step 1: Trigger Certbot renewal inside the certbot container
        # This assumes the worker container has 'docker-compose' client installed
        # and configured to communicate with the Docker daemon. This is a simplification.
        # In a real-world scenario, this might be triggered by an external scheduler
        # or the certbot container could run its own cronjob.
        renewal_command = [
            "docker-compose",
            "exec",
            "certbot",
            "/app/renew-certs.sh" # Path inside the certbot container
        ]
        # Assuming the renew-certs.sh script will output "Certificates were renewed or updated" if successful
        result = subprocess.run(renewal_command, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            logging.error(f"Certbot renewal command failed with exit code {result.returncode}: {result.stderr}")
            return {"status": "error", "message": f"Certbot renewal command failed: {result.stderr}"}

        logging.info(f"Certbot renewal command output: {result.stdout}")

        if "Certificates were renewed or updated" in result.stdout:
            logging.info("Certificates were renewed. Reloading services...")
            # Step 2: Reload services on the host if renewal was successful
            # This requires 'docker-compose' to be available on the host (where this Celery task orchestrator effectively runs)
            # and able to restart containers.
            reload_command = ["docker-compose", "restart", "web", "worker", "beat"]
            reload_result = subprocess.run(reload_command, capture_output=True, text=True, check=True)
            logging.info(f"Service reload output: {reload_result.stdout}")
            return {"status": "success", "message": "Certificates renewed and services reloaded."}
        else:
            logging.info("No certificates were renewed. Services not reloaded.")
            return {"status": "info", "message": "No certificates renewed."}

    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to restart services after renewal: {e.stderr}"
        logging.error(error_msg)
        return {"status": "error", "message": error_msg}
    except FileNotFoundError:
        error_msg = "Docker Compose command not found. Is it installed and in PATH?"
        logging.error(error_msg)
        return {"status": "error", "message": error_msg}
    except Exception as e:
        error_msg = f"An unexpected error occurred during certificate renewal: {str(e)}"
        logging.error(error_msg)
        return {"status": "error", "message": error_msg}


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
        MAX_SLM_CONTEXT = 2000 # Example token limit, adjust as needed
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

            if hostname and (hostname.endswith('signin.amazon.com') or hostname.endswith('kindle.amazon.com')):
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
        mcp_response = call_mcp_server(
            'create_context_and_goto',
            {'url': url, 'storageState': storage_state_json}
        )
        if not mcp_response.get('success'):
            raise Exception(f"Failed to navigate and get content from MCP server: {mcp_response.get('error', 'Unknown error')}")
        
        # Now take screenshot and save it to the shared volume
        screenshot_response = call_mcp_server(
            'screenshot_current_page',
            {'path': temp_screenshot_path} # mcp-server saves to /app/temp_screenshot_path
        )
        if not screenshot_response.get('success'):
            raise Exception(f"Failed to capture screenshot from MCP server: {screenshot_response.get('error', 'Unknown error')}")
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


celery.conf.beat_schedule = {
    'cleanup-every-5-minutes': {
        'task': 'tasks.cleanup_old_files',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
    'update-queue-metrics': {
        'task': 'tasks.update_metrics',
        'schedule': 30.0,  # Every 30 seconds
    },
    'renew-certificates-daily': {
        'task': 'tasks.renew_certificates',
        'schedule': crontab(hour=3, minute=0), # Every day at 3 AM
    },
}
