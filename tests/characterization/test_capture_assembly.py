"""Characterization of worker/tasks/capture.py::assemble_capture_session.

These tests pin current behaviour, including behaviour that may be undesirable. If you
change the behaviour deliberately, change the test in the same commit and say why.

Assembly has three mutually exclusive paths selected by `force_ocr` and `batches_queued`
(text merge / batch merge / OCR fallback), plus shared finalisation. It runs against a
real temp filesystem here, so the assertions are about files that actually exist.
"""
import os

import pytest

import tasks

from .conftest import make_page, make_session_meta, png_b64

pytestmark = [pytest.mark.characterization, pytest.mark.capture]

SESSION = 'sess-1'
JOB = 'job-1'


def output_dir(storage):
    return storage.get_local_path(JOB, folder='output')


def read_output(storage, name='Test_Book.md'):
    with open(os.path.join(output_dir(storage), name), encoding='utf-8') as f:
        return f.read()


class TestTextAssembly:

    def test_pages_are_joined_with_a_horizontal_rule_under_front_matter(
        self, storage, redis, job_meta
    ):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(0, '# One'), make_page(1, '# Two')]

        tasks.assemble_capture_session(SESSION, JOB)

        content = read_output(storage)
        assert content.startswith('---\ntitle: Test Book\n')
        assert 'source: https://example.test/book' in content
        assert 'pages: 2' in content
        assert '# One\n\n---\n\n# Two' in content

    def test_pages_are_ordered_by_page_hint_not_list_order(self, storage, redis, job_meta):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(2, '# Third'), make_page(0, '# First')]

        tasks.assemble_capture_session(SESSION, JOB)

        content = read_output(storage)
        assert content.index('# First') < content.index('# Third')

    def test_inline_image_is_written_and_its_link_rewritten(self, storage, redis, job_meta):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(
            0, 'See ![alt](diagram.png) here',
            images=[{'filename': 'diagram.png', 'b64': png_b64(), 'alt': 'alt'}],
        )]

        tasks.assemble_capture_session(SESSION, JOB)

        assert os.path.exists(os.path.join(output_dir(storage), 'images', 'diagram.png'))
        assert '(images/diagram.png)' in read_output(storage)

    def test_image_not_referenced_in_the_text_is_appended(self, storage, redis, job_meta):
        """An image the page never linked is still surfaced rather than dropped."""
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(
            0, 'No link here',
            images=[{'filename': 'orphan.png', 'b64': png_b64(), 'alt': 'a caption'}],
        )]

        tasks.assemble_capture_session(SESSION, JOB)

        assert '![a caption](images/orphan.png)' in read_output(storage)

    def test_screenshots_are_skipped_in_the_text_path(self, storage, redis, job_meta):
        """Screenshots exist for the OCR path; inlining them into text output is noise."""
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(
            0, 'text',
            images=[{'filename': 'shot.png', 'b64': png_b64(), 'is_screenshot': True}],
        )]

        tasks.assemble_capture_session(SESSION, JOB)

        assert not os.path.exists(os.path.join(output_dir(storage), 'images'))

    def test_blob_image_links_are_stripped(self, storage, redis, job_meta):
        """blob: URLs are only valid in the originating tab, so they cannot survive."""
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [
            make_page(0, 'before ![x](blob:https://site/abc-123) after')
        ]

        tasks.assemble_capture_session(SESSION, JOB)

        content = read_output(storage)
        assert 'blob:' not in content
        assert 'before  after' in content

    def test_undecodable_image_is_skipped_without_failing_the_job(
        self, storage, redis, job_meta
    ):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(
            0, 'text', images=[{'filename': 'bad.png', 'b64': 'not-valid-base64!!!'}],
        )]

        tasks.assemble_capture_session(SESSION, JOB)

        assert job_meta.merged[JOB]['status'] == 'SUCCESS'


