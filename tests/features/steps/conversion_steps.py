"""Steps for tests/features/capabilities/conversion/."""
import io
import time
import uuid

from pytest_bdd import given, parsers, then, when

from tests.support.factories import VALID_PDF_BYTES

from .common_steps import EXTENSIONS


def _payload_for(filename):
    """Bytes that will survive validate_file_content_type for this extension."""
    return VALID_PDF_BYTES if filename.endswith('.pdf') else b'# Heading\n\nBody text.\n'


def _submit(bdd_client, mock_redis, mock_celery, ctx, filenames, from_format, to_format,
            size_bytes=None):
    from unittest.mock import patch

    mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]
    data = {
        'file': [(io.BytesIO(_payload_for(name)), name) for name in filenames],
        'from_format': from_format,
        'to_format': to_format,
    }
    with patch('web.app.storage.get_file_size', return_value=size_bytes or 1024):
        ctx['response'] = bdd_client.post('/convert', data=data,
                                          content_type='multipart/form-data')
    ctx['celery'] = mock_celery
    return ctx['response']


# ── submitting ────────────────────────────────────────────────────────────────


@when(parsers.parse('I submit "{filename}" converting {from_format} to {to_format}'))
def _submit_one(bdd_client, mock_redis, mock_celery, ctx, filename, from_format, to_format):
    _submit(bdd_client, mock_redis, mock_celery, ctx, [filename], from_format, to_format)


@when(parsers.parse('I submit {count:d} markdown files converting {from_format} to {to_format}'))
def _submit_many(bdd_client, mock_redis, mock_celery, ctx, count, from_format, to_format):
    names = [f'doc_{i}.md' for i in range(count)]
    _submit(bdd_client, mock_redis, mock_celery, ctx, names, from_format, to_format)


@when(parsers.parse('I submit "{filename}" of {size:d} MB converting {from_format} to {to_format}'))
def _submit_sized(bdd_client, mock_redis, mock_celery, ctx, filename, size,
                  from_format, to_format):
    _submit(bdd_client, mock_redis, mock_celery, ctx, [filename], from_format, to_format,
            size_bytes=size * 1024 * 1024)


@given(parsers.parse('I submit "{filename}" converting {from_format} to {to_format}'))
def _given_submit(bdd_client, mock_redis, mock_celery, ctx, filename, from_format, to_format):
    _submit(bdd_client, mock_redis, mock_celery, ctx, [filename], from_format, to_format)


@then(parsers.parse('I receive {count:d} job id'))
@then(parsers.parse('I receive {count:d} job ids'))
def _job_ids(ctx, count):
    assert len(ctx['response'].get_json()['job_ids']) == count


@then(parsers.parse('{count:d} conversion task is dispatched'))
@then(parsers.parse('{count:d} conversion tasks are dispatched'))
def _tasks_dispatched(ctx, count):
    assert ctx['celery'].send_task.call_count == count


# ── routing ───────────────────────────────────────────────────────────────────


def _last_dispatch(ctx):
    call = ctx['celery'].send_task.call_args
    assert call is not None, 'no task was dispatched'
    return call


@then(parsers.parse('the task {task_name} is dispatched to the {queue} queue'))
def _task_and_queue(ctx, task_name, queue):
    call = _last_dispatch(ctx)
    assert call[0][0] == task_name, f"dispatched {call[0][0]}"
    assert call[1]['queue'] == queue, f"queued on {call[1]['queue']}"


@then(parsers.parse('the task {task_name} is dispatched'))
def _task_name(ctx, task_name):
    assert _last_dispatch(ctx)[0][0] == task_name


@then(parsers.parse('the task is dispatched to the {queue} queue'))
def _queue_only(ctx, queue):
    assert _last_dispatch(ctx)[1]['queue'] == queue


@then('it is not dispatched to the gpu queue')
def _not_gpu(ctx):
    assert _last_dispatch(ctx)[1]['queue'] != 'gpu'


@then('the task options include force_ocr and use_llm')
def _has_options(ctx):
    args = _last_dispatch(ctx)[1]['args']
    options = args[5]
    assert 'force_ocr' in options and 'use_llm' in options


