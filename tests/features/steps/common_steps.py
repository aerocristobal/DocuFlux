"""Steps and scenario state shared across the capability specs.

Import discipline (see the note in tests/bdd/conftest.py): web code is reached as
`web.*`, worker code as `tasks.*`, shared code flat. `pythonpath` makes several modules
importable under two names, and two import paths for one file are two distinct module
objects — patching the wrong one silently does nothing.
"""
import uuid

import pytest
from pytest_bdd import given, parsers, then

# Extension per format key, enough for the formats these specs submit.
EXTENSIONS = {
    'markdown': '.md',
    'gfm': '.md',
    'html': '.html',
    'pdf_marker': '.pdf',
    'pdf_hybrid': '.pdf',
    'pdf_marker_slm': '.pdf',
    'pdf_ocr': '.pdf',
}


@pytest.fixture
def ctx():
    """Mutable per-scenario state.

    Steps that produce a value another step needs by name should use
    `target_fixture=` instead; this is for the incidental bookkeeping (the last
    response, the job id under test) that would otherwise need a fixture each.
    """
    return {}


@pytest.fixture
def bdd_client(isolated_client):
    """The Flask test client every capability spec drives the app through."""
    return isolated_client


# ── shared givens ─────────────────────────────────────────────────────────────


@given('the service has free disk space')
def _free_disk(mock_disk_space):
    return mock_disk_space


@given('the service has no free disk space')
def _full_disk():
    from unittest.mock import patch
    patcher = patch('web.app.check_disk_space', return_value=False)
    patcher.start()
    yield
    patcher.stop()


@given('I am browsing with a session', target_fixture='session_id')
def _session(bdd_client):
    session_id = str(uuid.uuid4())
    with bdd_client.session_transaction() as sess:
        sess['session_id'] = session_id
    return session_id


# ── shared thens ──────────────────────────────────────────────────────────────


@then('the submission is accepted')
def _accepted(ctx):
    assert ctx['response'].status_code == 200, ctx['response'].get_data(as_text=True)


@then('the submission is rejected as a bad request')
def _rejected(ctx):
    assert ctx['response'].status_code == 400


@then('the request is rejected as a bad request')
def _request_rejected(ctx):
    assert ctx['response'].status_code == 400


@then('the submission is refused for lack of storage')
def _refused_storage(ctx):
    assert ctx['response'].status_code == 507


@then(parsers.parse('the error mentions "{fragment}"'))
def _error_mentions(ctx, fragment):
    body = ctx['response'].get_data(as_text=True)
    assert fragment in body, body


@then(parsers.parse('the API rejects it as a bad request'))
def _api_rejects(ctx):
    assert ctx['response'].status_code == 400


@then('the API reports the job was not found')
def _api_not_found(ctx):
    assert ctx['response'].status_code == 404
