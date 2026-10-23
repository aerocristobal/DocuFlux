"""DocuFlux-056: a non-GPU service's warmup probe must not overwrite the GPU
worker's 'available' result in the shared marker:gpu_* keys."""
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import fakeredis

_WORKER = os.path.join(os.path.dirname(__file__), "..", "..", "worker")
AVAILABLE = {"status": "available", "model": "RTX 3090", "vram_total": 25.32}
UNAVAILABLE = {"status": "unavailable", "error": "No module named 'torch'"}


def _load_warmup():
    if "_real_warmup_gpu" in sys.modules:
        return sys.modules["_real_warmup_gpu"]
    if "llama_cpp" not in sys.modules:
        stub = types.ModuleType("llama_cpp")
        stub.Llama = MagicMock()
        sys.modules["llama_cpp"] = stub
    spec = importlib.util.spec_from_file_location(
        "_real_warmup_gpu", os.path.join(_WORKER, "warmup.py"))
    mod = importlib.util.module_from_spec(spec)
    saved = sys.modules.get("warmup")
    sys.modules["_real_warmup_gpu"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        if saved is not None:
            sys.modules["warmup"] = saved
        else:
            sys.modules.pop("warmup", None)
    return mod


def _publish(fr, info):
    mod = _load_warmup()
    with patch.object(mod, "r", fr):
        mod._publish_gpu_info(info)


def test_unavailable_does_not_overwrite_available():
    fr = fakeredis.FakeRedis(decode_responses=True)
    _publish(fr, AVAILABLE)
    _publish(fr, UNAVAILABLE)
    assert fr.get("marker:gpu_status") == "available"
    assert fr.hget("marker:gpu_info", "vram_total") == "25.32"
    assert fr.hget("marker:gpu_info", "error") is None


def test_available_overwrites_earlier_unavailable():
    fr = fakeredis.FakeRedis(decode_responses=True)
    _publish(fr, UNAVAILABLE)
    _publish(fr, AVAILABLE)
    assert fr.get("marker:gpu_status") == "available"
    assert fr.hget("marker:gpu_info", "error") is None


def test_unavailable_recorded_when_nothing_published():
    fr = fakeredis.FakeRedis(decode_responses=True)
    _publish(fr, UNAVAILABLE)
    assert fr.get("marker:gpu_status") == "unavailable"
    assert fr.hget("marker:gpu_info", "error") == "No module named 'torch'"


def test_available_keys_carry_ttl():
    fr = fakeredis.FakeRedis(decode_responses=True)
    _publish(fr, AVAILABLE)
    mod = _load_warmup()
    assert 0 < fr.ttl("marker:gpu_status") <= mod.GPU_STATUS_TTL_SECONDS
    assert 0 < fr.ttl("marker:gpu_info") <= mod.GPU_STATUS_TTL_SECONDS


def test_refresh_extends_ttl_only_after_publishing_available():
    mod = _load_warmup()
    fr = fakeredis.FakeRedis(decode_responses=True)
    fr.set("marker:gpu_status", "available", ex=5)
    fr.hset("marker:gpu_info", mapping={"status": "available"})
    fr.expire("marker:gpu_info", 5)
    with patch.object(mod, "r", fr), patch.object(mod, "_gpu_published", False):
        mod.refresh_gpu_status()
    assert fr.ttl("marker:gpu_status") <= 5
    with patch.object(mod, "r", fr), patch.object(mod, "_gpu_published", True):
        mod.refresh_gpu_status()
    assert fr.ttl("marker:gpu_status") > 5
    assert fr.ttl("marker:gpu_info") > 5


def test_status_expires_when_worker_stops_refreshing():
    fr = fakeredis.FakeRedis(decode_responses=True)
    _publish(fr, AVAILABLE)
    fr.delete("marker:gpu_status")  # what TTL expiry does
    _publish(fr, UNAVAILABLE)
    assert fr.get("marker:gpu_status") == "unavailable"


def test_refresh_swallows_redis_errors():
    mod = _load_warmup()
    broken = MagicMock()
    broken.pipeline.side_effect = RuntimeError("redis down")
    with patch.object(mod, "r", broken), patch.object(mod, "_gpu_published", True):
        mod.refresh_gpu_status()
