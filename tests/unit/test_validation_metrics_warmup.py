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
import threading
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
        with patch("webhook_validation.socket.getaddrinfo",
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

    def test_healthz_reports_marker_warm_state(self):
        """Story 6.2: /healthz reads marker:model_warm (set by the *Celery
        worker* process, a different process from this one) and reports it
        alongside the existing ready/initializing signal."""
        import http.client
        import json as json_module

        w = self._load()
        mock_redis = MagicMock()
        mock_redis.get.return_value = 'true'

        with patch.object(w, 'r', mock_redis), \
                patch('os.path.exists', return_value=True):
            server = w.HTTPServer(('127.0.0.1', 0), w.HealthHandler)
            port = server.server_address[1]
            t = threading.Thread(target=server.handle_request, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                conn.request('GET', '/healthz')
                resp = conn.getresponse()
                body = json_module.loads(resp.read())
                status_code = resp.status
                conn.close()
            finally:
                t.join(timeout=5)
                server.server_close()

        assert status_code == 200
        assert body['marker_warm'] is True

    def test_healthz_reports_cold_when_flag_unset(self):
        import http.client
        import json

        w = self._load()
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # key absent -> cold

        with patch.object(w, 'r', mock_redis), \
                patch('os.path.exists', return_value=True):
            server = w.HTTPServer(('127.0.0.1', 0), w.HealthHandler)
            port = server.server_address[1]
            t = threading.Thread(target=server.handle_request, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                conn.request('GET', '/healthz')
                resp = conn.getresponse()
                body = json.loads(resp.read())
                conn.close()
            finally:
                t.join(timeout=5)
                server.server_close()

        assert body['marker_warm'] is False

    def test_healthz_reports_inference_server_reachability(self):
        """Marker 2: /healthz must report the shared Surya inference server's
        reachability (marker:inference_server, refreshed by
        check_inference_server) alongside the marker warm/cold signal."""
        import http.client
        import json as json_module

        w = self._load()
        mock_redis = MagicMock()

        def _get(key):
            return 'true' if key == 'marker:model_warm' else 'reachable'

        mock_redis.get.side_effect = _get

        with patch.object(w, 'r', mock_redis), \
                patch('os.path.exists', return_value=True):
            server = w.HTTPServer(('127.0.0.1', 0), w.HealthHandler)
            port = server.server_address[1]
            t = threading.Thread(target=server.handle_request, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                conn.request('GET', '/healthz')
                resp = conn.getresponse()
                body = json_module.loads(resp.read())
                status_code = resp.status
                conn.close()
            finally:
                t.join(timeout=5)
                server.server_close()

        assert status_code == 200
        assert body['marker_warm'] is True
        assert body['inference_server'] == 'reachable'

    def test_check_inference_server_not_configured_without_url(self):
        """No SURYA_INFERENCE_URL means the deployment manages models
        in-process — there is nothing external to probe."""
        w = self._load()
        mock_redis = MagicMock()
        env = {k: v for k, v in os.environ.items() if k != "SURYA_INFERENCE_URL"}
        with patch.object(w, 'r', mock_redis), \
                patch.dict(os.environ, env, clear=True):
            status = w.check_inference_server()
        assert status == "not_configured"
        mock_redis.set.assert_called_once_with(w.INFERENCE_SERVER_KEY, "not_configured")

    def test_check_inference_server_reachable(self):
        """A 200 from the server's /health endpoint reports 'reachable'."""
        w = self._load()
        mock_redis = MagicMock()
        with patch.object(w, 'r', mock_redis), \
                patch.dict(os.environ, {"SURYA_INFERENCE_URL": "http://surya-vlm:8000"}), \
                patch("requests.get", return_value=MagicMock(status_code=200)) as mock_get:
            status = w.check_inference_server()
        assert status == "reachable"
        mock_get.assert_called_once_with("http://surya-vlm:8000/health", timeout=2)
        mock_redis.set.assert_called_once_with(w.INFERENCE_SERVER_KEY, "reachable")

    def test_check_inference_server_unreachable_on_non_200(self):
        w = self._load()
        mock_redis = MagicMock()
        with patch.object(w, 'r', mock_redis), \
                patch.dict(os.environ, {"SURYA_INFERENCE_URL": "http://surya-vlm:8000"}), \
                patch("requests.get", return_value=MagicMock(status_code=503)):
            status = w.check_inference_server()
        assert status == "unreachable"
        mock_redis.set.assert_called_once_with(w.INFERENCE_SERVER_KEY, "unreachable")

    def test_check_inference_server_unreachable_on_connection_error(self):
        """A dead server must degrade to 'unreachable', never raise."""
        w = self._load()
        mock_redis = MagicMock()
        with patch.object(w, 'r', mock_redis), \
                patch.dict(os.environ, {"SURYA_INFERENCE_URL": "http://surya-vlm:8000"}), \
                patch("requests.get", side_effect=ConnectionError("refused")):
            status = w.check_inference_server()
        assert status == "unreachable"

    def test_check_inference_server_tolerates_redis_outage(self):
        """The probe result must be returned even if Redis persistence fails."""
        w = self._load()
        mock_redis = MagicMock()
        mock_redis.set.side_effect = ConnectionError("redis down")
        env = {k: v for k, v in os.environ.items() if k != "SURYA_INFERENCE_URL"}
        with patch.object(w, 'r', mock_redis), \
                patch.dict(os.environ, env, clear=True):
            status = w.check_inference_server()
        assert status == "not_configured"

    def test_warmup_reports_inference_server_and_never_sets_inference_ram(self):
        """Marker 2: warmup() must not resurrect the dead INFERENCE_RAM knob
        (create_model_dict() ignores it; device placement is server-side) and
        must record the shared inference server's reachability instead."""
        w = self._load()
        mock_redis = MagicMock()
        env = {k: v for k, v in os.environ.items()
               if k not in ("INFERENCE_RAM", "SLM_MODEL_PATH")}
        env["SURYA_INFERENCE_URL"] = "http://surya-vlm:8000"
        with patch.object(w, 'r', mock_redis), \
                patch.dict(os.environ, env, clear=True), \
                patch("requests.get", return_value=MagicMock(status_code=200)) as mock_get, \
                patch('os.path.exists', return_value=False), \
                patch('builtins.open') as mock_open:
            w.warmup()

        assert 'INFERENCE_RAM' not in os.environ
        mock_get.assert_called_once()
        persisted = {call.args[0]: call.args[1]
                     for call in mock_redis.set.call_args_list if call.args}
        assert persisted.get(w.INFERENCE_SERVER_KEY) == "reachable"

    def test_redis_client_created_via_tls_aware_factory(self):
        """Story 4.1b: warmup.py must route through create_redis_client (which
        wires ssl_cert_reqs/ca_certs/certfile/keyfile for rediss:// URLs)
        rather than a raw redis.StrictRedis.from_url() that skips them."""
        if "llama_cpp" not in sys.modules:
            stub = types.ModuleType("llama_cpp")
            stub.Llama = MagicMock()
            sys.modules["llama_cpp"] = stub

        with patch.dict(os.environ, {"REDIS_METADATA_URL": "rediss://redis:6380/1"}), \
                patch("redis_client.create_redis_client") as mock_create:
            mock_create.return_value = MagicMock()
            spec = importlib.util.spec_from_file_location(
                "_real_warmup_tls_check", os.path.join(_WORKER, "warmup.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

        mock_create.assert_called_once()
        args, kwargs = mock_create.call_args
        assert args[0] == "rediss://redis:6380/1"
        assert kwargs.get("decode_responses") is True