@then('the task is dispatched with no options')
def _no_options(ctx):
    args = _last_dispatch(ctx)[1]['args']
    assert len(args) == 5, f"pandoc conversions take five args, got {args}"


# ── job state ─────────────────────────────────────────────────────────────────


def _job_meta(status='SUCCESS', **extra):
    meta = {
        'status': status,
        'filename': 'notes.md',
        'from': 'markdown',
        'to': 'html',
        'created_at': str(time.time() - 60),
        'progress': '100' if status == 'SUCCESS' else '0',
        'file_count': '1',
    }
    if status == 'SUCCESS':
        meta['completed_at'] = str(time.time() - 5)
    meta.update({k: str(v) for k, v in extra.items()})
    return meta


@given(parsers.parse('a completed job graded "{grade}"'), target_fixture='job_id')
def _completed_graded(mock_redis, ctx, grade):
    job_id = str(uuid.uuid4())
    meta = _job_meta(quality_grade=grade, quality_score='40' if grade == 'poor' else '90')
    mock_redis.hgetall.return_value = meta
    mock_redis.lrange.return_value = [job_id]
    mock_redis.pipeline.return_value.execute.return_value = [meta]
    ctx['job_id'] = job_id
    ctx['meta'] = meta
    return job_id


@given(parsers.parse('a failed job with the error "{error}"'), target_fixture='job_id')
def _failed_job(mock_redis, ctx, error):
    job_id = str(uuid.uuid4())
    meta = _job_meta(status='FAILURE', error=error, completed_at=str(time.time()))
    mock_redis.hgetall.return_value = meta
    ctx['job_id'] = job_id
    return job_id


@given(parsers.parse('a job in progress at {percent:d} percent'), target_fixture='job_id')
def _in_progress(mock_redis, ctx, percent):
    job_id = str(uuid.uuid4())
    meta = _job_meta(status='PROCESSING', progress=percent)
    meta.pop('completed_at', None)
    mock_redis.hgetall.return_value = meta
    ctx['job_id'] = job_id
    return job_id


@given(parsers.parse('a completed job converted with {from_format}'), target_fixture='job_id')
def _completed_with_format(bdd_client, mock_redis, ctx, from_format):
    import web.app as web_app
    job_id = str(uuid.uuid4())
    mock_redis.hgetall.return_value = {
        'filename': f'doc{EXTENSIONS[from_format]}',
        'from': from_format,
        'to': 'markdown',
        'force_ocr': 'False',
        'use_llm': 'False',
    }
    mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]
    web_app.storage.makedirs(job_id, folder='upload')
    web_app.storage.save_file(job_id, f'doc{EXTENSIONS[from_format]}',
                              _payload_for(f'x{EXTENSIONS[from_format]}'), folder='upload')
    ctx['job_id'] = job_id
    return job_id


@given('a completed job whose uploaded file has been cleaned up', target_fixture='job_id')
def _cleaned_up(mock_redis, ctx):
    job_id = str(uuid.uuid4())
    mock_redis.hgetall.return_value = {
        'filename': 'notes.md', 'from': 'markdown', 'to': 'html',
        'force_ocr': 'False', 'use_llm': 'False',
    }
    ctx['job_id'] = job_id
    return job_id


@given('my session history references a job that no longer exists')
def _stale_history(mock_redis, ctx):
    ctx['stale_job_id'] = str(uuid.uuid4())
    mock_redis.lrange.return_value = [ctx['stale_job_id']]
    mock_redis.pipeline.return_value.execute.return_value = [{}]


# ── acting on jobs ────────────────────────────────────────────────────────────


@when('I retry that job')
def _retry(bdd_client, ctx, mock_celery):
    ctx['response'] = bdd_client.post(f"/api/retry/{ctx['job_id']}")
    ctx['celery'] = mock_celery


@when('I cancel that job')
def _cancel(bdd_client, ctx, mock_celery):
    ctx['response'] = bdd_client.post(f"/api/cancel/{ctx['job_id']}")
    ctx['celery'] = mock_celery


@when('I delete that job')
def _delete(bdd_client, ctx):
    from unittest.mock import patch
    with patch('shutil.rmtree') as rmtree:
        ctx['response'] = bdd_client.post(f"/api/delete/{ctx['job_id']}")
    ctx['rmtree'] = rmtree


