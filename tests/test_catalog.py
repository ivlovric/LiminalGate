"""Setup-panel catalogs: playback files and LSL discovery."""

from pathlib import Path

from fastapi.testclient import TestClient
from liminalgate.app import create_app
from liminalgate.catalog import discover_lsl, playback_files
from liminalgate.settings import Settings


def test_playback_files_are_csv_only(tmp_path: Path):
    (tmp_path / "session.csv").write_text("timestamp\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("nope", encoding="utf-8")
    nested = tmp_path / "inner"
    nested.mkdir()
    (nested / "again.csv").write_text("timestamp\n", encoding="utf-8")
    found = playback_files(tmp_path)
    assert str(tmp_path / "session.csv") in found
    assert str(nested / "again.csv") in found
    assert all(item.endswith(".csv") for item in found)


def test_lsl_discovery_reports_availability():
    found = discover_lsl(0.2)
    assert isinstance(found["streams"], list)
    if found["available"]:
        assert found["error"] is None or isinstance(found["error"], str)
    else:
        assert "pylsl" in str(found["error"])


def test_setup_catalog_routes(tmp_path: Path):
    (tmp_path / "clip.csv").write_text("timestamp,TP9\n", encoding="utf-8")
    app = create_app(Settings(data_dir=tmp_path, input_type="synthetic", token=""))
    with TestClient(app) as client:
        files = client.get("/api/playback/files")
        assert files.status_code == 200
        assert any(item.endswith("clip.csv") for item in files.json()["files"])
        streams = client.get("/api/lsl/streams")
        assert streams.status_code == 200
        body = streams.json()
        assert "available" in body and isinstance(body["streams"], list)
