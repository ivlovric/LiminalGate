"""Peak-to-peak heart rate from a PPG or optical pulse waveform."""

from __future__ import annotations

from collections import deque

import numpy as np


class HeartRateEstimator:
    def __init__(self, fs: float = 64.0, window_sec: float = 10.0) -> None:
        self.fs = float(fs)
        self._window = deque(maxlen=max(16, int(self.fs * window_sec)))

    def reset(self, fs: float | None = None) -> None:
        if fs is not None and fs > 0:
            self.fs = float(fs)
            self._window = deque(maxlen=max(16, int(self.fs * 10)))
        else:
            self._window.clear()

    def push(self, value: float) -> None:
        if value != value:  # NaN
            return
        self._window.append(float(value))

    def estimate(self) -> float | None:
        needed = int(self.fs * 4)
        if len(self._window) < needed:
            return None
        series = np.asarray(self._window, dtype=np.float64)
        kernel = np.ones(5) / 5.0
        smoothed = np.convolve(series, kernel, mode="same")
        smoothed = smoothed - float(np.mean(smoothed))
        peak = float(np.max(smoothed))
        if peak <= 0:
            return None
        threshold = 0.45 * peak
        refractory = max(1, int(self.fs * 0.3))
        peaks: list[int] = []
        for index in range(1, smoothed.size - 1):
            if smoothed[index] >= smoothed[index - 1] and smoothed[index] > smoothed[index + 1]:
                if smoothed[index] >= threshold and (not peaks or index - peaks[-1] >= refractory):
                    peaks.append(index)
        if len(peaks) < 3:
            return None
        intervals = np.diff(np.asarray(peaks, dtype=np.float64)) / self.fs
        median = float(np.median(intervals))
        if median <= 0:
            return None
        bpm = 60.0 / median
        if bpm < 30 or bpm > 220:
            return None
        return bpm
