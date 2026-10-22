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
