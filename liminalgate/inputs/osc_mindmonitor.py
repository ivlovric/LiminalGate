"""Mind Monitor OSC over UDP.

Spec: https://mind-monitor.com/FAQ.php#oscspec

``/muse/eeg`` is TP9, AF7, AF8, TP10, then up to four auxiliary channels, in
microvolts, nominally 256 Hz (220 Hz on the original 2014 headset — set
``eeg_fs``). Absolute band paths are in bels, either one average or four
channels. Horseshoe fit is 1 good, 2 medium, 4 bad. ``/Marker/1`` … ``/Marker/5``
are the on-screen marker buttons.

Incoming EEG is multiplied by ``scale`` so BrainFlow and muse-lsl sessions
can be aligned (those toolchains can disagree by about 4.5×).
"""

from __future__ import annotations

import logging
import re
import threading
import time

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

from liminalgate.constants import AUX_CHANNELS, EEG_CHANNELS
from liminalgate.errors import InputError
from liminalgate.signals import Event, Sample

logger = logging.getLogger("liminalgate.osc")

_MARKER = re.compile(r"^/Marker/(\d+)$")
_HEART = re.compile(r"(?:^|/)(heart_rate|heart|hr|bpm)$", re.IGNORECASE)
_ABSOLUTE = {
    "/muse/elements/delta_absolute": "delta",
    "/muse/elements/theta_absolute": "theta",
    "/muse/elements/alpha_absolute": "alpha",
    "/muse/elements/beta_absolute": "beta",
    "/muse/elements/gamma_absolute": "gamma",
}
_KNOWN_EXACT = {
    "/muse/eeg",
    "/muse/acc",
    "/muse/gyro",
    "/muse/ppg",
    "/muse/optics",
    "/muse/batt",
    "/muse/elements/horseshoe",
    "/muse/elements/is_good",
    "/muse/elements/blink",
    "/muse/elements/jaw_clench",
    "/muse/elements/touching_forehead",
    *_ABSOLUTE,
}


def _floats(args: tuple) -> tuple[float, ...] | None:
    try:
        return tuple(float(value) for value in args)
    except (TypeError, ValueError):
        return None


class SampleClock:
    """Turn bursty OSC arrivals into an evenly spaced sample clock.

    Mind Monitor often delivers a BLE packet's worth of ``/muse/eeg`` messages
    in one UDP burst (classically 12 samples, 256/12 = 21.333 Hz). Stamping each
    message with ``time.time()`` stacks those samples on one x position and
    draws a line across the gap to the next burst. This clock keeps arrival
    order and spaces samples by ``1/fs`` until the stream actually pauses.
    """

    def __init__(self, fs: float) -> None:
        self.fs = float(fs)
        self.step = 1.0 / self.fs if self.fs > 0 else 0.0
        self.next_t: float | None = None
        self.width: int | None = None
        self._last: tuple[float, ...] | None = None
        self._last_wall = 0.0
        self._stamp_wall: float | None = None
        self._last_emit: float | None = None

    def configure(self, fs: float) -> None:
        if abs(fs - self.fs) < 0.01:
            return
        self.fs = float(fs)
        self.step = 1.0 / self.fs if self.fs > 0 else 0.0
        self.next_t = None
        self._last_emit = None

    def duplicate(self, values: tuple[float, ...], now: float) -> bool:
        """A second copy of the same datagram inside a fraction of a sample."""

        if self._last == values and now - self._last_wall < 0.0004:
            return True
        self._last = values
        self._last_wall = now
        return False

    def timestamps(self, count: int, now: float) -> list[float]:
        if count <= 0:
            return []
        step = self.step
        previous = self._stamp_wall
        self._stamp_wall = now
        # Messages inside 50 ms of each other are one burst or a paced sample.
        # A longer hole is a real gap: start the clock at this arrival.
        continued = previous is not None and self.next_t is not None and now - previous <= 0.05
        if continued and self.next_t is not None:
            start = self.next_t
        else:
            start = now - (count - 1) * step
        # A burst schedules samples ahead of the wall clock. The next packet can
        # then look like a pause and snap `now` backwards over those samples.
        # Never emit a time earlier than the previous sample.
        if self._last_emit is not None and start < self._last_emit + step * 0.5:
            start = self._last_emit + step
        times = [start + index * step for index in range(count)]
        self.next_t = times[-1] + step
        self._last_emit = times[-1]
        return times


