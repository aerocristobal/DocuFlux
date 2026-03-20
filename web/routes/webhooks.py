"""Webhook route handlers."""

import time
from urllib.parse import urlparse

from flask import Blueprint, request, jsonify

import web.app as _app_mod

webhooks_bp = Blueprint('webhooks', __name__)


@webhooks_bp.route('/api/v1/webhooks', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("60 per hour")
def api_v1_register_webhook():
    """Register a webhook URL for job status notifications."""
    data = request.get_json(silent=True) or {}
    job_id = data.get('job_id', '').strip()
    webhook_url = data.get('webhook_url', '').strip()

    if not _app_mod.is_valid_uuid(job_id):
        return jsonify({'error': 'Invalid job_id'}), 400

    parsed = urlparse(webhook_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return jsonify({'error': 'webhook_url must be a valid http/https URL'}), 400

    metadata = _app_mod.get_job_metadata(job_id)
    if not metadata:
        return jsonify({'error': 'Job not found'}), 404

    _app_mod.redis_client.hset(f"job:{job_id}", 'webhook_url', webhook_url)
    return jsonify({'job_id': job_id, 'webhook_url': webhook_url, 'registered': True}), 201


@webhooks_bp.route('/api/v1/webhooks/<job_id>', methods=['GET'])
@_app_mod.csrf.exempt
def api_v1_get_webhook(job_id):
    """Return the registered webhook URL for a job, or 404 if none."""
    if not _app_mod.is_valid_uuid(job_id):
        return jsonify({'error': 'Invalid job_id'}), 400

    metadata = _app_mod.get_job_metadata(job_id)
    if not metadata:
        return jsonify({'error': 'Job not found'}), 404

    webhook_url = metadata.get('webhook_url')
    if not webhook_url:
        return jsonify({'error': 'No webhook registered for this job'}), 404

    return jsonify({'job_id': job_id, 'webhook_url': webhook_url}), 200