@when(parsers.parse('I {action} the job "{job_id}"'))
def _act_on_bad_id(bdd_client, ctx, mock_redis, action, job_id):
    mock_redis.reset_mock()
    ctx['response'] = bdd_client.post(f'/api/{action}/{job_id}')


@when('I ask the API for its status')
def _api_status(bdd_client, ctx):
    ctx['response'] = bdd_client.get(f"/api/v1/status/{ctx['job_id']}")


@when('I ask the API for the status of an unknown job')
def _api_status_unknown(bdd_client, mock_redis, ctx):
    mock_redis.hgetall.return_value = {}
    ctx['response'] = bdd_client.get(f"/api/v1/status/{uuid.uuid4()}")


@when(parsers.parse('I ask the API for the status of "{job_id}"'))
def _api_status_malformed(bdd_client, ctx, job_id):
    ctx['response'] = bdd_client.get(f'/api/v1/status/{job_id}')


@when('I ask the API for the status of a traversal path')
def _api_status_traversal(bdd_client, ctx):
    ctx['response'] = bdd_client.get('/api/v1/status/../../etc/passwd')


@when('I ask the web UI for my job list')
def _ui_jobs(bdd_client, ctx):
    ctx['response'] = bdd_client.get('/api/jobs')


@when('a different session asks for its job list')
def _other_session(bdd_client, mock_redis, ctx):
    with bdd_client.session_transaction() as sess:
        sess['session_id'] = str(uuid.uuid4())
    mock_redis.lrange.return_value = []
    ctx['response'] = bdd_client.get('/api/jobs')


# ── outcomes ──────────────────────────────────────────────────────────────────


@then(parsers.parse('the status is "{status}"'))
def _status_is(ctx, status):
    assert ctx['response'].get_json()['status'] == status


@then(parsers.parse('the response carries the quality grade "{grade}"'))
def _grade_is(ctx, grade):
    body = ctx['response'].get_json()
    assert body.get('quality', {}).get('grade') == grade, body


@then(parsers.parse('the response carries the error "{error}"'))
def _error_is(ctx, error):
    assert error in (ctx['response'].get_json().get('error') or '')


@then(parsers.parse('the response reports {percent:d} percent progress'))
def _progress_is(ctx, percent):
    assert int(ctx['response'].get_json()['progress']) == percent


@then(parsers.parse('the job is listed with status "{status}"'))
def _listed_with_status(ctx, status):
    jobs = ctx['response'].get_json()
    assert jobs, 'the job list is empty'
    assert jobs[0]['status'] == status


@then('the job is recorded in my session history')
def _in_history(mock_redis, session_id, ctx):
    pushed = [c.args[0] for c in mock_redis.lpush.call_args_list]
    assert f'history:{session_id}' in pushed, pushed


@then('that session sees no jobs')
def _no_jobs(ctx):
    assert ctx['response'].get_json() == []


@then('the list is empty')
def _list_empty(ctx):
    assert ctx['response'].get_json() == []


@then('the stale entry is pruned from my history')
def _pruned(mock_redis, ctx):
    removed = [c.args[2] for c in mock_redis.pipeline.return_value.lrem.call_args_list]
    assert ctx['stale_job_id'] in removed, removed


@then('the task is revoked')
def _revoked(ctx):
    ctx['celery'].control.revoke.assert_called_with(ctx['job_id'], terminate=True)


@then(parsers.parse('the job metadata is set to expire in {minutes:d} minutes'))
def _expiry(mock_redis, ctx, minutes):
    mock_redis.expire.assert_called_with(f"job:{ctx['job_id']}", minutes * 60)


@then("the job's stored files are removed")
def _files_removed(ctx):
    assert ctx['response'].get_json()['status'] == 'deleted'


@then('the job metadata is removed')
def _meta_removed(mock_redis, ctx):
    mock_redis.delete.assert_called_with(f"job:{ctx['job_id']}")


@then('the retry is refused as a bad request')
def _retry_refused(ctx):
    assert ctx['response'].status_code == 400


@then('no job metadata is read')
def _no_read(mock_redis):
    assert not mock_redis.hgetall.called
