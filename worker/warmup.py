import os
import time
import redis
import threading
import logging
import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

MODELS_READY_FILE = "/tmp/models_ready"
STATUS_KEY = "service:marker:status"
ETA_KEY = "service:marker:eta"
VRAM_KEY = "service:marker:gpu_vram_free"

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
    logging.info("Starting Marker warmup...")
    r.set(STATUS_KEY, "initializing")
    r.set(ETA_KEY, "Estimating...")

    # Detect GPU availability
    gpu_info = check_gpu_availability()

    try:
        # Set env var for marker based on detected GPU
        if gpu_info["status"] == "available" and "vram_total" in gpu_info:
            # Use detected VRAM, cap at 16GB for safety
            inference_ram = min(16, int(gpu_info["vram_total"]))
        else:
            # CPU mode or detection failed, use minimal RAM
            inference_ram = 4

        os.environ["INFERENCE_RAM"] = str(inference_ram)
        logging.info(f"Set INFERENCE_RAM={inference_ram} (GPU status: {gpu_info['status']})")
        
        logging.info("Loading Marker models...")
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        import torch
        import gc
        
        # This call will download if missing, or load if present
        converter = PdfConverter(artifact_dict=create_model_dict())
        
        # Release memory so the worker can use it
        del converter
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            
        logging.info("Marker models loaded and verified. Releasing memory.")
        
        # Signal ready
        with open(MODELS_READY_FILE, 'w') as f:
            f.write("ready")
            
        r.set(STATUS_KEY, "ready")
        r.set(ETA_KEY, "0s")
        r.set(VRAM_KEY, "Checking...") # Placeholder
        
    except Exception as e:
        logging.error(f"Warmup failed: {e}")
        r.set(STATUS_KEY, "error")
        # Do not touch ready file

if __name__ == "__main__":
    # Start health server in background thread
    t = threading.Thread(target=start_health_server, daemon=True)
    t.start()
    
    # Run warmup
    warmup()
    
    # Keep alive to serve health checks
    while True:
        time.sleep(10)
