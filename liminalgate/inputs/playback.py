"""Play a recorded CSV back as a live stream.

Supports Mind Monitor's RAW_/HSI_/Accelerometer_/Gyro_ export and the CSV
this app writes (``timestamp,TP9,AF7,AF8,TP10``). Rows are paced at ``1/rate``
so a backwards timestamp in the file cannot rewind the charts. ``speed <= 0``
emits as fast as the loop can run. AUX columns are not EEG lanes.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import time
from datetime import datetime
from pathlib import Path

from liminalgate.constants import EEG_CHANNELS
from liminalgate.errors import InputError
from liminalgate.signals import Event, Sample


def parse_timestamp(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _find(fieldnames: list[str], *candidates: str) -> str | None:
    lookup = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        found = lookup.get(candidate.lower())
        if found:
            return found
    return None


class ColumnMap:
    def __init__(self, fieldnames: list[str]) -> None:
        self.timestamp = _find(fieldnames, "TimeStamp", "timestamp", "time")
        self.eeg: list[tuple[str, str]] = []
        for channel in EEG_CHANNELS:
            column = _find(fieldnames, f"RAW_{channel}", channel)
            if column:
                self.eeg.append((channel, column))
        self.hsi = [_find(fieldnames, f"HSI_{channel}") for channel in EEG_CHANNELS]
        self.acc = [_find(fieldnames, f"Accelerometer_{axis}", f"acc_{axis}") for axis in ("X", "Y", "Z")]
        self.gyro = [_find(fieldnames, f"Gyro_{axis}", f"gyro_{axis}") for axis in ("X", "Y", "Z")]
        self.marker = _find(fieldnames, "Marker", "marker")
        self.elements = _find(fieldnames, "Elements", "Element")
        self.ppg = [_find(fieldnames, name) for name in ("PPG_Ambient", "PPG_IR", "PPG_Red")]

    @property
    def has_eeg(self) -> bool:
        return len(self.eeg) >= 4


def items_from_row(
    columns: ColumnMap,
    row: dict[str, str | None],
    scale: float,
    rate: float,
) -> list[Sample | Event]:
    stamp = parse_timestamp(row.get(columns.timestamp) or "") if columns.timestamp else None
    if stamp is None:
        return []
    items: list[Sample | Event] = []
    if columns.has_eeg:
        names: list[str] = []
        values: list[float] = []
        missing = False
        for channel, column in columns.eeg:
            number = _num(row.get(column))
            if number is None:
                missing = True
                break
            names.append(channel)
            values.append(number * scale)
        if not missing:
            # AUX is recorded with the EEG file but it is not an EEG lane.
            # A railed AUX column (~1450 µV) would set the scale for every trace.
            items.append(Sample(stamp, "eeg", tuple(names), tuple(values), rate))
        hsi_values = [_num(row.get(column)) if column else None for column in columns.hsi]
        if all(value is not None for value in hsi_values):
            items.append(
                Sample(stamp, "quality", EEG_CHANNELS, tuple(float(v) for v in hsi_values if v is not None), 10.0)
            )
    if all(columns.acc):
        axes = [_num(row.get(column or "")) for column in columns.acc]
        if all(value is not None for value in axes):
            items.append(Sample(stamp, "acc", ("x", "y", "z"), tuple(float(v) for v in axes if v is not None), 52.0))
    if all(columns.gyro):
        axes = [_num(row.get(column or "")) for column in columns.gyro]
        if all(value is not None for value in axes):
            items.append(Sample(stamp, "gyro", ("x", "y", "z"), tuple(float(v) for v in axes if v is not None), 52.0))
    if all(columns.ppg):
        pulse = [_num(row.get(column or "")) for column in columns.ppg]
        if all(value is not None for value in pulse):
            items.append(
                Sample(
                    stamp,
                    "ppg",
                    ("ambient", "ir", "red"),
                    tuple(float(v) for v in pulse if v is not None),
                    64.0,
                )
            )
    if columns.marker:
        raw = (row.get(columns.marker) or "").strip()
        if raw and raw not in {"0", "0.0"}:
            try:
                number = int(float(raw))
                items.append(Event(stamp, "marker", f"Marker {number}", source="playback", osc=number))
            except ValueError:
                items.append(Event(stamp, "marker", raw, source="playback"))
    if columns.elements:
        label = (row.get(columns.elements) or "").strip()
        if label and label not in {"0", "0.0"}:
            items.append(Event(stamp, "marker", label, source="playback"))
    return items


def estimate_rate(path: Path, columns: ColumnMap, fallback: float) -> float:
    stamps: list[float] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if columns.timestamp is None:
                break
            stamp = parse_timestamp(row.get(columns.timestamp) or "")
            if stamp is not None:
                stamps.append(stamp)
            if len(stamps) >= 40:
                break
    if len(stamps) < 5:
        return fallback
    deltas = [b - a for a, b in zip(stamps, stamps[1:], strict=False) if 0.0002 < (b - a) < 5]
    if not deltas:
        return fallback
    deltas.sort()
    median = deltas[len(deltas) // 2]
    if median <= 0:
        return fallback
    return 1.0 / median


def inspect_csv(path: Path, fallback_fs: float = 256.0) -> dict:
    """Duration, rate, and a warning for a file the user is about to play."""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        names = list(reader.fieldnames or [])
        columns = ColumnMap(names)
        stamps: list[float] = []
        for row in reader:
            if columns.timestamp is None:
                break
            stamp = parse_timestamp(row.get(columns.timestamp) or "")
            if stamp is not None:
                stamps.append(stamp)
    rate = estimate_rate(path, columns, fallback_fs) if columns.timestamp else fallback_fs
    samples = len(stamps)
    if len(stamps) >= 2 and max(stamps) > min(stamps):
        duration = max(stamps) - min(stamps)
    elif rate > 0:
        duration = samples / rate
    else:
        duration = 0.0
    warning = ""
    if columns.has_eeg and rate < 8:
        warning = (
            f"About {rate:.1f} rows/s. That is too slow for EEG. "
            "In Mind Monitor use a Constant interval, not one row per second."
        )
    elif columns.has_eeg and duration < 30:
        warning = f"Short file ({duration:.1f} s). With loop on it restarts that often."
    return {
        "path": str(path),
        "name": path.name,
        "eeg": columns.has_eeg,
        "rate": round(rate, 3),
        "samples": samples,
        "duration": round(duration, 3),
        "warning": warning,
    }


def sidecar_events(path: Path) -> list[tuple[float, str, str, int | None]]:
    """LiminalGate ``*_events.tsv`` beside an EEG CSV.

    ``sample`` is the EEG row the marker belongs to. ``onset`` is seconds from
    the file start and is used when the sample column is absent.
    """

    if path.name.endswith("_eeg.csv"):
        candidate = path.with_name(path.name[: -len("_eeg.csv")] + "_events.tsv")
    else:
        candidate = path.with_suffix(".tsv")
    if not candidate.is_file():
        return []
    found: list[tuple[float, str, str, int | None]] = []
    with candidate.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            try:
                onset = float(row.get("onset") or "")
            except ValueError:
                continue
            kind = (row.get("trial_type") or "marker").strip() or "marker"
            name = (row.get("value") or kind).strip() or kind
            sample_text = (row.get("sample") or "").strip()
            sample = int(sample_text) if sample_text.isdigit() else None
            found.append((onset, kind, name, sample))
    found.sort(key=lambda item: item[3] if item[3] is not None else item[0])
    return found


class PlaybackInput:
    name = "playback"

    def __init__(
        self,
        path: str,
        speed: float = 1.0,
        loop: bool = True,
        scale: float = 1.0,
        fallback_fs: float = 256.0,
    ) -> None:
        self.path = Path(path)
        self.speed = float(speed)
        self.loop = loop
        self.scale = float(scale)
        self.fallback_fs = float(fallback_fs)
        self.rate = float(fallback_fs)
        self._stop = asyncio.Event()
        self._resume = asyncio.Event()
        self._resume.set()
        self._restart = False
        self._seek: float | None = None
        self._task: asyncio.Task[None] | None = None
        info = inspect_csv(self.path, fallback_fs) if self.path.is_file() else {}
        self.state: dict[str, object] = {
            "file": self.path.name,
            "path": str(self.path),
            "position": 0.0,
            "duration": float(info.get("duration") or 0.0),
            "loop": 0,
            "paused": False,
            "rate": float(info.get("rate") or fallback_fs),
            "warning": str(info.get("warning") or ""),
        }

    def pause(self) -> None:
        self._resume.clear()
        self.state["paused"] = True

    def resume(self) -> None:
        self._resume.set()
        self.state["paused"] = False

    def restart(self) -> None:
        self._restart = True
        self._seek = 0.0
        self._resume.set()
        self.state["paused"] = False

    def seek(self, seconds: float) -> None:
        self._seek = max(0.0, float(seconds))
        self._resume.set()
        self.state["paused"] = False

    async def start(self, emit) -> None:
        if not self.path.is_file():
            raise InputError(f"playback file not found: {self.path}")
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise InputError("playback CSV has no header")
            columns = ColumnMap(list(reader.fieldnames))
        if not columns.has_eeg:
            raise InputError("playback CSV needs RAW_TP9..TP10 or TP9..TP10 columns")
        info = inspect_csv(self.path, self.fallback_fs)
        self.rate = float(info["rate"])
        self.state.update(
            {
                "duration": info["duration"],
                "rate": self.rate,
                "warning": info["warning"],
                "file": self.path.name,
                "loop": 0,
                "position": 0.0,
            }
        )
        self._stop = asyncio.Event()
        self._resume.set()
        self._task = asyncio.create_task(self._run(emit, columns), name="playback-input")

    async def stop(self) -> None:
        self._stop.set()
        self._resume.set()
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None

    async def _run(self, emit, columns: ColumnMap) -> None:
        events = sidecar_events(self.path)
        step = 1.0 / self.rate if self.rate > 0 else 0.0
        clock = time.time()
        try:
            while not self._stop.is_set():
                event_at = 0
                index = 0
                skip_rows = 0
                finished = True
                with self.path.open(newline="", encoding="utf-8-sig") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        if self._stop.is_set():
                            return
                        await self._resume.wait()
                        if self._stop.is_set():
                            return
                        if self._restart:
                            self._restart = False
                            self.state["position"] = 0.0
                            finished = False
                            break
                        if self._seek is not None:
                            skip_rows = int(self._seek * self.rate) if self.rate > 0 else 0
                            self._seek = None
                        items = items_from_row(columns, row, self.scale, self.rate)
                        if not items:
                            continue
                        position = index * step
                        if index < skip_rows:
                            event_at = _advance_events(events, event_at, index, position, emit=None, clock=clock)
                            index += 1
                            continue
                        if index > 0 and self.speed > 0 and step > 0:
                            remaining = min(step / self.speed, 2.0)
                            while remaining > 0 and not self._stop.is_set() and not self._restart:
                                await self._resume.wait()
                                slice_s = min(0.05, remaining)
                                await asyncio.sleep(slice_s)
                                remaining -= slice_s
                            if self._stop.is_set():
                                return
                            if self._restart:
                                self._restart = False
                                self.state["position"] = 0.0
                                finished = False
                                break
                        self.state["position"] = round(position, 3)
                        for item in items:
                            item.t = clock
                        for item in items:
                            emit(item)
                        event_at = _advance_events(events, event_at, index, position, emit, clock)
                        clock += step
                        index += 1
                if not finished:
                    continue
                if not self.loop or self._stop.is_set():
                    return
                self.state["loop"] = int(self.state.get("loop") or 0) + 1
                self.state["position"] = 0.0
        except asyncio.CancelledError:
            raise


def _event_due(event: tuple[float, str, str, int | None], index: int, position: float) -> bool:
    _onset, _kind, _name, sample = event
    if sample is not None:
        return sample <= index
    return event[0] <= position + 1e-3


def _advance_events(events, event_at: int, index: int, position: float, emit, clock: float) -> int:
    while event_at < len(events) and _event_due(events[event_at], index, position):
        _onset, kind, name, _sample = events[event_at]
        if emit is not None:
            emit(Event(clock, kind, name, source="playback"))
        event_at += 1
    return event_at
