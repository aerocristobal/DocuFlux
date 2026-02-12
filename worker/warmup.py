import os
import time
import redis
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from llama_cpp import Llama

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

MODELS_READY_FILE = "/tmp/models_ready"
STATUS_KEY = "service:marker:status"
ETA_KEY = "service:marker:eta"
VRAM_KEY = "service:marker:gpu_vram_free"

slm_model = None


# Connect to Redis
redis_url = os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1')
r = redis.StrictRedis.from_url(redis_url, decode_responses=True)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/healthz':
            if os.path.exists(MODELS_READY_FILE):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"Initializing")
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

        return gpu_info

    except Exception as e:
        logging.error(f"GPU detection failed: {e}")
        # Fallback to unavailable
        gpu_info = {"status": "unavailable", "error": str(e)}
        r.hset("marker:gpu_info", mapping=gpu_info)
        r.set("marker:gpu_status", "unavailable")
        return gpu_info

def warmup():
    global slm_model
    logging.info("Starting Marker and SLM warmup...")
    r.set(STATUS_KEY, "initializing")
    r.set(ETA_KEY, "Estimating...")

    gpu_info = check_gpu_availability()

    if gpu_info["status"] == "available" and "vram_total" in gpu_info:
        inference_ram = min(16, int(gpu_info["vram_total"]))
        n_gpu_layers = -1
    else:
        inference_ram = 4
        n_gpu_layers = 0

    os.environ["INFERENCE_RAM"] = str(inference_ram)
    logging.info(f"Set INFERENCE_RAM={inference_ram} (GPU status: {gpu_info['status']})")
    
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
    
    # Keep alive to serve health checks
    while True:
        time.sleep(10)
