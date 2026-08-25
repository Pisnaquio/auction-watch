from pathlib import Path

from fastapi.testclient import TestClient

from auction_watch import __version__
from auction_watch.config import Settings, get_settings
from auction_watch.main import app

client = TestClient(app)


def test_package_is_importable() -> None:
    assert __version__ == "0.1.0"


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "auction-watch", "version": "0.1.0"}


def test_readiness_with_usable_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AW_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    with TestClient(app) as running_client:
        response = running_client.get("/api/v1/readiness")
        assert response.status_code == 200
        assert response.json()["ok"] is True


def test_readiness_with_unusable_directory(tmp_path: Path, monkeypatch) -> None:
    data_path = tmp_path / "not-a-directory"
    data_path.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("AW_DATA_DIR", str(data_path))
    get_settings.cache_clear()
    with TestClient(app) as running_client:
        response = running_client.get("/api/v1/readiness")
        assert response.status_code == 503
        assert response.json()["ok"] is False


def test_default_configuration(monkeypatch) -> None:
    for key in ("AW_DATA_DIR", "AW_HOST", "AW_PORT", "AW_LOG_LEVEL", "AW_ENVIRONMENT"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()
    assert settings.data_dir == Path("/data")
    assert settings.host == "0.0.0.0"
    assert settings.port == 8789
    assert settings.log_level == "INFO"
    assert settings.environment == "production"


def test_environment_configuration(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AW_PORT", "9876")
    monkeypatch.setenv("AW_ENVIRONMENT", "test")
    settings = Settings()
    assert settings.data_dir == tmp_path
    assert settings.port == 9876
    assert settings.environment == "test"
