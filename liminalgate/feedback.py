"""Map band and ratio values onto tone volume and frequency.

Volume rules multiply. A gate fades a volume rule to silence instead of
cutting it off in one hop: ``mute_above`` is 1 at zero and 0 at the threshold.
"""

from __future__ import annotations

import math

from liminalgate.profiles import FeedbackRule


def metric_value(frame: dict, metric: str, source: str, scope: str) -> float | None:
    ratios = frame.get("ratios") or {}
    if metric in ratios:
        payload = ratios[metric]
        if scope == "mean":
            return _finite(payload.get("mean"))
        channels = frame.get("channels") or []
        if scope in channels:
            return _finite(payload["per_channel"][channels.index(scope)])
        return None
    bands = frame.get("bands") or {}
    if metric not in bands:
        return None
    payload = bands[metric]
    if scope == "mean":
        key = "relative_mean" if source == "relative" else "absolute_mean"
        return _finite(payload.get(key))
    channels = frame.get("channels") or []
    if scope not in channels:
        return None
    series_key = "relative" if source == "relative" else "absolute"
    return _finite(payload[series_key][channels.index(scope)])


def map_range(value: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    if in_max == in_min:
        return out_min
    span = (value - in_min) / (in_max - in_min)
    span = min(1.0, max(0.0, span))
    return out_min + span * (out_max - out_min)


def gate_factor(value: float, threshold: float | None, mode: str) -> float:
    if mode in {"", "none"} or threshold is None or threshold == 0:
        return 1.0
    if mode == "mute_above":
        return min(1.0, max(0.0, (threshold - value) / threshold))
    if mode == "mute_below":
        return min(1.0, max(0.0, value / threshold))
    return 1.0


class FeedbackEngine:
    def __init__(self, rules: list[FeedbackRule]) -> None:
        self.rules = list(rules)
        self._ema: list[float | None] = [None] * len(self.rules)

    def set_rules(self, rules: list[FeedbackRule]) -> None:
        self.rules = list(rules)
        self._ema = [None] * len(self.rules)

    def update(self, frame: dict) -> dict[str, float | bool]:
        volume = 1.0
        have_volume = False
        frequency = 220.0
        for index, rule in enumerate(self.rules):
            raw = metric_value(frame, rule.metric, rule.source, rule.scope)
            if raw is None:
                continue
            previous = self._ema[index]
            alpha = rule.smooth if rule.smooth > 0 else 1.0
            smoothed = raw if previous is None else alpha * raw + (1.0 - alpha) * previous
            self._ema[index] = smoothed
            mapped = map_range(smoothed, rule.in_min, rule.in_max, rule.out_min, rule.out_max)
            factor = 1.0
            if rule.gate_metric and rule.gate_mode != "none":
                gate_raw = metric_value(frame, rule.gate_metric, rule.gate_source, rule.gate_scope)
                if gate_raw is not None:
                    factor = gate_factor(gate_raw, rule.gate_threshold, rule.gate_mode)
            if rule.param == "volume":
                volume *= max(0.0, mapped) * factor
                have_volume = True
            elif rule.param == "frequency":
                frequency = mapped
        if not have_volume:
            volume = 0.0
        volume = min(1.0, max(0.0, volume))
        frequency = min(2000.0, max(40.0, frequency))
        return {"volume": volume, "frequency": frequency, "muted": volume <= 0.001}


def _finite(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number
