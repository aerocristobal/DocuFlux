import importlib.util
import os
from unittest.mock import MagicMock

import fakeredis
import pytest

from shared import surya_state as st

pytestmark = pytest.mark.unit

_path = os.path.join(os.path.dirname(__file__), "../../surya-controller/main.py")
_spec = importlib.util.spec_from_file_location("surya_controller_main_reconcile", _path)
ctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctl)

NOW = 1_000_000.0
MIN = 60


@pytest.fixture
def r():
    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture
def broker():
    return fakeredis.FakeStrictRedis(decode_responses=True)


def docker(state):
    d = MagicMock()
    d.state.return_value = state
    return d


def running_on_demand(r, idle_for_min, desired="running"):
    st.set_ocr_settings(r, mode="on_demand", idle_timeout_min=15)
    st.set_desired(r, desired)
    st.touch_last_used(r, NOW - idle_for_min * MIN)


def tick(d, r, broker=None):
    ctl.reconcile_once(d, r, broker, now=NOW)


def test_idle_engine_is_stopped(r, broker):
    running_on_demand(r, 16)
    d = docker("running")
    tick(d, r, broker)
    assert st.get_desired(r) == "stopped"
    d.stop.assert_called_once()
    assert st.get_state(r) == "stopping"


def test_recent_conversion_keeps_engine(r, broker):
    running_on_demand(r, 5)
    d = docker("running")
    tick(d, r, broker)
    assert st.get_desired(r) == "running"
    d.stop.assert_not_called()
    assert st.get_state(r) == "running"


def test_inflight_job_blocks_unload(r, broker):
    running_on_demand(r, 16)
    st.mark_inflight(r, "task-1")
    d = docker("running")
    tick(d, r, broker)
    assert st.get_desired(r) == "running"
    d.stop.assert_not_called()


def test_gpu_queue_blocks_unload(r, broker):
    running_on_demand(r, 16)
    broker.lpush("gpu", "msg")
    d = docker("running")
    tick(d, r, broker)
    assert st.get_desired(r) == "running"
    d.stop.assert_not_called()


def test_broker_error_blocks_unload(r):
    running_on_demand(r, 16)
    bad = MagicMock()
    bad.llen.side_effect = ConnectionError("down")
    d = docker("running")
    tick(d, r, bad)
    d.stop.assert_not_called()


def test_custom_timeout_respected(r, broker):
    st.set_ocr_settings(r, mode="on_demand", idle_timeout_min=30)
    st.set_desired(r, "running")
    st.touch_last_used(r, NOW - 20 * MIN)
    d = docker("running")
    tick(d, r, broker)
    d.stop.assert_not_called()


def test_pinned_engine_never_auto_stopped(r, broker):
    running_on_demand(r, 600)
    st.set_pinned(r, True)
    d = docker("running")
    tick(d, r, broker)
    assert st.get_desired(r) == "running"
    d.stop.assert_not_called()


def test_manual_unload_stops_engine(r, broker):
    running_on_demand(r, 1, desired="stopped")
    d = docker("running")
    tick(d, r, broker)
    d.stop.assert_called_once()


def test_never_used_does_not_stop_and_starts_clock(r, broker):
    st.set_ocr_settings(r, mode="on_demand")
    st.set_desired(r, "running")
    d = docker("running")
    tick(d, r, broker)
    d.stop.assert_not_called()
    assert st.get_last_used(r) == NOW


def test_fresh_start_resets_idle_clock(r, broker):
    st.set_ocr_settings(r, mode="on_demand", idle_timeout_min=15)
    st.set_desired(r, "running")
    st.touch_last_used(r, NOW - 5 * MIN)
    d = docker("exited")
    tick(d, r, broker)
    d.start.assert_called_once()
    assert st.get_state(r) == "starting"
    assert st.get_last_used(r) == NOW


def test_starting_becomes_running(r, broker):
    st.set_ocr_settings(r, mode="on_demand")
    st.set_desired(r, "running")
    st.touch_last_used(r, NOW)
    st.set_state(r, "starting")
    tick(docker("running"), r, broker)
    assert st.get_state(r) == "running"


def test_stopping_becomes_stopped(r, broker):
    st.set_ocr_settings(r, mode="on_demand")
    st.set_state(r, "stopping")
    tick(docker("exited"), r, broker)
    assert st.get_state(r) == "stopped"


def test_boot_reconcile_stops_unwanted_container(r, broker):
    st.set_ocr_settings(r, mode="on_demand")  # desired defaults to stopped
    d = docker("running")
    tick(d, r, broker)
    d.stop.assert_called_once()


@pytest.mark.parametrize("ds,expect_stop", [("running", True), ("exited", False), (None, False)])
def test_cpu_only_enforces_stopped(r, broker, ds, expect_stop):
    st.set_ocr_settings(r, mode="cpu_only")
    st.set_desired(r, "running")
    st.set_pinned(r, True)
    d = docker(ds)
    tick(d, r, broker)
    assert st.get_desired(r) == "stopped"
    assert not st.is_pinned(r)
    d.start.assert_not_called()
    assert d.stop.called is expect_stop


def test_docker_state_error_sets_error_state(r, broker):
    d = MagicMock()
    d.state.side_effect = ctl.DockerError("boom")
    tick(d, r, broker)
    assert st.get_state(r) == "error"


def test_docker_start_error_sets_error_state(r, broker):
    st.set_ocr_settings(r, mode="on_demand")
    st.set_desired(r, "running")
    d = docker("exited")
    d.start.side_effect = ctl.DockerError("nope")
    tick(d, r, broker)
    assert st.get_state(r) == "error"


def test_env_defaults_used_when_no_settings(r, broker, monkeypatch):
    monkeypatch.setenv("OCR_MODE", "cpu_only")
    d = docker("running")
    tick(d, r, broker)
    d.stop.assert_called_once()
