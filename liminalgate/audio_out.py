"""Optional host-side tone through ALSA (``aplay``). Off unless enabled.

The browser Web Audio tone is the primary biofeedback path. This writer is
for a speaker plugged into the machine that runs the gateway.
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import threading

logger = logging.getLogger("liminalgate.audio")

_RATE = 22050


def render_pcm(frequency: float, volume: float, n: int, phase: float, rate: int = _RATE) -> tuple[bytes, float]:
    """Return signed 16-bit mono PCM and the advanced oscillator phase."""

    if n <= 0:
        return b"", phase
    amplitude = max(0.0, min(1.0, volume)) * 0.25 * 32767.0
    step = 2.0 * math.pi * max(40.0, min(2000.0, frequency)) / rate
    samples = bytearray(n * 2)
    angle = phase
    for index in range(n):
        value = int(amplitude * math.sin(angle))
        samples[2 * index] = value & 0xFF
        samples[2 * index + 1] = (value >> 8) & 0xFF
        angle += step
    return bytes(samples), angle % (2.0 * math.pi)


class ServerTone:
    def __init__(self) -> None:
        self.enabled = False
        self.volume = 0.0
        self.frequency = 220.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None

    def update(self, volume: float, frequency: float) -> None:
        self.volume = max(0.0, min(1.0, float(volume)))
        self.frequency = max(40.0, min(2000.0, float(frequency)))

    def enable(self) -> None:
        if self.enabled:
            return
        if shutil.which("aplay") is None:
            raise RuntimeError("aplay is not installed; host audio stays off")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="liminalgate-tone", daemon=True)
        self._thread.start()
        self.enabled = True
        logger.info("server_audio_on")

    def disable(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None
        self._proc = None
        self.enabled = False

    def _run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                ["aplay", "-q", "-f", "S16_LE", "-c", "1", "-r", str(_RATE), "-"],
                stdin=subprocess.PIPE,
            )
        except OSError as exc:
            logger.warning("server_audio_failed", extra={"error": str(exc)})
            self.enabled = False
            return
        phase = 0.0
        chunk = _RATE // 20
        stdin = self._proc.stdin
        assert stdin is not None
        try:
            while not self._stop.is_set():
                payload, phase = render_pcm(self.frequency, self.volume, chunk, phase)
                stdin.write(payload)
                stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.warning("server_audio_stopped", extra={"error": str(exc)})
        finally:
            try:
                stdin.close()
            except OSError:
                pass
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
            self.enabled = False
