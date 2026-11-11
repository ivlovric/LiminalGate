"""Session files and the HTTP API, driven by the synthetic input."""

import io
import time
import zipfile
from pathlib import Path

import pyedflib
import pytest
from fastapi.testclient import TestClient
from liminalgate.app import create_app
from liminalgate.errors import NotFoundError
from liminalgate.library import file_inside
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


def test_session_file_cannot_escape_the_session_directory(tmp_path: Path):
    folder = tmp_path / "sub-01" / "ses-1"
    folder.mkdir(parents=True)
    (folder / "keep.csv").write_text("timestamp\n", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    assert file_inside(folder, "keep.csv") == (folder / "keep.csv").resolve()
    with pytest.raises(NotFoundError):
        file_inside(folder, "../../secret.txt")
    with pytest.raises(NotFoundError):
        file_inside(folder, "missing.csv")


def test_recordings_can_be_listed_downloaded_and_deleted(tmp_path: Path):
    app = create_app(Settings(data_dir=tmp_path, input_type="synthetic", token="secret"))
    headers = {"Authorization": "Bearer secret"}
    with TestClient(app) as client:
        assert client.get("/api/sessions").status_code == 401
        started = client.post("/api/sessions", json={"subject": "01", "task": "walk", "notes": ""}, headers=headers)
        assert started.status_code == 200
        time.sleep(0.25)
        blocked = client.delete(
            f"/api/sessions/01/{started.json()['session_id']}",
            headers=headers,
        )
        assert blocked.status_code == 409
        stopped = client.post("/api/sessions/stop", headers=headers)
        assert stopped.status_code == 200
        listed = client.get("/api/sessions", headers=headers).json()
        row = listed[0]
        assert row["subject_id"] == "01"
        assert row["local_start"]
        assert row["duration_sec"] is not None
        assert "eeg" in row["streams"]
        assert any(item["name"].endswith("_eeg.csv") for item in row["inventory"])
        archive = client.get(f"/api/sessions/01/{row['session_id']}/archive", headers=headers)
        assert archive.status_code == 200
        assert archive.headers["content-type"].startswith("application/zip")
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            names = bundle.namelist()
        assert any(name.endswith("_eeg.csv") for name in names)
        assert any(name.endswith("_events.tsv") or name.endswith("session.json") for name in names)
        csv_name = next(item["name"] for item in row["inventory"] if item["name"].endswith("_eeg.csv"))
        one = client.get(f"/api/sessions/01/{row['session_id']}/files/{csv_name}", headers=headers)
        assert one.status_code == 200
        assert one.content.startswith(b"timestamp")
        missing = client.get(f"/api/sessions/01/{row['session_id']}/files/missing.csv", headers=headers)
        assert missing.status_code == 404
        deleted = client.delete(f"/api/sessions/01/{row['session_id']}", headers=headers)
        assert deleted.status_code == 200
        assert client.get(f"/api/sessions/01/{row['session_id']}/archive", headers=headers).status_code == 404

        uploaded = client.post(
            "/api/playback/upload?name=clip.csv",
            content=b"timestamp,TP9,AF7,AF8,TP10\n0,1,2,3,4\n0.004,5,6,7,8\n",
            headers={**headers, "Content-Type": "text/csv"},
        )
        assert uploaded.status_code == 200
        body = uploaded.json()
        assert body["eeg"] is True
        assert Path(body["path"]).is_file()
        assert "Short file" in body["warning"]
        switched = client.put(
            "/api/config",
            json={
                "input_type": "playback",
                "playback_path": body["path"],
                "playback_speed": 0,
                "playback_loop": False,
            },
            headers=headers,
        )
        assert switched.status_code == 200
        status = client.get("/api/status", headers=headers).json()
        assert status["playback"]["file"] == "clip.csv"
        paused = client.post("/api/playback/control", json={"action": "pause"}, headers=headers)
        assert paused.status_code == 200
        assert paused.json()["paused"] is True
