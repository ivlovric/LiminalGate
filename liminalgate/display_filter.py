"""Display-only EEG filter.

Recording and band powers stay on the raw samples. This filter is a causal
SOS chain (high-pass, low-pass, powerline notch) with state kept per channel
so a live stream does not get a new transient on every packet.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos


class DisplayFilter:
    def __init__(self, fs: float, powerline_hz: float = 50.0) -> None:
        self.fs = float(fs)
        self.powerline_hz = float(powerline_hz)
        self._sos: np.ndarray | None = None
        self._zi_unit: np.ndarray | None = None
        self._zi: dict[str, np.ndarray] = {}
        self._build()

    def configure(self, fs: float, powerline_hz: float) -> None:
        if abs(fs - self.fs) < 0.01 and abs(powerline_hz - self.powerline_hz) < 0.01:
            return
        self.fs = float(fs)
        self.powerline_hz = float(powerline_hz)
        self._zi.clear()
        self._build()

    def reset(self) -> None:
        self._zi.clear()

    def apply(self, channels: tuple[str, ...], values: tuple[float, ...]) -> tuple[float, ...]:
        if self._sos is None or self._zi_unit is None or len(values) != len(channels):
            return tuple(float(value) for value in values)
        filtered: list[float] = []
        seen: set[str] = set()
        for name, value in zip(channels, values, strict=True):
            seen.add(name)
            sample = float(value)
            state = self._zi.get(name)
            if state is None:
                # Steady state for this sample's offset, so an ~800 µV baseline
                # does not ring for a second at the start of the stream.
                state = self._zi_unit * sample
            output, state = sosfilt(self._sos, np.asarray([sample]), zi=state)
            self._zi[name] = state
            filtered.append(float(output[0]))
        for name in list(self._zi):
            if name not in seen:
                self._zi.pop(name, None)
        return tuple(filtered)

    def _build(self) -> None:
        fs = self.fs
        if fs <= 10:
            self._sos = None
            self._zi_unit = None
            return
        high = butter(2, 1.0, btype="highpass", fs=fs, output="sos")
        low_edge = min(45.0, fs * 0.45)
        low = butter(4, low_edge, btype="lowpass", fs=fs, output="sos")
        sections = [high, low]
        line = self.powerline_hz
        if 1.0 < line < fs * 0.45:
            zeros, poles = iirnotch(line, 30.0, fs=fs)
            sections.append(tf2sos(zeros, poles))
        self._sos = np.vstack(sections)
        self._zi_unit = sosfilt_zi(self._sos)


def gap_before(previous: float | None, current: float, fs: float) -> bool:
    """True when ``current`` is too far after ``previous`` to draw a line."""

    if previous is None or fs <= 0:
        return False
    return current - previous > max(2.5 / fs, 0.02)
