"""BIDS-inspired session folders: CSV always, EDF+ for EEG, events as annotations.

XDF is intentionally not written in v0.1.

TODO: XDF export (pyxdf writer) once a second container format is worth the dependency.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from liminalgate import __version__
from liminalgate.signals import Event, Sample

_LABEL = re.compile(r"[^A-Za-z0-9]+")


def safe_label(value: str, fallback: str) -> str:
    cleaned = _LABEL.sub("", value or "")
    return cleaned[:32] or fallback


def _edf_label(name: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_+-]", "", name)[:16] or "CH"
    label = base
    index = 2
    while label in used:
        suffix = str(index)
        label = f"{base[: 16 - len(suffix)]}{suffix}"
        index += 1
    used.add(label)
    return label


class SessionRecorder:
    def __init__(
        self,
        root: Path,
        subject: str,
        task: str,
        notes: str,
        metadata: dict,
    ) -> None:
        self.root = root
        self.subject = safe_label(subject, "01")
        self.task = safe_label(task, "rest")
        self.notes = notes.strip()
        self.metadata = metadata
        self.session_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        self.prefix = f"sub-{self.subject}_ses-{self.session_id}_task-{self.task}"
        self.session_dir = root / f"sub-{self.subject}" / f"ses-{self.session_id}"
        self.eeg_dir = self.session_dir / "eeg"
        self.motion_dir = self.session_dir / "motion"
        self.physio_dir = self.session_dir / "physio"
        self.started_at = datetime.now(UTC)
        self.t0: float | None = None
        self.active = False
        self.eeg_channels: tuple[str, ...] = ()
        self.eeg_count = 0
        self._eeg_chunks: list[np.ndarray] = []
        self._files: dict[str, object] = {}
        self._writers: dict[str, csv.writer] = {}
        self._stream_channels: dict[str, tuple[str, ...]] = {}
        self.paths: dict[str, str] = {}

    def start(self) -> dict:
        self.eeg_dir.mkdir(parents=True, exist_ok=True)
        self._write_dataset_description()
        self._write_session(stopped=False)
        self.active = True
        return self.summary(stopped=False)

    def add_sample(self, sample: Sample) -> None:
        if not self.active:
            return
        if self.t0 is None:
            self.t0 = sample.t
        if sample.stream == "eeg":
            self._add_eeg(sample)
        elif sample.stream in {"acc", "gyro", "ppg", "optics", "battery", "hr"}:
            self._add_table(sample)

    def add_event(self, event: Event) -> None:
        if not self.active:
            return
        if self.t0 is None:
            self.t0 = event.t
        onset = max(0.0, event.t - self.t0)
        sample_index = self.eeg_count
        self._event_writer().writerow(
            [
                f"{onset:.6f}",
                "0",
                event.kind,
                event.name,
                event.source,
                str(sample_index),
            ]
        )
        handle = self._files.get("events")
        if handle is not None:
            handle.flush()  # type: ignore[attr-defined]

    def finish(self) -> dict:
        self.active = False
        self._close_tables()
        edf_path = self._write_edf()
        if edf_path is not None:
            self.paths["edf"] = str(edf_path)
        self._write_session(stopped=True)
        return self.summary(stopped=True)

    def summary(self, stopped: bool) -> dict:
        return {
            "subject": self.subject,
            "session_id": self.session_id,
            "task": self.task,
            "path": str(self.session_dir),
            "eeg_samples": self.eeg_count,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "stopped": stopped,
            "files": dict(self.paths),
        }

    def _add_eeg(self, sample: Sample) -> None:
        if not self.eeg_channels:
            self.eeg_channels = sample.channels
            self._open_eeg_csv()
            self._write_channels()
            self._write_eeg_json()
        if len(sample.values) != len(self.eeg_channels):
            return
        writer = self._writers["eeg"]
        writer.writerow([f"{sample.t:.6f}", *[f"{value:.6f}" for value in sample.values]])
        self.eeg_count += 1
        self._eeg_chunks.append(np.asarray(sample.values, dtype=np.float64))
        if self.eeg_count % 256 == 0:
            handle = self._files.get("eeg")
            if handle is not None:
                handle.flush()  # type: ignore[attr-defined]

    def _open_eeg_csv(self) -> None:
        path = self.eeg_dir / f"{self.prefix}_eeg.csv"
        handle = path.open("w", newline="", encoding="utf-8")
        writer = csv.writer(handle)
        writer.writerow(["timestamp", *self.eeg_channels])
        self._files["eeg"] = handle
        self._writers["eeg"] = writer
        self.paths["csv"] = str(path)

    def _add_table(self, sample: Sample) -> None:
        stream = sample.stream
        if stream not in self._writers:
            folder = self.physio_dir if stream in {"ppg", "optics", "battery", "hr"} else self.motion_dir
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{self.prefix}_{stream}.csv"
            handle = path.open("w", newline="", encoding="utf-8")
            writer = csv.writer(handle)
            writer.writerow(["timestamp", *sample.channels])
            self._files[stream] = handle
            self._writers[stream] = writer
            self._stream_channels[stream] = sample.channels
            self.paths[stream] = str(path)
        if sample.channels != self._stream_channels.get(stream):
            return
        self._writers[stream].writerow([f"{sample.t:.6f}", *[f"{value:.6f}" for value in sample.values]])

    def _event_writer(self) -> csv.writer:
        if "events" in self._writers:
            return self._writers["events"]
        path = self.eeg_dir / f"{self.prefix}_events.tsv"
        handle = path.open("w", newline="", encoding="utf-8")
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["onset", "duration", "trial_type", "value", "source", "sample"])
        self._files["events"] = handle
        self._writers["events"] = writer
        self.paths["events"] = str(path)
        return writer

    def _write_channels(self) -> None:
        path = self.eeg_dir / f"{self.prefix}_channels.tsv"
        rate = self.metadata.get("eeg_fs", "")
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["name", "type", "units", "sampling_frequency"])
            for name in self.eeg_channels:
                writer.writerow([name, "EEG", "uV", rate])
        self.paths["channels"] = str(path)

    def _write_eeg_json(self) -> None:
        path = self.eeg_dir / f"{self.prefix}_eeg.json"
        payload = {
            "TaskName": self.task,
            "SamplingFrequency": self.metadata.get("eeg_fs"),
            "EEGReference": "unknown",
            "PowerLineFrequency": self.metadata.get("powerline_hz"),
            "SoftwareFilters": "n/a",
            "RecordingType": "continuous",
            "EEGChannelCount": len(self.eeg_channels),
            "RecordingSoftware": f"LiminalGate {__version__}",
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.paths["eeg_json"] = str(path)

    def _write_edf(self) -> Path | None:
        if not self._eeg_chunks or not self.eeg_channels:
            return None
        import pyedflib

        data = np.vstack(self._eeg_chunks)
        path = self.eeg_dir / f"{self.prefix}_eeg.edf"
        writer = pyedflib.EdfWriter(str(path), len(self.eeg_channels), file_type=pyedflib.FILETYPE_EDFPLUS)
        try:
            used: set[str] = set()
            headers = []
            rate = int(round(float(self.metadata.get("eeg_fs") or 256)))
            for name in self.eeg_channels:
                headers.append(
                    {
                        "label": _edf_label(name, used),
                        "dimension": "uV",
                        "sample_frequency": rate,
                        "physical_max": 10000.0,
                        "physical_min": -10000.0,
                        "digital_max": 32767,
                        "digital_min": -32768,
                        "transducer": "",
                        "prefilter": "",
                    }
                )
            writer.setSignalHeaders(headers)
            writer.setEquipment(f"LiminalGate_{__version__}")
            writer.setPatientCode(self.subject)
            writer.setStartdatetime(self.started_at.replace(tzinfo=None))
            columns = [np.ascontiguousarray(data[:, index], dtype=np.float64) for index in range(data.shape[1])]
            writer.writeSamples(columns)
            self._write_annotations(writer, data.shape[0], rate)
        finally:
            writer.close()
        return path

    def _write_annotations(self, writer, n_samples: int, rate: int) -> None:
        events_path = self.paths.get("events")
        if not events_path:
            return
        duration = n_samples / rate if rate else 0.0
        # pyedflib pads to a whole second, so the file is at least this long.
        padded = duration + 1.0
        with Path(events_path).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                try:
                    onset = float(row["onset"])
                except (KeyError, TypeError, ValueError):
                    continue
                onset = min(max(0.0, onset), max(0.0, padded - 0.001))
                kind = row.get("trial_type") or "marker"
                name = (row.get("value") or "").replace("\n", " ")
                description = f"{kind}:{name}"[:120]
                writer.writeAnnotation(onset, 0.0, description)

    def _close_tables(self) -> None:
        for handle in self._files.values():
            try:
                handle.close()  # type: ignore[attr-defined]
            except OSError:
                pass
        self._files.clear()

    def _write_dataset_description(self) -> None:
        path = self.root / "dataset_description.json"
        if path.exists():
            return
        payload = {
            "Name": "LiminalGate recordings",
            "BIDSVersion": "1.9.0",
            "DatasetType": "raw",
            "License": "MIT",
            "GeneratedBy": [{"Name": "LiminalGate", "Version": __version__}],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _write_session(self, stopped: bool) -> None:
        payload = {
            "software": f"LiminalGate {__version__}",
            "subject": f"sub-{self.subject}",
            "session_id": self.session_id,
            "task": self.task,
            "notes": self.notes,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "stopped_at": datetime.now(UTC).isoformat(timespec="seconds") if stopped else None,
            "eeg_samples": self.eeg_count,
            "eeg_channels": list(self.eeg_channels),
            "files": dict(self.paths),
            "xdf": None,
            **self.metadata,
        }
        path = self.session_dir / "session.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.paths["session"] = str(path)


def list_sessions(root: Path) -> list[dict]:
    found: list[dict] = []
    if not root.exists():
        return found
    for path in root.glob("sub-*/ses-*/session.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["path"] = str(path.parent)
        raw_subject = str(payload.get("subject") or "")
        payload["subject_id"] = raw_subject[4:] if raw_subject.startswith("sub-") else raw_subject
        payload["local_start"] = _local_start(str(payload.get("started_at") or ""))
        payload["duration_sec"] = _duration_sec(payload)
        payload["streams"] = _streams(path.parent)
        found.append(payload)
    found.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return found


def _local_start(started: str) -> str:
    if not started:
        return ""
    try:
        moment = datetime.fromisoformat(started)
    except ValueError:
        return started
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone().isoformat(timespec="seconds")


def _duration_sec(payload: dict) -> float | None:
    samples = payload.get("eeg_samples")
    rate = payload.get("eeg_fs")
    from_samples = None
    if isinstance(samples, int) and isinstance(rate, int | float) and rate:
        from_samples = round(samples / float(rate), 1)
    started = payload.get("started_at")
    stopped = payload.get("stopped_at")
    clock = None
    if started and stopped:
        try:
            begin = datetime.fromisoformat(str(started))
            end = datetime.fromisoformat(str(stopped))
            clock = round(max(0.0, (end - begin).total_seconds()), 1)
        except ValueError:
            clock = None
    if clock is None:
        return from_samples
    if from_samples is not None and from_samples > clock:
        return from_samples
    return clock


def _streams(session_dir: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(session_dir.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.endswith("_eeg.csv"):
            label = "eeg"
        elif name.endswith("_eeg.edf"):
            label = "edf"
        elif name.endswith("_events.tsv"):
            label = "events"
        elif path.parent.name in {"physio", "motion"} and path.suffix == ".csv":
            label = path.stem.split("_")[-1]
        else:
            continue
        if label not in found:
            found.append(label)
    return found
