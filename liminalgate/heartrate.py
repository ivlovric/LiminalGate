"""Heart rate from a PPG or optics waveform, with an optional device reading.

Mind Monitor often sends only ``/muse/ppg`` (ambient, IR, red, sometimes more).
The pulse sits near 1 Hz, so each channel is band-passed to about 0.7–3.5 Hz
and the channel with the best signal-to-noise ratio is the one we count.
A device heart-rate path, when the headset sends one, wins over the detector.
"""

from __future__ import annotations

import time
from collections import deque

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt

_LOW_HZ = 0.7
_HIGH_HZ = 3.5


class HeartRateEstimator:
    def __init__(self, fs: float = 64.0, window_sec: float = 10.0) -> None:
        self.fs = float(fs)
        self.window_sec = float(window_sec)
        self._limit = max(16, int(self.fs * self.window_sec))
        self._series: dict[str, deque[float]] = {}
        self._kinds: dict[str, str] = {}
        self._saw_ppg = False
        self._device: tuple[float, float] | None = None
        self._device_mono = 0.0
        self._device_pending = False
        self._snap: dict = _empty_snap()
        self._last_compute = 0.0
        self._samples = 0

    @property
    def saw_ppg(self) -> bool:
        return self._saw_ppg

    def reset(self, fs: float | None = None) -> None:
        if fs is not None and fs > 0:
            self.fs = float(fs)
            self._limit = max(16, int(self.fs * self.window_sec))
        self._series.clear()
        self._kinds.clear()
        self._saw_ppg = False
        self._device = None
        self._device_mono = 0.0
        self._device_pending = False
        self._snap = _empty_snap()
        self._samples = 0
        self._last_compute = 0.0

    def push(self, value: float) -> None:
        """One sample of a single PPG channel. Kept for callers that have no frame."""

        if value != value:
            return
        self.push_frame(("ch1",), (float(value),), kind="ppg")

    def push_frame(self, names: tuple[str, ...], values: tuple[float, ...], kind: str = "ppg") -> None:
        if kind == "ppg":
            self._saw_ppg = True
        for name, value in zip(names, values, strict=False):
            if value != value:
                continue
            key = f"{kind}:{name}"
            series = self._series.get(key)
            if series is None or series.maxlen != self._limit:
                series = deque(maxlen=self._limit)
                self._series[key] = series
            series.append(float(value))
            self._kinds[key] = kind
        self._samples += 1

    def set_device_bpm(self, bpm: float, t: float) -> None:
        if 30.0 <= float(bpm) <= 220.0:
            self._device = (float(t), float(bpm))
            self._device_mono = time.monotonic()
            self._device_pending = True

    def estimate(self) -> float | None:
        snap = self.status(force=True)
        bpm = snap.get("bpm")
        return None if bpm is None else float(bpm)

    def status(self, force: bool = False) -> dict:
        # Peak picking is a few filters over a short window. Twice a second is enough.
        due = self._samples - self._last_compute >= max(1, int(self.fs * 0.5))
        if force or (due and self._samples != self._last_compute) or self._device_pending:
            self._snap = self._compute()
            self._last_compute = self._samples
            self._device_pending = False
        return dict(self._snap)

    def _compute(self) -> dict:
        keys = self._candidate_keys()
        if not keys and self._device is None:
            return _empty_snap()
        best_name = None
        best_snr = -1.0
        best_filtered: np.ndarray | None = None
        kind = "ppg"
        for key in keys:
            series = np.asarray(self._series[key], dtype=np.float64)
            filtered, snr = _prepare(series, self.fs)
            if filtered is None:
                continue
            if snr > best_snr:
                best_snr = snr
                best_filtered = filtered
                best_name = key.split(":", 1)[1]
                kind = self._kinds.get(key, "ppg")
        needed = int(self.fs * 4)
        have = max((len(self._series[key]) for key in keys), default=0)
        detected_bpm = None
        rmssd = None
        if best_filtered is not None and have >= needed:
            detected_bpm, rmssd = _beats(best_filtered, self.fs)
        device_bpm = None
        if self._device is not None and time.monotonic() - self._device_mono < 5.0:
            device_bpm = self._device[1]
        bpm = device_bpm if device_bpm is not None else detected_bpm
        if not keys and device_bpm is None:
            state = "no_ppg"
        elif bpm is None:
            state = "computing"
        else:
            state = "ready"
        quality = None
        if best_filtered is not None and have >= needed:
            quality = _quality(best_snr, detected_bpm is not None)
        source = None
        if device_bpm is not None:
            source = "device"
        elif detected_bpm is not None:
            source = kind
        return {
            "state": state,
            "bpm": bpm,
            "rmssd": rmssd,
            "quality": quality,
            "channel": best_name,
            "source": source,
        }

    def _candidate_keys(self) -> list[str]:
        if self._saw_ppg:
            return [key for key, kind in self._kinds.items() if kind == "ppg"]
        return [key for key, kind in self._kinds.items() if kind == "optics"]


