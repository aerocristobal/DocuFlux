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

def check_gpu_memory():
    try:
        # Simple nvidia-smi check if available
        # This is a placeholder as parsing nvidia-smi output requires popen
        # For now, we assume 16GB free if start succeeds or read from env
        pass
    except:
        pass

def warmup():
    logging.info("Starting Marker warmup...")
    r.set(STATUS_KEY, "initializing")
    r.set(ETA_KEY, "Estimating...")
    
    try:
        # Set env var for marker
        os.environ["INFERENCE_RAM"] = "16"
        
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
