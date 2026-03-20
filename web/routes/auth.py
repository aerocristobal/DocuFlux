"""Auth key management route handlers."""

import time

from flask import Blueprint, request, jsonify

import web.app as _app_mod

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/api/v1/auth/keys', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("10 per hour")
def api_v1_create_key():
    """Generate a new API key."""
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
    deleted = _app_mod.redis_client.delete(f"{_app_mod.APIKEY_PREFIX}{key}")
    if not deleted:
        return jsonify({'error': 'Key not found'}), 404
    return jsonify({'revoked': True}), 200
