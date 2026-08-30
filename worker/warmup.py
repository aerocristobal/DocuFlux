import os
import time
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
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
INFERENCE_SERVER_KEY = "marker:inference_server"

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

            # Marker 2 topology: report the shared Surya inference server's
            # reachability too. 'marker:inference_server' is refreshed by
            # check_inference_server() below; 'unknown' just means the probe
            # has not run yet. Informational only — it never flips this
            # endpoint's 200/503, because the worker attaches lazily and a
            # momentarily unreachable server is not a broken sidecar.
            try:
                inference_server = r.get(INFERENCE_SERVER_KEY) or 'unknown'
            except Exception:
                inference_server = 'unknown'

            payload = '{"status": "%s", "marker_warm": %s, "inference_server": "%s"}'
            warm_json = 'true' if marker_warm else 'false'

            if os.path.exists(MODELS_READY_FILE):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write((payload %
                                   ('OK', warm_json, inference_server)).encode())
            else:
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write((payload %
                                   ('Initializing', warm_json, inference_server)).encode())
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
    Returns GPU info dict with status, model, VRAM, CUDA version, etc.
    """
    try:
        import torch
        import subprocess

        if not torch.cuda.is_available():
            # No GPU detected
            gpu_info = {"status": "unavailable"}
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
                "cuda_version": torch.version.cuda if torch.version.cuda else "unknown"
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

        # Store in Redis
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
        gpu_info = {"status": "unavailable", "error": str(e)}
        r.hset("marker:gpu_info", mapping=gpu_info)
        r.set("marker:gpu_status", "unavailable")
        return gpu_info

def check_inference_server():
    """Probe the shared Surya inference server (marker 2 topology).

    Under marker 2 the worker is a thin client: model weights live in the
    surya-vlm process addressed by SURYA_INFERENCE_URL, so "Marker is warm"
    really means "the shared server is reachable". This probes its /health
    endpoint with a short timeout, stores the result in Redis for the web
    tier's service status, and returns it for /healthz. Best-effort — any
    failure degrades to 'unreachable', never raises.

    When SURYA_INFERENCE_URL is not set the deployment lets the worker manage
    models in-process; there is nothing external to probe, so the status is
    'not_configured'.
    """
    url = os.environ.get("SURYA_INFERENCE_URL")
    if not url:
        status = "not_configured"
    else:
        try:
            resp = requests.get(f"{url.rstrip('/')}/health", timeout=2)
            status = "reachable" if resp.status_code == 200 else "unreachable"
        except Exception as e:
            logging.warning(f"Inference server probe failed ({url}): {e}")
            status = "unreachable"
    try:
        r.set(INFERENCE_SERVER_KEY, status)
    except Exception as e:
        logging.debug(f"Inference server status not persisted: {e}")
    return status


def warmup():
    global slm_model
    logging.info("Starting Marker and SLM warmup...")
    r.set(STATUS_KEY, "initializing")
    r.set(ETA_KEY, "Estimating...")

    gpu_info = check_gpu_availability()

    # INFERENCE_RAM is gone under marker 2: create_model_dict() ignores it
    # (device placement happens in the surya inference server process, sized
    # by VLLM_GPU_MEMORY_UTILIZATION server-side), so the old env write was
    # dead config. The GPU check below only decides SLM layer offload now.
    if gpu_info["status"] == "available" and "vram_total" in gpu_info:
        n_gpu_layers = -1
    else:
        n_gpu_layers = 0

    inference_server_status = check_inference_server()
    logging.info(f"Inference server status: {inference_server_status} "
                 f"(GPU status: {gpu_info['status']})")
    
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
        if gpu_info["status"] == "available":
            logging.info("Verifying Marker models are cached (lazy loading mode)...")
            cache_dir = os.path.expanduser("~/.cache/huggingface")
            if os.path.exists(cache_dir):
                logging.info(f"Marker model cache verified at {cache_dir}")
                logging.info("Models will be loaded on-demand when first PDF conversion is requested")
            else:
                logging.warning("Marker model cache not found - models will download on first use")
        else:
            logging.info("GPU unavailable - Marker tasks will be disabled")
        
        with open(MODELS_READY_FILE, 'w') as f:
            f.write("ready")
            
        r.set(STATUS_KEY, "ready")
        r.set(ETA_KEY, "0s")
        r.set(VRAM_KEY, "Checking...")
        
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
    
    # Keep alive to serve health checks; refresh the inference-server probe
    # so /healthz and the web tier's service status track the shared surya
    # server even long after startup.
    while True:
        time.sleep(10)
        check_inference_server()
