"""Samples and events that move from an input to the pipeline and the recorder."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Sample:
    """One time point from a single stream.

    ``t`` is seconds since the Unix epoch. ``rate`` is the nominal stream rate
    in Hz when the input knows it.
    """

    t: float
    stream: str
    channels: tuple[str, ...]
    values: tuple[float, ...]
    rate: float | None = None


@dataclass(slots=True)
class Event:
    """A marker or a device event (blink, jaw clench)."""

    t: float
    kind: str
    name: str
    source: str = "device"
    color: str = ""
    osc: int | None = None
