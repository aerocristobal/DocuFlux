"""Browser extension capture session route handlers."""

import os
import uuid
import time
import json

from flask import Blueprint, request, jsonify

import web.app as _app_mod
from formats import FORMATS
from web.validation import require_valid_uuid, sanitize_string

capture_bp = Blueprint('capture', __name__)


@capture_bp.route('/api/v1/capture/sessions', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("200 per hour")
def capture_create_session():
    """Create a new capture session for the browser extension."""
    data = request.get_json(silent=True) or {}
    title = sanitize_string(data.get('title', 'Captured Document'), max_length=200)
    to_format = data.get('to_format', 'markdown')
    source_url = sanitize_string(data.get('source_url', ''), max_length=500)
    force_ocr = data.get('force_ocr', False)
    client_id = request.headers.get('X-Client-ID', 'unknown')

    if to_format not in [f['key'] for f in FORMATS]:
        to_format = 'markdown'

    session_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = str(time.time())

    _app_mod.storage.makedirs(job_id, 'batches', folder='output')

    _app_mod.update_job_metadata(job_id, {
        'status': 'CAPTURING',
        'created_at': now,
        'filename': title,
        'from': 'capture',
        'to': to_format,
        'session_id': session_id,
        'progress': '0',
    })

    captures_list_key = 'capture:all_jobs'
    _app_mod.redis_client.lpush(captures_list_key, job_id)
    _app_mod.redis_client.ltrim(captures_list_key, 0, 99)
    _app_mod.redis_client.expire(captures_list_key, 86400)

    session_key = f"capture:session:{session_id}"
    _app_mod.redis_client.hset(session_key, mapping={
        'status': 'active',
        'created_at': now,
        'title': title,
        'to_format': to_format,
        'source_url': source_url,
        'force_ocr': str(force_ocr),
        'page_count': '0',
        'client_id': client_id,
        'job_id': job_id,
        'batches_queued': '0',
        'batches_done': '0',
        'batches_failed': '0',
        'next_batch_start': '0',
    })
    _app_mod.redis_client.expire(session_key, _app_mod.app_settings.capture_session_ttl)

    client_sessions_key = f"capture:sessions:{client_id}"
    _app_mod.redis_client.lpush(client_sessions_key, session_id)
    _app_mod.redis_client.expire(client_sessions_key, _app_mod.app_settings.capture_session_ttl)

    return jsonify({
        'session_id': session_id,
        'job_id': job_id,
        'status': 'active',
        'max_pages': _app_mod.app_settings.max_capture_pages,
    }), 201


@capture_bp.route('/api/v1/capture/sessions/<session_id>/pages', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("1000 per hour")
@require_valid_uuid('session_id')
def capture_add_page(session_id):
    """Submit a captured page to an existing session."""

    session_key = f"capture:session:{session_id}"
    session_meta = _app_mod.redis_client.hgetall(session_key)
    if not session_meta:
        return jsonify({'error': 'Session not found or expired'}), 404
    if session_meta.get('status') != 'active':
        return jsonify({'error': 'Session is not active'}), 409

    page_count = int(session_meta.get('page_count', 0))
    if page_count >= _app_mod.app_settings.max_capture_pages:
        return jsonify({'error': f'Maximum pages ({_app_mod.app_settings.max_capture_pages}) reached'}), 422

    data = request.get_json(silent=True) or {}

    page_sequence = data.get('page_sequence')
    if page_sequence is not None:
        seen_key = f"capture:session:{session_id}:seen_pages"
        if _app_mod.redis_client.sismember(seen_key, str(page_sequence)):
            return jsonify({'status': 'duplicate', 'page_count': page_count}), 200
        _app_mod.redis_client.sadd(seen_key, str(page_sequence))
        _app_mod.redis_client.expire(seen_key, _app_mod.app_settings.capture_session_ttl)

    page_data = {
        'url': sanitize_string(data.get('url', ''), max_length=500),
        'title': sanitize_string(data.get('title', ''), max_length=200),
        'text': sanitize_string(data.get('text', ''), max_length=500000, allow_newlines=True),
        'images': data.get('images', []),
        'extraction_method': data.get('extraction_method', 'generic'),
        'page_hint': data.get('page_hint', page_count),
    }

    pages_key = f"capture:session:{session_id}:pages"
    _app_mod.redis_client.rpush(pages_key, json.dumps(page_data))
    _app_mod.redis_client.expire(pages_key, _app_mod.app_settings.capture_session_ttl)

    new_count = page_count + 1
    _app_mod.redis_client.hset(session_key, 'page_count', str(new_count))

    force_ocr = session_meta.get('force_ocr', 'false').lower() == 'true'
    if force_ocr:
        next_batch_start = int(session_meta.get('next_batch_start', 0))
        if new_count - next_batch_start >= _app_mod.app_settings.capture_batch_size:
            job_id = session_meta.get('job_id')
            batch_index = int(session_meta.get('batches_queued', 0))
            page_end = new_count
            batch_key = f"capture:batch:{session_id}:{batch_index}"
            _app_mod.redis_client.hset(batch_key, mapping={
                'status': 'queued',
                'page_start': str(next_batch_start),
                'page_end': str(page_end),
            })
            _app_mod.redis_client.expire(batch_key, _app_mod.app_settings.capture_session_ttl)
            _app_mod.redis_client.hset(session_key, mapping={
                'batches_queued': str(batch_index + 1),
                'next_batch_start': str(page_end),
            })
            _app_mod.celery.send_task(
                'tasks.process_capture_batch',
                args=[session_id, job_id, batch_index, next_batch_start, page_end],
                queue='default',
            )

    return jsonify({'status': 'accepted', 'page_count': new_count}), 200


@capture_bp.route('/api/v1/capture/sessions/<session_id>/images', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("2000 per hour")
@require_valid_uuid('session_id')
def capture_upload_image(session_id):
    """Upload a large image separately from a page submission."""

    session_key = f"capture:session:{session_id}"
    session_meta = _app_mod.redis_client.hgetall(session_key)
    if not session_meta:
        return jsonify({'error': 'Session not found or expired'}), 404
    if session_meta.get('status') != 'active':
        return jsonify({'error': 'Session is not active'}), 409

    if 'image' not in request.files:
        return jsonify({'error': 'No image file provided'}), 400

    image_file = request.files['image']
    alt = request.form.get('alt', '')
    is_screenshot = request.form.get('is_screenshot', 'false').lower() == 'true'

    # Store image to session's image directory
    upload_dir = os.path.join(_app_mod.app_settings.upload_folder, session_id, 'images')
    os.makedirs(upload_dir, exist_ok=True)

    # Use a deterministic filename based on content hash
    import hashlib
    content = image_file.read()
    content_hash = hashlib.sha256(content).hexdigest()[:16]
    ext = os.path.splitext(image_file.filename)[1] or '.jpg'
    safe_filename = f"{content_hash}{ext}"
    filepath = os.path.join(upload_dir, safe_filename)

    if not os.path.exists(filepath):
        with open(filepath, 'wb') as f:
            f.write(content)

    # Store image reference in Redis for assembly
    image_ref = f"images/{safe_filename}"
    images_key = f"capture:session:{session_id}:image_refs"
    image_meta = json.dumps({
        'ref': image_ref,
        'filename': image_file.filename,
        'alt': sanitize_string(alt, max_length=500),
        'is_screenshot': is_screenshot,
    })
    _app_mod.redis_client.hset(images_key, image_ref, image_meta)
    _app_mod.redis_client.expire(images_key, _app_mod.app_settings.capture_session_ttl)

    return jsonify({'image_ref': image_ref, 'status': 'uploaded'}), 200


@capture_bp.route('/api/v1/capture/sessions/<session_id>/finish', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("200 per hour")
@require_valid_uuid('session_id')
def capture_finish_session(session_id):
    """Finalize a session and queue assembly into a document."""

    session_key = f"capture:session:{session_id}"
    session_meta = _app_mod.redis_client.hgetall(session_key)
    if not session_meta:
        return jsonify({'error': 'Session not found or expired'}), 404
    if session_meta.get('status') != 'active':
        return jsonify({'error': 'Session already finished'}), 409

    page_count = int(session_meta.get('page_count', 0))
    if page_count == 0:
        return jsonify({'error': 'No pages captured in session'}), 422

    job_id = session_meta.get('job_id')
    if not job_id:
        return jsonify({'error': 'Session missing job_id — please start a new session'}), 500

    force_ocr = session_meta.get('force_ocr', 'false').lower() == 'true'
    next_batch_start = int(session_meta.get('next_batch_start', 0))
    if force_ocr and next_batch_start < page_count:
        batch_index = int(session_meta.get('batches_queued', 0))
        batch_key = f"capture:batch:{session_id}:{batch_index}"
        _app_mod.redis_client.hset(batch_key, mapping={
            'status': 'queued',
            'page_start': str(next_batch_start),
            'page_end': str(page_count),
        })
        _app_mod.redis_client.expire(batch_key, _app_mod.app_settings.capture_session_ttl)
        _app_mod.redis_client.hset(session_key, mapping={
            'batches_queued': str(batch_index + 1),
            'next_batch_start': str(page_count),
        })
        _app_mod.celery.send_task(
            'tasks.process_capture_batch',
            args=[session_id, job_id, batch_index, next_batch_start, page_count],
            queue='default',
        )

    _app_mod.redis_client.hset(session_key, 'status', 'assembling')

    _app_mod.celery.send_task(
        'tasks.assemble_capture_session',
        args=[session_id, job_id],
        queue='default'
    )

    return jsonify({
        'job_id': job_id,
        'status': 'assembling',
        'status_url': f'/api/v1/status/{job_id}',
    }), 202


@capture_bp.route('/api/v1/capture/sessions/<session_id>/status', methods=['GET'])
@_app_mod.csrf.exempt
@require_valid_uuid('session_id')
def capture_session_status(session_id):
    """Poll the status of a capture session."""

    session_key = f"capture:session:{session_id}"
    session_meta = _app_mod.redis_client.hgetall(session_key)
    if not session_meta:
        return jsonify({'error': 'Session not found or expired'}), 404

    response = {
        'session_id': session_id,
        'status': session_meta.get('status', 'unknown'),
        'page_count': int(session_meta.get('page_count', 0)),
        'title': session_meta.get('title', ''),
        'to_format': session_meta.get('to_format', 'markdown'),
    }

    job_id = session_meta.get('job_id')
    if job_id:
        response['job_id'] = job_id
        response['status_url'] = f'/api/v1/status/{job_id}'

    return jsonify(response), 200
