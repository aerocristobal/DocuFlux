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


def parse_sentinel_hosts(sentinel_hosts_str):
    """Parse 'host1:port1,host2:port2' into list of (host, port) tuples."""
    sentinels = []
    for entry in sentinel_hosts_str.split(','):
        entry = entry.strip()
        if not entry:
            continue
        host, port = entry.rsplit(':', 1)
        sentinels.append((host, int(port)))
    return sentinels


def create_sentinel_client(sentinel_hosts_str, service_name, db=0,
                           password=None, decode_responses=True,
                           socket_connect_timeout=None, socket_timeout=None):
    """Create a Redis client via Sentinel for high availability."""
    from redis.sentinel import Sentinel
    sentinels = parse_sentinel_hosts(sentinel_hosts_str)
    sentinel_kwargs = {}
    if password:
        sentinel_kwargs['password'] = password
    connection_kwargs = {'db': db, 'decode_responses': decode_responses}
    if socket_connect_timeout is not None:
        connection_kwargs['socket_connect_timeout'] = socket_connect_timeout
    if socket_timeout is not None:
        connection_kwargs['socket_timeout'] = socket_timeout
    sentinel = Sentinel(sentinels, sentinel_kwargs=sentinel_kwargs)
    logging.info("Redis Sentinel enabled: service=%s, sentinels=%s", service_name, sentinels)
    return sentinel.master_for(service_name, **connection_kwargs)
