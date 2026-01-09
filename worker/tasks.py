import os
import subprocess
import time
import shutil
import redis
from celery import Celery
from celery.schedules import crontab

# Metadata Redis client (DB 1) with connection pooling
redis_client = redis.Redis.from_url(
    os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1'),
    max_connections=10,
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
    """Update job metadata using Redis Hash (atomic operation)."""
    key = f"job:{job_id}"
    try:
        redis_client.hset(key, mapping=updates)
    except Exception as e:
        print(f"Error updating metadata for {job_id}: {e}")


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
def convert_document(job_id, input_path, output_path, from_format, to_format):
    print(f"Starting conversion for job {job_id}: {from_format} -> {to_format}")
    update_job_metadata(job_id, {'status': 'PROCESSING', 'started_at': str(time.time())})
    
    if not os.path.exists(input_path):
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': 'Input file missing'})
        raise FileNotFoundError(f"Input file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    cmd = [
        'pandoc',
        '-f', from_format,
        '-t', to_format if to_format != 'pdf' else 'pdf',
        input_path,
        '-o', output_path
    ]
    
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=500)
        print(f"Conversion successful: {output_path}")
        update_job_metadata(job_id, {'status': 'SUCCESS', 'completed_at': str(time.time())})
        return {"status": "success", "output_file": os.path.basename(output_path)}
    except subprocess.TimeoutExpired:
        error_msg = "Conversion timed out after 500 seconds"
        print(f"Timeout for job {job_id}: {error_msg}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': error_msg})
        raise Exception(error_msg)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr or e.stdout or "Unknown error"
        print(f"Pandoc error for job {job_id}: {error_msg}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(error_msg)[:500]})
        raise Exception(f"Pandoc failed: {error_msg}")
    except Exception as e:
        print(f"Unexpected error for job {job_id}: {str(e)}")
        update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500]})
        raise

@celery.task(name='tasks.cleanup_old_files')
def cleanup_old_files():
    now = time.time()
    
    # Retention Policies (seconds)
    RETENTION_SUCCESS_NO_DOWNLOAD = 3600 # 1 hour
    RETENTION_SUCCESS_DOWNLOADED = 600   # 10 minutes
    RETENTION_FAILURE = 300              # 5 minutes
    RETENTION_ORPHAN = 3600              # 1 hour (fallback)
    
    upload_dir = '/app/data/uploads'
    output_dir = '/app/data/outputs'
    
    # Collect all job IDs currently on disk
    job_ids = set()
    if os.path.exists(upload_dir):
        job_ids.update(os.listdir(upload_dir))
    if os.path.exists(output_dir):
        job_ids.update(os.listdir(output_dir))
    
    print(f"Running cleanup. Found {len(job_ids)} jobs on disk.")

    for job_id in job_ids:
        # Fetch metadata using Redis Hash
        key = f"job:{job_id}"
        meta = get_job_metadata(job_id)

        should_delete = False
        reason = ""

        if meta:
            status = meta.get('status')
            completed_at = float(meta.get('completed_at', 0)) if meta.get('completed_at') else None
            downloaded_at = float(meta.get('downloaded_at', 0)) if meta.get('downloaded_at') else None
            started_at = float(meta.get('started_at', 0)) if meta.get('started_at') else None

            # Check policies
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

            # Safety net for stuck PROCESSING jobs (e.g. worker crash)
            # If started > 2 hours ago and no completion
            if not completed_at and started_at and now > started_at + 7200:
                should_delete = True
                reason = "Stale processing job (2h)"
        
        else:
            # Orphaned (No metadata) - use file mtime fallback
            check_path = os.path.join(upload_dir, job_id)
            if not os.path.exists(check_path):
                 check_path = os.path.join(output_dir, job_id)
            
            if os.path.exists(check_path):
                 mtime = os.path.getmtime(check_path)
                 if now > mtime + RETENTION_ORPHAN:
                     should_delete = True
                     reason = "Orphaned job expired (1h fallback)"

        if should_delete:
            print(f"Deleting job {job_id}. Reason: {reason}")
            # Delete directories
            for base in [upload_dir, output_dir]:
                p = os.path.join(base, job_id)
                if os.path.exists(p):
                    try:
                        shutil.rmtree(p)
                    except Exception as e:
                        print(f"Error deleting {p}: {e}")
            # Delete metadata
            redis_client.delete(key)
