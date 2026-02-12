import requests

BASE_URL = "http://localhost:8000"

def check_endpoint(method, path, files=None):
    url = f"{BASE_URL}{path}"
    try:
        if method == "GET":
            r = requests.get(url)
        elif method == "POST":
            r = requests.post(url, files=files)
        
        print(f"{method} {path}: {r.status_code}")
        if r.status_code != 200:
            print(f"Response: {r.text[:500]}")
    except Exception as e:
        print(f"{method} {path}: Failed - {e}")

print("Checking Marker API endpoints...")
check_endpoint("GET", "/health")
check_endpoint("GET", "/docs") # FastAPI docs
check_endpoint("POST", "/convert", files={'pdf_file': ('test.pdf', b'dummy content', 'application/pdf')})
