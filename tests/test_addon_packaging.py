from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from auction_watch.addon_config import AddonOptions
from auction_watch.addon_runtime import apply_environment, load_options, migrate_data
from auction_watch.config import Settings
from auction_watch.core.models import SearchProfile
from auction_watch.main import create_app
from auction_watch.persistence.database import Database
from auction_watch.persistence.repository import ProfileRepository

ROOT = Path(__file__).resolve().parents[1]


def test_addon_options_accept_safe_configuration_and_reject_invalid_values() -> None:
    options = AddonOptions(timezone="America/Montevideo")
    assert options.smtp_enabled is False
    assert options.scheduler_enabled is False

    with pytest.raises(ValidationError):
        AddonOptions(timezone="not/a-timezone")
    with pytest.raises(ValidationError):
        AddonOptions(smtp_enabled=True, smtp_host="mail.example.test")
    with pytest.raises(ValidationError):
        AddonOptions(worker_poll_seconds=0)


def test_addon_runtime_enables_worker_only_in_addon_environment(tmp_path: Path) -> None:
    options_path = tmp_path / "options.json"
    options_path.write_text('{"timezone":"UTC"}', encoding="utf-8")
    options = load_options(options_path)
    environment: dict[str, str] = {"AW_SMTP_PASSWORD": "stale-value"}
    apply_environment(options, environment)

    assert Settings().worker_enabled is False
    assert environment["AW_WORKER_ENABLED"] == "true"
    assert environment["AW_DATA_DIR"] == "/data/auction-watch"
    assert environment["AW_SMTP_ENABLED"] == "false"
    assert "AW_SMTP_PASSWORD" not in environment


def test_addon_migration_is_idempotent_and_preserves_data(tmp_path: Path) -> None:
    migrate_data(tmp_path)
    first = Database.open(tmp_path)
    try:
        ProfileRepository(first).create(
            SearchProfile(
                id="libros", name="Libros", source_ids=["bavastro"], keywords_any=["libro"]
            )
        )
    finally:
        first.dispose()

    migrate_data(tmp_path)
    second = Database.open(tmp_path)
    try:
        loaded = ProfileRepository(second).get("libros")
        assert loaded is not None
        assert second.check_ready() is True
    finally:
        second.dispose()


def test_ingress_security_rejects_foreign_origin_without_open_cors(tmp_path: Path) -> None:
    application = create_app(Settings(data_dir=tmp_path))
    with TestClient(application) as client:
        rejected = client.get("/api/v1/health", headers={"Origin": "https://evil.test"})
        assert rejected.status_code == 403
        same_origin = client.get(
            "/api/v1/health", headers={"Origin": "http://testserver"}
        )
        assert same_origin.status_code == 200
        assert "access-control-allow-origin" not in {
            key.lower() for key in same_origin.headers
        }


def test_addon_artifact_inputs_are_explicitly_whitelisted() -> None:
    manifest = (ROOT / "config.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ingress: true" in manifest
    assert "map:\n  - data:rw" in manifest
    assert "smtp_enabled: false" in manifest
    assert "scheduler_enabled: false" in manifest
    assert "AW_WORKER_ENABLED=true" in dockerfile
    assert "AW_DATA_DIR=/data/auction-watch" in dockerfile
    assert "COPY rootfs /" in dockerfile
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env*" in dockerignore
    assert "tests" in dockerignore
    assert "*.sqlite3" in dockerignore

    package_script = (ROOT / "scripts/package_addon.sh").read_text(encoding="utf-8")
    for forbidden in (".env", "node_modules", "tests", "*.sqlite3"):
        assert forbidden not in package_script
    assert ".env.example" not in package_script


def test_packaged_artifact_audit_rejects_private_runtime_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo("data/auction-watch.sqlite3")
        member.size = 0
        archive.addfile(member)
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "audit_addon_artifact", ROOT / "scripts/audit_addon_artifact.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.audit(str(archive_path))