def split_eeg(values: tuple[float, ...], width: int | None = None) -> list[tuple[float, ...]]:
    """Split one OSC payload into samples.

    A message of 1–8 floats is one sample (4 EEG plus up to 4 AUX). A longer
    message is several samples concatenated. When the live stream has already
    shown its width (4, 5, 6, or 8), that width wins. Otherwise groups of 4
    are the classic layout. Preferring 4 for an Athena batch that is also
    divisible by 4 rotates AUX into AF7 and paints a sawtooth.
    """

    count = len(values)
    if count == 0:
        return []
    if count <= 8:
        return [values]

    def chunks(size: int) -> list[tuple[float, ...]]:
        return [values[index : index + size] for index in range(0, count, size)]

    if width in {4, 5, 6, 8} and count % width == 0:
        return chunks(width)
    for size in (4, 5, 6, 8):
        if count % size == 0:
            return chunks(size)
    return [values[:8]]


def ppg_channel_names(count: int) -> tuple[str, ...]:
    """Ambient, IR, red, then any extra Athena PPG channels."""

    base = ("ambient", "ir", "red")
    if count <= len(base):
        return base[:count]
    extra = tuple(f"ch{index}" for index in range(len(base) + 1, count + 1))
    return base + extra


def known_osc_address(address: str) -> bool:
    cleaned = address.strip()
    if cleaned in _KNOWN_EXACT or _MARKER.match(cleaned):
        return True
    return _HEART.search(cleaned) is not None


def device_heart_rate(address: str, values: tuple[float, ...]) -> float | None:
    """BPM from a Mind Monitor heart-rate path, if this message is one."""

    if not values or not _HEART.search(address.strip()):
        return None
    bpm = float(values[0])
    if 30.0 <= bpm <= 220.0:
        return bpm
    return None


def channel_names(width: int) -> tuple[str, ...]:
    extra = max(0, width - len(EEG_CHANNELS))
    return EEG_CHANNELS + AUX_CHANNELS[:extra]


