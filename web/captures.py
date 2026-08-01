"""Shared helpers for the capture index.

Three blueprints need these: capture.py writes the per-owner index, conversion.py serves
the caller's own captures at GET /api/captures, and auth.py serves the global view at
GET /api/v1/admin/captures. They live here rather than in one of those blueprints so no
blueprint has to import another — every route module depends only on `web.app` and small
shared helpers like this one.
"""
from flask import jsonify, request, session

import web.app as _app_mod

# Redis key holding every capture job id, newest first, regardless of owner.
GLOBAL_INDEX_KEY = 'capture:all_jobs'


def owner_index_key(owner):
    """Redis key holding the capture job ids belonging to `owner`."""
    return f"capture:jobs:{owner}"


def capture_owner():
    """Identity a capture belongs to: the extension's client id, else the UI session.

    The extension posts cross-origin without credentials (CORS supports_credentials is
    False), so it has no Flask session — X-Client-ID is the only identity it carries.
    Same-origin UI callers have a session instead.

    Returns None when the caller presents neither, in which case the capture is recorded
    only in the global index and is not listable by anyone but an admin.
    """
    client_id = request.headers.get('X-Client-ID', '').strip()
    if client_id and client_id != 'unknown':
        return f"client:{client_id}"
    session_id = session.get('session_id')
    if session_id:
        return f"session:{session_id}"
    return None


def render_capture_jobs(job_ids):
    """Shape a list of capture job ids into the API's response payload."""
    if not job_ids:
        return jsonify([])

    pipe = _app_mod.redis_client.pipeline()
    for jid in job_ids:
        pipe.hgetall(f"job:{jid}")
    results = pipe.execute()

    jobs_data = []
    for jid, meta in zip(job_ids, results):
        if not meta:
            continue
        is_zip = meta.get('is_zip') == 'true'
        download_url = None
        if meta.get('status') == 'SUCCESS':
            download_url = f"/download_zip/{jid}" if is_zip else f"/download/{jid}"
        jobs_data.append({
            'id': jid,
            'filename': meta.get('filename'),
            'from': meta.get('from', 'capture'),
            'to': meta.get('to'),
            'created_at': float(meta.get('created_at', 0)),
            'status': meta.get('status', 'PENDING'),
            'progress': meta.get('progress', '0'),
            'result': meta.get('error') if meta.get('status') == 'FAILURE' else None,
            'download_url': download_url,
            'is_zip': is_zip,
        })
    jobs_data.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify(jobs_data)
