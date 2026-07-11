"""Structured JSON logging shared by the web and worker tiers (Story 3.5).

One log line format, one place it's defined, so log aggregators can query
both tiers with the same field names. Named `logging_config.py` rather than
the backlog's literal `logging.py` — `shared/` is flattened into `/app` in
both Docker images (`COPY shared/ .`), so a module named `logging.py` would
shadow Python's own stdlib `logging` package for the whole application.

Carries whichever correlation IDs are relevant to the tier: `request_id`
(web, set per HTTP request) and `job_id`/`task_id` (worker, set per Celery
task). Fields not relevant to a given log line default to "-".
"""

import logging
import sys
import contextvars

_request_id_var = contextvars.ContextVar('_docuflux_request_id', default='-')
_job_id_var = contextvars.ContextVar('_docuflux_job_id', default='-')
_task_id_var = contextvars.ContextVar('_docuflux_task_id', default='-')

JSON_LOG_FORMAT = (
    '{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s", '
    '"request_id": "%(request_id)s", "job_id": "%(job_id)s", "task_id": "%(task_id)s", '
    '"message": "%(message)s"}'
)


class _CorrelationFilter(logging.Filter):
    """Injects the current contextvar-scoped correlation IDs into every record."""

    def filter(self, record):
        record.request_id = _request_id_var.get()
        record.job_id = _job_id_var.get()
        record.task_id = _task_id_var.get()
        return True


def configure_json_logging(level=logging.INFO, stream=None):
    """Install the shared JSON stdout handler on the root logger.

    Idempotent: calling this more than once (e.g. reimport during tests, or
    a dev-server reload) does not stack duplicate handlers.
    """
    root_logger = logging.getLogger()
    if any(getattr(h, '_docuflux_json', False) for h in root_logger.handlers):
        return root_logger

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(logging.Formatter(JSON_LOG_FORMAT))
    handler.addFilter(_CorrelationFilter())
    handler._docuflux_json = True
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
    return root_logger


def set_request_id(value):
    """Web tier: correlate every log line for this request (call in
    before_request)."""
    _request_id_var.set(value or '-')


def set_job_context(job_id=None, task_id=None):
    """Worker tier: correlate every log line for this task's execution.
    Call with no arguments to clear the context when the task finishes."""
    _job_id_var.set(job_id or '-')
    _task_id_var.set(task_id or '-')
