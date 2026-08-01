"""Uncovered branches of web/routes/health.py.

The happy paths are already covered by test_web.py::TestHealthEndpoints and the contract
tests. This targets what they miss: the disk-usage thresholds, the byte-decoding and
numeric coercion of GPU info, and every degraded/exception branch — i.e. exactly the
paths that only run when something is already going wrong.
"""
from unittest.mock import patch

import pytest

from tests.support.fixtures import isolated_client as client  # noqa: F401
from tests.support.fixtures import mock_redis as _bare_mock_redis  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.reliability]


@pytest.fixture
def mock_redis(_bare_mock_redis):
    """Bare mock_redis with JSON-serialisable defaults.

    The health endpoints put Redis values straight into the response body, so an
    unconfigured MagicMock return makes jsonify raise instead of exercising the branch
    under test. Individual tests override these.
    """
    _bare_mock_redis.get.return_value = None
    _bare_mock_redis.hgetall.return_value = {}
    _bare_mock_redis.ping.return_value = True
    return _bare_mock_redis


def _disk(total_gb=100, used_percent=50.0):
    """shutil.disk_usage returns (total, used, free) in bytes."""
    total = int(total_gb * 1024 ** 3)
    used = int(total * used_percent / 100)
    return total, used, total - used


class TestDetailedHealthDisk:

    def test_disk_below_90_percent_is_ok(self, client, mock_redis):
        with patch('shutil.disk_usage', return_value=_disk(used_percent=50)):
            body = client.get('/api/health').get_json()
        assert body['components']['disk']['status'] == 'ok'
        assert body['status'] == 'healthy'

    def test_disk_at_90_percent_warns_without_degrading_overall(self, client, mock_redis):
        with patch('shutil.disk_usage', return_value=_disk(used_percent=90)):
            body = client.get('/api/health').get_json()
        assert body['components']['disk']['status'] == 'warning'
        assert body['status'] == 'healthy'

    def test_disk_at_95_percent_is_critical_and_degrades_overall(self, client, mock_redis):
        with patch('shutil.disk_usage', return_value=_disk(used_percent=96)):
            response = client.get('/api/health')
        body = response.get_json()
        assert body['components']['disk']['status'] == 'critical'
        assert body['status'] == 'degraded'
        assert response.status_code == 200, "degraded is still serving; only unhealthy is 503"

    def test_disk_metrics_are_reported_in_gb(self, client, mock_redis):
        with patch('shutil.disk_usage', return_value=_disk(total_gb=100, used_percent=25)):
            disk = client.get('/api/health').get_json()['components']['disk']
        assert disk['total_gb'] == 100.0
        assert disk['used_gb'] == 25.0
        assert disk['used_percent'] == 25.0

    def test_unreadable_disk_reports_unknown(self, client, mock_redis):
        with patch('shutil.disk_usage', side_effect=OSError('no such path')):
            body = client.get('/api/health').get_json()
        assert body['components']['disk']['status'] == 'unknown'
        assert body['status'] == 'healthy', "an unreadable disk is not itself unhealthy"


class TestDetailedHealthRedis:

    def test_redis_down_is_unhealthy_with_503(self, client, mock_redis):
        mock_redis.ping.side_effect = ConnectionError('redis down')
        with patch('shutil.disk_usage', return_value=_disk()):
            response = client.get('/api/health')
        assert response.status_code == 503
        assert response.get_json()['status'] == 'unhealthy'

    def test_redis_error_detail_is_not_leaked(self, client, mock_redis):
        """The connection string can carry a password; the response must not echo it."""
        mock_redis.ping.side_effect = ConnectionError('redis://user:hunter2@internal:6379')
        with patch('shutil.disk_usage', return_value=_disk()):
            response = client.get('/api/health')
        assert b'hunter2' not in response.data