def parse_message(
    address: str,
    args: tuple,
    fs: float = 256.0,
    scale: float = 1.0,
    now: float | None = None,
    clock: SampleClock | None = None,
) -> list[Sample | Event]:
    """Turn one OSC message into samples and events. Unknown addresses yield nothing."""

    stamp = time.time() if now is None else now
    address = address.strip()
    values = _floats(tuple(args))
    if values is None:
        return []
    if address == "/muse/eeg":
        if clock is not None and clock.duplicate(values, stamp):
            return []
        frames = split_eeg(values, clock.width if clock is not None else None)
        if not frames:
            return []
        if clock is not None and len(values) <= 8:
            clock.width = len(values)
        if clock is not None:
            times = clock.timestamps(len(frames), stamp)
        else:
            step = 1.0 / fs if fs > 0 else 0.0
            last = len(frames) - 1
            times = [stamp - (last - index) * step for index in range(len(frames))]
        parsed: list[Sample | Event] = []
        for frame, frame_time in zip(frames, times, strict=True):
            names = channel_names(len(frame))
            scaled = tuple(float(value) * scale for value in frame[: len(names)])
            parsed.append(Sample(frame_time, "eeg", names, scaled, fs))
        return parsed
    if address == "/muse/acc" and len(values) >= 3:
        return [Sample(stamp, "acc", ("x", "y", "z"), values[:3], 52.0)]
    if address == "/muse/gyro" and len(values) >= 3:
        return [Sample(stamp, "gyro", ("x", "y", "z"), values[:3], 52.0)]
    if address == "/muse/ppg" and values:
        names = ppg_channel_names(len(values))
        return [Sample(stamp, "ppg", names, values[: len(names)], 64.0)]
    heart = device_heart_rate(address, values)
    if heart is not None:
        return [Sample(stamp, "device_hr", ("bpm",), (heart,), 1.0)]
    if address == "/muse/optics" and values:
        names = tuple(f"opt{i + 1}" for i in range(len(values)))
        return [Sample(stamp, "optics", names, values, 64.0)]
    if address in _ABSOLUTE and values:
        band = _ABSOLUTE[address]
        if len(values) == 1:
            return [Sample(stamp, "device_band", (band,), values, 10.0)]
        names = tuple(f"{band}_{channel}" for channel in EEG_CHANNELS)
        return [Sample(stamp, "device_band", names, values[:4], 10.0)]
    if address == "/muse/elements/horseshoe" and len(values) >= 4:
        return [Sample(stamp, "quality", EEG_CHANNELS, values[:4], 10.0)]
    if address == "/muse/elements/is_good" and len(values) >= 4:
        # is_good is 1/0. Horseshoe uses 4 for a bad fit, which is what the pipeline checks.
        mapped = tuple(1.0 if value >= 0.5 else 4.0 for value in values[:4])
        return [Sample(stamp, "quality", EEG_CHANNELS, mapped, 10.0)]
    if address == "/muse/elements/blink" and (not values or values[0] != 0):
        return [Event(stamp, "blink", "blink", source="osc", color="#f2d479")]
    if address == "/muse/elements/jaw_clench" and (not values or values[0] != 0):
        return [Event(stamp, "jaw_clench", "jaw_clench", source="osc", color="#e08a5a")]
    if address == "/muse/elements/touching_forehead" and values:
        return [Sample(stamp, "contact", ("touching_forehead",), (values[0],), 10.0)]
    if address == "/muse/batt" and values:
        return [Sample(stamp, "battery", ("level", "voltage", "adc", "temp"), values[:4], 0.1)]
    marker = _MARKER.match(address)
    if marker and (not values or values[0] != 0):
        number = int(marker.group(1))
        return [Event(stamp, "marker", f"Marker {number}", source="osc", osc=number)]
    return []


class OscMindMonitorInput:
    name = "osc"

    def __init__(self, host: str, port: int, fs: float = 256.0, scale: float = 1.0) -> None:
        self.host = host
        self.port = int(port)
        self.fs = float(fs)
        self.scale = float(scale)
        self.clock = SampleClock(self.fs)
        self.path_counts: dict[str, int] = {}
        self._path_lock = threading.Lock()
        self.bound_port = int(port)
        self._server: ThreadingOSCUDPServer | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.error: str | None = None
        self._emit = None

    async def start(self, emit) -> None:
        self._emit = emit
        self.error = None
        self._ready.clear()
        self._thread = threading.Thread(target=self._serve, name="liminalgate-osc", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=3):
            raise InputError("OSC socket did not bind")
        if self.error:
            raise InputError(self.error)

    async def stop(self) -> None:
        server = self._server
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self._server = None
        self._thread = None

    def _serve(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._on_message)
        try:
            self._server = ThreadingOSCUDPServer((self.host, self.port), dispatcher)
        except OSError as exc:
            self.error = f"could not bind OSC {self.host}:{self.port}: {exc}"
            self._ready.set()
            return
        self.bound_port = int(self._server.server_address[1])
        logger.info("osc_listening", extra={"host": self.host, "port": self.bound_port})
        self._ready.set()
        self._server.serve_forever()

    def path_snapshot(self) -> list[dict[str, int | str]]:
        with self._path_lock:
            rows = sorted(self.path_counts.items(), key=lambda item: (-item[1], item[0]))
        return [{"path": path, "count": count} for path, count in rows[:40]]

    def _on_message(self, address: str, *args: object) -> None:
        emit = self._emit
        if emit is None:
            return
        cleaned = address.strip()
        with self._path_lock:
            count = self.path_counts.get(cleaned, 0) + 1
            self.path_counts[cleaned] = count
        if count == 1 and not known_osc_address(cleaned):
            logger.debug("unknown OSC path %s", cleaned)
        try:
            for item in parse_message(cleaned, args, fs=self.fs, scale=self.scale, clock=self.clock):
                emit(item)
        except Exception:
            logger.exception("osc_handler_failed", extra={"address": cleaned})
