"""DocuFlux-pfm.8: warmup probe reports 'idle' (no warning) when Surya is
intentionally stopped, and eager Marker warmup is skipped in that case."""
import importlib.util
import logging
import os
import sys
import types
from unittest.mock import MagicMock, patch

import fakeredis

_WORKER = os.path.join(os.path.dirname(__file__), "..", "..", "worker")
URL = {"SURYA_INFERENCE_URL": "http://surya-vlm:8000"}


def _load_warmup():
    if "_real_warmup_idle" in sys.modules:
        return sys.modules["_real_warmup_idle"]
    if "llama_cpp" not in sys.modules:
        stub = types.ModuleType("llama_cpp")
        stub.Llama = MagicMock()
        sys.modules["llama_cpp"] = stub
    spec = importlib.util.spec_from_file_location(
        "_real_warmup_idle", os.path.join(_WORKER, "warmup.py"))
    mod = importlib.util.module_from_spec(spec)
    saved = sys.modules.get("warmup")
    sys.modules["_real_warmup_idle"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        if saved is not None:
            sys.modules["warmup"] = saved
        else:
            sys.modules.pop("warmup", None)
    return mod


def _redis(mode="on_demand", state=None, desired=None):
    fr = fakeredis.FakeRedis(decode_responses=True)
    fr.hset("settings:ocr", mapping={"mode": mode})
    if state:
        fr.set("surya:state", state)
    if desired:
        fr.set("surya:desired", desired)
    return fr


def _probe(w, fr, get=None):
    get = get or MagicMock(return_value=MagicMock(status_code=200))
    with patch.object(w, "r", fr), patch.dict(os.environ, URL), \
            patch("requests.get", get):
        return w.check_inference_server(), get


class TestProbeIdle:
    def test_cpu_only_is_idle_without_probe_or_warning(self, caplog):
        w = _load_warmup()
        fr = _redis(mode="cpu_only", state="running", desired="running")
        get = MagicMock(side_effect=ConnectionError("refused"))
        with caplog.at_level(logging.WARNING):
            status, get = _probe(w, fr, get)
        assert status == "idle"
        get.assert_not_called()
        assert not [x for x in caplog.records
                    if x.levelno >= logging.WARNING and "probe" in x.getMessage()]
        assert fr.get(w.INFERENCE_SERVER_KEY) == "idle"

    def test_stopped_and_desired_stopped_is_idle(self):
        w = _load_warmup()
        status, get = _probe(w, _redis(state="stopped", desired="stopped"))
        assert status == "idle"
        get.assert_not_called()

    def test_stopped_but_desired_running_still_probes_and_warns(self, caplog):
        w = _load_warmup()
        fr = _redis(state="stopped", desired="running")
        get = MagicMock(side_effect=ConnectionError("refused"))
        with caplog.at_level(logging.WARNING):
            status, _ = _probe(w, fr, get)
        assert status == "unreachable"
        assert [x for x in caplog.records
                if x.levelno == logging.WARNING and "probe" in x.getMessage()]

    def test_running_on_demand_is_reachable(self):
        w = _load_warmup()
        status, _ = _probe(w, _redis(state="running", desired="running"))
        assert status == "reachable"

    def test_redis_failure_falls_back_to_normal_probe(self):
        w = _load_warmup()
        bad = MagicMock()
        bad.hgetall.side_effect = ConnectionError("redis down")
        bad.get.side_effect = ConnectionError("redis down")
        status, get = _probe(w, bad)
        assert status == "reachable"
        get.assert_called_once_with("http://surya-vlm:8000/health", timeout=2)


class TestEagerWarmupGuard:
    def _run(self, fr):
        import tasks
        with patch.object(tasks.app_settings, "eager_marker_warmup", True), \
                patch.object(tasks.conversion, "get_model_dict") as get_md, \
                patch.object(tasks, "redis_client", fr):
            tasks._eager_marker_warmup()
        return get_md

    def test_skipped_in_cpu_only(self):
        assert not self._run(_redis(mode="cpu_only")).called

    def test_skipped_when_stopped(self):
        assert not self._run(_redis(state="stopped", desired="stopped")).called

    def test_runs_when_running(self):
        self._run(_redis(state="running", desired="running")).assert_called_once()

    def test_redis_failure_keeps_previous_behavior(self):
        bad = MagicMock()
        bad.hgetall.side_effect = ConnectionError("redis down")
        bad.get.side_effect = ConnectionError("redis down")
        self._run(bad).assert_called_once()
