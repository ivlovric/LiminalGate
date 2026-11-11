"""EEG window, custom bands, ratios, and a display spectrum."""

from __future__ import annotations

import math

import numpy as np

from liminalgate.expressions import ExpressionError, eval_expr
from liminalgate.profiles import Profile
from liminalgate.ring_buffer import RingBuffer
from liminalgate.spectrum import integrate_band, welch_psd

_BAD_FIT = 4.0
# Unsigned ADC rails. Classic headsets reach the FAQ's 1682 µV; Athena's
# 14-bit front end rails near 1450 µV (16383 * 0.0885). Anything at either
# rail is not EEG.
_LOW_RAIL = 1.0
_HIGH_RAIL = 1400.0
# Mind Monitor's default absolute graph: five bands, drawn as log10 power (Bels).
TIMELINE_BANDS = (
    ("delta", 1.0, 4.0, "#8b7cf6"),
    ("theta", 4.0, 8.0, "#7aa2ff"),
    ("alpha", 8.0, 12.0, "#5dcaa5"),
    ("beta", 13.0, 30.0, "#e0b15a"),
    ("gamma", 30.0, 45.0, "#e07a8a"),
)


class Pipeline:
    def __init__(self, profile: Profile, fs: float) -> None:
        self.profile = profile
        self.fs = float(fs)
        self.buffer = RingBuffer(capacity=max(256, int(self.fs * 16)))
        self.quality: list[float] | None = None
        self.quality_map: dict[str, float] = {}
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
        self.quality_map = {}
        self.device_absolute = {}

    def add_eeg(self, t: float, channels: tuple[str, ...], values: tuple[float, ...]) -> None:
        self.buffer.append(t, channels, values)

    def set_quality(self, channels: tuple[str, ...], values: tuple[float, ...]) -> None:
        self.quality_map = {str(name): float(value) for name, value in zip(channels, values, strict=False)}
        self.quality = [float(value) for value in values]

    def set_device_band(self, channels: tuple[str, ...], values: tuple[float, ...]) -> None:
        if len(channels) == 1:
            self.device_absolute[channels[0]] = [float(values[0])]
            return
        # Channel labels arrive as "delta_TP9" or just the four values in order.
        band = channels[0].split("_", 1)[0]
        self.device_absolute[band] = [float(value) for value in values]

    def compute(self) -> dict | None:
        needed = int(self.fs * self.profile.window_sec)
        window = self.buffer.latest(needed)
        if window is None:
            return None
        times, data, channels = window
        if data.shape[0] < max(16, int(self.fs * 0.5)):
            return None
        block, names, good = _prepare(data, channels, self.quality_map)
        spectrum = welch_psd(block, self.fs, segment_sec=2.0)
        if spectrum is None:
            return None
        freqs, psd = spectrum
        mask = good
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
            abs_mean = _masked_mean(absolute, mask)
            rel_mean = _masked_mean(relative, mask)
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
            for index in range(len(names)):
                bindings = _names_for_channel(bands, index)
                try:
                    per_channel.append(float(eval_expr(ratio.expression, bindings)))
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
        display = _display_spectrum(self)
        excluded = [name for name, keep in zip(names, good, strict=False) if not keep]
        return {
            "t": float(times[-1]),
            "channels": list(channels),
            "bands": bands,
            "ratios": ratios,
            "spectrum": display,
            "timeline": _timeline(freqs, psd, good, self.device_absolute),
            "excluded": excluded,
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


def _is_aux(name: str) -> bool:
    return name.upper().startswith("AUX")


def _prepare(
    data: np.ndarray,
    channels: tuple[str, ...],
    quality: dict[str, float],
) -> tuple[np.ndarray, list[str], np.ndarray]:
    """EEG columns only, with rail hits interpolated out, plus a good-channel mask."""

    indexes = [index for index, name in enumerate(channels) if not _is_aux(name)]
    if not indexes:
        indexes = list(range(data.shape[1]))
    names = [channels[index] for index in indexes]
    block = np.array(data[:, indexes], dtype=np.float64, copy=True)
    clipped = np.zeros(block.shape, dtype=bool)
    for column in range(block.shape[1]):
        column_values = block[:, column]
        # Unsigned Muse ADC sits near 800 µV. A zero-centered stream (synthetic,
        # playback of an already-referenced file) crosses 0 and must not be
        # treated as a low-rail clip.
        unsigned = bool(np.nanmedian(column_values) > 200.0)
        bad = ~np.isfinite(column_values) | (column_values >= _HIGH_RAIL)
        if unsigned:
            bad = bad | (column_values <= _LOW_RAIL)
        clipped[:, column] = bad
        if not np.any(bad):
            continue
        good_rows = np.flatnonzero(~bad)
        if good_rows.size == 0:
            block[:, column] = 0.0
            continue
        block[bad, column] = np.interp(np.flatnonzero(bad), good_rows, block[good_rows, column])
    deviation = np.std(block, axis=0)
    clip_fraction = np.mean(clipped, axis=0)
    keep = np.ones(len(names), dtype=bool)
    for index, name in enumerate(names):
        horse = quality.get(name)
        horse_bad = horse is not None and horse >= _BAD_FIT
        flat = bool(deviation[index] < 1.0)
        rail = bool(clip_fraction[index] > 0.02)
        keep[index] = not (horse_bad or flat or rail)
    return block, names, keep


def _masked_mean(values: np.ndarray, keep: np.ndarray) -> float:
    if keep.shape[0] != values.shape[0] or not bool(np.any(keep)):
        return float("nan")
    return float(np.mean(values[keep]))


def _timeline(
    freqs: np.ndarray,
    psd: np.ndarray,
    keep: np.ndarray,
    device: dict[str, list[float]],
) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for name, low, high, color in TIMELINE_BANDS:
        power = _masked_mean(integrate_band(freqs, psd, low, high), keep)
        log_power = None if not math.isfinite(power) or power <= 0 else math.log10(power)
        device_values = [value for value in device.get(name, []) if math.isfinite(value)]
        device_mean = float(sum(device_values) / len(device_values)) if device_values else None
        rows[name] = {"log10": log_power, "device": device_mean, "color": color, "f_low": low, "f_high": high}
    return rows


def _display_spectrum(pipeline: Pipeline) -> dict[str, list]:
    window = pipeline.buffer.latest(int(pipeline.fs * 8))
    empty: dict[str, list] = {"freqs": [], "psd_log10": []}
    if window is None:
        return empty
    _times, data, channels = window
    if data.shape[0] < max(16, int(pipeline.fs * 0.5)):
        return empty
    block, _names, keep = _prepare(data, channels, pipeline.quality_map)
    spectrum = welch_psd(block, pipeline.fs, segment_sec=4.0)
    if spectrum is None:
        return empty
    freqs, psd = spectrum
    if not bool(np.any(keep)):
        return empty
    mean_psd = np.mean(psd[:, keep], axis=1)
    selected = (freqs >= 1.0) & (freqs <= 45.0)
    return {
        "freqs": freqs[selected].tolist(),
        "psd_log10": np.log10(np.maximum(mean_psd[selected], 1e-12)).tolist(),
    }


def _names_for_means(absolute: dict[str, float], relative: dict[str, float]) -> dict[str, float]:
    names: dict[str, float] = {}
    for name, value in absolute.items():
        names[name] = value
        names[f"{name}_abs"] = value
        names[f"{name}_rel"] = relative[name]
    return names
