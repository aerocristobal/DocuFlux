"""
Unit tests for the browser extension capture API endpoints.

Tests /api/v1/capture/sessions/* endpoints using the existing
conftest.py fixtures (app, client) with mocked Redis and Celery.
"""

import json
import uuid
from unittest.mock import MagicMock, call


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_session_meta(status='active', page_count='2', to_format='markdown',
                      job_id=None, force_ocr='False'):
    import uuid as _uuid
    return {
        'status': status,
        'created_at': '1700000000.0',
        'title': 'Test Book',
        'to_format': to_format,
        'source_url': 'https://example.com',
        'force_ocr': force_ocr,
        'page_count': page_count,
        'client_id': 'test-client',
        'job_id': job_id or str(_uuid.uuid4()),
        'batches_queued': '0',
        'batches_done': '0',
        'batches_failed': '0',
        'next_batch_start': '0',
    }


# ─── POST /api/v1/capture/sessions ────────────────────────────────────────────

class TestCaptureCreateSession:
    def test_creates_session_returns_201(self, client):
        response = client.post(
            '/api/v1/capture/sessions',
            json={'title': 'My Book', 'to_format': 'markdown'},
            headers={'X-Client-ID': 'test-client'},
        )
        assert response.status_code == 201
        data = response.get_json()
        assert 'session_id' in data
        # Verify it is a valid UUID
        uuid.UUID(data['session_id'])
        assert data['status'] == 'active'
        assert 'max_pages' in data

    def test_creates_session_redis_called(self, client):
        import web.app as app_module
        response = client.post(
            '/api/v1/capture/sessions',
            json={'title': 'My Book', 'to_format': 'docx'},
        )
        assert response.status_code == 201
        # Redis hset should have been called to store session metadata
        app_module.redis_client.hset.assert_called()
        app_module.redis_client.expire.assert_called()

    def test_invalid_format_defaults_to_markdown(self, client):
        response = client.post(
            '/api/v1/capture/sessions',
            json={'title': 'Test', 'to_format': 'invalid_format_xyz'},
        )
        assert response.status_code == 201
        # Session created with fallback; to_format stored as 'markdown'
        import web.app as app_module
        call_args = app_module.redis_client.hset.call_args
        mapping = call_args[1].get('mapping', {}) or call_args[0][2] if call_args[0] else {}
        if mapping:
            assert mapping.get('to_format') == 'markdown'

    def test_empty_body_uses_defaults(self, client):
        response = client.post('/api/v1/capture/sessions', json={})
        assert response.status_code == 201


# ─── POST /api/v1/capture/sessions/<id>/pages ─────────────────────────────────

class TestCaptureAddPage:
    def test_adds_page_success(self, client):
        import web.app as app_module
        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session_meta()
        app_module.redis_client.rpush.return_value = 1

        response = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json={
                'url': 'https://example.com/page/1',
                'title': 'Chapter 1',
                'text': '# Chapter 1\n\nContent here.',
                'images': [],
                'page_hint': 1,
            },
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'accepted'
        assert data['page_count'] == 3  # 2 existing + 1

    def test_invalid_session_id_returns_400(self, client):
        response = client.post('/api/v1/capture/sessions/not-a-uuid/pages', json={})
        assert response.status_code == 400

    def test_session_not_found_returns_404(self, client):
        import web.app as app_module
        app_module.redis_client.hgetall.return_value = {}
        session_id = str(uuid.uuid4())
        response = client.post(f'/api/v1/capture/sessions/{session_id}/pages', json={})
        assert response.status_code == 404

    def test_inactive_session_returns_409(self, client):
        import web.app as app_module
        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session_meta(status='assembling')
        response = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json={'text': 'some content'},
        )
        assert response.status_code == 409

    def test_page_data_stored_in_redis(self, client):
        import web.app as app_module
        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session_meta()

        client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json={'text': '# Test', 'page_hint': 5},
        )
        app_module.redis_client.rpush.assert_called()
        stored = json.loads(app_module.redis_client.rpush.call_args[0][1])
        assert stored['page_hint'] == 5
        assert stored['text'] == '# Test'


# ─── POST /api/v1/capture/sessions/<id>/finish ────────────────────────────────

class TestCaptureFinishSession:
    def test_finish_queues_task_returns_202(self, client):
        import web.app as app_module
        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session_meta(page_count='3')

        response = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert response.status_code == 202
        data = response.get_json()
        assert 'job_id' in data
        uuid.UUID(data['job_id'])
        assert data['status'] == 'assembling'
        assert 'status_url' in data

    def test_finish_dispatches_celery_task(self, client):
        import web.app as app_module
        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session_meta(page_count='1')

        client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        app_module.celery.send_task.assert_called()
        task_name = app_module.celery.send_task.call_args[0][0]
        assert task_name == 'tasks.assemble_capture_session'

    def test_finish_invalid_session_id_returns_400(self, client):
        response = client.post('/api/v1/capture/sessions/bad-id/finish')
        assert response.status_code == 400

    def test_finish_no_pages_returns_422(self, client):
        import web.app as app_module
        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session_meta(page_count='0')
        response = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert response.status_code == 422

    def test_finish_already_assembling_returns_409(self, client):
        import web.app as app_module
        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session_meta(status='assembling', page_count='5')
        response = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert response.status_code == 409


# ─── GET /api/v1/capture/sessions/<id>/status ─────────────────────────────────

class TestCaptureSessionStatus:
    def test_get_status_active_session(self, client):
        import web.app as app_module
        session_id = str(uuid.uuid4())
        app_module.redis_client.hgetall.return_value = make_session_meta(page_count='7')

        response = client.get(f'/api/v1/capture/sessions/{session_id}/status')
        assert response.status_code == 200
        data = response.get_json()
        assert data['session_id'] == session_id
        assert data['status'] == 'active'
        assert data['page_count'] == 7
        assert data['title'] == 'Test Book'

    def test_get_status_with_job_id(self, client):
        import web.app as app_module
        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        meta = make_session_meta(status='assembling', page_count='5')
        meta['job_id'] = job_id
        app_module.redis_client.hgetall.return_value = meta

        response = client.get(f'/api/v1/capture/sessions/{session_id}/status')
        assert response.status_code == 200
        data = response.get_json()
        assert data['job_id'] == job_id
        assert 'status_url' in data

    def test_get_status_invalid_id_returns_400(self, client):
        response = client.get('/api/v1/capture/sessions/bad-id/status')
        assert response.status_code == 400

    def test_get_status_not_found_returns_404(self, client):
        import web.app as app_module
        app_module.redis_client.hgetall.return_value = {}
        session_id = str(uuid.uuid4())
        response = client.get(f'/api/v1/capture/sessions/{session_id}/status')
        assert response.status_code == 404
