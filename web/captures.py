"""Shared helpers for the capture index.

Three blueprints need these: capture.py writes the per-owner index, conversion.py serves
the caller's own captures at GET /api/captures, and auth.py serves the global view at
GET /api/v1/admin/captures. They live here rather than in one of those blueprints so no
blueprint has to import another — every route module depends only on `web.app` and small
shared helpers like this one.
"""
import re

from flask import jsonify, request, session

import web.app as _app_mod

# Redis key holding every capture job id, newest first, regardless of owner.
GLOBAL_INDEX_KEY = 'capture:all_jobs'

# X-Client-ID is caller-supplied and lands in a Redis key name, so it is constrained
# before use: an unbounded header would let any caller mint arbitrarily long keys, and
# glob/whitespace characters would interfere with key scans. The extension generates 32
# hex characters (extension-src/background.js::generateId); this is deliberately wider
# than that so an older extension build is not locked out.
_CLIENT_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


def owner_index_key(owner):
    """Redis key holding the capture job ids belonging to `owner`."""
    return f"capture:jobs:{owner}"


def client_id_from_request():
    """The caller's X-Client-ID, or None when absent or not a usable identity.

    'unknown' is the extension's own fallback value, so it identifies no one — treating
    it as an identity would pool every such caller into one shared capture list.
    """
    client_id = request.headers.get('X-Client-ID', '').strip()
    if not client_id or client_id == 'unknown':
        return None
    if not _CLIENT_ID_RE.match(client_id):
        return None
    return client_id


def capture_owners():
    """Every identity the caller presents, most specific first.

    The extension posts cross-origin without credentials (CORS supports_credentials is
    False), so it has no Flask session — X-Client-ID is the only identity it carries.
    Same-origin UI callers have a session. The DocuFlux web UI has *both*: its own
    session, plus the extension's client id, which the popup publishes into the page's
    localStorage so the UI can list extension-created captures.

    A capture is indexed under every identity its creator presented, and the listing
    reads every identity its caller presents. Returning a list rather than picking one
    is what stops the UI losing sight of its own session's captures the moment the
    extension publishes a client id into the page.

    Empty when the caller presents neither, in which case the capture is recorded only
    in the global index and is not listable by anyone but an admin.
    """
    owners = []
    client_id = client_id_from_request()
    if client_id:
        owners.append(f"client:{client_id}")
    session_id = session.get('session_id')
    if session_id:
        owners.append(f"session:{session_id}")
    return owners


def render_capture_jobs(job_ids, limit=None):
    """Shape a list of capture job ids into the API's response payload.

    `limit` truncates *after* sorting, so a caller merging several owner indexes gets
    the newest N captures overall rather than the first N of whichever index it read
    first.
    """
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
    if limit is not None:
        jobs_data = jobs_data[:limit]
    return jsonify(jobs_data)


def read_owner_indexes(owners, limit):
    """Merge the newest `limit` job ids from each owner's index, de-duplicated.

    A job created by a caller presenting both identities is in both lists; returning it
    twice would render it twice.
    """
    job_ids = []
    seen = set()
    for owner in owners:
        for jid in _app_mod.redis_client.lrange(owner_index_key(owner), 0, limit - 1):
            if jid not in seen:
                seen.add(jid)
                job_ids.append(jid)
    return job_ids
