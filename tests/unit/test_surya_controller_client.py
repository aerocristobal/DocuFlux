import importlib.util
import json
import os
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_path = os.path.join(os.path.dirname(__file__), "../../surya-controller/main.py")
_spec = importlib.util.spec_from_file_location("surya_controller_main", _path)
ctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctl)

SVC = {"com.docker.compose.service": "surya-vlm"}


def container(cid, labels=None, state="running"):
    return {"Id": cid, "State": state, "Labels": labels if labels is not None else SVC}


def resp(status=200, body=None):
    r = MagicMock()
    r.status = status
    r.read.return_value = json.dumps(body).encode() if body is not None else b""
    r.__enter__.return_value = r
    return r


def client(**kw):
    return ctl.DockerClient("http://proxy:2375/", **kw)


def http_error(req, code):
    return urllib.error.HTTPError(req.full_url, code, "x", {}, None)


def test_find_container_sends_label_filter_and_returns_match():
    with patch.object(ctl.urllib.request, "urlopen", return_value=resp(200, [container("abc")])) as op:
        c = client().find_container()
    assert c["Id"] == "abc"
    req = op.call_args[0][0]
    assert req.get_method() == "GET"
    assert req.full_url.startswith("http://proxy:2375/containers/json?")
    assert "com.docker.compose.service%3Dsurya-vlm" in req.full_url
    assert "all=1" in req.full_url


def test_find_container_filters_out_non_matching_labels():
    other = container("x", {"com.docker.compose.service": "worker"})
    with patch.object(ctl.urllib.request, "urlopen", return_value=resp(200, [other])):
        assert client().find_container() is None


def test_find_container_project_scoping():
    mine = container("a", {**SVC, "com.docker.compose.project": "docuflux"})
    theirs = container("b", {**SVC, "com.docker.compose.project": "other"})
    with patch.object(ctl.urllib.request, "urlopen", return_value=resp(200, [mine, theirs])):
        assert client(project="docuflux").find_container()["Id"] == "a"


def test_find_container_ambiguous_raises():
    with patch.object(ctl.urllib.request, "urlopen",
                      return_value=resp(200, [container("a"), container("b")])):
        with pytest.raises(ctl.DockerError, match="refusing"):
            client().find_container()


def test_start_posts_to_matched_container():
    calls = []

    def fake(req, timeout=None):
        calls.append((req.get_method(), req.full_url))
        return resp(200, [container("abc", state="exited")]) if req.get_method() == "GET" else resp(204)

    with patch.object(ctl.urllib.request, "urlopen", side_effect=fake):
        assert client().start() == 204
    assert calls[-1] == ("POST", "http://proxy:2375/containers/abc/start")


def test_stop_passes_grace_period_and_treats_304_as_ok():
    calls = []

    def fake(req, timeout=None):
        calls.append(req.full_url)
        if req.get_method() == "GET":
            return resp(200, [container("abc")])
        raise http_error(req, 304)

    with patch.object(ctl.urllib.request, "urlopen", side_effect=fake):
        assert client().stop(grace_seconds=10) == 304
    assert calls[-1] == "http://proxy:2375/containers/abc/stop?t=10"


def test_stop_failure_raises():
    def fake(req, timeout=None):
        if req.get_method() == "GET":
            return resp(200, [container("abc")])
        raise http_error(req, 403)

    with patch.object(ctl.urllib.request, "urlopen", side_effect=fake):
        with pytest.raises(ctl.DockerError, match="HTTP 403"):
            client().stop()


def test_start_without_container_raises():
    with patch.object(ctl.urllib.request, "urlopen", return_value=resp(200, [])):
        with pytest.raises(ctl.DockerError, match="no container"):
            client().start()


def test_unreachable_proxy_raises_docker_error():
    with patch.object(ctl.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")):
        with pytest.raises(ctl.DockerError, match="unreachable"):
            client().state()


def test_state_returns_docker_state_or_none():
    with patch.object(ctl.urllib.request, "urlopen", return_value=resp(200, [container("a", state="exited")])):
        assert client().state() == "exited"
    with patch.object(ctl.urllib.request, "urlopen", return_value=resp(200, [])):
        assert client().state() is None


def test_heartbeat_fresh_stale_and_missing(tmp_path):
    hb = str(tmp_path / "hb")
    assert ctl.heartbeat_ok(hb) is False
    ctl.touch_heartbeat(hb)
    assert ctl.heartbeat_ok(hb) is True
    assert ctl.heartbeat_ok(hb, max_age=30, now=time.time() + 100) is False


def test_healthcheck_flag_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "HEARTBEAT_FILE", str(tmp_path / "hb"))
    assert ctl.main(["--healthcheck"]) == 1
    ctl.touch_heartbeat()
    assert ctl.main(["--healthcheck"]) == 0
