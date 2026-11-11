"""Session files and the HTTP API, driven by the synthetic input."""

import time
from pathlib import Path

import pyedflib
import pytest
from fastapi.testclient import TestClient
from liminalgate.app import create_app
from liminalgate.recording import SessionRecorder
from liminalgate.settings import Settings
from liminalgate.signals import Event, Sample


def test_csv_and_edf_roundtrip(tmp_path: Path):
    recorder = SessionRecorder(
        tmp_path,
        subject="01",
        task="rest",
        notes="unit",
        metadata={"eeg_fs": 256, "powerline_hz": 50, "device": {"input": "test", "eeg_scale": 1.0}},
    )
    recorder.start()
    channels = ("TP9", "AF7", "AF8", "TP10")
    t0 = 1_700_000_000.0
    for index in range(300):
        recorder.add_sample(
            Sample(
                t0 + index / 256.0,
                "eeg",
                channels,
                (float(index), 1.0, 2.0, 3.0),
                256,
            )
        )
    recorder.add_event(Event(t0 + 0.5, "marker", "threshold", source="test", color="#d4a0ff"))
    summary = recorder.finish()
    csv_path = Path(summary["files"]["csv"])
    text = csv_path.read_text(encoding="utf-8").splitlines()
    assert text[0].startswith("timestamp,TP9")
    assert len(text) == 301
    events = Path(summary["files"]["events"]).read_text(encoding="utf-8")
    assert "threshold" in events
    reader = pyedflib.EdfReader(summary["files"]["edf"])
    try:
        assert reader.signals_in_file == 4
        signal = reader.readSignal(0)
        assert abs(signal[0]) < 1.0
        assert abs(signal[10] - 10) < 1.0
        onsets, _durations, descriptions = reader.readAnnotations()
        assert any("threshold" in str(item) for item in descriptions)
        assert onsets[0] == pytest.approx(0.5, abs=0.05)
    finally:
        reader.close()
    session = Path(summary["files"]["session"]).read_text(encoding="utf-8")
    assert "LiminalGate" in session
    assert (tmp_path / "dataset_description.json").is_file()


def test_api_session_profile_marker_and_openapi(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        input_type="synthetic",
        profile="hypnagogic",
        token="",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        schema = client.get("/openapi.json")
        assert schema.status_code == 200
        body = schema.json()
        assert body["info"]["title"] == "LiminalGate"
        assert "/api/sessions" in body["paths"]
        assert "/api/profiles/{name}" in body["paths"]

        deadline = time.time() + 5
        seen = 0
        while time.time() < deadline:
            status = client.get("/api/status").json()
            seen = status["input"]["samples_seen"]
            if seen > 100 and status["feedback"] and status["feedback"]["volume"] > 0.05:
                break
            time.sleep(0.05)
        assert seen > 100
        assert status["profile"] == "hypnagogic"
        assert status["feedback"]["volume"] > 0.05

        profiles = client.get("/api/profiles").json()
        assert {row["name"] for row in profiles} >= {"classic", "hypnagogic", "alpha_relaxation"}
        custom = client.get("/api/profiles/hypnagogic").json()
        custom["bands"].append({"name": "gamma40", "f_low": 40, "f_high": 45, "color": "#e07a8a"})
        saved = client.put("/api/profiles/hypnagogic", json=custom)
        assert saved.status_code == 200
        assert any(band["name"] == "gamma40" for band in saved.json()["bands"])

        started = client.post("/api/sessions", json={"subject": "01", "task": "rest", "notes": "api"})
        assert started.status_code == 200
        time.sleep(0.3)
        marked = client.post("/api/markers", json={"name": "threshold"})
        assert marked.status_code == 200
        time.sleep(0.2)
        stopped = client.post("/api/sessions/stop")
        assert stopped.status_code == 200
        summary = stopped.json()
        assert summary["eeg_samples"] > 10
        assert Path(summary["files"]["csv"]).is_file()
        assert Path(summary["files"]["edf"]).is_file()
        assert "threshold" in Path(summary["files"]["events"]).read_text(encoding="utf-8")
        listed = client.get("/api/sessions").json()
        assert listed and listed[0]["session_id"] == summary["session_id"]


def test_token_protects_api_but_not_health(tmp_path: Path):
    app = create_app(Settings(data_dir=tmp_path, input_type="synthetic", token="secret"))
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/status").status_code == 401
        ok = client.get("/api/status", headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 200


def test_websocket_marker(tmp_path: Path):
    app = create_app(Settings(data_dir=tmp_path, input_type="synthetic", token=""))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as socket:
            hello = socket.receive_json()
            assert hello["type"] == "hello"
            assert hello["profile"]["name"] == "hypnagogic"
            socket.send_json({"type": "marker", "name": "threshold"})
            deadline = time.time() + 3
            found = None
            while time.time() < deadline:
                message = socket.receive_json()
                if message.get("type") == "event" and message.get("name") == "threshold":
                    found = message
                    break
            assert found is not None
            assert found["kind"] == "marker"
