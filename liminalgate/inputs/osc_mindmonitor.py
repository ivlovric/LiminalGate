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
_ABSOLUTE = {
    "/muse/elements/delta_absolute": "delta",
    "/muse/elements/theta_absolute": "theta",
    "/muse/elements/alpha_absolute": "alpha",
    "/muse/elements/beta_absolute": "beta",
    "/muse/elements/gamma_absolute": "gamma",
}


def _floats(args: tuple) -> tuple[float, ...] | None:
    try:
        return tuple(float(value) for value in args)
    except (TypeError, ValueError):
        return None


def split_eeg(values: tuple[float, ...]) -> list[tuple[float, ...]]:
    """One Mind Monitor packet is a single sample. Longer packets are split by 4."""

    count = len(values)
    if count == 0:
        return []
    if count <= 8:
        return [values]
    if count % 4 == 0:
        return [values[index : index + 4] for index in range(0, count, 4)]
    if count % 5 == 0:
        return [values[index : index + 5] for index in range(0, count, 5)]
    return [values[:8]]


def channel_names(width: int) -> tuple[str, ...]:
    extra = max(0, width - len(EEG_CHANNELS))
    return EEG_CHANNELS + AUX_CHANNELS[:extra]


def parse_message(
    address: str,
    args: tuple,
    fs: float = 256.0,
    scale: float = 1.0,
    now: float | None = None,
) -> list[Sample | Event]:
    """Turn one OSC message into samples and events. Unknown addresses yield nothing."""

    stamp = time.time() if now is None else now
    address = address.strip()
    values = _floats(tuple(args))
    if values is None:
        return []
    if address == "/muse/eeg":
        frames = split_eeg(values)
        if not frames:
            return []
        step = 1.0 / fs if fs > 0 else 0.0
        parsed: list[Sample | Event] = []
        last = len(frames) - 1
        for index, frame in enumerate(frames):
            names = channel_names(len(frame))
            scaled = tuple(float(value) * scale for value in frame[: len(names)])
            parsed.append(Sample(stamp - (last - index) * step, "eeg", names, scaled, fs))
        return parsed
    if address == "/muse/acc" and len(values) >= 3:
        return [Sample(stamp, "acc", ("x", "y", "z"), values[:3], 52.0)]
    if address == "/muse/gyro" and len(values) >= 3:
        return [Sample(stamp, "gyro", ("x", "y", "z"), values[:3], 52.0)]
    if address == "/muse/ppg" and len(values) >= 3:
        return [Sample(stamp, "ppg", ("ambient", "ir", "red"), values[:3], 64.0)]
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

    def _on_message(self, address: str, *args: object) -> None:
        emit = self._emit
        if emit is None:
            return
        try:
            for item in parse_message(address, args, fs=self.fs, scale=self.scale):
                emit(item)
        except Exception:
            logger.exception("osc_handler_failed", extra={"address": address})
