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


def reconcile_once(docker, redis_client):
    """Hook for bead pfm.4: read desired/pinned/mode + idle state from Redis
    (surya_state), compare with docker.state(), then docker.start()/stop() and
    surya_state.set_state(). Intentionally a no-op in this scaffold."""


def run(docker, redis_client, interval=5.0):
    while True:
        try:
            reconcile_once(docker, redis_client)
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
    touch_heartbeat()
    run(docker, r, float(os.environ.get("RECONCILE_INTERVAL_SECONDS", "5")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
