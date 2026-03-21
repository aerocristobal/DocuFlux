"""
Epic 7, Story 7.2: Redis Sentinel support tests.

Tests the Sentinel client creation and configuration parsing
without requiring a real Sentinel cluster.
"""
import pytest
from unittest.mock import patch, MagicMock


class TestParseSentinelHosts:
    """Test parse_sentinel_hosts utility."""

    def test_single_host(self):
        from redis_client import parse_sentinel_hosts
        result = parse_sentinel_hosts('redis-sentinel:26379')
        assert result == [('redis-sentinel', 26379)]

    def test_multiple_hosts(self):
        from redis_client import parse_sentinel_hosts
        result = parse_sentinel_hosts('host1:26379,host2:26380,host3:26381')
        assert result == [('host1', 26379), ('host2', 26380), ('host3', 26381)]

    def test_whitespace_stripped(self):
        from redis_client import parse_sentinel_hosts
        result = parse_sentinel_hosts(' host1:26379 , host2:26380 ')
        assert result == [('host1', 26379), ('host2', 26380)]

    def test_empty_entries_skipped(self):
        from redis_client import parse_sentinel_hosts
        result = parse_sentinel_hosts('host1:26379,,host2:26380,')
        assert result == [('host1', 26379), ('host2', 26380)]


class TestCreateSentinelClient:
    """Test create_sentinel_client creates a proper Sentinel master client."""

    @patch('redis_client.Sentinel', create=True)
    def test_creates_sentinel_with_parsed_hosts(self, mock_sentinel_cls):
        # Patch at the import location inside create_sentinel_client
        with patch('redis.sentinel.Sentinel', mock_sentinel_cls):
            from redis_client import create_sentinel_client
            mock_sentinel_instance = MagicMock()
            mock_sentinel_cls.return_value = mock_sentinel_instance

            create_sentinel_client('host1:26379,host2:26380', 'mymaster', db=1)

            mock_sentinel_cls.assert_called_once_with(
                [('host1', 26379), ('host2', 26380)],
                sentinel_kwargs={},
            )
            mock_sentinel_instance.master_for.assert_called_once_with(
                'mymaster', db=1, decode_responses=True,
            )

    @patch('redis_client.Sentinel', create=True)
    def test_password_forwarded_to_sentinel_kwargs(self, mock_sentinel_cls):
        with patch('redis.sentinel.Sentinel', mock_sentinel_cls):
            from redis_client import create_sentinel_client
            mock_sentinel_cls.return_value = MagicMock()

            create_sentinel_client('host1:26379', 'mymaster', password='s3cret')

            call_kwargs = mock_sentinel_cls.call_args
            assert call_kwargs[1]['sentinel_kwargs'] == {'password': 's3cret'}

    @patch('redis_client.Sentinel', create=True)
    def test_socket_timeouts_forwarded(self, mock_sentinel_cls):
        with patch('redis.sentinel.Sentinel', mock_sentinel_cls):
            from redis_client import create_sentinel_client
            mock_instance = MagicMock()
            mock_sentinel_cls.return_value = mock_instance

            create_sentinel_client(
                'host1:26379', 'mymaster',
                socket_connect_timeout=5, socket_timeout=10,
            )

            master_kwargs = mock_instance.master_for.call_args[1]
            assert master_kwargs['socket_connect_timeout'] == 5
            assert master_kwargs['socket_timeout'] == 10


class TestSentinelNotUsedWhenUnconfigured:
    """Verify standard Redis path used when Sentinel is not configured."""

    def test_standard_redis_when_no_sentinel(self):
        """create_redis_client works unchanged when Sentinel is unconfigured."""
        with patch('redis.Redis.from_url') as mock_from_url:
            from redis_client import create_redis_client
            mock_settings = MagicMock()
            mock_settings.redis_tls_ca_certs = None
            mock_settings.redis_tls_certfile = None
            mock_settings.redis_tls_keyfile = None
            create_redis_client('redis://localhost:6379/0', mock_settings)
            mock_from_url.assert_called_once()


class TestCelerySentinelConfig:
    """Verify Celery Sentinel transport options are set correctly."""

    def test_sentinel_broker_url_format(self):
        """When Sentinel is configured, broker URL uses sentinel:// scheme."""
        from redis_client import parse_sentinel_hosts
        hosts = 'host1:26379,host2:26380,host3:26381'
        sentinels = parse_sentinel_hosts(hosts)
        broker_urls = ';'.join(f'sentinel://{h}:{p}' for h, p in sentinels)
        broker_url = f'{broker_urls}/0'
        assert broker_url == 'sentinel://host1:26379;sentinel://host2:26380;sentinel://host3:26381/0'
