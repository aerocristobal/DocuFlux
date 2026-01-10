import requests
import time
import os
import sys

BASE_URL = "http://localhost:5000"

def wait_for_service():
    print("Waiting for web service...")
    retries = 30
    while retries > 0:
        try:
            requests.get(BASE_URL)
            print("Web service is up!")
            return True
        except requests.exceptions.ConnectionError:
            time.sleep(2)
            retries -= 1
            print(".", end="", flush=True)
    print("\nService failed to start.")
    return False

def run_conversion(session, filename, from_fmt, to_fmt, test_name, save_to=None):
    print(f"\n--- {test_name} ({filename} -> {to_fmt}) ---")
    file_path = os.path.join("tests/samples", filename)
    
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return False

    with open(file_path, 'rb') as f:
        files = {'file': f}
        data = {'from_format': from_fmt, 'to_format': to_fmt}
        try:
            r = session.post(f"{BASE_URL}/convert", files=files, data=data)
            if r.status_code != 200:
                print(f"Submission failed: {r.text}")
                return False
            
            job_id = r.json().get('job_id')
            print(f"Job submitted: {job_id}")
            
            # Poll for status
            for _ in range(60): # 60 * 2s = 120s timeout (Marker is slow)
                status_r = session.get(f"{BASE_URL}/api/jobs")
                jobs = status_r.json()
                my_job = next((j for j in jobs if j['id'] == job_id), None)
                
                if my_job:
                    print(f"Status: {my_job['status']}")
                    if my_job['status'] == 'SUCCESS':
                        # Try downloading
                        dl_r = session.get(f"{BASE_URL}{my_job['download_url']}")
                        if dl_r.status_code == 200:
                            print("Download successful!")
                            if save_to:
                                with open(save_to, 'wb') as f_out:
                                    f_out.write(dl_r.content)
                                print(f"Saved output to {save_to}")
                            return True
                        else:
                            print(f"Download failed: {dl_r.status_code}")
                            return False
                    elif my_job['status'] == 'FAILURE':
                        print(f"Job failed: {my_job.get('result')}")
                        return False
                
                time.sleep(2)
            
            print("Timeout waiting for completion")
            return False
            
        except Exception as e:
            print(f"Exception: {e}")
            return False

def main():
    if not wait_for_service():
        sys.exit(1)
    
    session = requests.Session()
    
    # Prerequisite: Generate a PDF for testing Marker
    print("\n[Setup] Generating PDF for Marker test...")
    if not run_conversion(session, "test.md", "markdown", "pdf", "Setup: Generate PDF", "tests/samples/generated_test.pdf"):
        print("Failed to generate PDF. Skipping Marker test.")
        marker_result = "SKIPPED"
    else:
        marker_result = "PENDING"

    tests = [
        ("test.md", "markdown", "docx", "Markdown to Docx"),
        ("test.html", "html", "epub3", "HTML to EPUB"),
    ]
    
    results = {}
    
    if marker_result == "PENDING":
        success = run_conversion(session, "generated_test.pdf", "pdf_marker", "markdown", "PDF (Marker) to Markdown")
        results["PDF (Marker) to Markdown"] = "PASS" if success else "FAIL"
    
    for filename, from_fmt, to_fmt, name in tests:
        success = run_conversion(session, filename, from_fmt, to_fmt, name)
        results[name] = "PASS" if success else "FAIL"
        
    print("\n--- Summary ---")
    for name, res in results.items():
        print(f"{name}: {res}")

if __name__ == "__main__":
    main()
