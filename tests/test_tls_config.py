import os
import shutil
import uuid
from pathlib import Path

from tls_config import sanitize_tls_ca_bundle_env


def _workspace_tmp():
    path = Path("tests") / ".tmp" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_sanitize_tls_ca_bundle_env_removes_invalid_postgresql_path(monkeypatch):
    bad_path = r"C:\Program Files\PostgreSQL\18\ssl\certs\ca-bundle.crt"
    monkeypatch.setenv("CURL_CA_BUNDLE", bad_path)

    removed = sanitize_tls_ca_bundle_env()

    assert removed == {"CURL_CA_BUNDLE": bad_path}
    assert "CURL_CA_BUNDLE" not in os.environ


def test_sanitize_tls_ca_bundle_env_preserves_existing_ca_file(monkeypatch):
    tmp_path = _workspace_tmp()
    try:
        ca_file = tmp_path / "ca-bundle.crt"
        ca_file.write_text("test ca", encoding="utf-8")
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_file))

        removed = sanitize_tls_ca_bundle_env()

        assert removed == {}
        assert Path(os.environ["REQUESTS_CA_BUNDLE"]) == ca_file
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
