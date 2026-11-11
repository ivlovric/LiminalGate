"""Display filter, gap breaks, and bursty Mind Monitor packets."""

import numpy as np
from liminalgate.display_filter import DisplayFilter, gap_before
from liminalgate.hub import _without_aux
from liminalgate.inputs.osc_mindmonitor import SampleClock, parse_message
from liminalgate.pipeline import Pipeline
from liminalgate.profiles import builtin_profiles


def test_filtered_trace_centers_an_offset_signal_and_marks_gaps():
    fs = 256.0
    filt = DisplayFilter(fs, powerline_hz=50.0)
    duration = 3.0
    times = np.arange(0.0, duration, 1.0 / fs)
    # 800 µV is the unsigned ADC midpoint. A 10 Hz sine should survive the 1–45 Hz pass.
    signal = 800.0 + 20.0 * np.sin(2 * np.pi * 10.0 * times)
    filtered = [filt.apply(("TP9",), (float(value),))[0] for value in signal]
    settled = np.asarray(filtered[int(fs) :])
    assert abs(float(np.mean(settled))) < 5.0
    assert float(np.std(settled)) > 8.0

    # A pause in the timestamps is a gap. The filter is not asked to invent samples across it.
    streamed = [float(times[0]), float(times[1]), float(times[2]), float(times[2]) + 0.2]
    flags = [gap_before(streamed[index - 1] if index else None, stamp, fs) for index, stamp in enumerate(streamed)]
    assert flags == [False, False, False, True]


def test_bursty_osc_preserves_order_and_sample_spacing():
    clock = SampleClock(256.0)
    now = 1_000.0
    parsed = []
    for index in range(12):
        parsed.extend(parse_message("/muse/eeg", (float(index), 0.0, 0.0, 0.0), fs=256.0, now=now, clock=clock))
    assert [item.values[0] for item in parsed] == [float(index) for index in range(12)]
    deltas = np.diff([item.t for item in parsed])
    assert np.allclose(deltas, 1.0 / 256.0)

    # A real pause starts a new segment instead of stretching the previous clock across the hole.
    later = parse_message("/muse/eeg", (99.0, 0.0, 0.0, 0.0), fs=256.0, now=now + 0.25, clock=clock)
    assert later[0].t > parsed[-1].t + 0.1

    # Identical datagram inside the same instant is a duplicate, not another sample.
    again = parse_message("/muse/eeg", (99.0, 0.0, 0.0, 0.0), fs=256.0, now=now + 0.25 + 1e-5, clock=clock)
    assert again == []


def test_sample_clock_never_steps_backwards_after_a_long_burst():
    clock = SampleClock(256.0)
    now = 1_000.0
    parsed = []
    for index in range(20):
        parsed.extend(parse_message("/muse/eeg", (float(index), 0.0, 0.0, 0.0), fs=256.0, now=now, clock=clock))
    # Twenty samples are ~78 ms of timeline, but the wall clock has not moved.
    # The next datagram 60 ms later used to snap back under the last sample.
    later = parse_message("/muse/eeg", (20.0, 0.0, 0.0, 0.0), fs=256.0, now=now + 0.06, clock=clock)
    assert later[0].t >= parsed[-1].t + 0.5 / 256.0
    stamps = [item.t for item in parsed] + [later[0].t]
    assert all(right > left for left, right in zip(stamps, stamps[1:], strict=False))


def test_established_width_keeps_aux_from_rotating_into_eeg():
    clock = SampleClock(256.0)
    first = parse_message("/muse/eeg", (1, 2, 3, 4, 5), fs=256.0, now=5.0, clock=clock)
    assert first[0].channels == ("TP9", "AF7", "AF8", "TP10", "AUX1")
    # 20 floats are four samples of 5, and also five samples of 4. The live width decides.
    batch = parse_message("/muse/eeg", tuple(range(20)), fs=256.0, now=5.1, clock=clock)
    assert len(batch) == 4
    assert batch[0].values == (0.0, 1.0, 2.0, 3.0, 4.0)
    assert batch[1].values == (5.0, 6.0, 7.0, 8.0, 9.0)
    assert batch[1].channels[-1] == "AUX1"
    assert batch[-1].t > batch[0].t


def test_aux_channels_leave_the_display_lanes():
    channels, values = _without_aux(
        ("TP9", "AF7", "AF8", "TP10", "AUX1", "AUX4"),
        (1.0, 2.0, 3.0, 4.0, 800.0, 1450.0),
    )
    assert channels == ("TP9", "AF7", "AF8", "TP10")
    assert values == (1.0, 2.0, 3.0, 4.0)


def test_is_good_maps_onto_horseshoe_bad():
    quality = parse_message("/muse/elements/is_good", (1, 0, 1, 0), now=1.0)
    assert quality[0].stream == "quality"
    assert quality[0].values == (1.0, 4.0, 1.0, 4.0)


def test_railed_channel_is_left_out_of_the_mean_spectrum():
    fs = 256.0
    t = np.arange(0.0, 4.0, 1.0 / fs)
    clean = 800.0 + 30.0 * np.sin(2 * np.pi * 10.0 * t)
    # Athena full scale is ~1450 µV. A reset every 12 samples is 256/12 = 21.333 Hz.
    saw = 800.0 + 200.0 * ((np.arange(t.size) % 12) / 11.0)
    saw[::12] = 1450.0
    profile = builtin_profiles()["classic"]
    pipeline = Pipeline(profile, fs)
    channels = ("TP9", "AF7", "AF8", "TP10")
    for index, stamp in enumerate(t):
        pipeline.add_eeg(
            float(stamp),
            channels,
            (float(clean[index]), float(saw[index]), float(clean[index]), float(clean[index])),
        )
    frame = pipeline.compute()
    assert frame is not None
    assert "AF7" in frame["excluded"]
    assert "TP9" not in frame["excluded"]
    alpha = frame["bands"]["alpha"]["relative_mean"]
    assert alpha > 0.2
    spectrum = frame["spectrum"]
    freqs = np.asarray(spectrum["freqs"])
    assert freqs.size and float(freqs.min()) >= 1.0 and float(freqs.max()) <= 45.0
