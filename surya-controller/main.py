"""Surya VLM lifecycle controller.

Sole owner of starting/stopping the surya-vlm container. It talks to the Docker
Engine API only through docker-socket-proxy (never a raw socket) and finds its
target by compose label. The reconcile/idle policy lives in bead pfm.4; this
file provides the Docker client, the loop skeleton and the healthcheck.
"""
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVICE_LABEL_KEY = "com.docker.compose.service"
PROJECT_LABEL_KEY = "com.docker.compose.project"
TARGET_SERVICE = "surya-vlm"

HEARTBEAT_FILE = os.environ.get("CONTROLLER_HEARTBEAT_FILE", "/tmp/controller.heartbeat")
HEARTBEAT_MAX_AGE = 30.0

GPU_QUEUE = "gpu"

try:  # repo layout (tests, local) vs. flat copy inside the controller image
    from shared import surya_state
except ImportError:
    import surya_state

log = logging.getLogger("surya-controller")


class DockerError(Exception):
    pass


class DockerClient:
    """Minimal Docker Engine API client scoped to one compose service."""

    def __init__(self, base_url, service=TARGET_SERVICE, project=None, timeout=60):
        self.base_url = base_url.rstrip("/")
        self.service = service
        self.project = project
        self.timeout = timeout

    def _request(self, method, path, params=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            return e.code, None
        except (urllib.error.URLError, OSError) as e:
            raise DockerError(f"docker API unreachable: {e}") from e

    def find_container(self):
        """Return the target container summary dict, or None if absent.

        The proxy cannot scope to one container, so selection is done here:
        the API-side label filter is re-checked client-side, and ambiguity is
        an error rather than a guess.
        """
        wanted = [f"{SERVICE_LABEL_KEY}={self.service}"]
        if self.project:
            wanted.append(f"{PROJECT_LABEL_KEY}={self.project}")
        status, body = self._request(
            "GET", "/containers/json",
            {"all": "1", "filters": json.dumps({"label": wanted})},
        )
        if status != 200:
            raise DockerError(f"list containers failed: HTTP {status}")
        matches = [
            c for c in body or []
            if all((c.get("Labels") or {}).get(k) == v
                   for k, v in (w.split("=", 1) for w in wanted))
        ]
        if len(matches) > 1:
            raise DockerError(f"{len(matches)} containers match label {wanted}; refusing to act")
        return matches[0] if matches else None

    def state(self):
        """Docker state string ('running', 'exited', ...) or None if no container."""
        c = self.find_container()
        return c.get("State") if c else None

    def _action(self, verb, params=None):
        c = self.find_container()
        if c is None:
            raise DockerError(f"no container with label {SERVICE_LABEL_KEY}={self.service}")
        status, _ = self._request("POST", f"/containers/{c['Id']}/{verb}", params)
        if status not in (204, 304):  # 304: already in the requested state
            raise DockerError(f"{verb} failed: HTTP {status}")
        return status

    def start(self):
        return self._action("start")

    def stop(self, grace_seconds=30):
        return self._action("stop", {"t": str(grace_seconds)})


def touch_heartbeat(path=None):
    path = path or HEARTBEAT_FILE
    with open(path, "w") as f:
        f.write(repr(time.time()))


def heartbeat_ok(path=None, max_age=HEARTBEAT_MAX_AGE, now=None):
    path = path or HEARTBEAT_FILE
    try:
        with open(path) as f:
            return (time.time() if now is None else now) - float(f.read()) <= max_age
    except (OSError, ValueError):
        return False


def reconcile_once(docker, redis_client, broker_client=None, now=None):
    """One reconcile tick: converge the docker container to Redis desired state
    and apply the idle-unload policy. DockerError is recorded as state=error."""
    now = time.time() if now is None else now
    r = redis_client
    settings = surya_state.get_ocr_settings(r, _env_settings())
    try:
        ds = docker.state()
    except DockerError:
        log.exception("docker state lookup failed")
        surya_state.set_state(r, "error")
        return

    if settings["mode"] == "cpu_only":
        surya_state.set_desired(r, "stopped")
        surya_state.set_pinned(r, False)
    elif (surya_state.get_desired(r) == "running" and not surya_state.is_pinned(r)
          and _idle_expired(r, broker_client, settings["idle_timeout_min"], now)):
        log.info("surya idle for >%s min with no work; unloading", settings["idle_timeout_min"])
        surya_state.set_desired(r, "stopped")

    desired = surya_state.get_desired(r)
    try:
        if desired == "running" and ds != "running":
            docker.start()
            # Fresh start: reset the idle clock so it is not stopped on stale last_used.
            surya_state.touch_last_used(r, now)
            surya_state.set_state(r, "starting")
        elif desired == "stopped" and ds == "running":
            docker.stop()
            surya_state.set_state(r, "stopping")
        else:
            surya_state.set_state(r, "running" if ds == "running" else "stopped")
    except DockerError:
        log.exception("docker action failed")
        surya_state.set_state(r, "error")


def _env_settings():
    """Config defaults for get_ocr_settings (the controller has no config module)."""
    from types import SimpleNamespace
    try:
        idle = max(1, int(os.environ.get("SURYA_IDLE_TIMEOUT_MINUTES", "15")))
    except ValueError:
        idle = 15
    mode = os.environ.get("OCR_MODE", "on_demand")
    return SimpleNamespace(
        ocr_mode=mode if mode in surya_state.OCR_MODES else "on_demand",
        surya_idle_timeout_minutes=idle,
    )


def _idle_expired(r, broker_client, idle_min, now):
    """True when Surya has been unused past the timeout with no work pending."""
    last = surya_state.get_last_used(r)
    if last is None:
        # Never used: start the clock now rather than stopping immediately.
        surya_state.touch_last_used(r, now)
        return False
    if now - last <= idle_min * 60:
        return False
    if surya_state.inflight_count(r) > 0:
        return False
    if broker_client is not None:
        try:
            if broker_client.llen(GPU_QUEUE) > 0:
                return False
        except Exception:
            log.exception("broker LLEN failed; assuming queue is non-empty")
            return False
    return True


def run(docker, redis_client, interval=5.0, broker_client=None):
    while True:
        try:
            reconcile_once(docker, redis_client, broker_client)
            touch_heartbeat()
        except Exception:
            log.exception("reconcile failed")
        time.sleep(interval)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--healthcheck" in argv:
        return 0 if heartbeat_ok() else 1
    import redis

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    docker = DockerClient(
        os.environ.get("DOCKER_HOST_URL", "http://docker-socket-proxy:2375"),
        project=os.environ.get("COMPOSE_PROJECT_NAME") or None,
    )
    r = redis.Redis.from_url(os.environ["REDIS_METADATA_URL"], decode_responses=True)
    broker = None
    if os.environ.get("CELERY_BROKER_URL"):
        broker = redis.Redis.from_url(os.environ["CELERY_BROKER_URL"], decode_responses=True)
    touch_heartbeat()
    run(docker, r, float(os.environ.get("RECONCILE_INTERVAL_SECONDS", "5")), broker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
