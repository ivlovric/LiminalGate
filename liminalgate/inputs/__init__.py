"""Input plugins. One is active at a time."""

from __future__ import annotations

from collections.abc import Callable

from liminalgate.errors import InputError
from liminalgate.inputs.brainflow_input import BrainFlowInput
from liminalgate.inputs.lsl_input import LSLInput
from liminalgate.inputs.osc_mindmonitor import OscMindMonitorInput
from liminalgate.inputs.playback import PlaybackInput
from liminalgate.inputs.synthetic import SyntheticInput
from liminalgate.settings import Settings
from liminalgate.signals import Event, Sample

Emit = Callable[[Sample | Event], None]


def build_input(settings: Settings) -> SyntheticInput | OscMindMonitorInput | PlaybackInput | BrainFlowInput | LSLInput:
    kind = settings.input_type
    scale = settings.eeg_scale
    if kind == "synthetic":
        return SyntheticInput(fs=settings.eeg_fs, scale=scale)
    if kind == "osc":
        return OscMindMonitorInput(settings.osc_host, settings.osc_port, fs=settings.eeg_fs, scale=scale)
    if kind == "playback":
        if not settings.playback_path:
            raise InputError("playback_path is empty")
        return PlaybackInput(
            settings.playback_path,
            speed=settings.playback_speed,
            loop=settings.playback_loop,
            scale=scale,
            fallback_fs=settings.eeg_fs,
        )
    if kind == "brainflow":
        return BrainFlowInput(
            board=settings.brainflow_board,
            scale=scale,
            mac=settings.brainflow_mac,
            serial=settings.brainflow_serial,
            other_info=settings.brainflow_other_info,
        )
    if kind == "lsl":
        return LSLInput(name=settings.lsl_name, scale=scale, fallback_fs=settings.eeg_fs)
    raise InputError(f"unknown input {kind}")
