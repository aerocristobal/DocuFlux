"""Characterization of worker/tasks/capture.py::process_capture_batch.

These tests pin current behaviour, including behaviour that may be undesirable. If you
change the behaviour deliberately, change the test in the same commit and say why.

Batches run concurrently and write into a shared images/ directory at assembly time, so
the global image counter is what stops batch 2's images overwriting batch 1's. That
arithmetic is the main thing worth freezing here.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

import tasks

from .conftest import make_page, png_b64

pytestmark = [pytest.mark.characterization, pytest.mark.capture]

SESSION = 'sess-1'
JOB = 'job-1'


def batch_dir(storage, index=0):
    return storage.get_local_path(JOB, os.path.join('batches', f'batch_{index}'), folder='output')


def read_batch_md(storage, index=0):
    with open(os.path.join(batch_dir(storage, index), 'batch.md'), encoding='utf-8') as f:
        return f.read()


def set_marker_output(markdown='# Page', image_names=()):
    """Configure the stubbed Marker pipeline to return `markdown` and named images."""
    images = {}
    for name in image_names:
        img = MagicMock()
        img.save.side_effect = lambda path: open(path, 'wb').write(b'\x89PNG\r\n')
        images[name] = img
    sys.modules['marker.output'].text_from_rendered.return_value = (markdown, {}, images)
    return images


@pytest.fixture(autouse=True)
def marker_pipeline():
    """Stub the Marker converter and the model dict for every test in this module."""
    sys.modules['marker.converters.pdf'].PdfConverter.reset_mock()
    sys.modules['marker.output'].text_from_rendered.reset_mock()
    with patch.object(tasks, 'get_model_dict', return_value={}), \
         patch.object(tasks, '_cleanup_marker_memory', MagicMock()):
        yield


class TestEmptyBatches:

    def test_batch_with_no_images_writes_an_empty_file_and_completes(
        self, storage, redis, job_meta
    ):
        redis.lrange.return_value = [make_page(0, 'text only')]

        result = tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        assert result == {'status': 'success', 'images': 0}
        assert read_batch_md(storage) == ''

    def test_empty_batch_still_counts_as_done(self, storage, redis, job_meta):
        """Assembly waits on batches_done; an empty batch that never increments hangs it."""
        redis.lrange.return_value = [make_page(0, 'text only')]

        tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        redis.hincrby.assert_any_call(f'capture:session:{SESSION}', 'batches_done', 1)

    def test_undecodable_images_fall_back_to_the_empty_path(self, storage, redis, job_meta):
        redis.lrange.return_value = [make_page(
            0, 'text', images=[{'filename': 'x.png', 'b64': 'not-base64!!'}],
        )]

        result = tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        assert result == {'status': 'success', 'images': 0}
        assert read_batch_md(storage) == ''


class TestImageNaming:

    def _one_screenshot_page(self):
        return [make_page(0, 'p', images=[{'filename': 's.png', 'b64': png_b64(),
                                           'is_screenshot': True}])]

    def test_images_are_renamed_with_the_global_counter(self, storage, redis, job_meta):
        redis.lrange.return_value = self._one_screenshot_page()
        redis.incrby.return_value = 2      # two images now reserved in total
        redis.hget.return_value = '1'
        set_marker_output('![a](_page_0.png) ![b](_page_1.png)', ['_page_0.png', '_page_1.png'])

        tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        names = sorted(os.listdir(os.path.join(batch_dir(storage), 'images')))
        assert names == ['img_00000.png', 'img_00001.png']

    def test_second_batch_offsets_its_names_so_images_do_not_collide(
        self, storage, redis, job_meta
    ):
        """base_offset = new_total - num_images, so batch 2 starts where batch 1 ended."""
        redis.lrange.return_value = self._one_screenshot_page()
        redis.incrby.return_value = 5      # 3 already reserved, this batch adds 2
        redis.hget.return_value = '1'
        set_marker_output('![a](_page_0.png) ![b](_page_1.png)', ['_page_0.png', '_page_1.png'])

        tasks.process_capture_batch(SESSION, JOB, 1, 0, 1)

        names = sorted(os.listdir(os.path.join(batch_dir(storage, 1), 'images')))
        assert names == ['img_00003.png', 'img_00004.png']

    def test_markdown_links_are_rewritten_to_the_new_names(self, storage, redis, job_meta):
        redis.lrange.return_value = self._one_screenshot_page()
        redis.incrby.return_value = 1
        redis.hget.return_value = '1'
        set_marker_output('see ![a](_page_0.png)', ['_page_0.png'])

        tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        content = read_batch_md(storage)
        assert '(images/img_00000.png)' in content
        assert '_page_0.png' not in content

    def test_counter_key_is_given_a_ttl(self, storage, redis, job_meta):
        """Without expiry the counter outlives its session and leaks a Redis key."""
        redis.lrange.return_value = self._one_screenshot_page()
        redis.incrby.return_value = 1
        redis.hget.return_value = '1'
        set_marker_output('x', ['_page_0.png'])

        tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        redis.expire.assert_any_call(f'capture:session:{SESSION}:image_counter',
                                     tasks.app_settings.capture_session_ttl)

    def test_counter_is_not_touched_when_there_are_no_images(self, storage, redis, job_meta):
        redis.lrange.return_value = self._one_screenshot_page()
        redis.hget.return_value = '1'
        set_marker_output('no images', [])

        tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        redis.incrby.assert_not_called()


class TestProgress:

    def test_progress_is_scaled_to_seventy_five_percent(self, storage, redis, job_meta):
        """Batches are only part of the job; assembly owns the last quarter."""
        redis.lrange.return_value = [make_page(0, 'p', images=[
            {'filename': 's.png', 'b64': png_b64(), 'is_screenshot': True}])]
        redis.incrby.return_value = 0
        redis.hget.side_effect = lambda key, field: {'batches_queued': '4',
                                                     'batches_done': '2'}.get(field)
        set_marker_output('x', [])

        tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        assert job_meta.merged[JOB]['progress'] == str(int(2 / 4 * 75))

    def test_final_batch_reaches_seventy_five(self, storage, redis, job_meta):
        redis.lrange.return_value = [make_page(0, 'p', images=[
            {'filename': 's.png', 'b64': png_b64(), 'is_screenshot': True}])]
        redis.incrby.return_value = 0
        redis.hget.side_effect = lambda key, field: {'batches_queued': '2',
                                                     'batches_done': '2'}.get(field)
        set_marker_output('x', [])

        tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        assert job_meta.merged[JOB]['progress'] == '75'


class TestFailure:

    def test_failure_marks_the_batch_and_reraises(self, storage, redis, job_meta):
        redis.lrange.side_effect = RuntimeError('redis exploded')

        with pytest.raises(RuntimeError, match='redis exploded'):
            tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        redis.hset.assert_any_call(
            f'capture:batch:{SESSION}:0',
            mapping={'status': 'failed', 'error': 'redis exploded'},
        )

    def test_failure_increments_the_session_failure_counter(self, storage, redis, job_meta):
        """Assembly reads this to decide whether to attach batch_warnings."""
        redis.lrange.side_effect = RuntimeError('boom')

        with pytest.raises(RuntimeError):
            tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        redis.hincrby.assert_any_call(f'capture:session:{SESSION}', 'batches_failed', 1)

    def test_failure_does_not_increment_batches_done(self, storage, redis, job_meta):
        redis.lrange.side_effect = RuntimeError('boom')

        with pytest.raises(RuntimeError):
            tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        done_calls = [c for c in redis.hincrby.call_args_list if c.args[1] == 'batches_done']
        assert done_calls == []

    def test_batch_error_is_truncated_to_500_characters(self, storage, redis, job_meta):
        redis.lrange.side_effect = RuntimeError('x' * 2000)

        with pytest.raises(RuntimeError):
            tasks.process_capture_batch(SESSION, JOB, 0, 0, 1)

        mapping = redis.hset.call_args_list[-1].kwargs['mapping']
        assert len(mapping['error']) == 500
