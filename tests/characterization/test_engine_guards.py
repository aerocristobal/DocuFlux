"""Characterization of the guards every conversion engine shares.

These tests pin current behaviour, including behaviour that may be undesirable. If you
change the behaviour deliberately, change the test in the same commit and say why.

All five engines repeat the same two safety properties, and the repetition is exactly why
they drift. Sweeping them parametrically means adding a sixth engine without the guard
fails here rather than in production:

  * a task re-delivered for an already-FAILED or REVOKED job is skipped, not re-run.
    Celery uses acks_late, so a worker that dies mid-task has its message redelivered;
    without this a cancelled job silently resurrects and rewrites its own output.
  * the S3 local staging directory is released in a finally block, so a raising task
    cannot leak it.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

import tasks

pytestmark = [pytest.mark.characterization, pytest.mark.reliability]

# (task callable name, extra positional args beyond the five standard ones)
ENGINES = [
    'convert_document',
    'convert_with_marker',
    'convert_with_marker_slm',
    'convert_with_hybrid',
    'convert_with_ocr',
]


def call_engine(name, job_id):
    """Invoke an engine with a standard argument set."""
    task = getattr(tasks, name)
    return task(job_id, 'in.pdf', 'out.md', 'pdf_marker', 'markdown')


@pytest.fixture
def job_id():
    return str(uuid.uuid4())


class TestRedeliveryGuard:
    """A re-delivered task must not re-run a job that is already finished or cancelled."""

    @pytest.mark.parametrize('engine', ENGINES)
    @pytest.mark.parametrize('status', ['FAILURE', 'REVOKED'])
    def test_skips_an_already_finished_job(self, engine, status, redis, job_meta, job_id):
        redis.hget.return_value = status

        result = call_engine(engine, job_id)

        assert result == {'status': 'skipped', 'reason': status}, (
            f"{engine} re-ran a {status} job instead of skipping it"
        )

    @pytest.mark.parametrize('engine', ENGINES)
    def test_skipped_job_metadata_is_left_untouched(self, engine, redis, job_meta, job_id):
        """Skipping must not overwrite the FAILURE status with PROCESSING."""
        redis.hget.return_value = 'REVOKED'

        call_engine(engine, job_id)

        assert job_id not in job_meta.merged, f"{engine} wrote metadata for a skipped job"

    @pytest.mark.parametrize('engine', ENGINES)
    def test_skip_happens_before_any_storage_access(self, engine, redis, job_meta,
                                                    storage, job_id, monkeypatch):
        """The guard returns before the try block, so no staging dir is created —
        and therefore none is released either."""
        released = []
        monkeypatch.setattr(storage, 'cleanup_local_stage', lambda j: released.append(j))
        redis.hget.return_value = 'REVOKED'

        call_engine(engine, job_id)

        assert released == [], f"{engine} touched the local stage for a skipped job"

    @pytest.mark.parametrize('engine', ENGINES)
    def test_a_running_job_is_not_skipped(self, engine, redis, job_meta, storage, job_id):
        """Only FAILURE and REVOKED skip; PROCESSING and PENDING must proceed."""
        redis.hget.return_value = 'PROCESSING'

        # The input file does not exist, so each engine proceeds far enough to fail on
        # that instead of returning 'skipped'.
        try:
            result = call_engine(engine, job_id)
        except Exception:
            return  # proceeded and raised, which is the point
        assert result.get('status') != 'skipped', f"{engine} skipped a live job"


class TestInvalidJobId:

    @pytest.mark.parametrize('engine', ENGINES)
    def test_a_malformed_job_id_is_rejected_before_redis(self, engine, redis, job_meta):
        """job_id reaches secure_filename and the filesystem, so it is validated first."""
        result = call_engine(engine, '../../etc/passwd')

        assert result == {'status': 'error', 'message': 'Invalid job ID'}
        redis.hget.assert_not_called()


class TestLocalStageRelease:
    """The finally block is what stops a hard-killed S3 worker leaking its staging dir."""

    @pytest.mark.parametrize('engine', ENGINES)
    def test_stage_is_released_when_the_input_is_missing(self, engine, redis, job_meta,
                                                         storage, job_id, monkeypatch):
        released = []
        monkeypatch.setattr(storage, 'cleanup_local_stage', lambda j: released.append(j))
        redis.hget.return_value = 'PROCESSING'

        with pytest.raises(Exception):
            call_engine(engine, job_id)

        assert released, f"{engine} did not release the local stage on failure"

    @pytest.mark.parametrize('engine', ENGINES)
    def test_stage_is_released_under_the_sanitised_job_id(self, engine, redis, job_meta,
                                                          storage, job_id, monkeypatch):
        """cleanup takes secure_filename(job_id); a mismatch here leaks the real dir."""
        released = []
        monkeypatch.setattr(storage, 'cleanup_local_stage', lambda j: released.append(j))
        redis.hget.return_value = 'PROCESSING'

        with pytest.raises(Exception):
            call_engine(engine, job_id)

        from werkzeug.utils import secure_filename
        assert released[0] == secure_filename(job_id)

    @pytest.mark.parametrize('engine', ENGINES)
    def test_missing_input_marks_the_job_failed(self, engine, redis, job_meta,
                                                storage, job_id):
        redis.hget.return_value = 'PROCESSING'

        with pytest.raises(Exception):
            call_engine(engine, job_id)

        assert job_meta.merged[job_id]['status'] == 'FAILURE'

    @pytest.mark.parametrize('engine', ENGINES)
    def test_missing_input_reports_a_clean_error(self, engine, redis, job_meta,
                                                 storage, job_id):
        """The message must not carry the input path.

        /api/v1/status serves this error field to API callers, so a path here exposes
        the container's filesystem layout. Four engines get this for free because their
        generic handler sits in an inner try that begins after the existence check;
        convert_with_ocr uses a single try and raises _MissingInput so its handler
        re-raises rather than overwriting.
        """
        redis.hget.return_value = 'PROCESSING'

        with pytest.raises(Exception):
            call_engine(engine, job_id)

        error = job_meta.merged[job_id]['error']
        assert 'Input file missing' in error
        assert '/uploads/' not in error, f"{engine} leaked the staging path: {error}"

    def test_ocr_missing_input_is_still_a_file_not_found_error(self, redis, job_meta,
                                                               storage, job_id):
        """_MissingInput subclasses FileNotFoundError, so existing callers and
        tests that catch that type keep working."""
        redis.hget.return_value = 'PROCESSING'

        with pytest.raises(FileNotFoundError):
            call_engine('convert_with_ocr', job_id)


class TestProvisionalBehaviour:
    """Behaviour that is known-incomplete, pinned so a refactor cannot drift it further."""

    @pytest.mark.provisional
    def test_screenshot_layout_analysis_returns_a_simulation_stub(self, redis, job_meta,
                                                                  storage):
        """worker/tasks/capture.py::analyze_screenshot_layout does not analyse anything.

        It captures a real screenshot via MCP, reads only the image's dimensions, and
        then returns hardcoded "Simulated ..." regions scaled to those dimensions. The
        result is persisted to job metadata as `layout_results`, so any consumer is
        reading fabricated data.

        Pinned deliberately as known-incomplete. When it grows a real implementation,
        replace this test rather than deleting it.
        """
        mcp_response = {
            'success': True,
            'script_execution_results': [{'action': 'screenshot', 'success': True}],
        }
        fake_image = MagicMock()
        fake_image.__enter__.return_value.size = (1000, 800)

        # `from PIL import Image` happens inside the function, so it resolves through
        # the stubbed sys.modules entry at call time rather than a module attribute.
        import sys
        with patch.object(tasks, 'call_mcp_server', return_value=mcp_response), \
             patch.object(sys.modules['PIL'], 'Image') as image_mod:
            image_mod.open.return_value = fake_image
            result = tasks.analyze_screenshot_layout(str(uuid.uuid4()), 'https://example.test')

        assert result['status'] == 'success'
        regions = result['layout_results']
        contents = [r['content'] for r in regions['text_regions']]
        assert contents == ['Simulated text content from OCR', 'More simulated text'], (
            "these strings are hardcoded, not derived from the screenshot"
        )
        # The only thing actually read from the image is its size.
        assert regions['text_regions'][0]['bbox'] == [0, 0, 1000, 800 * 0.7]
