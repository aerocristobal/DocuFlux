"""
Integration tests for the browser extension capture session lifecycle.

These tests exercise multi-step flows (create → add pages → finish → status)
that are not covered by the unit tests in tests/unit/test_capture_api.py,
which only test individual endpoints in isolation.

All external services (Redis, Celery) are mocked via the conftest.py fixtures.
"""

import json
import uuid
from unittest.mock import MagicMock, call, patch


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_session(status='active', page_count='0', force_ocr='false',
                 next_batch_start='0', batches_queued='0', job_id=None):
    """Return a Redis session hash dict for use in hgetall mocks."""
    return {
        'status': status,
        'created_at': '1700000000.0',
        'title': 'Test Document',
        'to_format': 'markdown',
        'source_url': 'https://example.com',
        'force_ocr': force_ocr,
        'page_count': page_count,
        'client_id': 'test-client',
        'job_id': job_id or str(uuid.uuid4()),
        'batches_queued': batches_queued,
        'batches_done': '0',
        'batches_failed': '0',
        'next_batch_start': next_batch_start,
    }


def page_payload(n=1):
    """Return a minimal page POST body."""
    return {
        'url': f'https://example.com/page/{n}',
        'title': f'Page {n}',
        'text': f'# Page {n}\n\nContent for page {n}.',
        'images': [],
        'page_hint': n,
    }


# ─── Lifecycle: create → add pages → finish ───────────────────────────────────

class TestCaptureSessionLifecycle:

    def test_full_text_pipeline(self, client):
        """Create session, add 3 pages, finish — assemble_capture_session dispatched."""
        import web.app as app_module

        # Step 1: create session
        resp = client.post(
            '/api/v1/capture/sessions',
            json={'title': 'My Book', 'to_format': 'markdown'},
        )
        assert resp.status_code == 201
        session_id = resp.get_json()['session_id']
        assert uuid.UUID(session_id)

        # Step 2: add 3 pages (each call sees page_count incrementing)
        for n in range(1, 4):
            app_module.redis_client.hgetall.return_value = make_session(page_count=str(n - 1))
            resp = client.post(
                f'/api/v1/capture/sessions/{session_id}/pages',
                json=page_payload(n),
            )
            assert resp.status_code == 200
            assert resp.get_json()['page_count'] == n

        # Step 3: finish — session now has 3 pages
        app_module.redis_client.hgetall.return_value = make_session(page_count='3')
        resp = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert resp.status_code == 202
        finish_data = resp.get_json()
        assert finish_data['status'] == 'assembling'
        assert 'job_id' in finish_data
        assert finish_data['status_url'] == f"/api/v1/status/{finish_data['job_id']}"

        # Verify assembly task was dispatched
        dispatched = [c[0][0] for c in app_module.celery.send_task.call_args_list]
        assert 'tasks.assemble_capture_session' in dispatched

    def test_page_count_increments_correctly(self, client):
        """page_count in the response tracks how many pages have been added."""
        import web.app as app_module

        resp = client.post('/api/v1/capture/sessions', json={'title': 'Book'})
        assert resp.status_code == 201
        session_id = resp.get_json()['session_id']

        for n in range(1, 6):
            app_module.redis_client.hgetall.return_value = make_session(page_count=str(n - 1))
            resp = client.post(
                f'/api/v1/capture/sessions/{session_id}/pages',
                json=page_payload(n),
            )
            assert resp.status_code == 200
            assert resp.get_json()['page_count'] == n, f"Expected {n} after adding page {n}"

    def test_status_shows_assembling_after_finish(self, client):
        """After finish, status endpoint returns assembling with job_id and status_url."""
        import web.app as app_module
        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        # Simulate a session that has been finished
        app_module.redis_client.hgetall.return_value = make_session(
            status='assembling', page_count='2', job_id=job_id
        )
        resp = client.get(f'/api/v1/capture/sessions/{session_id}/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'assembling'
        assert data['job_id'] == job_id
        assert data['status_url'] == f'/api/v1/status/{job_id}'

    def test_duplicate_finish_returns_409(self, client):
        """Calling finish a second time on an assembling session returns 409."""
        import web.app as app_module
        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session(
            status='assembling', page_count='5'
        )
        resp = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert resp.status_code == 409


# ─── Force OCR batch dispatch ─────────────────────────────────────────────────

class TestCaptureBatchOCR:

    def test_batch_dispatched_at_threshold(self, client):
        """Adding the Nth page (capture_batch_size) dispatches process_capture_batch."""
        import web.app as app_module

        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        batch_size = app_module.app_settings.capture_batch_size  # default 10

        # Session has force_ocr=True and page_count = batch_size - 1
        # so adding this page pushes new_count to exactly batch_size
        app_module.redis_client.hgetall.return_value = make_session(
            page_count=str(batch_size - 1),
            force_ocr='true',
            next_batch_start='0',
            batches_queued='0',
            job_id=job_id,
        )
        app_module.celery.send_task.reset_mock()

        resp = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json=page_payload(batch_size),
        )
        assert resp.status_code == 200
        assert resp.get_json()['page_count'] == batch_size

        # Verify process_capture_batch was dispatched
        task_names = [c[0][0] for c in app_module.celery.send_task.call_args_list]
        assert 'tasks.process_capture_batch' in task_names

        batch_call = next(
            c for c in app_module.celery.send_task.call_args_list
            if c[0][0] == 'tasks.process_capture_batch'
        )
        args = batch_call[1]['args']  # send_task uses args= keyword
        assert args[0] == session_id
        assert args[1] == job_id
        assert args[2] == 0             # batch_index
        assert args[3] == 0             # page_start
        assert args[4] == batch_size    # page_end

    def test_batch_not_dispatched_below_threshold(self, client):
        """Adding a page below the batch threshold does NOT dispatch a batch task."""
        import web.app as app_module

        session_id = str(uuid.uuid4())
        batch_size = app_module.app_settings.capture_batch_size

        # page_count is below threshold (batch_size - 2 → new_count = batch_size - 1)
        app_module.redis_client.hgetall.return_value = make_session(
            page_count=str(batch_size - 2),
            force_ocr='true',
            next_batch_start='0',
            batches_queued='0',
        )
        app_module.celery.send_task.reset_mock()

        resp = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json=page_payload(1),
        )
        assert resp.status_code == 200

        task_names = [c[0][0] for c in app_module.celery.send_task.call_args_list]
        assert 'tasks.process_capture_batch' not in task_names

    def test_second_batch_dispatched_at_second_threshold(self, client):
        """Second batch (next_batch_start=10) dispatches when new_count reaches 20."""
        import web.app as app_module

        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        batch_size = app_module.app_settings.capture_batch_size

        # First batch already done: next_batch_start=batch_size, batches_queued=1
        # page_count = 2*batch_size - 1, so adding one more hits the threshold
        app_module.redis_client.hgetall.return_value = make_session(
            page_count=str(2 * batch_size - 1),
            force_ocr='true',
            next_batch_start=str(batch_size),
            batches_queued='1',
            job_id=job_id,
        )
        app_module.celery.send_task.reset_mock()

        resp = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json=page_payload(2 * batch_size),
        )
        assert resp.status_code == 200

        task_names = [c[0][0] for c in app_module.celery.send_task.call_args_list]
        assert 'tasks.process_capture_batch' in task_names

        batch_call = next(
            c for c in app_module.celery.send_task.call_args_list
            if c[0][0] == 'tasks.process_capture_batch'
        )
        args = batch_call[1]['args']  # send_task uses args= keyword
        assert args[2] == 1              # batch_index = 1 (second batch)
        assert args[3] == batch_size     # page_start = end of first batch
        assert args[4] == 2 * batch_size # page_end


