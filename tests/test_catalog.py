"""Setup-panel catalogs: playback files, LSL discovery, and BrainFlow BLE."""

from pathlib import Path

from fastapi.testclient import TestClient
from liminalgate.app import create_app
from liminalgate.brainflow_scan import (
    BLUETOOTH_HINT,
    BluetoothUnreachable,
    classify_headset,
    list_boards,
    scan_headsets,
)
from liminalgate.catalog import discover_lsl, playback_files
from liminalgate.settings import Settings


def test_playback_files_are_csv_only(tmp_path: Path):
    (tmp_path / "session.csv").write_text("timestamp\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("nope", encoding="utf-8")
    nested = tmp_path / "inner"
    nested.mkdir()
    (nested / "again.csv").write_text("timestamp\n", encoding="utf-8")
    found = playback_files(tmp_path)
    paths = [str(item["path"]) for item in found]
    assert str(tmp_path / "session.csv") in paths
    assert str(nested / "again.csv") in paths
    assert all(str(item["path"]).endswith(".csv") for item in found)
    assert all(item["eeg"] is False for item in found)


def test_lsl_discovery_reports_availability():
    found = discover_lsl(0.2)
    assert isinstance(found["streams"], list)
    if found["available"]:
        assert found["error"] is None or isinstance(found["error"], str)
    else:
        assert "pylsl" in str(found["error"])


def test_muse_names_map_to_brainflow_ids():
    athena = classify_headset("MuseS-0B12")
    assert athena is not None
    assert athena["board"] == "muse_s_athena"
    assert athena["board_id"] == 67
    assert athena["board_label"] == "MUSE_S_ATHENA_BOARD"
    assert athena["serial"] == "MuseS-0B12"
    named = classify_headset("Muse S Athena")
    assert named is not None and named["board_id"] == 67
    muse2 = classify_headset("Muse-86B5")
    assert muse2 is not None and muse2["board"] == "muse_2" and muse2["board_id"] == 38
    assert classify_headset("LE_WH-1000XM4") is None
    boards = list_boards()
    athena_row = next(row for row in boards if row["value"] == "muse_s_athena")
    assert athena_row["board_id"] == 67
    assert boards[0]["value"] == "synthetic"


def test_scan_keeps_headsets_and_reports_a_missing_adapter():
    found = scan_headsets(
        0.1,
        scanner=lambda _timeout: [
            {"name": "LE_WH-1000XM4", "mac": "c8-cd-41-44-81-5c", "rssi": -40},
            {"name": "MuseS-0B12", "mac": "00:55:da:ba:0b:12", "rssi": -62},
            {"name": "Muse-86B5", "mac": "00:55:DA:B7:86:B5", "rssi": -70},
        ],
    )
    assert found["available"] is True
    assert found["error"] is None
    devices = found["devices"]
    assert isinstance(devices, list)
    assert [item["name"] for item in devices] == ["MuseS-0B12", "Muse-86B5"]
    assert devices[0]["mac"] == "00:55:DA:BA:0B:12"
    assert devices[0]["board_id"] == 67
    assert devices[0]["serial"] == "MuseS-0B12"

    def down(_timeout: float) -> list[dict]:
        raise OSError("org.bluez not available")

    blocked = scan_headsets(0.1, scanner=down)
    assert blocked["available"] is False
    assert blocked["devices"] == []
    assert BLUETOOTH_HINT in str(blocked["error"])
    assert "compose.host.yaml" in str(blocked["error"])

    def unreachable(_timeout: float) -> list[dict]:
        raise BluetoothUnreachable("no adapter")

    raised = scan_headsets(0.1, scanner=unreachable)
    assert "compose.host.yaml" in str(raised["error"])


def test_setup_catalog_routes(tmp_path: Path, monkeypatch):
    (tmp_path / "clip.csv").write_text("timestamp,TP9\n", encoding="utf-8")
    app = create_app(Settings(data_dir=tmp_path, input_type="synthetic", token=""))
    with TestClient(app) as client:
        files = client.get("/api/playback/files")
        assert files.status_code == 200
        assert any(str(item["path"]).endswith("clip.csv") and item["eeg"] is False for item in files.json()["files"])
        streams = client.get("/api/lsl/streams")
        assert streams.status_code == 200
        body = streams.json()
        assert "available" in body and isinstance(body["streams"], list)
        boards = client.get("/api/brainflow/boards")
        assert boards.status_code == 200
        assert any(row["value"] == "muse_s_athena" and row["board_id"] == 67 for row in boards.json()["boards"])
        monkeypatch.setattr(
            "liminalgate.api.scan_headsets",
            lambda timeout=5.0: {"available": False, "devices": [], "error": BLUETOOTH_HINT},
        )
        devices = client.get("/api/brainflow/devices")
        assert devices.status_code == 200
        assert devices.json()["available"] is False
        assert "compose.host.yaml" in devices.json()["error"]
