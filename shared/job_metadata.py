import time
import logging
import requests


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
        payload = {'job_id': job_id, 'status': status, 'timestamp': str(time.time())}
        if extra:
            payload.update(extra)
        requests.post(webhook_url, json=payload, timeout=5)
        logging.info(f"Webhook fired for job {job_id} -> {webhook_url} (status={status})")
    except Exception as e:
        logging.warning(f"Webhook delivery failed for job {job_id}: {e}")
