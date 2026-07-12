"""Tests for shared/logging_config.py (Story 3.5)."""

import io
import json
import logging

import pytest

from logging_config import (
    configure_json_logging,
    set_request_id,
    set_job_context,
)


@pytest.fixture(autouse=True)
def _reset_logging():
    """Each test gets a clean root logger and cleared correlation context."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    set_request_id(None)
    set_job_context()
    yield
    root.handlers = saved_handlers
    root.level = saved_level
    set_request_id(None)
    set_job_context()


def _install_test_stream():
    stream = io.StringIO()
    configure_json_logging(stream=stream)
    return stream


def test_log_line_is_valid_json_with_expected_fields():
    stream = _install_test_stream()
    logging.getLogger("test").info("hello world")

    line = stream.getvalue().strip()
    data = json.loads(line)
    assert data["message"] == "hello world"
    assert data["level"] == "INFO"
    assert set(["time", "level", "module", "request_id", "job_id", "task_id", "message"]) <= set(data.keys())


def test_defaults_to_dash_when_no_context_set():
    stream = _install_test_stream()
    logging.getLogger("test").info("no context")

    data = json.loads(stream.getvalue().strip())
    assert data["request_id"] == "-"
    assert data["job_id"] == "-"
    assert data["task_id"] == "-"


def test_set_request_id_appears_in_log_line():
    stream = _install_test_stream()
    set_request_id("req-abc123")
    logging.getLogger("test").info("web request")

    data = json.loads(stream.getvalue().strip())
    assert data["request_id"] == "req-abc123"
    assert data["job_id"] == "-"


def test_set_job_context_appears_in_log_line():
    stream = _install_test_stream()
    set_job_context(job_id="11111111-1111-1111-1111-111111111111", task_id="task-42")
    logging.getLogger("test").info("worker task")

    data = json.loads(stream.getvalue().strip())
    assert data["job_id"] == "11111111-1111-1111-1111-111111111111"
    assert data["task_id"] == "task-42"
    assert data["request_id"] == "-"


def test_clearing_job_context_resets_to_dash():
    stream = _install_test_stream()
    set_job_context(job_id="job-1", task_id="task-1")
    set_job_context()  # clear, as task_postrun does
    logging.getLogger("test").info("after task")

    data = json.loads(stream.getvalue().strip())
    assert data["job_id"] == "-"
    assert data["task_id"] == "-"


def test_configure_json_logging_is_idempotent():
    """Calling configure_json_logging() twice must not stack duplicate handlers."""
    stream = _install_test_stream()
    configure_json_logging(stream=stream)  # second call, same/any stream
    logging.getLogger("test").info("only once")

    lines = [l for l in stream.getvalue().strip().split("\n") if l]
    assert len(lines) == 1
