"""Behavioural tests for worker/tasks/metadata.py's pure helpers.

`_parse_slm_json` has to cope with whatever a small local model emits — fenced blocks,
prose wrappers, wrong types — and `_sample_for_slm_context` bounds SLM latency for long
documents. Both are pure functions, so they test cleanly without the Celery machinery.
The task-level paths are covered by test_worker.py::TestExtractSlmMetadata.
"""
import json

import pytest

from tasks.metadata import _parse_slm_json, _sample_for_slm_context

pytestmark = pytest.mark.unit


class TestParseSlmJson:

    def test_parses_a_bare_json_object(self):
        raw = '{"title": "T", "tags": ["a"], "summary": "S"}'
        assert _parse_slm_json(raw) == {'title': 'T', 'tags': ['a'], 'summary': 'S'}

    def test_extracts_json_from_surrounding_prose(self):
        """SLMs routinely wrap the object in commentary."""
        raw = 'Sure! Here is the JSON:\n{"title": "T", "tags": [], "summary": "S"}\nHope that helps.'
        assert _parse_slm_json(raw)['title'] == 'T'

    def test_extracts_json_from_a_markdown_fence(self):
        raw = '```json\n{"title": "T", "tags": ["x"], "summary": "S"}\n```'
        assert _parse_slm_json(raw)['tags'] == ['x']

    def test_uses_the_outermost_braces(self):
        """rfind('}') means a nested object is captured whole, not truncated."""
        raw = '{"title": "T", "tags": ["a"], "summary": "S", "extra": {"k": "v"}}'
        assert _parse_slm_json(raw)['extra'] == {'k': 'v'}

    def test_scalar_tags_are_coerced_to_a_list(self):
        """Callers json.dumps() tags; a bare string would serialise wrongly."""
        raw = '{"title": "T", "tags": "single", "summary": "S"}'
        assert _parse_slm_json(raw)['tags'] == ['single']

    def test_numeric_tags_are_coerced_to_a_list_of_string(self):
        raw = '{"title": "T", "tags": 42, "summary": "S"}'
        assert _parse_slm_json(raw)['tags'] == ['42']

    def test_list_tags_are_left_alone(self):
        raw = '{"title": "T", "tags": ["a", "b"], "summary": "S"}'
        assert _parse_slm_json(raw)['tags'] == ['a', 'b']

    def test_raises_when_no_object_is_present(self):
        with pytest.raises(ValueError, match='No valid JSON'):
            _parse_slm_json('I could not complete that request.')

    def test_raises_on_malformed_json(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            _parse_slm_json('{"title": "T", "tags": [,,], }')

    @pytest.mark.parametrize('raw', [
        '{"tags": ["a"], "summary": "S"}',   # no title
        '{"title": "T", "summary": "S"}',    # no tags
        '{"title": "T", "tags": ["a"]}',     # no summary
    ])
    def test_raises_when_a_required_key_is_missing(self, raw):
        with pytest.raises(ValueError, match='Invalid metadata structure'):
            _parse_slm_json(raw)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _parse_slm_json('')


class TestSampleForSlmContext:

    def test_short_document_passes_through_unchanged(self):
        content = 'one two three'
        sampled, was_sampled = _sample_for_slm_context(content, max_words=100)
        assert sampled == content
        assert was_sampled is False

    def test_document_exactly_at_the_limit_is_not_sampled(self):
        content = ' '.join(f'w{i}' for i in range(50))
        sampled, was_sampled = _sample_for_slm_context(content, max_words=50)
        assert was_sampled is False
        assert sampled == content

    def test_document_over_the_limit_is_sampled(self):
        content = ' '.join(f'w{i}' for i in range(200))
        sampled, was_sampled = _sample_for_slm_context(content, max_words=50)
        assert was_sampled is True
        assert '[... content omitted ...]' in sampled

    def test_sampling_keeps_both_head_and_tail(self):
        """Head-only truncation loses facts that appear late; this keeps the tail."""
        content = ' '.join(f'w{i}' for i in range(200))
        sampled, _ = _sample_for_slm_context(content, max_words=50)
        assert 'w0' in sampled
        assert 'w199' in sampled

    def test_sampled_size_is_bounded_by_max_words(self):
        """SLM latency must not grow with document length."""
        short = ' '.join(f'w{i}' for i in range(200))
        long = ' '.join(f'w{i}' for i in range(100_000))
        short_sampled, _ = _sample_for_slm_context(short, max_words=50)
        long_sampled, _ = _sample_for_slm_context(long, max_words=50)

        marker_words = 4  # the '[... content omitted ...]' separator
        assert len(short_sampled.split()) <= 50 + marker_words
        assert len(long_sampled.split()) <= 50 + marker_words

    def test_head_ratio_controls_the_split(self):
        content = ' '.join(f'w{i}' for i in range(1000))
        sampled, _ = _sample_for_slm_context(content, max_words=100, head_ratio=0.6)
        head = sampled.split('[... content omitted ...]')[0]
        assert len(head.split()) == 60

    def test_head_ratio_of_one_yields_head_only_with_no_separator(self):
        content = ' '.join(f'w{i}' for i in range(200))
        sampled, was_sampled = _sample_for_slm_context(content, max_words=50, head_ratio=1.0)
        assert was_sampled is True
        assert '[... content omitted ...]' not in sampled
        assert 'w199' not in sampled

    def test_empty_document_is_not_sampled(self):
        sampled, was_sampled = _sample_for_slm_context('', max_words=50)
        assert was_sampled is False
        assert sampled == ''
