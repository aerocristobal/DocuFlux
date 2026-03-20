import logging
from secrets_manager import load_all_secrets


def load_settings(base_settings):
    """Load secrets, merge into settings, apply fallback defaults.

    Returns new Settings instance. Raises ValueError on failure.
    """
    loaded_secrets = load_all_secrets()
    settings_override_data = {
        k.lower(): v for k, v in loaded_secrets.items() if v is not None
    }
    app_settings = base_settings.model_copy(update=settings_override_data)
    if app_settings.storage_uri is None:
        app_settings.storage_uri = app_settings.redis_metadata_url
    if app_settings.socketio_message_queue is None:
        app_settings.socketio_message_queue = app_settings.redis_metadata_url
    return app_settings
