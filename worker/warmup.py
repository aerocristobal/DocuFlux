import os
import time
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from llama_cpp import Llama

from config import settings
from settings_loader import load_settings
from redis_client import create_redis_client

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

MODELS_READY_FILE = "/tmp/models_ready"
STATUS_KEY = "service:marker:status"
ETA_KEY = "service:marker:eta"
VRAM_KEY = "service:marker:gpu_vram_free"

slm_model = None


# Story 4.1b: route through the same TLS-aware factory as every other Redis
# consumer, instead of a raw redis.StrictRedis.from_url() that skips the
# ssl_cert_reqs/ca_certs/certfile/keyfile kwargs the rest of the app requires.
_app_settings = load_settings(settings)
redis_url = os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1')
r = create_redis_client(redis_url, _app_settings, decode_responses=True)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/healthz':
            # Story 6.2: report Marker warm/cold state alongside the existing
            # ready/initializing signal. marker:model_warm is set by the
            # Celery worker process (worker/tasks/__init__.py) — a separate
            # process from this one — via Redis, since eager warmup happens
            # in the worker process, not here.
            try:
                marker_warm = r.get('marker:model_warm') == 'true'
            except Exception:
                marker_warm = False

            if os.path.exists(MODELS_READY_FILE):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(('{"status": "OK", "marker_warm": %s}' %
                                   ('true' if marker_warm else 'false')).encode())
            else:
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(('{"status": "Initializing", "marker_warm": %s}' %
                                   ('true' if marker_warm else 'false')).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass # Suppress HTTP logs

def start_health_server():
    server_address = ('', 8080)
    httpd = HTTPServer(server_address, HealthHandler)
    logging.info("Starting health check server on port 8080")
    httpd.serve_forever()

def check_gpu_availability():
    """
    Detect GPU availability and store detailed info in Redis.
    In v2 client/server mode, the worker process holds no models —
    VRAM figures should describe the inference server's headroom, not the worker's.

    Returns GPU info dict with status, model, VRAM, CUDA version, etc.
    """
    try:
        import torch
        import subprocess

        # Check if inference server is reachable (v2 model)
        # The worker process holds no models; VRAM reported should reflect
        # the inference server's GPU headroom, not the worker's VRAM.
        inference_reachable = _check_inference_server_health()

        if not torch.cuda.is_available():
            # No GPU detected
            gpu_info = {"status": "unavailable", "inference_reachable": inference_reachable}
            logging.warning("No GPU detected - running in CPU-only mode")
        else:
            # GPU detected - get detailed information
            device_props = torch.cuda.get_device_properties(0)
            vram_total_gb = device_props.total_memory / 1e9
            vram_allocated_gb = torch.cuda.memory_allocated(0) / 1e9
            vram_available_gb = vram_total_gb - vram_allocated_gb

            gpu_info = {
                "status": "available",
                "model": torch.cuda.get_device_name(0),
                "vram_total": round(vram_total_gb, 2),
                "vram_available": round(vram_available_gb, 2),
                "cuda_version": torch.version.cuda if torch.version.cuda else "unknown",
                "inference_reachable": inference_reachable,
            }

            # Try to get driver version from nvidia-smi
            try:
                result = subprocess.run(
                    ['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    gpu_info["driver_version"] = result.stdout.strip()
                else:
                    gpu_info["driver_version"] = "unknown"
            except Exception:
                gpu_info["driver_version"] = "unknown"

            # Try to get GPU utilization
            try:
                result = subprocess.run(
                    ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    gpu_info["utilization"] = int(result.stdout.strip())
                else:
                    gpu_info["utilization"] = 0
            except Exception:
                gpu_info["utilization"] = 0

            logging.info(f"GPU detected: {gpu_info['model']} with {gpu_info['vram_total']} GB VRAM")

        # Store in Redis - worker VRAM, with inference reachability flag
        r.hset("marker:gpu_info", mapping=gpu_info)
        r.set("marker:gpu_status", gpu_info["status"])

        # Emit WebSocket event so frontend updates immediately without waiting for poll
        try:
            from flask_socketio import SocketIO as FSocketIO
            _redis_url = os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1')
            FSocketIO(message_queue=_redis_url).emit(
                'gpu_status_update',
                {'gpu_status': gpu_info['status'], 'gpu_info': gpu_info},
                namespace='/'
            )
            logging.info("Emitted gpu_status_update via WebSocket")
        except Exception as _e:
            logging.debug(f"WebSocket emit skipped (non-critical): {_e}")

        return gpu_info

    except Exception as e:
        logging.error(f"GPU detection failed: {e}")
        # Fallback to unavailable
        gpu_info = {"status": "unavailable", "error": str(e), "inference_reachable": False}
        r.hset("marker:gpu_info", mapping=gpu_info)
        r.set("marker:gpu_status", "unavailable")
        return gpu_info


def _check_inference_server_health():
    """
    Check if the inference server is reachable and ready.

    In v2 client/server mode, the worker process holds no models —
    model loading happens in the inference server process.
    This probes the server's /health endpoint to determine readiness.

    Returns:
        bool: True if the inference server is reachable and reports ready status.
    """
    import httpx

    inference_url = os.environ.get('INFERENCE_SERVER_URL', 'http://localhost:8080/healthz')
    try:
        response = httpx.get(inference_url, timeout=2.0)
        # Server is reachable and returned a 200 with OK status
        return response.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        # Inference server is unreachable
        return False

def warmup():
    global slm_model
    logging.info("Starting Marker and SLM warmup...")
    r.set(STATUS_KEY, "initializing")
    r.set(ETA_KEY, "Estimating...")

    gpu_info = check_gpu_availability()

    # Under v2 client/server, the worker process holds no models.
    # inference_ram should reflect the inference server's headroom.
    # Since we can't know the server's VRAM from the worker, use a safe default.
    inference_ram = 4  # conservative default for CPU-only / thin worker
    n_gpu_layers = 0

    if gpu_info["status"] == "available" and gpu_info.get("inference_reachable", False):
        # Inference server is reachable — use its reported VRAM headroom
        inference_ram = min(16, int(gpu_info.get("vram_available", 16)))
        n_gpu_layers = -1
    elif gpu_info["status"] == "available":
        # GPU available but inference server unreachable — conservative
        inference_ram = 4
        n_gpu_layers = 0
    else:
        # No GPU
        inference_ram = 4
        n_gpu_layers = 0

    os.environ["INFERENCE_RAM"] = str(inference_ram)
    logging.info(f"Set INFERENCE_RAM={inference_ram} (GPU status: {gpu_info['status']}, inference reachable: {gpu_info.get('inference_reachable', False)})")

    slm_status = "unavailable"
    slm_model_path_env = os.environ.get("SLM_MODEL_PATH")
    default_slm_model_path_dir = "/app/models/TinyLlama-1.1B-Chat-v1.0-GGUF"
    default_slm_model_path_file = os.path.join(default_slm_model_path_dir, "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf")

    model_to_load = None
    if slm_model_path_env and os.path.exists(slm_model_path_env):
        model_to_load = slm_model_path_env
    elif os.path.exists(default_slm_model_path_file):
        model_to_load = default_slm_model_path_file

    if model_to_load:
        logging.info(f"Attempting to load SLM model from: {model_to_load} with n_gpu_layers: {n_gpu_layers}")
        try:
            slm_model = Llama(model_path=model_to_load, n_gpu_layers=n_gpu_layers, verbose=False)
            logging.info("SLM model loaded successfully.")
            slm_status = "ready"
        except Exception as e:
            logging.error(f"Failed to load SLM model: {e}")
            slm_status = "error"
            slm_model = None
    else:
        logging.warning(f"SLM model not found at {default_slm_model_path_file} or via SLM_MODEL_PATH. SLM features will be unavailable.")
        slm_status = "not_found"

    r.set("slm:status", slm_status)

    try:
        # In v2, marker models are loaded on-demand in the inference server,
        # not in the worker. Report GPU availability and let the server handle warmup.
        if gpu_info["status"] == "available":
            logging.info("GPU available - Marker models will load on-demand in inference server")
            # Do NOT set marker:model_warm from the worker — that flag reflected v1
            # model-loading behavior that no longer happens here. The inference server
            # manages its own warmup state independently.
        else:
            logging.info("GPU unavailable - Marker tasks will be disabled")

        # Do not set MODELS_READY_FILE or marker:model_warm from the worker.
        # These reflected v1 behavior where the worker pre-loaded models.
        # Under v2, the inference server manages its own readiness.

        r.set(STATUS_KEY, "ready")
        r.set(ETA_KEY, "0s")
        # Do not set VRAM_KEY from worker — the inference server reports its own VRAM

    except Exception as e:
        logging.error(f"Marker Warmup failed: {e}")
        r.set(STATUS_KEY, "error")

def get_slm_model():
    """Returns the globally loaded SLM model instance."""
    return slm_model

if __name__ == "__main__":
    # Start health server in background thread
    t = threading.Thread(target=start_health_server, daemon=True)
    t.start()
    
    # Run warmup
    warmup()
    
    # Keep alive to serve health checks
    while True:
        time.sleep(10)
