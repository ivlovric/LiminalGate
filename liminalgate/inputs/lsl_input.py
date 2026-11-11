"""Minimal LSL EEG inlet.

TODO: marker inlet, clock-sync correction beyond the inlet timestamps, and
irregular streams whose nominal rate is 0. v0.1 reads one EEG stream when
``pylsl`` is installed and reports a clear error when it is not.
"""

from __future__ import annotations

import logging
import threading

from liminalgate.errors import InputError
from liminalgate.signals import Sample

logger = logging.getLogger("liminalgate.lsl")


def _channel_names(info, count: int) -> tuple[str, ...]:
    names: list[str] = []
    try:
        channel = info.desc().child("channels").child("channel")
        for index in range(count):
            if channel.empty():
                names.append(f"CH{index + 1}")
            else:
                label = channel.child_value("label") or f"CH{index + 1}"
                names.append(str(label))
                channel = channel.next_sibling()
    except Exception:
        logger.info("lsl_channel_labels_unavailable")
        names = [f"CH{index + 1}" for index in range(count)]
    if len(names) != count:
        names = [f"CH{index + 1}" for index in range(count)]
    return tuple(names)


class LSLInput:
    name = "lsl"

    def __init__(self, name: str = "", scale: float = 1.0, fallback_fs: float = 256.0) -> None:
        self.stream_name = name
        self.scale = float(scale)
        self.fallback_fs = float(fallback_fs)
        self.error: str | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._emit = None

    async def start(self, emit) -> None:
        self._emit = emit
        self.error = None
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._serve, name="liminalgate-lsl", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=15):
            self._stop.set()
            raise InputError("LSL resolve timed out")
        if self.error:
            raise InputError(self.error)

    async def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self._thread = None

    def _serve(self) -> None:
        try:
            import pylsl
        except ImportError:
            self.error = "pylsl is not installed. pip install pylsl  — LSL is optional in v0.1."
            self._ready.set()
            return
        try:
            if self.stream_name:
                streams = pylsl.resolve_byprop("name", self.stream_name, timeout=5.0)
            else:
                streams = pylsl.resolve_byprop("type", "EEG", timeout=5.0)
            if not streams:
                self.error = "no LSL EEG stream found"
                self._ready.set()
                return
            info = streams[0]
            inlet = pylsl.StreamInlet(info, max_buflen=60)
            count = int(info.channel_count())
            names = _channel_names(info, count)
            nominal = float(info.nominal_srate() or 0.0)
            rate = nominal if nominal > 0 else self.fallback_fs
            self._ready.set()
            logger.info("lsl_connected", extra={"name": info.name(), "channels": count, "fs": rate})
            while not self._stop.is_set():
                chunk, stamps = inlet.pull_chunk(timeout=0.1, max_samples=64)
                for stamp, row in zip(stamps, chunk, strict=False):
                    values = tuple(float(value) * self.scale for value in row)
                    emit = self._emit
                    if emit is not None:
                        emit(Sample(float(stamp), "eeg", names, values, rate))
        except Exception as exc:
            logger.exception("lsl_failed")
            self.error = self.error or str(exc)
            self._ready.set()
