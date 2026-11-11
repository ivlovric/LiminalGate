"""Biofeedback mapping and PPG heart rate."""

import numpy as np
from liminalgate.feedback import FeedbackEngine, gate_factor, map_range
from liminalgate.heartrate import HeartRateEstimator
from liminalgate.inputs.synthetic import render_ppg
from liminalgate.profiles import FeedbackRule


def test_map_and_gate():
    assert map_range(0.5, 0.0, 1.0, 0.0, 10.0) == 5.0
    assert map_range(-1.0, 0.0, 1.0, 0.0, 10.0) == 0.0
    assert gate_factor(0.0, 0.4, "mute_above") == 1.0
    assert gate_factor(0.4, 0.4, "mute_above") == 0.0
    assert gate_factor(0.2, 0.4, "mute_above") == 0.5


def test_volume_follows_metric_and_fades():
    rules = [
        FeedbackRule(
            metric="theta_peak",
            source="relative",
            param="volume",
            in_min=0.0,
            in_max=1.0,
            out_min=0.0,
            out_max=1.0,
            smooth=1.0,
            gate_metric="alpha",
            gate_source="relative",
            gate_threshold=0.4,
            gate_mode="mute_above",
        )
    ]
    engine = FeedbackEngine(rules)
    loud = _frame(theta=0.8, alpha=0.0)
    quiet = _frame(theta=0.8, alpha=0.4)
    assert engine.update(loud)["volume"] == 0.8
    assert engine.update(quiet)["volume"] == 0.0


def test_heart_rate_from_synthetic_pulse():
    fs = 64.0
    t = np.arange(0, 12.0, 1.0 / fs)
    pulse = render_ppg(t, bpm=60.0)
    estimator = HeartRateEstimator(fs=fs)
    for value in pulse[:, 1]:
        estimator.push(float(value))
    bpm = estimator.estimate()
    assert bpm is not None
    assert 55 <= bpm <= 65


def test_multichannel_ppg_picks_the_pulsatile_channel():
    fs = 64.0
    t = np.arange(0, 12.0, 1.0 / fs)
    pulse = render_ppg(t, bpm=72.0)
    rng = np.random.default_rng(0)
    extra = 0.4 + 0.05 * rng.normal(size=pulse.shape[0])
    estimator = HeartRateEstimator(fs=fs)
    assert estimator.status()["state"] == "no_ppg"
    for row, noise in zip(pulse, extra, strict=True):
        estimator.push_frame(("ambient", "ir", "red", "ch4"), (*row.tolist(), float(noise)))
    snap = estimator.status(force=True)
    assert snap["state"] == "ready"
    assert snap["channel"] in {"ir", "red"}
    assert snap["bpm"] is not None
    assert 66 <= snap["bpm"] <= 78
    short = HeartRateEstimator(fs=fs)
    short.push_frame(("ir",), (0.4,))
    assert short.status(force=True)["state"] == "computing"
    estimator.set_device_bpm(88.0, 1.0)
    override = estimator.status(force=True)
    assert override["source"] == "device"
    assert override["bpm"] == 88.0


def _frame(theta: float, alpha: float) -> dict:
    return {
        "channels": ["TP9"],
        "bands": {
            "theta_peak": {
                "absolute": [theta],
                "relative": [theta],
                "absolute_mean": theta,
                "relative_mean": theta,
            },
            "alpha": {
                "absolute": [alpha],
                "relative": [alpha],
                "absolute_mean": alpha,
                "relative_mean": alpha,
            },
        },
        "ratios": {},
    }