# ─── Error cases ──────────────────────────────────────────────────────────────

class TestCaptureErrorCases:

    def test_max_pages_returns_422(self, client):
        """Adding a page when page_count is already at max returns 422."""
        import web.app as app_module

        session_id = str(uuid.uuid4())
        max_pages = app_module.app_settings.max_capture_pages
        app_module.redis_client.hgetall.return_value = make_session(
            page_count=str(max_pages)
        )
        resp = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json=page_payload(1),
        )
        assert resp.status_code == 422
        assert 'Maximum pages' in resp.get_json()['error']

    def test_finish_zero_pages_returns_422(self, client):
        """Finishing a session with no pages returns 422."""
        import web.app as app_module

        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session(page_count='0')
        resp = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert resp.status_code == 422

    def test_add_page_to_assembling_session_returns_409(self, client):
        """Adding a page to an assembling session returns 409."""
        import web.app as app_module

        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session(status='assembling')
        resp = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json=page_payload(1),
        )
        assert resp.status_code == 409

    def test_add_page_expired_session_returns_404(self, client):
        """Adding a page to a non-existent/expired session returns 404."""
        import web.app as app_module

        app_module.redis_client.hgetall.return_value = {}
        session_id = str(uuid.uuid4())
        resp = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json=page_payload(1),
        )
        assert resp.status_code == 404

    def test_invalid_uuid_returns_400_on_all_endpoints(self, client):
        """Non-UUID session_id returns 400 from add, finish, and status endpoints."""
        bad_id = 'not-a-valid-uuid'
        assert client.post(f'/api/v1/capture/sessions/{bad_id}/pages', json={}).status_code == 400
        assert client.post(f'/api/v1/capture/sessions/{bad_id}/finish').status_code == 400
        assert client.get(f'/api/v1/capture/sessions/{bad_id}/status').status_code == 400

    def test_assemble_task_args_contain_session_and_job_id(self, client):
        """assemble_capture_session is dispatched with (session_id, job_id)."""
        import web.app as app_module

        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session(
            page_count='2', job_id=job_id
        )
        app_module.celery.send_task.reset_mock()

        client.post(f'/api/v1/capture/sessions/{session_id}/finish')

        assemble_calls = [
            c for c in app_module.celery.send_task.call_args_list
            if c[0][0] == 'tasks.assemble_capture_session'
        ]
        assert len(assemble_calls) == 1
        args = assemble_calls[0][1]['args']  # send_task uses args= keyword
        assert args[0] == session_id
        assert args[1] == job_id
