"""
Prometheus Metrics for DocuFlux Worker

This module defines and exposes Prometheus metrics for monitoring worker performance,
task execution, queue depth, and GPU utilization.

Epic 21.5: Prometheus Metrics Endpoint
"""

import logging
from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, CONTENT_TYPE_LATEST
from flask import Flask, Response

# Epic 21.5: Metrics definitions

# Task execution metrics
conversion_total = Counter(
    'docuflux_conversion_total',
    'Total number of conversions',
    ['format_from', 'format_to', 'status']
)

conversion_duration_seconds = Histogram(
    'docuflux_conversion_duration_seconds',
    'Time spent converting documents',
    ['format_from', 'format_to'],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0]
)

conversion_failures_total = Counter(
    'docuflux_conversion_failures_total',
    'Total number of failed conversions',
    ['format_from', 'format_to', 'error_type']
)

# Queue metrics
queue_depth = Gauge(
    'docuflux_queue_depth',
    'Number of tasks waiting in queue',
    ['queue_name']
)

# GPU metrics (if available)
gpu_utilization = Gauge(
    'docuflux_gpu_utilization_percent',
    'GPU utilization percentage'
)

gpu_memory_used_bytes = Gauge(
    'docuflux_gpu_memory_used_bytes',
    'GPU memory currently in use (bytes)'
)

gpu_memory_total_bytes = Gauge(
    'docuflux_gpu_memory_total_bytes',
    'Total GPU memory available (bytes)'
)

gpu_temperature_celsius = Gauge(
    'docuflux_gpu_temperature_celsius',
    'GPU temperature in Celsius'
)

# Worker metrics
worker_tasks_active = Gauge(
    'docuflux_worker_tasks_active',
    'Number of tasks currently being processed'
)

worker_info = Info(
    'docuflux_worker',
    'Information about the worker'
)

# System metrics
disk_usage_bytes = Gauge(
    'docuflux_disk_usage_bytes',
    'Disk space used by DocuFlux data (bytes)',
    ['path']
)

disk_total_bytes = Gauge(
    'docuflux_disk_total_bytes',
    'Total disk space available (bytes)',
    ['path']
)


def update_gpu_metrics():
    """
    Update GPU-related metrics using nvidia-smi or PyTorch.

    This function is called periodically to keep GPU metrics current.
    If GPU is unavailable, metrics are not updated (remain at 0).
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return

        # Get GPU utilization and memory
        device_props = torch.cuda.get_device_properties(0)
        total_memory = device_props.total_memory
        allocated_memory = torch.cuda.memory_allocated(0)

        gpu_memory_total_bytes.set(total_memory)
        gpu_memory_used_bytes.set(allocated_memory)

        # Try to get utilization and temperature from nvidia-smi
        try:
            import subprocess
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=utilization.gpu,temperature.gpu',
                 '--format=csv,noheader,nounits'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                utilization, temperature = result.stdout.strip().split(',')
                gpu_utilization.set(float(utilization.strip()))
                gpu_temperature_celsius.set(float(temperature.strip()))
        except Exception as e:
            logging.warning(f"Could not get GPU utilization from nvidia-smi: {e}")

    except Exception as e:
        logging.error(f"Error updating GPU metrics: {e}")


def update_disk_metrics(data_path='/app/data'):
    """
    Update disk usage metrics for the data directory.

    Args:
        data_path: Path to the data directory to monitor
    """
    try:
        import shutil

        # Get disk usage stats
        total, used, free = shutil.disk_usage(data_path)

        disk_total_bytes.labels(path=data_path).set(total)
        disk_usage_bytes.labels(path=data_path).set(used)

    except Exception as e:
        logging.error(f"Error updating disk metrics: {e}")


def update_queue_metrics(redis_client):
    """
    Update queue depth metrics from Celery/Redis.

    Args:
        redis_client: Redis connection to query queue lengths
    """
    try:
        # Get queue lengths from Redis
        celery_queue_len = redis_client.llen('celery')

        queue_depth.labels(queue_name='celery').set(celery_queue_len)

    except Exception as e:
        logging.error(f"Error updating queue metrics: {e}")


# Flask app for metrics endpoint
metrics_app = Flask('metrics')


@metrics_app.route('/metrics')
def metrics():
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus format for scraping.
    """
    # Update metrics before serving
    update_gpu_metrics()
    update_disk_metrics()

    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@metrics_app.route('/healthz')
def healthz():
    """
    Liveness probe for metrics server.
    """
    return 'OK', 200


def start_metrics_server(port=9090, host='0.0.0.0'):
    """
    Start the metrics HTTP server.

    Args:
        port: Port to listen on (default: 9090)
        host: Host to bind to (default: 0.0.0.0)
    """
    logging.info(f"Starting Prometheus metrics server on {host}:{port}")
    metrics_app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    # For testing
    logging.basicConfig(level=logging.INFO)
    start_metrics_server()
