"""Steps for tests/features/capabilities/reliability/."""
import time
from unittest.mock import patch

from pytest_bdd import given, parsers, then, when


def _disk(used_percent):
    total = 100 * 1024 ** 3
    used = int(total * used_percent / 100)
    return total, used, total - used


@given('Redis is unreachable')
def _redis_down(mock_redis, ctx):
    mock_redis.ping.side_effect = ConnectionError('redis down')
    ctx['disk_percent'] = 50


@given('Redis is reachable')
def _redis_up(mock_redis, ctx):
    mock_redis.ping.return_value = True
    ctx['disk_percent'] = 50


@given(parsers.parse('the data disk is {percent:d} percent full'))
def _disk_full(mock_redis, ctx, percent):
    mock_redis.ping.return_value = True
    mock_redis.get.return_value = None
    mock_redis.hgetall.return_value = {
        'updated_at': str(time.time()), 'worker_count': '2', 'status': 'up',
    }
    ctx['disk_percent'] = percent


@given('no Celery workers are registered')
def _no_workers(mock_redis, ctx):
    mock_redis.ping.return_value = True
    mock_redis.get.return_value = None
    mock_redis.hgetall.return_value = {
        'updated_at': str(time.time()), 'worker_count': '0', 'status': 'up',
    }
    ctx['disk_percent'] = 50


@when('I probe liveness')
def _liveness(bdd_client, ctx):
    ctx['response'] = bdd_client.get('/healthz')


@when('I probe readiness')
def _readiness(bdd_client, ctx):
    ctx['response'] = bdd_client.get('/readyz')


@when('I ask for detailed health')
def _detailed(bdd_client, mock_redis, ctx):
    mock_redis.get.return_value = None
    if not isinstance(mock_redis.hgetall.return_value, dict):
        mock_redis.hgetall.return_value = {}
    with patch('shutil.disk_usage', return_value=_disk(ctx.get('disk_percent', 50))):
        ctx['response'] = bdd_client.get('/api/health')


@then('the service reports it is alive')
def _alive(ctx):
    assert ctx['response'].status_code == 200


@then('the service reports it is ready')
def _ready(ctx):
    assert ctx['response'].status_code == 200


@then('the service reports it is not ready')
def _not_ready(ctx):
    assert ctx['response'].status_code == 503


@then(parsers.parse('the detailed health is "{status}"'))
def _detailed_status(ctx, status):
    assert ctx['response'].get_json()['status'] == status


@then(parsers.parse('the detailed health responds with {code:d}'))
def _detailed_code(ctx, code):
    assert ctx['response'].status_code == code


@then(parsers.parse('the disk component is "{status}"'))
def _disk_component(ctx, status):
    assert ctx['response'].get_json()['components']['disk']['status'] == status
