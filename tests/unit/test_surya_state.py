import fnmatch
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../shared')))

import surya_state  # noqa: E402

pytestmark = pytest.mark.unit


class FakeRedis:
    """Just enough of redis-py (decode_responses=True) for surya_state."""

    def __init__(self):
        self.kv = {}
        self.hashes = {}
        self.ttls = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None):
        self.kv[key] = str(value)
        if ex is not None:
            self.ttls[key] = ex

    def delete(self, key):
        self.kv.pop(key, None)
        self.ttls.pop(key, None)

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def scan_iter(self, match=None):
        return iter([k for k in list(self.kv) if match is None or fnmatch.fnmatch(k, match)])


@pytest.fixture
def r():
    return FakeRedis()


APP = SimpleNamespace(ocr_mode="on_demand", surya_idle_timeout_minutes=15)


def test_ocr_settings_fall_back_to_config(r):
    assert surya_state.get_ocr_settings(r, APP) == {"mode": "on_demand", "idle_timeout_min": 15}


def test_ocr_settings_default_to_global_config(r):
    from config import settings
    assert surya_state.get_ocr_settings(r)["mode"] == settings.ocr_mode


def test_ocr_settings_round_trip_and_partial_update(r):
    surya_state.set_ocr_settings(r, mode="cpu_only", idle_timeout_min=5)
    assert surya_state.get_ocr_settings(r, APP) == {"mode": "cpu_only", "idle_timeout_min": 5}
    surya_state.set_ocr_settings(r, idle_timeout_min=30)
    assert surya_state.get_ocr_settings(r, APP) == {"mode": "cpu_only", "idle_timeout_min": 30}


def test_ocr_settings_corrupt_timeout_falls_back(r):
    r.hset(surya_state.SETTINGS_KEY, mapping={"idle_timeout_min": "abc"})
    assert surya_state.get_ocr_settings(r, APP)["idle_timeout_min"] == 15


@pytest.mark.parametrize("kwargs", [
    {"mode": "gpu_only"}, {"mode": ""},
    {"idle_timeout_min": 0}, {"idle_timeout_min": -3}, {"idle_timeout_min": "5"},
    {"idle_timeout_min": 1.5}, {"idle_timeout_min": True},
])
def test_ocr_settings_reject_invalid(r, kwargs):
    with pytest.raises(ValueError):
        surya_state.set_ocr_settings(r, **kwargs)
    assert r.hashes == {}


def test_set_ocr_settings_noop_writes_nothing(r):
    assert surya_state.set_ocr_settings(r) == {}
    assert r.hashes == {}


def test_desired_defaults_and_validation(r):
    assert surya_state.get_desired(r) == "stopped"
    surya_state.set_desired(r, "running")
    assert surya_state.get_desired(r) == "running"
    with pytest.raises(ValueError):
        surya_state.set_desired(r, "paused")
    assert surya_state.get_desired(r) == "running"


def test_pinned_toggle(r):
    assert surya_state.is_pinned(r) is False
    surya_state.set_pinned(r, True)
    assert surya_state.is_pinned(r) is True
    surya_state.set_pinned(r, False)
    assert surya_state.is_pinned(r) is False


@pytest.mark.parametrize("state", surya_state.STATES)
def test_state_accepts_valid(r, state):
    surya_state.set_state(r, state)
    assert surya_state.get_state(r) == state


def test_state_default_and_invalid(r):
    assert surya_state.get_state(r) == "stopped"
    with pytest.raises(ValueError):
        surya_state.set_state(r, "booting")
    assert surya_state.get_state(r) == "stopped"


def test_last_used_touch(r):
    assert surya_state.get_last_used(r) is None
    surya_state.touch_last_used(r, now=1234.5)
    assert surya_state.get_last_used(r) == 1234.5
    surya_state.touch_last_used(r)
    assert surya_state.get_last_used(r) > 1234.5


def test_last_used_corrupt_value(r):
    r.set(surya_state.LAST_USED_KEY, "nope")
    assert surya_state.get_last_used(r) is None


def test_inflight_tracking_with_ttl(r):
    assert surya_state.inflight_count(r) == 0
    surya_state.mark_inflight(r, "t1")
    surya_state.mark_inflight(r, "t2", ttl=60)
    r.set("surya:state", "running")  # unrelated key must not be counted
    assert surya_state.inflight_count(r) == 2
    assert r.ttls["surya:inflight:t1"] == surya_state.INFLIGHT_TTL_SECONDS
    assert r.ttls["surya:inflight:t2"] == 60
    surya_state.clear_inflight(r, "t1")
    assert surya_state.inflight_count(r) == 1


def _gpu(r, status, vram=None):
    r.set(surya_state.GPU_STATUS_KEY, status)
    if vram is not None:
        r.hset(surya_state.GPU_INFO_KEY, mapping={"status": status, "vram_total": vram})


@pytest.mark.parametrize("status,vram,expected", [
    ("available", 24.0, True),
    ("available", 16.0, True),
    ("available", 15.99, False),
    ("available", 8.0, False),
    ("unavailable", 24.0, False),
    ("available", None, False),
    ("available", "garbage", False),
])
def test_surya_supported(r, status, vram, expected):
    _gpu(r, status, vram)
    assert surya_state.surya_supported(r) is expected


def test_surya_supported_without_gpu_keys(r):
    assert surya_state.surya_supported(r) is False
