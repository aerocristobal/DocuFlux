import os
import subprocess
import time
import shutil
import redis
import requests
import logging
import sys
from celery import Celery
from celery.schedules import crontab
from flask_socketio import SocketIO

# Configure Structured Logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s", "message": "%(message)s"}'))
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

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

# Metadata Redis client (DB 1) with connection pooling optimization
redis_client = redis.Redis.from_url(
    os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1'),
    max_connections=20,
    decode_responses=True
)

celery = Celery(
    'tasks',
    broker=os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/0'),
    backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
)

celery.conf.beat_schedule = {
    'cleanup-every-5-minutes': {
        'task': 'tasks.cleanup_old_files',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
}

def update_job_metadata(job_id, updates):
    """Update job metadata using Redis Hash (atomic operation) and broadcast via WebSocket."""
    key = f"job:{job_id}"
    try:
        redis_client.hset(key, mapping=updates)
        # Fetch full data to broadcast complete state
        full_meta = redis_client.hgetall(key)
        full_meta['id'] = job_id
        socketio.emit('job_update', full_meta, namespace='/')
    except Exception as e:
        logging.error(f"Error updating metadata for {job_id}: {e}")


def get_job_metadata(job_id):
    """Get all job metadata as a dictionary."""
    key = f"job:{job_id}"
    return redis_client.hgetall(key)

@celery.task(
    name='tasks.convert_document',
    time_limit=600,           # Hard limit: 10 minutes
    soft_time_limit=540,      # Soft limit: 9 minutes
    acks_late=True,           # Re-queue if worker dies
    reject_on_worker_lost=True
)
def convert_document(job_id, input_filename, output_filename, from_format, to_format):
    if not is_valid_uuid(job_id):
        logging.error(f"Invalid job_id received: {job_id}")
        return {"status": "error", "message": "Invalid job ID"}

    input_path = os.path.join(UPLOAD_FOLDER, job_id, input_filename)
    output_path = os.path.join(OUTPUT_FOLDER, job_id, output_filename)

    logging.info(f"Starting conversion for job {job_id}: {from_format} -> {to_format}")
    update_job_metadata(job_id, {
        'status': 'PROCESSING', 
        'started_at': str(time.time()),
        'progress': '10'
    })
    
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
    
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=500)
        logging.info(f"Conversion successful: {output_path}")
        update_job_metadata(job_id, {
            'status': 'SUCCESS', 
            'completed_at': str(time.time()),
            'progress': '100'
        })
        return {"status": "success", "output_file": os.path.basename(output_path)}
    except subprocess.TimeoutExpired:
        error_msg = "Conversion timed out after 500 seconds"
        logging.error(f"Timeout for job {job_id}: {error_msg}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': error_msg, 'progress': '0'})
        raise Exception(error_msg)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr or e.stdout or "Unknown error"
        logging.error(f"Pandoc error for job {job_id}: {error_msg}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(error_msg)[:500], 'progress': '0'})
        raise Exception(f"Pandoc failed: {error_msg}")
    except Exception as e:
        logging.error(f"Unexpected error for job {job_id}: {str(e)}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500], 'progress': '0'})
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
    if not is_valid_uuid(job_id):
        logging.error(f"Invalid job_id received: {job_id}")
        return {"status": "error", "message": "Invalid job ID"}
    
    if options is None:
        options = {}

    input_path = os.path.join(UPLOAD_FOLDER, job_id, input_filename)
    output_dir = os.path.join(OUTPUT_FOLDER, job_id)
    output_path = os.path.join(output_dir, output_filename)

    logging.info(f"Starting Marker conversion for job {job_id} (Attempt {self.request.retries + 1}) with options: {options}")
    update_job_metadata(job_id, {
        'status': 'PROCESSING',
        'started_at': str(time.time()),
        'progress': '5'
    })

    if not os.path.exists(input_path):
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': 'Input file missing'})
        raise FileNotFoundError(f"Input file not found: {input_path}")

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

        update_job_metadata(job_id, {'progress': '90'})
        logging.info(f"Marker conversion successful: {output_path}")
        
        update_job_metadata(job_id, {
            'status': 'SUCCESS',
            'completed_at': str(time.time()),
            'progress': '100'
        })
        
        return {"status": "success", "output_file": os.path.basename(output_path)}

    except Exception as e:
        error_msg = f"Marker conversion failed: {str(e)}"
        logging.error(f"Error for job {job_id}: {error_msg}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500], 'progress': '0'})
        raise

@celery.task(name='tasks.cleanup_old_files')
def cleanup_old_files():
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
    
    logging.info(f"Running cleanup. Found {len(job_ids)} jobs on disk.")

    for job_id in job_ids:
        if not is_valid_uuid(job_id):
            continue

        key = f"job:{job_id}"
        meta = get_job_metadata(job_id)

        should_delete = False
        reason = ""

        if meta:
            status = meta.get('status')
            completed_at = float(meta.get('completed_at', 0)) if meta.get('completed_at') else None
            downloaded_at = float(meta.get('downloaded_at', 0)) if meta.get('downloaded_at') else None
            started_at = float(meta.get('started_at', 0)) if meta.get('started_at') else None

            if status == 'FAILURE':
                if completed_at and now > completed_at + RETENTION_FAILURE:
                    should_delete = True
                    reason = "Failed job expired (5m)"
            elif status == 'SUCCESS':
                if downloaded_at:
                    if now > downloaded_at + RETENTION_SUCCESS_DOWNLOADED:
                        should_delete = True
                        reason = "Downloaded job expired (10m)"
                elif completed_at:
                    if now > completed_at + RETENTION_SUCCESS_NO_DOWNLOAD:
                        should_delete = True
                        reason = "Completed job (not downloaded) expired (1h)"
            if not completed_at and started_at and now > started_at + 7200:
                should_delete = True
                reason = "Stale processing job (2h)"
        else:
            check_path = os.path.join(upload_dir, job_id)
            if not os.path.exists(check_path):
                 check_path = os.path.join(output_dir, job_id)
            if os.path.exists(check_path):
                 mtime = os.path.getmtime(check_path)
                 if now > mtime + RETENTION_ORPHAN:
                     should_delete = True
                     reason = "Orphaned job expired (1h fallback)"

        if should_delete:
            logging.info(f"Deleting job {job_id}. Reason: {reason}")
            for base in [upload_dir, output_dir]:
                p = os.path.join(base, job_id)
                if os.path.exists(p):
                    try:
                        shutil.rmtree(p)
                    except Exception as e:
                        logging.error(f"Error deleting {p}: {e}")
            redis_client.delete(key)
