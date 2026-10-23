"""On-demand Surya lifecycle hooks for the Marker tasks.

Before a task calls _run_marker it enters ``inference_session(job_id)``, which
registers the job as in flight, waits for the shared inference server to be
healthy (the surya-controller starts it on demand), and on exit records
last-used and clears the in-flight marker. A daemon thread keeps both fresh
during long jobs so the idle timer never unloads Surya mid-conversion.
"""
import logging
import os
import threading
import time
from contextlib import contextmanager

import requests

import tasks as _pkg
import surya_state

HEALTH_POLL_INTERVAL_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 60.0


class InferenceServerTimeout(RuntimeError):
    """The Surya inference server did not become healthy in time."""


def _healthy(url):
    try:
        return requests.get(surya_state.health_url(url), timeout=2).status_code == 200
    except Exception:
        return False


def _best_effort(fn, *args):
    """Activity tracking must never fail a conversion."""
    try:
        fn(_pkg.redis_client, *args)
    except Exception as e:
        logging.warning("Surya activity tracking failed (%s): %s", getattr(fn, "__name__", fn), e)


def ensure_inference_server(task_id, job_id=None):
    """Register task_id as in flight and block until the server is healthy.

    Returns immediately when the server is already healthy, when no external
    server is configured (SURYA_INFERENCE_URL unset), or in cpu_only mode
    (the routes reject Marker formats there). Raises InferenceServerTimeout
    after surya_cold_start_timeout_seconds.
    """
    _best_effort(surya_state.mark_inflight, task_id)
    _best_effort(surya_state.touch_last_used)

    url = os.environ.get("SURYA_INFERENCE_URL")
    if not url:
        return
    try:
        mode = surya_state.get_ocr_settings(_pkg.redis_client, _pkg.app_settings)["mode"]
    except Exception:
        mode = None
    if mode == "on_demand":
        # Re-assert in case the controller's idle unload overwrote the web
        # tier's request; harmless when it is already running.
        _best_effort(surya_state.set_desired, "running")
    if mode == "cpu_only" or _healthy(url):
        return

    timeout = _pkg.app_settings.surya_cold_start_timeout_seconds
    if job_id:
        _pkg.update_job_metadata(job_id, {'stage': 'Starting AI engine'})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(HEALTH_POLL_INTERVAL_SECONDS)
        _best_effort(surya_state.mark_inflight, task_id)
        if _healthy(url):
            return
    raise InferenceServerTimeout(
        f"The AI engine did not start within {timeout} seconds. Please try again."
    )


@contextmanager
def inference_session(task_id, job_id=None):
    """Wrap a Marker run: ensure the server, heartbeat, then release."""
    stop = threading.Event()

    def _heartbeat():
        while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            _best_effort(surya_state.mark_inflight, task_id)
            _best_effort(surya_state.touch_last_used)

    thread = threading.Thread(target=_heartbeat, daemon=True, name="surya-heartbeat")
    try:
        ensure_inference_server(task_id, job_id)
        thread.start()
        yield
    finally:
        stop.set()
        _best_effort(surya_state.touch_last_used)
        _best_effort(surya_state.clear_inflight, task_id)
