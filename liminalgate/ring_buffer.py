"""Fixed-capacity sample buffer used for the spectral window."""

from __future__ import annotations

import numpy as np


class RingBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity < 16:
            raise ValueError("capacity must be at least 16")
        self.capacity = int(capacity)
        self.channels: tuple[str, ...] = ()
        self._times: np.ndarray | None = None
        self._data: np.ndarray | None = None
        self._pos = 0
        self._count = 0

    def reset(self) -> None:
        self.channels = ()
        self._times = None
        self._data = None
        self._pos = 0
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def append(self, t: float, channels: tuple[str, ...], values: tuple[float, ...]) -> None:
        if len(values) != len(channels) or not channels:
            return
        if channels != self.channels or self._data is None or self._times is None:
            self.channels = channels
            self._data = np.zeros((self.capacity, len(channels)), dtype=np.float64)
            self._times = np.zeros(self.capacity, dtype=np.float64)
            self._pos = 0
            self._count = 0
        self._data[self._pos, :] = values
        self._times[self._pos] = t
        self._pos = (self._pos + 1) % self.capacity
        self._count = min(self.capacity, self._count + 1)

    def latest(self, n: int) -> tuple[np.ndarray, np.ndarray, tuple[str, ...]] | None:
        """Return the newest ``n`` rows in chronological order."""

        if self._count == 0 or self._data is None or self._times is None:
            return None
        n = min(int(n), self._count)
        if self._pos >= n:
            return (
                self._times[self._pos - n : self._pos].copy(),
                self._data[self._pos - n : self._pos].copy(),
                self.channels,
            )
        first = self.capacity - (n - self._pos)
        times = np.concatenate((self._times[first:], self._times[: self._pos]))
        data = np.concatenate((self._data[first:], self._data[: self._pos]), axis=0)
        return times, data, self.channels
