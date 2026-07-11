import time
import logging
import requests


def build_job_metadata(filename, from_format, to_format, status='PENDING',
                        created_at=None, progress=None, **extra):
    """Build the metadata dict for a newly-created job (Story 6.4b).

    Consolidates the job-creation shape duplicated across
    web/routes/conversion.py (convert, retry_job, api_v1_convert) and
    web/routes/capture.py. Extra fields specific to a call site
    (force_ocr, use_llm, engine, session_id, ...) pass through via
    **extra so each caller's existing field set is preserved exactly.
    """
    metadata = {
        'status': status,
        'created_at': created_at if created_at is not None else str(time.time()),
        'filename': filename,
        'from': from_format,
        'to': to_format,
    }
    if progress is not None:
        metadata['progress'] = progress
    metadata.update(extra)
    return metadata


def update_job_metadata(redis_client, socketio, job_id, updates):
    """Write updates to Redis hash and emit WebSocket event."""
    redis_client.hset(f"job:{job_id}", mapping=updates)
    try:
        if socketio:
            socketio.emit('job_update', {'id': job_id, **updates}, namespace='/')
    except Exception as e:
        logging.error(f"WebSocket emit failed for job {job_id}: {e}")


def get_job_metadata(redis_client, job_id):
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


def fire_webhook(redis_client, job_id, status, extra=None):
    """Fire webhook POST if URL registered for this job."""
    try:
        meta = redis_client.hget(f"job:{job_id}", 'webhook_url')
        if not meta:
            return
        webhook_url = meta.decode('utf-8') if isinstance(meta, bytes) else meta

        # Defense-in-depth SSRF check (registration also validates)
        from web.validation import validate_webhook_url
        is_valid, error = validate_webhook_url(webhook_url)
        if not is_valid:
            logging.warning(f"Webhook SSRF blocked for job {job_id}: {error}")
            return

        payload = {'job_id': job_id, 'status': status, 'timestamp': str(time.time())}
        if extra:
            payload.update(extra)
        requests.post(webhook_url, json=payload, timeout=5)
        logging.info(f"Webhook fired for job {job_id} -> {webhook_url} (status={status})")
    except Exception as e:
        logging.warning(f"Webhook delivery failed for job {job_id}: {e}")
