"""Stub the heavy third-party modules the worker imports, so `import tasks` works.

`worker/tasks/*.py` imports marker, torch, PIL and friends *inside function bodies*,
so they cannot be patched with `unittest.mock.patch` on a module attribute — they have
to be intercepted at the `sys.modules` level before the import executes.

This used to live at the top of `tests/unit/test_worker.py`, which meant the stubs were
only installed once pytest happened to import that module. Collection order made that
work by accident; `tests/bdd/` and `tests/characterization/` sort before `tests/unit/`,
so anything importing `tasks` from those directories would have got the real modules.
Installing from `tests/conftest.py` instead makes it deterministic.

Ordering constraint: call this *after* `tests/conftest.py` imports the real `config`,
`secrets_manager` and `web.app`. Those need the genuine modules; the worker stubs only
need to be in place before the first test module is imported.
"""
import sys
from unittest.mock import MagicMock

_INSTALLED = False

# Always replaced. These are either heavy third-party packages that may not be installed
# at all (marker, torch), or modules whose import has side effects the unit tier must not
# incur: worker/tasks/__init__.py builds a real `SocketIO(message_queue=...)` at module
# scope, so leaving flask_socketio real makes `import tasks` open a live Redis connection.
# `metrics` and `warmup` are first-party but the tests that exercise them for real
# (tests/unit/test_validation_metrics_warmup.py) load them from source specifically to
# survive this.
_ALWAYS_STUB = (
    'metrics',
    'warmup',
    'PIL',
    'PIL.Image',
    'flask_socketio',
    'torch',
    'marker',
    'marker.models',
    'marker.converters',
    'marker.converters.pdf',
    'marker.output',
    'prometheus_client',
)

# Stubbed only when not already imported for real. The worker imports these inside
# function bodies (worker/tasks/metadata.py), so nothing needs them mocked at import
# time — and tests/integration/test_encryption_pipeline.py calls importlib.reload() on
# encryption and key_manager, which fails outright against a MagicMock.
_STUB_IF_ABSENT = (
    'encryption',
    'key_manager',
    'secrets_manager',
)


def install_worker_stubs():
    """Install the worker's heavy-dependency stubs. Idempotent and safe to re-call.

    Replaces the listed modules unconditionally, which is what the block in
    tests/unit/test_worker.py did. That matters for more than speed: `worker/tasks/
    __init__.py` builds a real `SocketIO(message_queue=...)` at module scope, so leaving
    flask_socketio real makes `import tasks` open a live Redis connection and costs the
    suite several seconds. Stubbing keeps the unit tier hermetic.

    Tests that need the genuine modules (tests/unit/test_encryption.py,
    test_key_secrets.py) load them from source via tests/unit/crypto_helpers.py precisely
    to survive this, so clobbering here does not weaken them.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    for name in _ALWAYS_STUB:
        sys.modules[name] = MagicMock()

    for name in _STUB_IF_ABSENT:
        existing = sys.modules.get(name)
        if existing is None or isinstance(existing, MagicMock):
            sys.modules[name] = MagicMock()

    # secrets_manager: startup validation returns an empty dict rather than a MagicMock,
    # so callers that iterate the result do not blow up.
    sys.modules['secrets_manager'].validate_secrets_at_startup.return_value = {}

    # metrics: every counter/gauge is used as `metric.labels(...).inc()`, so `.labels()`
    # has to return the metric itself for the chain to work.
    counter = MagicMock()
    counter.labels.return_value = counter
    gauge = MagicMock()
    gauge.labels.return_value = gauge

    metrics = sys.modules['metrics']
    metrics.conversion_total = counter
    metrics.conversion_duration_seconds = counter
    metrics.conversion_failures_total = counter
    metrics.worker_tasks_active = gauge
    metrics.worker_info = MagicMock()
    metrics.update_queue_metrics = MagicMock()
    metrics.update_redis_pool_metrics = MagicMock()
    metrics.redis_pool_active = MagicMock()
    metrics.redis_pool_available = MagicMock()
    metrics.start_metrics_server = MagicMock()
    metrics.dlq_total = MagicMock()

    sys.modules['encryption'].EncryptionService = MagicMock()
    sys.modules['key_manager'].create_key_manager = MagicMock()
    sys.modules['warmup'].get_slm_model = MagicMock(return_value=None)

    # torch.cuda is probed for availability and memory accounting on the GPU paths.
    torch = sys.modules['torch']
    torch.cuda.is_available.return_value = True
    torch.cuda.empty_cache = MagicMock()
    torch.cuda.memory_allocated.return_value = 0
    torch.cuda.memory_reserved.return_value = 0

    # marker.models.shutdown_models is called on worker exit during v2 shutdown.
    # It is safe to call multiple times; the real implementation is a no-op when the
    # worker did not spawn the server. Stub it as a no-op so callers do not crash.
    sys.modules['marker.models'].shutdown_models = MagicMock()
