"""
Maintenance tasks: cleanup and metrics.
"""

import os
import time
import shutil
import logging

import tasks as _pkg


def _get_disk_usage_percent(path='/app/data'):
    """Get disk usage percentage for the given path."""
    try:
        total, used, free = shutil.disk_usage(path)
        return (used / total) * 100
    except Exception as e:
        logging.error(f"Error getting disk usage: {e}")
        return 0.0


def _get_directory_size(path):
    """Get total size of a directory in bytes."""
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
    """Decide whether a job should be deleted and return (should_delete, reason, priority)."""
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


@_pkg.celery.task(name='tasks.cleanup_old_files')
def cleanup_old_files():
    """Performs intelligent cleanup of old job files based on retention policies."""
    RETENTION_SUCCESS_NO_DOWNLOAD = 3600
    RETENTION_SUCCESS_DOWNLOADED = 600
    RETENTION_FAILURE = 300
    RETENTION_ORPHAN = 3600

    upload_dir = os.environ.get('UPLOAD_FOLDER', 'data/uploads')
    output_dir = os.environ.get('OUTPUT_FOLDER', 'data/outputs')

    job_ids = set()
    if os.path.exists(upload_dir):
        job_ids.update(os.listdir(upload_dir))
    if os.path.exists(output_dir):
        job_ids.update(os.listdir(output_dir))

    disk_usage_percent = _pkg._get_disk_usage_percent(upload_dir)
    emergency_cleanup = disk_usage_percent > 95

    if emergency_cleanup:
        logging.warning(f"EMERGENCY CLEANUP: Disk usage at {disk_usage_percent:.1f}%")
    elif disk_usage_percent > 80:
        logging.info(f"Aggressive cleanup: Disk usage at {disk_usage_percent:.1f}%")

    logging.info(f"Running cleanup. Found {len(job_ids)} jobs on disk.")

    now = time.time()
    deletion_candidates = []

    for job_id in job_ids:
        if not _pkg.is_valid_uuid(job_id):
            continue

        meta = _pkg.get_job_metadata(job_id)
        should_delete, reason, priority = _pkg._job_retention_decision(
            job_id, meta, now, upload_dir, output_dir,
            RETENTION_FAILURE, RETENTION_SUCCESS_DOWNLOADED,
            RETENTION_SUCCESS_NO_DOWNLOAD, RETENTION_ORPHAN,
            emergency_cleanup
        )

        if should_delete:
            upload_path = os.path.join(upload_dir, job_id)
            output_path = os.path.join(output_dir, job_id)
            total_size = (
                (_pkg._get_directory_size(upload_path) if os.path.exists(upload_path) else 0) +
                (_pkg._get_directory_size(output_path) if os.path.exists(output_path) else 0)
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

        _pkg.redis_client.delete(candidate['key'])

    logging.info(f"Cleanup complete. Freed {total_freed / (1024 * 1024):.2f} MB")

    try:
        for key in _pkg.redis_client.scan_iter("capture:session:*"):
            if _pkg.redis_client.ttl(key) == -1:
                _pkg.redis_client.delete(key)
                logging.info(f"Deleted orphaned capture session key: {key}")
    except Exception as e:
        logging.warning(f"Error cleaning up capture session keys: {e}")


@_pkg.celery.task(name='tasks.update_metrics')
def update_metrics():
    """Periodic task to update queue metrics."""
    try:
        from metrics import update_queue_metrics
        update_queue_metrics(_pkg.redis_client)
    except Exception as e:
        logging.error(f"Error updating metrics: {e}")
