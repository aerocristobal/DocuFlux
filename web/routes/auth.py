"""Auth key management route handlers."""

import time

from flask import Blueprint, request, jsonify

import web.app as _app_mod

auth_bp = Blueprint('auth', __name__)


def _check_admin_secret():
    """Validate Authorization header against ADMIN_API_SECRET.
    Returns (None, None) on success or (response, status_code) on failure.
    """
    admin_secret = _app_mod.app_settings.admin_api_secret
    if not admin_secret:
        return jsonify({'error': 'API key management is disabled (ADMIN_API_SECRET not configured)'}), 503

    auth_header = request.headers.get('Authorization', '').strip()
    if not auth_header:
        return jsonify({'error': 'Admin authentication required'}), 401

    parts = auth_header.split(' ', 1)
    if len(parts) != 2 or parts[0] != 'Bearer':
        return jsonify({'error': 'Authorization header must be Bearer <secret>'}), 401

    provided = parts[1]
    expected = admin_secret.get_secret_value() if hasattr(admin_secret, 'get_secret_value') else admin_secret
    if provided != expected:
        return jsonify({'error': 'Invalid admin secret'}), 403

    return None, None


@auth_bp.route('/api/v1/auth/keys', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("10 per hour")
def api_v1_create_key():
    """Generate a new API key."""
    err = _check_admin_secret()
    if err[0] is not None:
        return err
    data = request.get_json(silent=True) or {}
    label = str(data.get('label', ''))[:100]
    key = _app_mod._generate_api_key()
    now = str(time.time())
    _app_mod.redis_client.hset(f"{_app_mod.APIKEY_PREFIX}{key}", mapping={'created_at': now, 'label': label})
    return jsonify({'api_key': key, 'created_at': now, 'label': label}), 201


@auth_bp.route('/api/v1/auth/keys/<key>', methods=['DELETE'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("30 per hour")
def api_v1_revoke_key(key):
    """Revoke an API key."""
    err = _check_admin_secret()
    if err[0] is not None:
        return err
    deleted = _app_mod.redis_client.delete(f"{_app_mod.APIKEY_PREFIX}{key}")
    if not deleted:
        return jsonify({'error': 'Key not found'}), 404
    return jsonify({'revoked': True}), 200
