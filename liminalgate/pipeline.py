"""EEG window, custom bands, ratios, and a display spectrum."""

from __future__ import annotations

import numpy as np

from liminalgate.expressions import ExpressionError, eval_expr
from liminalgate.profiles import Profile
from liminalgate.ring_buffer import RingBuffer
from liminalgate.spectrum import integrate_band, welch_psd

_BAD_FIT = 4.0


class Pipeline:
    def __init__(self, profile: Profile, fs: float) -> None:
        self.profile = profile
        self.fs = float(fs)
        self.buffer = RingBuffer(capacity=max(256, int(self.fs * 16)))
        self.quality: list[float] | None = None
        self.device_absolute: dict[str, list[float]] = {}

    def set_fs(self, fs: float) -> None:
        if fs <= 0 or abs(fs - self.fs) < 0.01:
            return
        self.fs = float(fs)
        self.buffer = RingBuffer(capacity=max(256, int(self.fs * 16)))

    def reset(self, fs: float | None = None) -> None:
        if fs is not None:
            self.fs = float(fs)
        self.buffer = RingBuffer(capacity=max(256, int(self.fs * 16)))
        self.quality = None
        self.device_absolute = {}

    def add_eeg(self, t: float, channels: tuple[str, ...], values: tuple[float, ...]) -> None:
        self.buffer.append(t, channels, values)

    def set_quality(self, values: tuple[float, ...]) -> None:
        self.quality = [float(value) for value in values]

    def set_device_band(self, channels: tuple[str, ...], values: tuple[float, ...]) -> None:
        if len(channels) == 1:
            self.device_absolute[channels[0]] = [float(values[0])]
            return
        # Channel labels arrive as "delta_TP9" or just the four values in order.
        band = channels[0].split("_", 1)[0]
        self.device_absolute[band] = [float(value) for value in values]

    def _good_mask(self, n_channels: int) -> np.ndarray:
        mask = np.ones(n_channels, dtype=bool)
        if self.quality:
            for index, value in enumerate(self.quality[:n_channels]):
                if value >= _BAD_FIT:
                    mask[index] = False
        if not bool(mask.any()):
            mask[:] = True
        return mask

    def compute(self) -> dict | None:
        needed = int(self.fs * self.profile.window_sec)
        window = self.buffer.latest(needed)
        if window is None:
            return None
        times, data, channels = window
        if data.shape[0] < max(16, int(self.fs * 0.5)):
            return None
        spectrum = welch_psd(data, self.fs)
        if spectrum is None:
            return None
        freqs, psd = spectrum
        mask = self._good_mask(data.shape[1])
        low, high = self.profile.relative_range
        total = integrate_band(freqs, psd, low, high)
        bands: dict[str, dict] = {}
        absolute_means: dict[str, float] = {}
        relative_means: dict[str, float] = {}
        for band in self.profile.bands:
            absolute = integrate_band(freqs, psd, band.f_low, band.f_high)
            relative = np.divide(
                absolute,
                total,
                out=np.zeros_like(absolute),
                where=total > 1e-18,
            )
            abs_mean = float(np.mean(absolute[mask]))
            rel_mean = float(np.mean(relative[mask]))
            absolute_means[band.name] = abs_mean
            relative_means[band.name] = rel_mean
            bands[band.name] = {
                "absolute": absolute.tolist(),
                "relative": relative.tolist(),
                "absolute_mean": abs_mean,
                "relative_mean": rel_mean,
                "f_low": band.f_low,
                "f_high": band.f_high,
                "color": band.color,
            }
        ratios: dict[str, dict] = {}
        for ratio in self.profile.ratios:
            per_channel: list[float] = []
            for index in range(data.shape[1]):
                names = _names_for_channel(bands, index)
                try:
                    per_channel.append(float(eval_expr(ratio.expression, names)))
                except (ExpressionError, ValueError, ZeroDivisionError):
                    per_channel.append(float("nan"))
            mean_names = _names_for_means(absolute_means, relative_means)
            try:
                mean_value = float(eval_expr(ratio.expression, mean_names))
            except (ExpressionError, ValueError, ZeroDivisionError):
                mean_value = float("nan")
            ratios[ratio.name] = {
                "expression": ratio.expression,
                "per_channel": per_channel,
                "mean": mean_value,
            }
        kept = freqs <= 60.0
        mean_psd = np.mean(psd[:, mask], axis=1)
        log_psd = np.log10(np.maximum(mean_psd[kept], 1e-12))
        return {
            "t": float(times[-1]),
            "channels": list(channels),
            "bands": bands,
            "ratios": ratios,
            "spectrum": {"freqs": freqs[kept].tolist(), "psd_log10": log_psd.tolist()},
            "quality": list(self.quality) if self.quality is not None else None,
            "device_absolute": {key: list(value) for key, value in self.device_absolute.items()},
        }


def _names_for_channel(bands: dict[str, dict], index: int) -> dict[str, float]:
    names: dict[str, float] = {}
    for name, payload in bands.items():
        names[name] = float(payload["absolute"][index])
        names[f"{name}_abs"] = names[name]
        names[f"{name}_rel"] = float(payload["relative"][index])
    return names


def _names_for_means(absolute: dict[str, float], relative: dict[str, float]) -> dict[str, float]:
    names: dict[str, float] = {}
    for name, value in absolute.items():
        names[name] = value
        names[f"{name}_abs"] = value
        names[f"{name}_rel"] = relative[name]
    return names
