"""
DocuFlux Load Testing Suite

Tests the full API surface under concurrent load using Locust.

Usage:
    # Install: pip install locust
    # Headless run (CI):
    locust -f tests/load/locustfile.py \
        --headless -u 20 -r 5 --run-time 60s \
        --host http://localhost:5000 \
        --csv=tests/load/results

    # Interactive UI:
    locust -f tests/load/locustfile.py --host http://localhost:5000

Environment variables:
    DOCUFLUX_API_KEY  - API key for /api/v1/ endpoints (optional)
    LOCUST_HOST       - Base URL (default: http://localhost:5000)

Thresholds (checked by run_load_test.sh):
    p95 response time < 2000ms for /api/v1/convert
    p95 response time < 200ms  for /api/v1/status
    error rate < 1%
"""
import os
import io
import json
import uuid
from locust import HttpUser, task, between, events, constant_pacing
from locust.exception import RescheduleTask

API_KEY = os.environ.get('DOCUFLUX_API_KEY', '')
API_HEADERS = {'X-API-Key': API_KEY} if API_KEY else {}

# Minimal sample files for load testing
_MARKDOWN_CONTENT = b'# Load Test\n\nThis is a load test document.\n\n## Section\n\nSome content.\n'
_HTML_CONTENT = b'<html><body><h1>Load Test</h1><p>Content</p></body></html>'


class WebUIUser(HttpUser):
    """
    Simulates a browser user using the Web UI endpoints (no API key).
    Represents 70% of traffic.
    """
    weight = 7
    wait_time = between(1, 3)

    def on_start(self):
        self.job_ids = []

    @task(3)
    def view_index(self):
        """Load the main page."""
        self.client.get('/', name='GET /')

    @task(2)
    def list_jobs(self):
        """Poll job list."""
        self.client.get('/api/jobs', name='GET /api/jobs')

    @task(3)
    def submit_markdown_to_html(self):
        """Submit a small markdown→html conversion."""
        data = {
            'file': ('test.md', io.BytesIO(_MARKDOWN_CONTENT), 'text/markdown'),
            'from_format': 'markdown',
            'to_format': 'html',
        }
        with self.client.post(
            '/convert',
            files=data,
            name='POST /convert (md→html)',
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                try:
                    job_ids = resp.json().get('job_ids', [])
                    self.job_ids.extend(job_ids)
                except Exception:
                    resp.failure('Non-JSON response')
            elif resp.status_code == 507:
                resp.success()  # Disk full is a valid operational state
            else:
                resp.failure(f'Unexpected status {resp.status_code}')

    @task(2)
    def check_service_status(self):
        """Poll service health."""
        self.client.get('/api/status/services', name='GET /api/status/services')

    @task(1)
    def health_check(self):
        """Liveness probe."""
        self.client.get('/healthz', name='GET /healthz')


class ApiUser(HttpUser):
    """
    Simulates an API integration client using /api/v1/ endpoints.
    Represents 30% of traffic.
    """
    weight = 3
    wait_time = between(0.5, 2)

    def on_start(self):
        self.job_ids = []
        self.headers = dict(API_HEADERS)

    @task(4)
    def submit_and_poll(self):
        """Full workflow: submit job → poll status until complete (or timeout)."""
        data = {
            'file': ('document.md', io.BytesIO(_MARKDOWN_CONTENT), 'text/markdown'),
            'from_format': 'markdown',
            'to_format': 'html',
        }
        with self.client.post(
            '/api/v1/convert',
            files=data,
            headers=self.headers,
            name='POST /api/v1/convert',
            catch_response=True
        ) as resp:
            if resp.status_code == 202:
                job_id = resp.json().get('job_id')
                if job_id:
                    self.job_ids.append(job_id)
            elif resp.status_code in (401, 403):
                resp.success()  # No API key in this run — expected
            elif resp.status_code == 507:
                resp.success()  # Disk full
            else:
                resp.failure(f'Unexpected {resp.status_code}')

    @task(5)
    def poll_status(self):
        """Poll status of a known job (or a random UUID to test 404 path)."""
        job_id = self.job_ids[-1] if self.job_ids else str(uuid.uuid4())
        self.client.get(
            f'/api/v1/status/{job_id}',
            name='GET /api/v1/status/{job_id}'
        )

    @task(1)
    def list_formats(self):
        """List available formats — should be very fast (no DB)."""
        self.client.get('/api/v1/formats', name='GET /api/v1/formats')

    @task(1)
    def readyz(self):
        """Readiness probe."""
        self.client.get('/readyz', name='GET /readyz')


class SpikeUser(HttpUser):
    """
    Simulates burst traffic: rapid fire of small jobs.
    Used to test queue saturation behaviour.
    """
    weight = 1
    wait_time = constant_pacing(0.2)  # 5 req/s per user

    @task
    def burst_submit(self):
        data = {
            'file': ('burst.md', io.BytesIO(b'# Burst\n'), 'text/markdown'),
            'from_format': 'markdown',
            'to_format': 'html',
        }
        with self.client.post(
            '/convert',
            files=data,
            name='POST /convert (burst)',
            catch_response=True
        ) as resp:
            # 429 and 507 are acceptable under spike load
            if resp.status_code in (200, 429, 507):
                resp.success()
            else:
                resp.failure(f'Unexpected {resp.status_code}')


@events.quitting.add_listener
def assert_thresholds(environment, **kwargs):
    """Fail the run if SLA thresholds are breached."""
    stats = environment.runner.stats
    errors = 0

    for name, entry in stats.entries.items():
        if entry.num_requests == 0:
            continue

        error_pct = (entry.num_failures / entry.num_requests) * 100
        p95 = entry.get_response_time_percentile(0.95)

        if name[1] == 'POST /api/v1/convert' and p95 > 2000:
            print(f'SLA BREACH: {name} p95={p95}ms (limit 2000ms)')
            errors += 1

        if name[1] == 'GET /api/v1/status/{job_id}' and p95 > 500:
            print(f'SLA BREACH: {name} p95={p95}ms (limit 500ms)')
            errors += 1

        if error_pct > 1.0:
            print(f'SLA BREACH: {name} error_rate={error_pct:.1f}% (limit 1%)')
            errors += 1

    if errors:
        environment.process_exit_code = 1