class TestDetailedHealthWorkers:

    def _worker_cache(self, mock_redis, cache):
        mock_redis.hgetall.side_effect = lambda key: cache if key == 'workers:status' else {}

    def test_stale_cache_reports_unknown(self, client, mock_redis):
        self._worker_cache(mock_redis, {'updated_at': '0', 'worker_count': '3'})
        with patch('shutil.disk_usage', return_value=_disk()):
            workers = client.get('/api/health').get_json()['components']['celery_workers']
        assert workers['status'] == 'unknown'
        assert 'stale' in workers['reason']

    def test_zero_workers_degrades_overall_status(self, client, mock_redis):
        import time
        self._worker_cache(mock_redis, {
            'updated_at': str(time.time()), 'worker_count': '0', 'status': 'up',
        })
        with patch('shutil.disk_usage', return_value=_disk()):
            body = client.get('/api/health').get_json()
        assert body['components']['celery_workers']['worker_count'] == 0
        assert body['status'] == 'degraded'

    def test_healthy_workers_keep_status_healthy(self, client, mock_redis):
        import time
        self._worker_cache(mock_redis, {
            'updated_at': str(time.time()), 'worker_count': '2', 'status': 'up',
        })
        with patch('shutil.disk_usage', return_value=_disk()):
            body = client.get('/api/health').get_json()
        assert body['components']['celery_workers']['worker_count'] == 2
        assert body['status'] == 'healthy'

    def test_absent_cache_reports_unknown(self, client, mock_redis):
        self._worker_cache(mock_redis, {})
        with patch('shutil.disk_usage', return_value=_disk()):
            workers = client.get('/api/health').get_json()['components']['celery_workers']
        assert workers['status'] == 'unknown'
        assert 'no cached worker status' in workers['reason']

    def test_unparseable_cache_reports_unknown(self, client, mock_redis):
        """A non-numeric updated_at must not 500 the health endpoint."""
        self._worker_cache(mock_redis, {'updated_at': 'not-a-number'})
        with patch('shutil.disk_usage', return_value=_disk()):
            response = client.get('/api/health')
        assert response.status_code in (200, 503)
        workers = response.get_json()['components']['celery_workers']
        assert workers['status'] == 'unknown'


class TestServiceStatusGpuInfo:

    def _redis_gpu(self, mock_redis, gpu_info, gpu_status='available'):
        mock_redis.get.side_effect = lambda key: {
            'service:marker:status': 'ready',
            'service:marker:eta': 'done',
            'marker:gpu_status': gpu_status,
        }.get(key)
        mock_redis.hgetall.return_value = gpu_info

    def test_byte_keys_and_values_are_decoded(self, client, mock_redis):
        self._redis_gpu(mock_redis, {b'name': b'RTX 4090'})
        body = client.get('/api/status/services').get_json()
        assert body['gpu_info']['name'] == 'RTX 4090'

    def test_integer_strings_are_coerced_to_int(self, client, mock_redis):
        self._redis_gpu(mock_redis, {'memory_total_mb': '24576'})
        body = client.get('/api/status/services').get_json()
        assert body['gpu_info']['memory_total_mb'] == 24576

    def test_decimal_strings_are_coerced_to_float(self, client, mock_redis):
        self._redis_gpu(mock_redis, {'utilization': '87.5'})
        body = client.get('/api/status/services').get_json()
        assert body['gpu_info']['utilization'] == 87.5

    def test_non_numeric_strings_are_left_alone(self, client, mock_redis):
        self._redis_gpu(mock_redis, {'driver': '535.104.05'})
        body = client.get('/api/status/services').get_json()
        assert body['gpu_info']['driver'] == '535.104.05'

    def test_absent_gpu_info_reports_initializing(self, client, mock_redis):
        self._redis_gpu(mock_redis, {})
        body = client.get('/api/status/services').get_json()
        assert body['gpu_info'] == {'status': 'initializing'}

    def test_redis_failure_reports_unavailable(self, client, mock_redis):
        mock_redis.get.side_effect = ConnectionError('redis down')
        body = client.get('/api/status/services').get_json()
        assert body['gpu_status'] == 'unavailable'
        assert body['gpu_info']['status'] == 'unavailable'

    def test_missing_marker_status_defaults_to_initializing(self, client, mock_redis):
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}
        body = client.get('/api/status/services').get_json()
        assert body['marker'] == 'initializing'
        assert body['models_cached'] is False

    def test_low_disk_is_reported(self, client, mock_redis):
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}
        with patch('web.app.check_disk_space', return_value=False):
            body = client.get('/api/status/services').get_json()
        assert body['disk_space'] == 'low'
