"""Story 5.1c: unit tests for shared/key_manager.py and shared/secrets_manager.py.

KeyManager: per-job DEK generation/storage, retrieval round-trip (wrap/unwrap
via the master key), and deletion (revocation). secrets_manager: source
precedence (Docker secret file -> environment variable -> default), and the
required/missing behaviour. These are the exact modules Story 4.3 will modify.

Built on the 5.1a scaffolding; modules are loaded from source so the tests are
resilient to sys.modules mock pollution from other test files.
"""

import base64
import importlib.util
import os
import sys

import pytest
import fakeredis

from tests.unit.crypto_helpers import MASTER_KEY_B64, make_encryption_service

_SHARED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shared")


def _load_real(mod_name, filename):
    cached = sys.modules.get(mod_name)
    if cached is not None and type(cached).__name__ not in (
        "MagicMock", "Mock", "NonCallableMagicMock",
    ):
        return cached
    if "encryption" not in sys.modules or type(sys.modules["encryption"]).__name__ in (
        "MagicMock", "Mock", "NonCallableMagicMock",
    ):
        espec = importlib.util.spec_from_file_location(
            "encryption", os.path.join(_SHARED, "encryption.py"))
        emod = importlib.util.module_from_spec(espec)
        sys.modules["encryption"] = emod
        espec.loader.exec_module(emod)
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(_SHARED, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# key_manager.py — KeyManager lifecycle
# ---------------------------------------------------------------------------
class TestKeyManager:

    def _km(self):
        km_mod = _load_real("key_manager", "key_manager.py")
        r = fakeredis.FakeStrictRedis(decode_responses=True)
        svc = make_encryption_service()
        return km_mod.KeyManager(r, svc), r

    def test_generate_and_retrieve_roundtrip(self):
        km, r = self._km()
        dek = km.generate_job_key("job-1", metadata={"user": "alice"})
        assert isinstance(dek, str) and len(base64.urlsafe_b64decode(dek)) == 32
        # The stored value is wrapped; get_job_key unwraps back to the same DEK.
        assert km.get_job_key("job-1") == dek

    def test_stored_key_is_wrapped_not_plaintext(self):
        km, r = self._km()
        dek = km.generate_job_key("job-2")
        wrapped = r.get("job:job-2:dek")
        assert wrapped is not None and wrapped != dek

    def test_get_missing_key_returns_none(self):
        km, r = self._km()
        assert km.get_job_key("does-not-exist") is None

    def test_delete_revokes_key(self):
        km, r = self._km()
        km.generate_job_key("job-3")
        assert km.delete_job_key("job-3") is True
        assert km.get_job_key("job-3") is None
        # Deleting again returns False (nothing to delete).
        assert km.delete_job_key("job-3") is False

    def test_empty_job_id_rejected(self):
        km, r = self._km()
        with pytest.raises(ValueError):
            km.generate_job_key("")

    def test_metadata_stored(self):
        km, r = self._km()
        km.generate_job_key("job-4", metadata={"filename": "doc.pdf"})
        meta = r.hgetall("job:job-4:key_metadata")
        assert meta.get("filename") == "doc.pdf"
        assert meta.get("job_id") == "job-4"

    def test_factory_create_key_manager(self):
        km_mod = _load_real("key_manager", "key_manager.py")
        r = fakeredis.FakeStrictRedis(decode_responses=True)
        km = km_mod.create_key_manager(r, master_key=MASTER_KEY_B64)
        dek = km.generate_job_key("job-5")
        assert km.get_job_key("job-5") == dek


# ---------------------------------------------------------------------------
# secrets_manager.py — source precedence
# ---------------------------------------------------------------------------
class TestSecretsPrecedence:

    def _sm(self):
        return _load_real("secrets_manager", "secrets_manager.py")

    def test_env_var_used_when_no_docker_secret(self, monkeypatch):
        sm = self._sm()
        monkeypatch.setenv("MY_SECRET", "from-env")
        assert sm.load_secret("my_secret") == "from-env"

    def test_docker_secret_takes_precedence_over_env(self, monkeypatch, tmp_path):
        sm = self._sm()
        # Point the Docker secret path at a temp file via Path patching.
        secret_file = tmp_path / "my_secret"
        secret_file.write_text("from-docker\n")
        monkeypatch.setenv("MY_SECRET", "from-env")

        import pathlib
        real_path = pathlib.Path

        class _P(type(real_path())):
            pass

        def fake_path(arg, *a, **k):
            if str(arg) == "/run/secrets/my_secret":
                return secret_file
            return real_path(arg, *a, **k)

        monkeypatch.setattr(sm, "Path", fake_path)
        assert sm.load_secret("my_secret") == "from-docker"

    def test_default_used_when_nothing_set(self, monkeypatch):
        sm = self._sm()
        monkeypatch.delenv("MY_SECRET", raising=False)
        monkeypatch.setenv("FLASK_ENV", "testing")
        assert sm.load_secret("my_secret", default="fallback") == "fallback"

    def test_required_missing_raises(self, monkeypatch):
        sm = self._sm()
        monkeypatch.delenv("MISSING_SECRET", raising=False)
        with pytest.raises(ValueError):
            sm.load_secret("missing_secret", required=True)

    def test_insecure_default_rejected_in_production(self, monkeypatch):
        sm = self._sm()
        monkeypatch.delenv("MY_SECRET", raising=False)
        monkeypatch.setenv("FLASK_ENV", "production")
        with pytest.raises(ValueError):
            sm.load_secret("my_secret", default="change-me-in-production")