class TestBatchMerge:

    def _write_batch(self, storage, index, markdown='# Batch', image_name=None):
        batch_dir = os.path.join(output_dir(storage), 'batches', f'batch_{index}')
        os.makedirs(batch_dir, exist_ok=True)
        with open(os.path.join(batch_dir, 'batch.md'), 'w', encoding='utf-8') as f:
            f.write(markdown)
        if image_name:
            images = os.path.join(batch_dir, 'images')
            os.makedirs(images, exist_ok=True)
            with open(os.path.join(images, image_name), 'wb') as f:
                f.write(b'\x89PNG\r\n')

    def test_batches_are_merged_in_order(self, storage, redis, job_meta):
        storage.makedirs(JOB, 'images', folder='output')
        self._write_batch(storage, 0, '# First batch')
        self._write_batch(storage, 1, '# Second batch')
        redis.hgetall.return_value = make_session_meta(force_ocr='true', batches_queued=2)
        redis.hget.return_value = 'done'
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        content = read_output(storage)
        assert content.index('# First batch') < content.index('# Second batch')

    def test_failed_batch_inserts_a_visible_tombstone(self, storage, redis, job_meta):
        """A silently-short document is worse than one that says pages are missing."""
        storage.makedirs(JOB, 'images', folder='output')
        self._write_batch(storage, 0, '# Good batch')
        redis.hgetall.return_value = make_session_meta(
            force_ocr='true', batches_queued=2, batches_failed=1
        )
        redis.hget.side_effect = lambda key, field: 'failed' if key.endswith(':1') else 'done'
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        content = read_output(storage)
        assert '⚠ Batch 1 failed to process' in content
        assert '# Good batch' in content, "surviving batches are still merged"

    def test_partial_failure_still_succeeds_but_records_a_warning(
        self, storage, redis, job_meta
    ):
        storage.makedirs(JOB, 'images', folder='output')
        self._write_batch(storage, 0, '# Good')
        redis.hgetall.return_value = make_session_meta(
            force_ocr='true', batches_queued=2, batches_failed=1
        )
        redis.hget.side_effect = lambda key, field: 'failed' if key.endswith(':1') else 'done'
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        meta = job_meta.merged[JOB]
        assert meta['status'] == 'SUCCESS'
        assert '1 batch(es) had failures' in meta['batch_warnings']

    def test_no_warning_key_when_every_batch_succeeded(self, storage, redis, job_meta):
        storage.makedirs(JOB, 'images', folder='output')
        self._write_batch(storage, 0, '# Good')
        redis.hgetall.return_value = make_session_meta(force_ocr='true', batches_queued=1)
        redis.hget.return_value = 'done'
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        assert 'batch_warnings' not in job_meta.merged[JOB]

    def test_batch_images_are_copied_into_the_output_images_dir(
        self, storage, redis, job_meta
    ):
        storage.makedirs(JOB, 'images', folder='output')
        self._write_batch(storage, 0, '# B', image_name='img_00001.png')
        redis.hgetall.return_value = make_session_meta(force_ocr='true', batches_queued=1)
        redis.hget.return_value = 'done'
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        assert os.path.exists(os.path.join(output_dir(storage), 'images', 'img_00001.png'))

    def test_batch_scratch_directory_is_removed_after_merge(self, storage, redis, job_meta):
        storage.makedirs(JOB, 'images', folder='output')
        self._write_batch(storage, 0, '# B')
        redis.hgetall.return_value = make_session_meta(force_ocr='true', batches_queued=1)
        redis.hget.return_value = 'done'
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        assert not os.path.exists(os.path.join(output_dir(storage), 'batches'))


class TestFinalisation:

    def test_pages_list_is_deleted_from_redis(self, storage, redis, job_meta):
        """The page payloads are large; leaving them costs Redis memory for a day."""
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        redis.delete.assert_any_call(f'capture:session:{SESSION}:pages')

    def test_empty_images_dir_is_removed_and_file_count_corrected(
        self, storage, redis, job_meta
    ):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(0, 'no images')]

        tasks.assemble_capture_session(SESSION, JOB)

        assert not os.path.exists(os.path.join(output_dir(storage), 'images'))
        assert job_meta.merged[JOB]['file_count'] == '1'

    def test_file_count_includes_images(self, storage, redis, job_meta):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(
            0, '![a](one.png)', images=[{'filename': 'one.png', 'b64': png_b64()}],
        )]

        tasks.assemble_capture_session(SESSION, JOB)

        assert job_meta.merged[JOB]['file_count'] == '2'

    def test_successful_job_metadata_expires_in_two_hours(self, storage, redis, job_meta):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        redis.expire.assert_any_call(f'job:{JOB}', 7200)

    def test_output_is_never_marked_encrypted(self, storage, redis, job_meta):
        """File encryption is dormant on this path; pinning it so a change is deliberate."""
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        assert job_meta.merged[JOB]['encrypted'] == 'false'

    def test_progress_never_moves_backwards(self, storage, redis, job_meta):
        """Progress used to be set to 88 and then to 85 before finishing at 100, so the
        UI's progress bar stepped backwards near the end of every capture. The two
        updates were swapped; this guards the ordering.
        """
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        progress = [int(u['progress']) for _job, u in job_meta.sequence if 'progress' in u]
        assert progress == sorted(progress), f"progress went backwards: {progress}"
        assert progress[-1] == 100


class TestFailurePath:

    def test_session_with_no_pages_fails_the_job_and_reraises(self, storage, redis, job_meta):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = []

        with pytest.raises(ValueError, match='No pages found'):
            tasks.assemble_capture_session(SESSION, JOB)

        meta = job_meta.merged[JOB]
        assert meta['status'] == 'FAILURE'
        assert 'No pages found' in meta['error']
        assert meta['progress'] == '0'

    def test_failed_job_metadata_expires_in_ten_minutes(self, storage, redis, job_meta):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = []

        with pytest.raises(ValueError):
            tasks.assemble_capture_session(SESSION, JOB)

        redis.expire.assert_any_call(f'job:{JOB}', 600)

    def test_error_message_is_truncated_to_500_characters(self, storage, redis, job_meta):
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.side_effect = RuntimeError('x' * 2000)

        with pytest.raises(RuntimeError):
            tasks.assemble_capture_session(SESSION, JOB)

        assert len(job_meta.merged[JOB]['error']) <= 500

    def test_local_stage_is_released_on_success(self, storage, redis, job_meta, monkeypatch):
        calls = []
        monkeypatch.setattr(storage, 'cleanup_local_stage', lambda job_id: calls.append(job_id))
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = [make_page(0)]

        tasks.assemble_capture_session(SESSION, JOB)

        assert calls == [JOB]

    def test_local_stage_is_released_on_failure(self, storage, redis, job_meta, monkeypatch):
        """The finally block is what stops a killed S3 worker leaking its staging dir."""
        calls = []
        monkeypatch.setattr(storage, 'cleanup_local_stage', lambda job_id: calls.append(job_id))
        redis.hgetall.return_value = make_session_meta()
        redis.lrange.return_value = []

        with pytest.raises(ValueError):
            tasks.assemble_capture_session(SESSION, JOB)

        assert calls == [JOB]
