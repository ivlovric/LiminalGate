"""Band power, ratios, and the ring buffer."""

import numpy as np
from liminalgate.expressions import ExpressionError, eval_expr
from liminalgate.inputs.synthetic import render_eeg
from liminalgate.pipeline import Pipeline
from liminalgate.profiles import builtin_profiles
from liminalgate.ring_buffer import RingBuffer


def test_expression_arithmetic_and_names():
    assert eval_expr("theta/delta", {"theta": 4.0, "delta": 2.0}) == 2.0
    assert eval_expr("(a + b) * 2", {"a": 1.0, "b": 2.0}) == 6.0
    assert np.isnan(eval_expr("a/b", {"a": 1.0, "b": 0.0}))
    try:
        eval_expr("nope", {"a": 1.0})
    except ExpressionError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected ExpressionError")


def test_ring_buffer_wraps_in_order():
    ring = RingBuffer(16)
    for index in range(20):
        ring.append(float(index), ("A",), (float(index),))
    times, data, channels = ring.latest(4)
    assert channels == ("A",)
    assert times.tolist() == [16.0, 17.0, 18.0, 19.0]
    assert data[:, 0].tolist() == [16.0, 17.0, 18.0, 19.0]


def test_sine_lands_in_theta_peak_not_alpha():
    fs = 256.0
    t = np.arange(0, 4.0, 1.0 / fs)
    # 20 µV peak at 6 Hz on channel 0. Power is about A²/2 = 200 µV².
    data = np.column_stack(
        [
            20.0 * np.sin(2 * np.pi * 6.0 * t),
            np.zeros_like(t),
            np.zeros_like(t),
            np.zeros_like(t),
        ]
    )
    profile = builtin_profiles()["hypnagogic"]
    pipeline = Pipeline(profile, fs)
    for index in range(data.shape[0]):
        pipeline.add_eeg(t[index], ("TP9", "AF7", "AF8", "TP10"), tuple(float(v) for v in data[index]))
    frame = pipeline.compute()
    assert frame is not None
    peak = frame["bands"]["theta_peak"]["absolute"][0]
    alpha = frame["bands"]["alpha"]["absolute"][0]
    theta = frame["bands"]["theta"]["absolute"][0]
    assert 80 < peak < 400
    assert peak > alpha * 5
    assert theta > alpha * 5


def test_synthetic_mix_is_theta_dominant_and_ratio_is_finite():
    fs = 256.0
    t = np.arange(0, 4.0, 1.0 / fs)
    data = render_eeg(t, scale=1.0)
    profile = builtin_profiles()["hypnagogic"]
    pipeline = Pipeline(profile, fs)
    channels = ("TP9", "AF7", "AF8", "TP10")
    for index in range(0, data.shape[0], 1):
        pipeline.add_eeg(float(t[index]), channels, tuple(float(v) for v in data[index]))
    frame = pipeline.compute()
    assert frame is not None
    assert frame["bands"]["theta_peak"]["relative_mean"] > frame["bands"]["alpha"]["relative_mean"]
    ratio = frame["ratios"]["theta_over_delta"]["mean"]
    assert ratio > 1.0
    assert np.isfinite(ratio)
