"""Admin endpoints for the on-demand Surya VLM lifecycle (epic pfm)."""

import hmac
import logging

from flask import Blueprint, request, jsonify

import web.app as _app_mod
import surya_state

admin_surya_bp = Blueprint('admin_surya', __name__)


def _check_admin_secret():
    """Constant-time Bearer check against ADMIN_API_SECRET.
    Returns None on success or (response, status_code) on failure.
    """
    admin_secret = _app_mod.app_settings.admin_api_secret
    if not admin_secret:
        return jsonify({'error': 'Admin API is disabled (ADMIN_API_SECRET not configured)'}), 503

    auth_header = request.headers.get('Authorization', '').strip()
    if not auth_header:
        return jsonify({'error': 'Admin authentication required'}), 401

    parts = auth_header.split(' ', 1)
    if len(parts) != 2 or parts[0] != 'Bearer':
        return jsonify({'error': 'Authorization header must be Bearer <secret>'}), 401

    expected = admin_secret.get_secret_value() if hasattr(admin_secret, 'get_secret_value') else admin_secret
    if not hmac.compare_digest(parts[1].encode(), expected.encode()):
        return jsonify({'error': 'Invalid admin secret'}), 403
    return None


def _snapshot():
    r = _app_mod.redis_client
    settings = surya_state.get_ocr_settings(r, _app_mod.app_settings)
    return {
        'state': surya_state.get_state(r),
        'desired': surya_state.get_desired(r),
        'pinned': surya_state.is_pinned(r),
        'surya_supported': surya_state.surya_supported(r),
        'mode': settings['mode'],
        'idle_timeout_min': settings['idle_timeout_min'],
    }


def _emit_state_update():
    """Push the new state to open dashboards; never fails the request."""
    try:
        _app_mod.socketio.emit(
            'surya_state_update',
            surya_state.status_snapshot(_app_mod.redis_client, _app_mod.app_settings))
    except Exception as e:
        logging.warning(f"surya_state_update emit skipped: {e}")


@admin_surya_bp.route('/api/v1/admin/surya', methods=['GET'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("60 per minute")
def api_v1_surya_status():
    """Read-only Surya lifecycle state (no admin secret required)."""
    return jsonify(_snapshot())


@admin_surya_bp.route('/api/v1/admin/surya', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("30 per minute")
def api_v1_surya_action():
    """Manually load (pin) or unload the Surya VLM."""
    err = _check_admin_secret()
    if err is not None:
        return err
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    if action not in ('load', 'unload'):
        return jsonify({'error': "action must be 'load' or 'unload'"}), 400

    r = _app_mod.redis_client
    if action == 'load':
        snap = _snapshot()
        if not snap['surya_supported']:
            return jsonify({'error': 'Surya VLM is not supported on this host (no GPU with enough VRAM)'}), 409
        if snap['mode'] == 'cpu_only':
            return jsonify({'error': 'OCR mode is cpu_only; switch to on_demand to load Surya'}), 409
        surya_state.set_desired(r, 'running')
        surya_state.set_pinned(r, True)
    else:
        surya_state.set_desired(r, 'stopped')
        surya_state.set_pinned(r, False)
    _emit_state_update()
    return jsonify(_snapshot()), 202


@admin_surya_bp.route('/api/v1/admin/ocr-settings', methods=['GET'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("60 per minute")
def api_v1_ocr_settings_get():
    """Read-only OCR settings (no admin secret required)."""
    return jsonify(surya_state.get_ocr_settings(_app_mod.redis_client, _app_mod.app_settings))


@admin_surya_bp.route('/api/v1/admin/ocr-settings', methods=['PUT'])
@_app_mod.csrf.exempt
@_app_mod.limiter.limit("30 per minute")
def api_v1_ocr_settings_put():
    """Update OCR mode and/or idle timeout."""
    err = _check_admin_secret()
    if err is not None:
        return err
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'JSON object body required'}), 400
    r = _app_mod.redis_client
    try:
        surya_state.set_ocr_settings(r, mode=data.get('mode'),
                                    idle_timeout_min=data.get('idle_timeout_min'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    _emit_state_update()
    return jsonify(surya_state.get_ocr_settings(r, _app_mod.app_settings))
