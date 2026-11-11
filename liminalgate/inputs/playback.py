"""Play a recorded CSV back as a live stream.

Supports Mind Monitor's RAW_/HSI_/Accelerometer_/Gyro_ export and the CSV
this app writes (``timestamp,TP9,AF7,AF8,TP10``). Rows are paced by their
timestamps. ``speed <= 0`` emits as fast as the loop can run.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
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
        self.aux = [name for name in fieldnames if "aux" in name.lower()]
        self.hsi = [_find(fieldnames, f"HSI_{channel}") for channel in EEG_CHANNELS]
        self.acc = [_find(fieldnames, f"Accelerometer_{axis}", f"acc_{axis}") for axis in ("X", "Y", "Z")]
        self.gyro = [_find(fieldnames, f"Gyro_{axis}", f"gyro_{axis}") for axis in ("X", "Y", "Z")]
        self.marker = _find(fieldnames, "Marker", "marker")
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
            for column in columns.aux:
                number = _num(row.get(column))
                if number is None:
                    continue
                names.append(column)
                values.append(number * scale)
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
        self._task: asyncio.Task[None] | None = None

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
        self.rate = estimate_rate(self.path, columns, self.fallback_fs)
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(emit, columns), name="playback-input")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None

    async def _run(self, emit, columns: ColumnMap) -> None:
        try:
            while not self._stop.is_set():
                previous: float | None = None
                with self.path.open(newline="", encoding="utf-8-sig") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        if self._stop.is_set():
                            return
                        items = items_from_row(columns, row, self.scale, self.rate)
                        if not items:
                            continue
                        stamp = items[0].t
                        if previous is not None and self.speed > 0:
                            delay = (stamp - previous) / self.speed
                            if delay > 0:
                                await asyncio.sleep(min(delay, 2.0))
                        previous = stamp
                        for item in items:
                            emit(item)
                if not self.loop:
                    return
        except asyncio.CancelledError:
            raise
