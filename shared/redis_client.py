import redis
import logging


def create_redis_client(url, app_settings, max_connections=20,
                        decode_responses=True, socket_connect_timeout=None,
                        socket_timeout=None):
    """Create a Redis client with optional TLS from rediss:// URLs."""
    kwargs = {'max_connections': max_connections, 'decode_responses': decode_responses}
    if socket_connect_timeout is not None:
        kwargs['socket_connect_timeout'] = socket_connect_timeout
    if socket_timeout is not None:
        kwargs['socket_timeout'] = socket_timeout
    if url.startswith('rediss://'):
        kwargs['ssl'] = True
        kwargs['ssl_cert_reqs'] = 'required'
        if app_settings.redis_tls_ca_certs:
            kwargs['ssl_ca_certs'] = app_settings.redis_tls_ca_certs
        if app_settings.redis_tls_certfile:
            kwargs['ssl_certfile'] = app_settings.redis_tls_certfile
        if app_settings.redis_tls_keyfile:
            kwargs['ssl_keyfile'] = app_settings.redis_tls_keyfile
        logging.info(f"Redis TLS enabled with CA: {app_settings.redis_tls_ca_certs}")
    return redis.Redis.from_url(url, **kwargs)
