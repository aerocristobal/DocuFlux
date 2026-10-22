"""Tests for worker/tasks/surya_lifecycle.py (on-demand Surya activity tracking)."""
import threading
from unittest.mock import MagicMock, patch

import pytest

import tasks  # noqa: F401  (ensures package + stubs are loaded)
from tasks import surya_lifecycle as sl


@pytest.fixture
def env():
    """Patch collaborators; yields a namespace of mocks."""
    ns = MagicMock()
    with patch.object(sl, "surya_state") as state, \
         patch.object(sl, "_healthy") as healthy, \
         patch.object(sl, "_pkg") as pkg, \
         patch.object(sl.time, "sleep") as sleep, \
         patch.dict("os.environ", {"SURYA_INFERENCE_URL": "http://surya:8000"}):
        state.get_ocr_settings.return_value = {"mode": "on_demand", "idle_timeout_min": 15}
        pkg.app_settings.surya_cold_start_timeout_seconds = 5
        ns.state, ns.healthy, ns.pkg, ns.sleep = state, healthy, pkg, sleep
        yield ns


def test_already_healthy_skips_wait(env):
    env.healthy.return_value = True
    sl.ensure_inference_server("t1", "j1")
    env.state.mark_inflight.assert_called_once_with(env.pkg.redis_client, "t1")
    env.sleep.assert_not_called()
    env.pkg.update_job_metadata.assert_not_called()


def test_cold_start_waits_and_publishes_stage(env):
    env.healthy.side_effect = [False, False, True]
    sl.ensure_inference_server("t1", "j1")
    env.pkg.update_job_metadata.assert_called_once_with("j1", {"stage": "Starting AI engine"})
    assert env.sleep.call_count == 2


def test_timeout_raises_clear_error(env):
    env.healthy.return_value = False
    with patch.object(sl.time, "monotonic", side_effect=[0, 1, 6]):
        with pytest.raises(sl.InferenceServerTimeout, match="AI engine did not start"):
            sl.ensure_inference_server("t1", "j1")


def test_cpu_only_does_not_wait(env):
    env.state.get_ocr_settings.return_value = {"mode": "cpu_only", "idle_timeout_min": 15}
    env.healthy.return_value = False
    sl.ensure_inference_server("t1", "j1")
    env.sleep.assert_not_called()
    env.pkg.update_job_metadata.assert_not_called()


def test_no_url_does_not_wait(env):
    with patch.dict("os.environ", {}, clear=True):
        sl.ensure_inference_server("t1", "j1")
    env.healthy.assert_not_called()
    env.state.mark_inflight.assert_called_once()


def test_session_clears_inflight_and_touches_on_success(env):
    env.healthy.return_value = True
    with sl.inference_session("t1", "j1"):
        pass
    env.state.clear_inflight.assert_called_once_with(env.pkg.redis_client, "t1")
    env.state.touch_last_used.assert_called()


def test_session_cleans_up_on_body_error(env):
    env.healthy.return_value = True
    with pytest.raises(ValueError):
        with sl.inference_session("t1", "j1"):
            raise ValueError("boom")
    env.state.clear_inflight.assert_called_once_with(env.pkg.redis_client, "t1")


def test_session_cleans_up_on_timeout(env):
    env.healthy.return_value = False
    with patch.object(sl.time, "monotonic", side_effect=[0, 6]):
        with pytest.raises(sl.InferenceServerTimeout):
            with sl.inference_session("t1", "j1"):
                pytest.fail("body must not run")
    env.state.clear_inflight.assert_called_once()


def test_heartbeat_refreshes_during_long_job(env):
    env.healthy.return_value = True
    refreshed = threading.Event()
    env.state.mark_inflight.side_effect = lambda *a: (
        refreshed.set() if env.state.mark_inflight.call_count > 1 else None)
    with patch.object(sl, "HEARTBEAT_INTERVAL_SECONDS", 0.01):
        with sl.inference_session("t1", "j1"):
            assert refreshed.wait(2)


def test_redis_failure_in_tracking_does_not_fail_job(env):
    env.healthy.return_value = True
    env.state.mark_inflight.side_effect = RuntimeError("redis down")
    env.state.clear_inflight.side_effect = RuntimeError("redis down")
    with sl.inference_session("t1", "j1"):
        pass


def test_healthy_probe_false_on_exception():
    with patch.object(sl.requests, "get", side_effect=OSError("refused")):
        assert sl._healthy("http://x") is False
    with patch.object(sl.requests, "get", return_value=MagicMock(status_code=200)):
        assert sl._healthy("http://x/") is True
