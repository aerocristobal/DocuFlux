"""Redis-backed state for the on-demand Surya VLM lifecycle.

Every function takes a redis client (DB 1, decode_responses=True) built by the
caller via shared.redis_client.create_redis_client; nothing here touches Redis
at import time. Keys are plain strings so the web tier, worker and the
lifecycle controller can all share them.
"""
import time

SETTINGS_KEY = "settings:ocr"            # hash: mode, idle_timeout_min
DESIRED_KEY = "surya:desired"            # running | stopped
PINNED_KEY = "surya:pinned"              # "1" while a manual load pins the instance
STATE_KEY = "surya:state"                # observed lifecycle state
LAST_USED_KEY = "surya:last_used"        # epoch seconds of the last Marker job activity
INFLIGHT_PREFIX = "surya:inflight:"      # one key per running task: surya:inflight:<task_id>

GPU_STATUS_KEY = "marker:gpu_status"     # written by worker/warmup.py
GPU_INFO_KEY = "marker:gpu_info"

OCR_MODES = ("cpu_only", "on_demand")
DESIRED_VALUES = ("running", "stopped")
STATES = ("stopped", "starting", "running", "stopping", "error")

# Per-task inflight keys expire on their own so a crashed worker cannot pin
# Surya loaded forever; live tasks refresh them via mark_inflight().
INFLIGHT_TTL_SECONDS = 3600

# Minimum total GPU memory (GB, as marker:gpu_info reports it) for Surya VLM.
MIN_VRAM_GB = 16


def _check(value, allowed, name):
    if value not in allowed:
        raise ValueError(f"invalid {name} {value!r}; expected one of {', '.join(allowed)}")
    return value


def get_ocr_settings(r, app_settings=None):
    """Return {'mode', 'idle_timeout_min'}, falling back to config defaults."""
    if app_settings is None:
        from config import settings as app_settings
    stored = r.hgetall(SETTINGS_KEY) or {}
    mode = stored.get("mode") or app_settings.ocr_mode
    try:
        idle = int(stored.get("idle_timeout_min"))
    except (TypeError, ValueError):
        idle = app_settings.surya_idle_timeout_minutes
    return {"mode": mode, "idle_timeout_min": idle}


def set_ocr_settings(r, mode=None, idle_timeout_min=None):
    """Validate and persist the given fields; omitted ones are left unchanged."""
    mapping = {}
    if mode is not None:
        mapping["mode"] = _check(mode, OCR_MODES, "mode")
    if idle_timeout_min is not None:
        if isinstance(idle_timeout_min, bool) or not isinstance(idle_timeout_min, int) or idle_timeout_min < 1:
            raise ValueError(f"invalid idle_timeout_min {idle_timeout_min!r}; expected an integer >= 1")
        mapping["idle_timeout_min"] = idle_timeout_min
    if mapping:
        r.hset(SETTINGS_KEY, mapping=mapping)
    return mapping


def get_desired(r):
    return r.get(DESIRED_KEY) or "stopped"


def set_desired(r, value):
    r.set(DESIRED_KEY, _check(value, DESIRED_VALUES, "desired state"))


def is_pinned(r):
    return bool(r.get(PINNED_KEY))


def set_pinned(r, pinned):
    if pinned:
        r.set(PINNED_KEY, "1")
    else:
        r.delete(PINNED_KEY)


def get_state(r):
    return r.get(STATE_KEY) or "stopped"


def set_state(r, value):
    r.set(STATE_KEY, _check(value, STATES, "state"))


def touch_last_used(r, now=None):
    r.set(LAST_USED_KEY, repr(time.time() if now is None else float(now)))


def get_last_used(r):
    """Epoch seconds of the last activity, or None if Surya was never used."""
    raw = r.get(LAST_USED_KEY)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def mark_inflight(r, task_id, ttl=INFLIGHT_TTL_SECONDS):
    r.set(f"{INFLIGHT_PREFIX}{task_id}", "1", ex=ttl)


def clear_inflight(r, task_id):
    r.delete(f"{INFLIGHT_PREFIX}{task_id}")


def inflight_count(r):
    return sum(1 for _ in r.scan_iter(match=f"{INFLIGHT_PREFIX}*"))


def surya_supported(r):
    """True when a GPU is present with at least MIN_VRAM_GB of total memory."""
    if r.get(GPU_STATUS_KEY) != "available":
        return False
    try:
        return float(r.hget(GPU_INFO_KEY, "vram_total")) >= MIN_VRAM_GB
    except (TypeError, ValueError):
        return False
