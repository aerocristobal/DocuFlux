import requests
import time
import sys

BASE_URL = "http://localhost:8000"

print("Waiting for Marker API to start...")
start_time = time.time()
while time.time() - start_time < 300: # Wait up to 5 minutes
    try:
        r = requests.get(f"{BASE_URL}/docs")
        if r.status_code == 200:
            print("Marker API is up!")
            sys.exit(0)
    except requests.exceptions.ConnectionError:
        pass
    time.sleep(5)
    print(".", end="", flush=True)

print("\nTimeout waiting for Marker API.")
sys.exit(1)
