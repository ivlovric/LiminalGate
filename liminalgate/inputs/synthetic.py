"""A hardware-free EEG/PPG source with a known theta-dominant mix.

The 6 Hz component is stronger than alpha so the shipped hypnagogic profile
produces an audible tone without a headset. ``render_eeg`` is deterministic
when called without extra noise, which the tests rely on.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time

import numpy as np

from liminalgate.constants import EEG_CHANNELS
from liminalgate.signals import Event, Sample

PPG_CHANNELS = ("ambient", "ir", "red")
ACC_CHANNELS = ("x", "y", "z")


def render_eeg(t: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Four channels, µV, theta-dominant with a little delta, alpha, and beta."""

    t = np.asarray(t, dtype=np.float64)
    tp9 = 28 * np.sin(2 * np.pi * 6.0 * t) + 8 * np.sin(2 * np.pi * 2.0 * t) + 4 * np.sin(2 * np.pi * 10.0 * t)
    af7 = 26 * np.sin(2 * np.pi * 6.0 * t) + 7 * np.sin(2 * np.pi * 2.2 * t) + 5 * np.sin(2 * np.pi * 10.0 * t)
    af8 = 24 * np.sin(2 * np.pi * 5.8 * t) + 9 * np.sin(2 * np.pi * 1.8 * t) + 4 * np.sin(2 * np.pi * 10.0 * t)
    tp10 = 22 * np.sin(2 * np.pi * 6.1 * t) + 6 * np.sin(2 * np.pi * 2.0 * t) + 6 * np.sin(2 * np.pi * 20.0 * t)
    return np.column_stack((tp9, af7, af8, tp10)) * scale


def render_ppg(t: np.ndarray, bpm: float = 72.0) -> np.ndarray:
    phase = np.mod(np.asarray(t, dtype=np.float64) * (bpm / 60.0), 1.0)
    pulse = np.exp(-((phase - 0.18) ** 2) / 0.0012)
    ambient = np.full(pulse.shape, 0.15)
    infrared = 0.35 + pulse
    red = 0.25 + 0.6 * pulse
    return np.column_stack((ambient, infrared, red))


class SyntheticInput:
    name = "synthetic"

    def __init__(self, fs: float = 256.0, scale: float = 1.0, bpm: float = 72.0) -> None:
        self.fs = float(fs)
        self.scale = float(scale)
        self.bpm = float(bpm)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._rng = np.random.default_rng()

    async def start(self, emit) -> None:
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(emit), name="synthetic-input")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None

    async def _run(self, emit) -> None:
        fs = self.fs
        ppg_fs = 64.0
        motion_fs = 52.0
        mono = time.monotonic()
        wall0 = time.time()
        n_eeg = 0
        n_ppg = 0
        n_motion = 0
        next_quality = 0.0
        next_blink = 8.0
        try:
            while not self._stop.is_set():
                elapsed = time.monotonic() - mono
                target_eeg = int(elapsed * fs)
                if target_eeg - n_eeg > int(fs):
                    n_eeg = target_eeg - int(fs)
                while n_eeg < target_eeg:
                    count = min(32, target_eeg - n_eeg)
                    idx = np.arange(n_eeg, n_eeg + count)
                    t_rel = idx / fs
                    block = render_eeg(t_rel, self.scale)
                    block = block + self._rng.normal(0.0, 1.2, size=block.shape)
                    for offset in range(count):
                        values = tuple(float(v) for v in block[offset])
                        emit(Sample(wall0 + float(t_rel[offset]), "eeg", EEG_CHANNELS, values, fs))
                    n_eeg += count
                    if count == 32:
                        await asyncio.sleep(0)
                target_ppg = int(elapsed * ppg_fs)
                if target_ppg > n_ppg:
                    idx = np.arange(n_ppg, target_ppg)
                    block = render_ppg(idx / ppg_fs, self.bpm)
                    for offset, row in enumerate(block):
                        t_rel = (n_ppg + offset) / ppg_fs
                        emit(
                            Sample(
                                wall0 + t_rel,
                                "ppg",
                                PPG_CHANNELS,
                                (float(row[0]), float(row[1]), float(row[2])),
                                ppg_fs,
                            )
                        )
                    n_ppg = target_ppg
                target_motion = int(elapsed * motion_fs)
                while n_motion < target_motion:
                    t_rel = n_motion / motion_fs
                    acc = (
                        0.05 * math.sin(2 * math.pi * 0.3 * t_rel),
                        0.04 * math.sin(2 * math.pi * 0.2 * t_rel),
                        0.98,
                    )
                    gyro = (
                        2.0 * math.sin(2 * math.pi * 0.25 * t_rel),
                        1.5 * math.sin(2 * math.pi * 0.18 * t_rel),
                        0.4 * math.sin(2 * math.pi * 0.15 * t_rel),
                    )
                    emit(Sample(wall0 + t_rel, "acc", ACC_CHANNELS, acc, motion_fs))
                    emit(Sample(wall0 + t_rel, "gyro", ACC_CHANNELS, gyro, motion_fs))
                    n_motion += 1
                if elapsed >= next_quality:
                    emit(Sample(wall0 + elapsed, "quality", EEG_CHANNELS, (1.0, 1.0, 1.0, 1.0), 2.0))
                    next_quality += 0.5
                if elapsed >= next_blink:
                    emit(Event(wall0 + elapsed, "blink", "blink", source="synthetic", color="#f2d479"))
                    next_blink += 8.0
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise
