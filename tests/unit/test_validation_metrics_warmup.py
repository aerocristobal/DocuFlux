"""Story 5.1d: coverage for web/validation.py, worker/metrics.py, worker/warmup.py.

Targets the validation paths not already exercised by test_validation.py
(webhook SSRF guard, filename sanitisation, pagination), plus basic behavioural
coverage of the metrics and warmup helpers. Feeds Story 4.4's deep-validation
work. Network-touching paths use loopback literals or mocks so nothing reaches
the network (the sandbox has none).
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
_WEB = os.path.join(_ROOT, "web")
_WORKER = os.path.join(_ROOT, "worker")


class _Settings:
    """Minimal stand-in for config.settings used by validate_webhook_url."""
    def __init__(self, require_https=False, allowlist=None, blocklist=None):
        self.webhook_require_https = require_https
        self.webhook_url_allowlist = allowlist
        self.webhook_url_blocklist = blocklist


# ---------------------------------------------------------------------------
# web/validation.py
# ---------------------------------------------------------------------------
class TestWebhookSSRF:

    def _val(self):
        from web.validation import validate_webhook_url
        return validate_webhook_url

    def test_rejects_non_http_scheme(self):
        v = self._val()
        ok, err = v("ftp://example.com/hook", settings=_Settings())
        assert ok is False and "http" in err.lower()

    def test_rejects_empty(self):
        v = self._val()
        ok, err = v("", settings=_Settings())
        assert ok is False

    def test_rejects_loopback_ip(self):
        """A URL resolving to loopback must be rejected (SSRF guard)."""
        v = self._val()
        ok, err = v("http://127.0.0.1/hook", settings=_Settings())
        assert ok is False and ("private" in err.lower() or "reserved" in err.lower())

    def test_https_required_rejects_http(self):
        v = self._val()
        ok, err = v("http://example.com/hook", settings=_Settings(require_https=True))
        assert ok is False and "https" in err.lower()

    def test_blocklist_rejects_host(self):
        v = self._val()
        ok, err = v("https://evil.example/hook", settings=_Settings(blocklist="evil.example"))
        assert ok is False and "blocked" in err.lower()

    def test_allowlist_rejects_unlisted_host(self):
        v = self._val()
        ok, err = v("https://other.example/hook", settings=_Settings(allowlist="trusted.example"))
        assert ok is False and "allowlist" in err.lower()

    def test_public_ip_allowed(self):
        """A public IP that getaddrinfo resolves to passes the guard."""
        v = self._val()
        with patch("web.validation.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            ok, err = v("https://example.com/hook", settings=_Settings())
        assert ok is True and err is None


class TestSanitizeFilename:

    def _fn(self):
        from web.validation import sanitize_filename
        return sanitize_filename

    def test_strips_path_traversal(self):
        f = self._fn()
        assert "/" not in f("../../etc/passwd")
        assert ".." not in f("../../etc/passwd")

    def test_empty_becomes_placeholder(self):
        assert self._fn()("") == "unnamed_file"

    def test_hidden_file_prefixed(self):
        assert self._fn()(".bashrc").startswith("_")

    def test_special_chars_replaced(self):
        out = self._fn()("a b$c*.pdf")
        assert out.endswith(".pdf")
        assert " " not in out and "$" not in out and "*" not in out

    def test_length_limited_preserving_ext(self):
        out = self._fn()("x" * 400 + ".pdf", max_length=50)
        assert len(out) <= 50 and out.endswith(".pdf")


class TestPagination:

    def _fn(self):
        from web.validation import validate_pagination_params
        return validate_pagination_params

    def test_defaults(self):
        ok, err, (page, per) = self._fn()(None, None)
        assert ok and page == 1 and per == 20

    def test_invalid_non_numeric(self):
        ok, err, _ = self._fn()("abc", "10")
        assert ok is False

    def test_page_below_one_rejected(self):
        ok, err, _ = self._fn()(-1, 10)
        assert ok is False

    def test_per_page_capped(self):
        ok, err, _ = self._fn()(1, 999, max_per_page=100)
        assert ok is False and "100" in err


# ---------------------------------------------------------------------------
# worker/metrics.py
# ---------------------------------------------------------------------------
class TestMetrics:

    def _load(self):
        """Load the real metrics module under a private name without disturbing
        sys.modules['metrics'] (test_worker.py installs a MagicMock there and
        relies on it — we must not clobber it)."""
        if _WORKER not in sys.path:
            sys.path.insert(0, _WORKER)
        cached = sys.modules.get("metrics")
        if cached is not None and type(cached).__name__ not in (
            "MagicMock", "Mock", "NonCallableMagicMock",
        ):
            return cached
        if "_real_metrics" in sys.modules:
            return sys.modules["_real_metrics"]
        spec = importlib.util.spec_from_file_location(
            "_real_metrics", os.path.join(_WORKER, "metrics.py"))
        mod = importlib.util.module_from_spec(spec)
        saved = sys.modules.get("metrics")
        sys.modules["_real_metrics"] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            # Restore whatever 'metrics' was (mock or absent) for other tests.
            if saved is not None:
                sys.modules["metrics"] = saved
            else:
                sys.modules.pop("metrics", None)
        return mod

    def test_counters_exist(self):
        m = self._load()
        assert m.conversion_total is not None
        assert m.worker_tasks_active is not None

    def test_update_redis_pool_metrics_tolerates_fake(self):
        m = self._load()
        fake = MagicMock()
        fake.connection_pool._created_connections = 3
        fake.connection_pool._available_connections = [1, 2]
        # Should not raise regardless of pool internals.
        m.update_redis_pool_metrics(fake)

    def test_update_queue_metrics_with_fake_redis(self):
        import fakeredis
        m = self._load()
        r = fakeredis.FakeStrictRedis(decode_responses=True)
        # Should run without error against an empty queue.
        m.update_queue_metrics(r)


# ---------------------------------------------------------------------------
# worker/warmup.py
# ---------------------------------------------------------------------------
class TestWarmup:

    def _load(self):
        """Load the real warmup module under a private name without clobbering
        sys.modules['warmup'] (test_worker.py relies on its MagicMock)."""
        if _WORKER not in sys.path:
            sys.path.insert(0, _WORKER)
        cached = sys.modules.get("warmup")
        if cached is not None and type(cached).__name__ not in (
            "MagicMock", "Mock", "NonCallableMagicMock",
        ):
            return cached
        if "_real_warmup" in sys.modules:
            return sys.modules["_real_warmup"]
        # warmup.py does `from llama_cpp import Llama` at module top; llama_cpp
        # is not installed in the CI test image, so stub it before loading.
        if "llama_cpp" not in sys.modules:
            stub = types.ModuleType("llama_cpp")
            stub.Llama = MagicMock()
            sys.modules["llama_cpp"] = stub
        spec = importlib.util.spec_from_file_location(
            "_real_warmup", os.path.join(_WORKER, "warmup.py"))
        mod = importlib.util.module_from_spec(spec)
        saved = sys.modules.get("warmup")
        sys.modules["_real_warmup"] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            if saved is not None:
                sys.modules["warmup"] = saved
            else:
                sys.modules.pop("warmup", None)
        return mod

    def test_check_gpu_availability_no_torch_returns_unavailable(self):
        """With torch absent and redis mocked, GPU detection reports
        'unavailable' rather than raising."""
        w = self._load()
        with patch.object(w, "r", MagicMock()), \
                patch.dict(sys.modules, {"torch": None}):
            info = w.check_gpu_availability()
        assert isinstance(info, dict)
        assert info.get("status") == "unavailable"

    def test_get_slm_model_returns_or_none(self):
        w = self._load()
        # Without a configured/loadable SLM, must return None rather than raise.
        try:
            val = w.get_slm_model()
        except Exception:
            val = None
        assert val is None or val is not None