def _empty_snap() -> dict:
    return {"state": "no_ppg", "bpm": None, "rmssd": None, "quality": None, "channel": None, "source": None}


def _prepare(series: np.ndarray, fs: float) -> tuple[np.ndarray | None, float]:
    if series.size < max(16, int(fs * 2)) or fs <= _HIGH_HZ * 2:
        return None, 0.0
    centered = series - float(np.mean(series))
    if float(np.std(centered)) < 1e-6:
        return None, 0.0
    sos = butter(2, [_LOW_HZ, _HIGH_HZ], btype="bandpass", fs=fs, output="sos")
    try:
        filtered = np.asarray(sosfiltfilt(sos, centered), dtype=np.float64)
    except ValueError:
        return None, 0.0
    noise = centered - filtered
    snr = float(np.var(filtered) / (np.var(noise) + 1e-12))
    return filtered, snr


def _beats(filtered: np.ndarray, fs: float) -> tuple[float | None, float | None]:
    # A narrow pulse rings below zero after the band-pass. Those lobes are not beats.
    # Optics can be inverted, so the polarity with the steadier intervals wins.
    upright = _beats_one(filtered, fs)
    inverted = _beats_one(-filtered, fs)
    chosen = _steadier(upright, inverted)
    if chosen is None:
        return None, None
    return chosen[0], chosen[1]


def _steadier(
    left: tuple[float, float | None, int, float, float] | None,
    right: tuple[float, float | None, int, float, float] | None,
) -> tuple[float, float | None, int, float, float] | None:
    if left is None:
        return right
    if right is None:
        return left
    # A band-passed spike rings at a steady double rate, but those lobes are shorter.
    if left[4] > right[4] * 1.4:
        return left
    if right[4] > left[4] * 1.4:
        return right
    if abs(left[3] - right[3]) > 0.05:
        return left if left[3] < right[3] else right
    return left if left[2] >= right[2] else right


def _beats_one(filtered: np.ndarray, fs: float) -> tuple[float, float | None, int, float, float] | None:
    prominence = float(np.std(filtered)) * 0.35
    if prominence <= 0:
        return None
    distance = max(1, int(fs * 0.33))
    peaks, _props = find_peaks(filtered, distance=distance, prominence=prominence)
    peaks = peaks[filtered[peaks] > 0]
    if peaks.size < 3:
        return None
    intervals = np.diff(peaks.astype(np.float64)) / fs
    plausible = intervals[(intervals >= 0.3) & (intervals <= 1.5)]
    if plausible.size < 2:
        return None
    median = float(np.median(plausible))
    if median <= 0:
        return None
    bpm = 60.0 / median
    if not 30.0 <= bpm <= 220.0:
        return None
    rmssd = None
    if plausible.size >= 4:
        diffs = np.diff(plausible) * 1000.0
        rmssd = float(np.sqrt(np.mean(diffs**2)))
    spread = float(np.std(plausible) / median)
    height = float(np.median(filtered[peaks]))
    return bpm, rmssd, int(plausible.size), spread, height


def _quality(snr: float, locked: bool) -> str:
    if not locked or snr < 0.35:
        return "poor"
    if snr < 1.2:
        return "fair"
    return "good"
