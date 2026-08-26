"""Health check route handlers."""

import time
import json
import httpx
import shutil
import logging

from flask import Blueprint, jsonify

import web.app as _app_mod

health_bp = Blueprint('health', __name__)


@health_bp.route('/healthz')
@_app_mod.limiter.exempt
def healthz():
    """Liveness/probe - is the inference server ready?

    In v2 client/server mode, this probes the inference server's /healthz
    endpoint rather than relying on a Redis flag set by a process that loaded
    nothing. Reports not-ready when the inference server is unreachable.
    """
    try:
        inference_url = os.environ.get('INFERENCE_SERVER_URL', 'http://localhost:8080/healthz')
        response = httpx.get(inference_url, timeout=2.0)
        if response.status_code == 200:
            body = response.json()
            marker_warm = body.get('marker_warm', False)
            # Report readiness based on actual server state, not a stale flag
            return jsonify({
                'status': 'OK',
                'marker_warm': marker_warm,
                'inference_server_reachable': True,
                'timestamp': time.time()
            }), 200
        else:
            return jsonify({
                'status': 'Initializing',
                'marker_warm': False,
                'inference_server_reachable': True,
                'timestamp': time.time()
            }), 503
    except (httpx.ConnectError, httpx.TimeoutException):
        # Inference server is unreachable
        return jsonify({
            'status': 'not-ready',
            'marker_warm': False,
            'inference_server_reachable': False,
            'error': 'Inference server unreachable',
            'timestamp': time.time()
        }), 503
    except Exception as e:
        logging.error(f"Health check failed: {e}")
        return jsonify({
            'status': 'not-ready',
            'marker_warm': False,
            'inference_server_reachable': False,
            'error': str(e),
            'timestamp': time.time()
        }), 503


@health_bp.route('/readyz')
def readyz():
    """Readiness probe - is the service ready to accept traffic?"""
    try:
        _app_mod.redis_client.ping()
        return jsonify({
            'status': 'ready',
            'redis': 'connected',
            'timestamp': time.time()
        }), 200
    except Exception as e:
        logging.error(f"Readiness check failed: {e}")
        return jsonify({
            'status': 'not_ready',
            'error': 'Could not connect to Redis',
            'timestamp': time.time()
        }), 503


@health_bp.route('/api/health')
def health_detailed():
    """Detailed health check with component status."""
    health_status = {
        'status': 'healthy',
        'timestamp': time.time(),
        'components': {}
    }

    try:
        _app_mod.redis_client.ping()
        health_status['components']['redis'] = {
            'status': 'up',
            'response_time_ms': 'OK'
        }
    except Exception as e:
        logging.error(f"Health check failed for Redis: {e}")
        health_status['status'] = 'unhealthy'
        health_status['components']['redis'] = {
            'status': 'down',
            'error': 'Could not connect to Redis'
        }

    try:
        total, used, free = shutil.disk_usage('/app/data')
        used_percent = (used / total) * 100
        health_status['components']['disk'] = {
            'status': 'ok' if used_percent < 90 else 'warning',
            'total_gb': round(total / (1024**3), 2),
            'used_gb': round(used / (1024**3), 2),
            'free_gb': round(free / (1024**3), 2),
            'used_percent': round(used_percent, 1)
        }
        if used_percent >= 95:
            health_status['components']['disk']['status'] = 'critical'
            health_status['status'] = 'degraded'
    except Exception as e:
        logging.error(f"Health check failed for disk space: {e}")
        health_status['components']['disk'] = {
            'status': 'unknown',
            'error': 'Could not read disk space'
        }

    try:
        gpu_status = _app_mod.redis_client.get('marker:gpu_status')
        gpu_info = _app_mod.redis_client.hgetall('marker:gpu_info')
        health_status['components']['gpu'] = {
            'status': gpu_status or 'unknown',
            'info': gpu_info if gpu_info else {}
        }
    except Exception as e:
        logging.error(f"Health check failed for GPU status: {e}")
        health_status['components']['gpu'] = {
            'status': 'unknown',
            'error': 'Could not query GPU status from Redis'
        }

    # Check Celery worker availability (read from Redis cache set by update_metrics task)
    try:
        worker_cache = _app_mod.redis_client.hgetall('workers:status')
        if worker_cache:
            updated_at = float(worker_cache.get('updated_at', 0))
            stale = (time.time() - updated_at) > 300  # >5 min = stale
            if stale:
                health_status['components']['celery_workers'] = {
                    'status': 'unknown', 'reason': 'cached status stale (>5min)'}
            else:
                count = int(worker_cache.get('worker_count', 0))
                health_status['components']['celery_workers'] = {
                    'status': worker_cache.get('status', 'unknown'),
                    'worker_count': count}
                if count == 0:
                    health_status['status'] = 'degraded'
        else:
            health_status['components']['celery_workers'] = {
                'status': 'unknown', 'reason': 'no cached worker status'}
    except Exception as e:
        logging.error(f"Health check failed for Celery workers: {e}")
        health_status['components']['celery_workers'] = {
            'status': 'unknown',
            'error': 'Could not read worker status from Redis'
        }

    status_code = 200
    if health_status['status'] == 'unhealthy':
        status_code = 503

    return jsonify(health_status), status_code


