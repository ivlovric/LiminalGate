"""Welch PSD and band-power integration.

Narrow custom bands (for example 5.5–6 Hz) need a segment of at least two
seconds so the bin width is 0.5 Hz or better. The upper edge is inclusive so
a band written as 5.5–6 contains the 6 Hz bin.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import welch

_trapz = getattr(np, "trapezoid", None) or np.trapz


def welch_psd(
    data: np.ndarray,
    fs: float,
    segment_sec: float = 2.0,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return ``(freqs, psd)`` with ``psd`` shaped ``(n_freqs, n_channels)``.

    ``segment_sec`` is the Welch segment. Overlap is 50 percent. A 4 s segment
    on a longer window averages several segments, which is what the spectrum
    chart uses.
    """

    if data.ndim != 2 or data.shape[0] < 16 or fs <= 0:
        return None
    nperseg = min(data.shape[0], max(16, int(fs * segment_sec)))
    freqs, psd = welch(
        data,
        fs=fs,
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        scaling="density",
        detrend="constant",
        axis=0,
    )
    if psd.ndim == 1:
        psd = psd[:, None]
    return freqs, np.asarray(psd, dtype=np.float64)


def integrate_band(freqs: np.ndarray, psd: np.ndarray, f_low: float, f_high: float) -> np.ndarray:
    """Integrate PSD over ``[f_low, f_high]`` (both edges included).

    ``psd`` is ``(n_freqs,)`` or ``(n_freqs, n_channels)``. The result is one
    power value per channel, in signal-units squared (µV² when the input is µV).
    """

    single = psd.ndim == 1
    matrix = psd[:, None] if single else psd
    out = np.zeros(matrix.shape[1], dtype=np.float64)
    if f_high < f_low or freqs.size == 0:
        return out
    selected = np.flatnonzero((freqs >= f_low - 1e-9) & (freqs <= f_high + 1e-9))
    if selected.size >= 2:
        out = np.asarray(_trapz(matrix[selected], freqs[selected], axis=0), dtype=np.float64)
    elif selected.size == 1:
        df = float(np.median(np.diff(freqs))) if freqs.size > 1 else float(f_high - f_low)
        out = matrix[selected[0]] * df
    else:
        center = 0.5 * (f_low + f_high)
        nearest = int(np.argmin(np.abs(freqs - center)))
        out = matrix[nearest] * float(f_high - f_low)
    return out
