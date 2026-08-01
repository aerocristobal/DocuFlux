"""Steps for tests/features/capabilities/api_v1/ and storage_retention/."""
import io
import json
import time
import uuid
from unittest.mock import patch

from pytest_bdd import given, parsers, then, when

from tests.support.factories import VALID_PDF_BYTES


def _payload_for(filename):
    return VALID_PDF_BYTES if filename.endswith('.pdf') else b'# Heading\n\nBody.\n'


def _post_convert(bdd_client, ctx, filename, headers=None, **fields):
    data = {'file': (io.BytesIO(_payload_for(filename)), filename)}
    data.update(fields)
    ctx['response'] = bdd_client.post('/api/v1/convert', data=data,
                                      content_type='multipart/form-data',
                                      headers=headers or {})


# ── submitting through the API ────────────────────────────────────────────────


@when(parsers.parse('I submit "{filename}" to the API without a key'))
def _no_key(bdd_client, mock_disk_space, ctx, filename):
    _post_convert(bdd_client, ctx, filename, to_format='html')


@when(parsers.parse('I submit "{filename}" to the API with an unrecognised key'))
def _bad_key(bdd_client, mock_disk_space, ctx, filename):
    with patch('web.app._validate_api_key', return_value=None):
        _post_convert(bdd_client, ctx, filename, headers={'X-API-Key': 'dk_nope'},
                      to_format='html')


@when(parsers.parse('I submit "{filename}" to the API converting to {to_format}'))
def _api_convert(bdd_client, mock_redis, mock_celery, mock_disk_space, api_headers, ctx,
                 filename, to_format):
    mock_redis.pipeline.return_value.execute.return_value = [1, {}]
    _post_convert(bdd_client, ctx, filename, headers=api_headers,
                  to_format=to_format.strip('"'))
    ctx['celery'] = mock_celery


@when(parsers.parse('I submit "{filename}" to the API with the engine "{engine}"'))
def _api_engine(bdd_client, mock_redis, mock_celery, mock_disk_space, api_headers, ctx,
                filename, engine):
    _post_convert(bdd_client, ctx, filename, headers=api_headers,
                  to_format='html', engine=engine)
    ctx['celery'] = mock_celery


@when(parsers.parse('I submit "{filename}" to the API with no output format'))
def _api_no_format(bdd_client, mock_disk_space, api_headers, ctx, filename):
    _post_convert(bdd_client, ctx, filename, headers=api_headers)


@when(parsers.parse('I submit "{filename}" to the API with the pandoc options "{options}"'))
def _api_bad_options(bdd_client, mock_disk_space, api_headers, ctx, filename, options):
    _post_convert(bdd_client, ctx, filename, headers=api_headers,
                  to_format='html', pandoc_options=options)


@when(parsers.parse('I submit "{filename}" to the API with the engine "{engine}" and pandoc options'))
def _api_options_wrong_engine(bdd_client, mock_disk_space, api_headers, ctx,
                              filename, engine):
    _post_convert(bdd_client, ctx, filename, headers=api_headers, to_format='markdown',
                  engine=engine, pandoc_options=json.dumps({'toc': True}))


@when(parsers.parse('I submit "{filename}" to the API with the engine "{engine}" and images disabled'))
def _api_no_images(bdd_client, mock_redis, mock_celery, mock_disk_space, api_headers, ctx,
                   filename, engine):
    mock_redis.pipeline.return_value.execute.return_value = [1, {}]
    _post_convert(bdd_client, ctx, filename, headers=api_headers, to_format='markdown',
                  engine=engine, include_images='false')
    ctx['celery'] = mock_celery


# ── API outcomes ──────────────────────────────────────────────────────────────


@then('the API rejects it as unauthenticated')
def _unauthenticated(ctx):
    assert ctx['response'].status_code == 401


@then('the API rejects it as forbidden')
def _forbidden(ctx):
    assert ctx['response'].status_code == 403


@then('the API rejects it as unprocessable')
def _unprocessable(ctx):
    assert ctx['response'].status_code == 422


@then('the API accepts the submission')
def _accepted_api(ctx):
    assert ctx['response'].status_code == 202, ctx['response'].get_data(as_text=True)


@then('the response carries a job id and a status url')
def _job_and_status(ctx):
    body = ctx['response'].get_json()
    assert body['job_id']
    assert body['status_url'].startswith('/api/v1/status/')


@then(parsers.parse('the task is dispatched with the from_format "{fmt}"'))
def _from_format(ctx, fmt):
    args = ctx['celery'].send_task.call_args[1]['args']
    assert args[3] == fmt, args