@health_bp.route('/api/status/services')
@_app_mod.limiter.exempt
def service_status():
    """Retrieves the current status of various application services."""
    status = {'disk_space': 'ok'}
    if not _app_mod.check_disk_space():
        status['disk_space'] = 'low'

    # In v2, marker readiness is determined by the inference server's actual state,
    # not by a Redis flag set at Celery import time. Probe the inference server.
    try:
        inference_url = os.environ.get('INFERENCE_SERVER_URL', 'http://localhost:8080/healthz')
        inference_reachable = False
        marker_status = "unavailable"
        try:
            resp = httpx.get(inference_url, timeout=2.0)
            if resp.status_code == 200:
                inference_reachable = True
                marker_status = resp.json().get('status', 'unknown') or 'unknown'
        except Exception:
            pass
        status['marker'] = marker_status
        status['marker_status'] = marker_status
        status['inference_server_reachable'] = inference_reachable
    except Exception as e:
        logging.error(f"Error checking inference server status: {e}")
        status['marker'] = 'error'
        status['marker_status'] = 'error'
        status['inference_server_reachable'] = False

    # LLM download ETA is no longer set from the worker in v2
    status['llm_download_eta'] = "managed by inference server"

    # GPU status now reflects the inference server's headroom, not the worker's
    try:
        gpu_status = _app_mod.redis_client.get("marker:gpu_status") or "initializing"
        gpu_info_raw = _app_mod.redis_client.hgetall("marker:gpu_info")
        status['gpu_status'] = gpu_status
        if gpu_info_raw:
            gpu_info = {}
            for key, value in gpu_info_raw.items():
                if isinstance(key, bytes):
                    key = key.decode('utf-8')
                if isinstance(value, bytes):
                    value = value.decode('utf-8')
                try:
                    if '.' in value:
                        gpu_info[key] = float(value)
                    elif value.isdigit():
                        gpu_info[key] = int(value)
                    else:
                        gpu_info[key] = value
                except (ValueError, AttributeError):
                    gpu_info[key] = value
            # Rename VRAM key to clarify it describes inference server headroom
            if 'vram_available' in gpu_info:
                gpu_info['inference_vram_available_gb'] = gpu_info.pop('vram_available')
            if 'vram_total' in gpu_info:
                gpu_info['inference_vram_total_gb'] = gpu_info.pop('vram_total')
            status['gpu_info'] = gpu_info
        else:
            status['gpu_info'] = {"status": "initializing", "note": "VRAM describes inference server headroom"}
    except Exception as e:
        logging.error(f"Error checking GPU status: {e}")
        status['gpu_status'] = 'unavailable'
        status['gpu_info'] = {"status": "unavailable", "error": "GPU status unavailable"}

    # Check Celery worker availability (read from Redis cache set by update_metrics task)
    try:
        worker_cache = _app_mod.redis_client.hgetall('workers:status')
        if worker_cache:
            updated_at = float(worker_cache.get('updated_at', 0))
            stale = (time.time() - updated_at) > 300  # >5 min = stale
            if stale:
                health_status['components']['celery_workers'] = {
                    'status': 'unknown', 'reason': 'cached status stale (>5min)'}
            else:
                count = int(worker_cache.get('worker_count', 0))
                health_status['components']['celery_workers'] = {
                    'status': worker_cache.get('status', 'unknown'),
                    'worker_count': count}
                if count == 0:
                    health_status['status'] = 'degraded'
        else:
            health_status['components']['celery_workers'] = {
                'status': 'unknown', 'reason': 'no cached worker status'}
    except Exception as e:
        logging.error(f"Health check failed for Celery workers: {e}")
        health_status['components']['celery_workers'] = {
            'status': 'unknown',
            'error': 'Could not read worker status from Redis'}

    status_code = 200
    if health_status['status'] == 'unhealthy':
        status_code = 503

    return jsonify(health_status), status_code
