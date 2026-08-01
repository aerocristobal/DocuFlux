"""Steps for tests/features/capabilities/capture/."""
import uuid

from pytest_bdd import given, parsers, then, when


def _session_meta(status='active', page_count=0, **extra):
    meta = {
        'status': status,
        'page_count': str(page_count),
        'job_id': str(uuid.uuid4()),
        'to_format': 'markdown',
        'force_ocr': 'false',
        'batches_queued': '0',
        'batches_done': '0',
        'batches_failed': '0',
        'next_batch_start': '0',
        'title': 'Captured Document',
        'source_url': '',
    }
    meta.update({k: str(v) for k, v in extra.items()})
    return meta


@when(parsers.parse('I start a capture session titled "{title}"'))
def _start_titled(bdd_client, mock_redis, ctx, title):
    ctx['response'] = bdd_client.post('/api/v1/capture/sessions', json={'title': title})


@when(parsers.parse('I start a capture session requesting the "{fmt}" format'))
def _start_format(bdd_client, mock_redis, ctx, fmt):
    ctx['response'] = bdd_client.post('/api/v1/capture/sessions',
                                      json={'title': 'T', 'to_format': fmt})


@then('the session is created')
def _created(ctx):
    assert ctx['response'].status_code == 201, ctx['response'].get_data(as_text=True)


@then('it reports a session id and a job id')
def _ids(ctx):
    body = ctx['response'].get_json()
    assert body['session_id'] and body['job_id']


@then('it reports the page limit')
def _limit(ctx):
    import web.app as web_app
    assert ctx['response'].get_json()['max_pages'] == web_app.app_settings.max_capture_pages


@then(parsers.parse('the session records the "{fmt}" format'))
def _records_format(mock_redis, ctx, fmt):
    mapping = next(c.kwargs['mapping'] for c in mock_redis.hset.call_args_list
                   if 'to_format' in c.kwargs.get('mapping', {}))
    assert mapping['to_format'] == fmt


# ── session state ─────────────────────────────────────────────────────────────


@given('an active capture session', target_fixture='session')
def _active(mock_redis, ctx):
    meta = _session_meta()
    mock_redis.hgetall.return_value = meta
    ctx['session_id'] = str(uuid.uuid4())
    ctx['session_meta'] = meta
    return meta


@given('a capture session that has already finished', target_fixture='session')
def _finished(mock_redis, ctx):
    meta = _session_meta(status='finished', page_count=3)
    mock_redis.hgetall.return_value = meta
    ctx['session_id'] = str(uuid.uuid4())
    return meta


@given('no such capture session')
def _no_session(mock_redis, ctx):
    mock_redis.hgetall.return_value = {}
    ctx['session_id'] = str(uuid.uuid4())


@given('an active capture session that has reached the page limit', target_fixture='session')
def _at_limit(mock_redis, ctx):
    import web.app as web_app
    meta = _session_meta(page_count=web_app.app_settings.max_capture_pages)
    mock_redis.hgetall.return_value = meta
    ctx['session_id'] = str(uuid.uuid4())
    return meta


@given(parsers.parse('page {sequence:d} has already been captured'))
def _page_seen(mock_redis, ctx, sequence):
    # The route recognises a duplicate by testing the seen-pages set, so that is what
    # has to be configured. Leaving it to MagicMock's default truthiness would make the
    # scenario pass for the wrong reason, and change meaning the day the shared fixture
    # gains falsy defaults.
    mock_redis.sismember.return_value = True
    ctx['duplicate_sequence'] = sequence


@given(parsers.parse('{count:d} pages have been captured'))
def _pages_captured(mock_redis, ctx, count):
    ctx['session_meta']['page_count'] = str(count)
    mock_redis.hgetall.return_value = ctx['session_meta']


# ── acting ────────────────────────────────────────────────────────────────────


@when('I submit a page to it')
def _submit_page(bdd_client, ctx):
    ctx['response'] = bdd_client.post(
        f"/api/v1/capture/sessions/{ctx['session_id']}/pages",
        json={'page_sequence': 1, 'text': 'Some captured text.'},
    )


@when(parsers.parse('I submit page {sequence:d} again'))
def _submit_dup(bdd_client, ctx, sequence):
    ctx['response'] = bdd_client.post(
        f"/api/v1/capture/sessions/{ctx['session_id']}/pages",
        json={'page_sequence': sequence, 'text': 'Some captured text.'},
    )


@when('I finish the session')
def _finish(bdd_client, mock_celery, ctx):
    ctx['response'] = bdd_client.post(
        f"/api/v1/capture/sessions/{ctx['session_id']}/finish", json={})
    ctx['celery'] = mock_celery


# ── outcomes ──────────────────────────────────────────────────────────────────


@then('the page is refused as a conflict')
def _conflict(ctx):
    assert ctx['response'].status_code == 409


@then('the session is reported as not found')
def _session_missing(ctx):
    assert ctx['response'].status_code == 404


@then('the page is refused as unprocessable')
def _page_unprocessable(ctx):
    assert ctx['response'].status_code == 422


@then('the page is accepted')
def _page_ok(ctx):
    assert ctx['response'].status_code == 200, ctx['response'].get_data(as_text=True)


@then('the page is not stored a second time')
def _not_duplicated(mock_redis, ctx):
    stored = [c for c in mock_redis.rpush.call_args_list if ':pages' in str(c.args[0])]
    assert stored == [], 'a duplicate page was appended to the session'


@then('finishing is refused as unprocessable')
def _finish_refused(ctx):
    assert ctx['response'].status_code == 422


@then('the session is finished')
def _finished_ok(ctx):
    # 202: assembly is queued, not done.
    assert ctx['response'].status_code == 202, ctx['response'].get_data(as_text=True)


@then('an assembly task is queued')
def _assembly_queued(ctx):
    dispatched = [c[0][0] for c in ctx['celery'].send_task.call_args_list]
    assert 'tasks.assemble_capture_session' in dispatched, dispatched
