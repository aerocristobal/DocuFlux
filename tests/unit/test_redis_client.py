"""Behavioural tests for shared/redis_client.py.

`tests/unit/test_redis_sentinel.py` covers Sentinel construction; this covers the plain
client's TLS/kwargs handling, which nothing asserted. The TLS branch matters: a silent
failure to set ssl=True on a rediss:// URL downgrades the connection.
"""
from unittest.mock import patch

import pytest

from redis_client import create_redis_client, parse_sentinel_hosts

pytestmark = pytest.mark.unit


class _TlsSettings:
    def __init__(self, ca=None, cert=None, key=None):
        self.redis_tls_ca_certs = ca
        self.redis_tls_certfile = cert
        self.redis_tls_keyfile = key


def _build(url, settings=None, **kwargs):
    """Call create_redis_client and return the kwargs handed to redis.Redis.from_url."""
    with patch('redis_client.redis.Redis.from_url') as from_url:
        create_redis_client(url, settings or _TlsSettings(), **kwargs)
    assert from_url.called
    return from_url.call_args


class TestPlainConnections:

    def test_redis_url_does_not_enable_tls(self):
        _args, kwargs = _build('redis://localhost:6379/0')
        assert 'ssl' not in kwargs
        assert 'ssl_cert_reqs' not in kwargs

    def test_url_is_passed_through(self):
        args, _kwargs = _build('redis://localhost:6379/0')
        assert args[0] == 'redis://localhost:6379/0'

    def test_defaults_are_applied(self):
        _args, kwargs = _build('redis://localhost:6379/0')
        assert kwargs['max_connections'] == 20
        assert kwargs['decode_responses'] is True

    def test_max_connections_is_configurable(self):
        _args, kwargs = _build('redis://localhost:6379/0', max_connections=50)
        assert kwargs['max_connections'] == 50

    def test_decode_responses_can_be_disabled(self):
        _args, kwargs = _build('redis://localhost:6379/0', decode_responses=False)
        assert kwargs['decode_responses'] is False


class TestTimeouts:

    def test_timeouts_are_omitted_when_not_given(self):
        """Passing socket_timeout=None explicitly would change redis-py's behaviour."""
        _args, kwargs = _build('redis://localhost:6379/0')
        assert 'socket_connect_timeout' not in kwargs
        assert 'socket_timeout' not in kwargs

    def test_timeouts_are_forwarded_when_given(self):
        _args, kwargs = _build('redis://localhost:6379/0',
                               socket_connect_timeout=2, socket_timeout=5)
        assert kwargs['socket_connect_timeout'] == 2
        assert kwargs['socket_timeout'] == 5

    def test_zero_timeout_is_forwarded(self):
        """0 is falsy but is not None, so it must survive the is-not-None check."""
        _args, kwargs = _build('redis://localhost:6379/0', socket_timeout=0)
        assert kwargs['socket_timeout'] == 0


class TestTls:

    def test_rediss_url_enables_tls_with_required_cert_checking(self):
        _args, kwargs = _build('rediss://localhost:6379/0')
        assert kwargs['ssl'] is True
        assert kwargs['ssl_cert_reqs'] == 'required'

    def test_ca_cert_is_forwarded(self):
        _args, kwargs = _build('rediss://localhost:6379/0',
                               settings=_TlsSettings(ca='/certs/ca.pem'))
        assert kwargs['ssl_ca_certs'] == '/certs/ca.pem'

    def test_client_cert_and_key_are_forwarded(self):
        _args, kwargs = _build(
            'rediss://localhost:6379/0',
            settings=_TlsSettings(cert='/certs/client.pem', key='/certs/client.key'),
        )
        assert kwargs['ssl_certfile'] == '/certs/client.pem'
        assert kwargs['ssl_keyfile'] == '/certs/client.key'

    def test_unset_tls_paths_are_omitted(self):
        _args, kwargs = _build('rediss://localhost:6379/0')
        assert 'ssl_ca_certs' not in kwargs
        assert 'ssl_certfile' not in kwargs
        assert 'ssl_keyfile' not in kwargs

    def test_tls_paths_are_ignored_for_a_plain_url(self):
        """Configuring certs must not imply TLS on a redis:// URL."""
        _args, kwargs = _build('redis://localhost:6379/0',
                               settings=_TlsSettings(ca='/certs/ca.pem'))
        assert 'ssl_ca_certs' not in kwargs


class TestParseSentinelHosts:

    def test_single_host(self):
        assert parse_sentinel_hosts('sentinel:26379') == [('sentinel', 26379)]

    def test_multiple_hosts(self):
        assert parse_sentinel_hosts('a:26379,b:26380') == [('a', 26379), ('b', 26380)]

    def test_whitespace_is_stripped(self):
        assert parse_sentinel_hosts(' a:26379 , b:26380 ') == [('a', 26379), ('b', 26380)]

    def test_empty_entries_are_skipped(self):
        assert parse_sentinel_hosts('a:26379,,b:26380,') == [('a', 26379), ('b', 26380)]

    def test_empty_string_yields_no_sentinels(self):
        assert parse_sentinel_hosts('') == []

    def test_port_is_an_int(self):
        [(_host, port)] = parse_sentinel_hosts('sentinel:26379')
        assert isinstance(port, int)

    def test_ipv6_style_host_splits_on_the_last_colon(self):
        """rsplit(':', 1) keeps a bracketed IPv6 literal intact."""
        assert parse_sentinel_hosts('[::1]:26379') == [('[::1]', 26379)]

    def test_entry_without_a_port_raises(self):
        """Misconfiguration should fail loudly at startup, not connect somewhere odd."""
        with pytest.raises(ValueError):
            parse_sentinel_hosts('sentinel-with-no-port')