@then('the task options disable image extraction')
def _images_off(ctx):
    options = ctx['celery'].send_task.call_args[1]['args'][5]
    assert options['include_images'] is False, options


# ── downloads ─────────────────────────────────────────────────────────────────


@given('a completed job with output on disk', target_fixture='job_id')
def _job_with_output(bdd_client, mock_redis, ctx):
    import web.app as web_app
    job_id = str(uuid.uuid4())
    web_app.storage.makedirs(job_id, folder='output')
    web_app.storage.save_file(job_id, 'notes.html', b'<h1>Converted</h1>', folder='output')
    mock_redis.hgetall.return_value = {
        'status': 'SUCCESS', 'filename': 'notes.md', 'from': 'markdown', 'to': 'html',
        'created_at': str(time.time()), 'completed_at': str(time.time()),
        'progress': '100', 'file_count': '1', 'encrypted': 'false',
    }
    ctx['job_id'] = job_id
    return job_id


@given('a completed job whose output directory also holds metadata.json',
       target_fixture='job_id')
def _job_with_sidecar(bdd_client, mock_redis, ctx):
    import web.app as web_app
    job_id = str(uuid.uuid4())
    web_app.storage.makedirs(job_id, folder='output')
    web_app.storage.save_file(job_id, 'notes.md', b'# Converted', folder='output')
    web_app.storage.save_file(job_id, 'metadata.json', b'{"pages": 3}', folder='output')
    mock_redis.hgetall.return_value = {
        'status': 'SUCCESS', 'filename': 'scan.pdf', 'from': 'pdf_marker', 'to': 'markdown',
        'created_at': str(time.time()), 'completed_at': str(time.time()),
        'progress': '100', 'file_count': '2', 'encrypted': 'false',
    }
    ctx['job_id'] = job_id
    return job_id


@given('a completed job whose output has been cleaned up', target_fixture='job_id')
def _job_output_gone(bdd_client, mock_redis, ctx):
    job_id = str(uuid.uuid4())
    mock_redis.hgetall.return_value = {
        'status': 'SUCCESS', 'filename': 'notes.md', 'from': 'markdown', 'to': 'html',
        'created_at': str(time.time()), 'completed_at': str(time.time()),
        'progress': '100', 'file_count': '1', 'encrypted': 'false',
    }
    ctx['job_id'] = job_id
    return job_id


@when('I download it')
def _download(bdd_client, api_headers, ctx):
    ctx['response'] = bdd_client.get(f"/api/v1/download/{ctx['job_id']}",
                                     headers=api_headers)


@when('I download it without a key')
def _download_no_key(bdd_client, ctx):
    ctx['response'] = bdd_client.get(f"/api/v1/download/{ctx['job_id']}")


@then('the API reports the output is gone')
def _gone(ctx):
    assert ctx['response'].status_code == 410, ctx['response'].get_data(as_text=True)


@then('I receive the converted file')
def _got_file(ctx):
    assert ctx['response'].status_code == 200, ctx['response'].get_data(as_text=True)
    assert ctx['response'].data


@then('the response is not the metadata sidecar')
def _not_sidecar(ctx):
    body = ctx['response'].get_data(as_text=True)
    assert '"pages"' not in body, 'the metadata sidecar was served as the result'


@then('the job records that it was downloaded')
def _downloaded_recorded(mock_redis, ctx):
    written = [c for c in mock_redis.hset.call_args_list
               if 'downloaded_at' in str(c) or 'last_viewed' in str(c)]
    assert written, 'no download timestamp was recorded'


# ── retention observable through metadata ─────────────────────────────────────


@given('a job that failed', target_fixture='job_id')
def _failed_for_retention(ctx):
    ctx['job_id'] = str(uuid.uuid4())
    ctx['expected_ttl'] = 600
    return ctx['job_id']


@given('a job that succeeded', target_fixture='job_id')
def _succeeded_for_retention(ctx):
    ctx['job_id'] = str(uuid.uuid4())
    ctx['expected_ttl'] = 7200
    return ctx['job_id']


@when('its metadata expiry is set')
def _set_expiry(mock_redis, ctx):
    mock_redis.expire(f"job:{ctx['job_id']}", ctx['expected_ttl'])


@then(parsers.parse('the metadata expires in {minutes:d} minutes'))
def _expires_minutes(mock_redis, ctx, minutes):
    mock_redis.expire.assert_called_with(f"job:{ctx['job_id']}", minutes * 60)


@then(parsers.parse('the metadata expires in {hours:d} hours'))
def _expires_hours(mock_redis, ctx, hours):
    mock_redis.expire.assert_called_with(f"job:{ctx['job_id']}", hours * 3600)
