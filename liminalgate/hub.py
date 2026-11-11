"""Wires an input, the spectral pipeline, recording, and browser clients."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
from collections import deque
from datetime import UTC, datetime

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from liminalgate import __version__
from liminalgate.audio_out import ServerTone
from liminalgate.errors import InputError, ProfileError, SessionError
from liminalgate.feedback import FeedbackEngine
from liminalgate.heartrate import HeartRateEstimator
from liminalgate.inputs import build_input
from liminalgate.pipeline import Pipeline
from liminalgate.profiles import Profile, ProfileStore
from liminalgate.recording import SessionRecorder, list_sessions
from liminalgate.settings import CONFIG_FIELDS, Settings, save_config
from liminalgate.signals import Event, Sample

logger = logging.getLogger("liminalgate.hub")


def _jsonable(value: object) -> object:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 5)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, str | int | bool) or value is None:
        return value
    return str(value)


class _Sink:
    def __init__(self) -> None:
        self.items: deque[tuple[str, bool]] = deque()
        self.event = asyncio.Event()

    def push(self, raw: str, droppable: bool) -> None:
        if len(self.items) >= 48:
            for index, item in enumerate(self.items):
                if item[1]:
                    del self.items[index]
                    break
            else:
                self.items.popleft()
        self.items.append((raw, droppable))
        self.event.set()


class Hub:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = ProfileStore(settings.data_dir / "profiles")
        self.profile = self._load_initial_profile()
        self.pipeline = Pipeline(self.profile, settings.eeg_fs)
        self.feedback = FeedbackEngine(self.profile.feedback)
        self.hr = HeartRateEstimator(fs=64.0)
        self.tone = ServerTone()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=8000)
        self.lock = asyncio.Lock()
        self.clients: dict[WebSocket, _Sink] = {}
        self.recorder: SessionRecorder | None = None
        self.input = None
        self.input_running = False
        self.last_error: str | None = None
        self.samples_seen = 0
        self.dropped = 0
        self.loop: asyncio.AbstractEventLoop | None = None
        self._accepting = False
        self._consume_task: asyncio.Task[None] | None = None
        self._pending: list[tuple[float, tuple[float, ...]]] = []
        self._decim_count = 0
        self._decim = 4
        self._eeg_channels: tuple[str, ...] = ()
        self._hr_value: float | None = None
        self.battery: float | None = None
        self.touching: float | None = None
        self.last_feedback: dict | None = None
        self.recent_events: deque[dict] = deque(maxlen=40)
        self._last_device: dict[str, float] = {}

    def _load_initial_profile(self) -> Profile:
        try:
            return self.store.load(self.settings.profile)
        except ProfileError:
            logger.warning("profile_fallback", extra={"profile": self.settings.profile})
            return self.store.load("hypnagogic")

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._accepting = True
        self._decim = self._decimation()
        self._consume_task = asyncio.create_task(self._consume(), name="liminalgate-consume")
        await self.restart_input()
        self._sync_audio()
        logger.info(
            "hub_started",
            extra={"input": self.settings.input_type, "profile": self.profile.name},
        )

    async def stop(self) -> None:
        self._accepting = False
        await self.stop_input()
        with contextlib.suppress(SessionError):
            await self.stop_session(force=True)
        self.tone.disable()
        task = self._consume_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for socket in list(self.clients):
            with contextlib.suppress(Exception):
                await socket.close()
        self.clients.clear()

    def emit(self, item: Sample | Event) -> None:
        loop = self.loop
        if loop is None or not self._accepting:
            return
        try:
            loop.call_soon_threadsafe(self._enqueue, item)
        except RuntimeError:
            return

    def _enqueue(self, item: Sample | Event) -> None:
        if not self._accepting:
            return
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1

    async def restart_input(self) -> None:
        await self.stop_input()
        async with self.lock:
            self.pipeline.reset(fs=self.settings.eeg_fs)
            self.hr.reset()
            self.samples_seen = 0
            self.dropped = 0
            self._pending.clear()
            self._decim_count = 0
            self._decim = self._decimation()
            self.last_error = None
        try:
            plugin = build_input(self.settings)
            await plugin.start(self.emit)
            self.input = plugin
            self.input_running = True
        except Exception as exc:
            self.input = None
            self.input_running = False
            self.last_error = str(exc)
            logger.warning("input_start_failed", extra={"error": str(exc), "input": self.settings.input_type})
        self.publish(self.status(), droppable=False)

    async def stop_input(self) -> None:
        plugin = self.input
        self.input = None
        self.input_running = False
        if plugin is not None:
            await plugin.stop()

    async def apply_config(self, updates: dict) -> dict:
        if self.recorder is not None and self.recorder.active:
            raise SessionError("stop the recording before changing the input")
        merged = self.settings.public_config()
        merged.update({key: value for key, value in updates.items() if key in CONFIG_FIELDS})
        try:
            self.settings = self.settings.model_copy(update=merged)
        except ValidationError as exc:
            raise InputError(str(exc)) from exc
        save_config(self.settings)
        if self.settings.profile != self.profile.name:
            self.activate_profile(self.store.load(self.settings.profile))
        await self.restart_input()
        self._sync_audio()
        return self.settings.public_config()

    def activate_profile(self, profile: Profile) -> Profile:
        self.profile = profile
        self.pipeline.profile = profile
        self.feedback.set_rules(profile.feedback)
        self.settings = self.settings.model_copy(update={"profile": profile.name})
        save_config(self.settings)
        self.publish({"type": "profile", "profile": profile.model_dump(mode="json")}, droppable=False)
        return profile

    def save_profile(self, profile: Profile, activate: bool) -> Profile:
        self.store.save(profile)
        if activate or profile.name == self.profile.name:
            return self.activate_profile(profile)
        return profile

    async def start_session(self, subject: str, task: str, notes: str) -> dict:
        async with self.lock:
            if self.recorder is not None and self.recorder.active:
                raise SessionError("a session is already recording")
            metadata = {
                "device": {
                    "input": self.settings.input_type,
                    "eeg_scale": self.settings.eeg_scale,
                    "eeg_fs": self.pipeline.fs,
                    "brainflow_board": self.settings.brainflow_board,
                    "note": (
                        "eeg_scale multiplies raw EEG at the input. "
                        "BrainFlow and muse-lsl can differ by about 4.5x on Athena."
                    ),
                },
                "eeg_fs": self.pipeline.fs,
                "powerline_hz": self.settings.powerline_hz,
                "profile": self.profile.model_dump(mode="json"),
            }
            recorder = SessionRecorder(self.settings.data_dir, subject, task, notes, metadata)
            summary = recorder.start()
            self.recorder = recorder
        self.publish({"type": "session", "state": "started", **summary}, droppable=False)
        logger.info("session_started", extra={"path": summary["path"]})
        return summary

    async def stop_session(self, force: bool = False) -> dict | None:
        async with self.lock:
            recorder = self.recorder
            self.recorder = None
        if recorder is None:
            if force:
                return None
            raise SessionError("no active session")
        summary = await asyncio.to_thread(recorder.finish)
        self.publish({"type": "session", "state": "stopped", **summary}, droppable=False)
        logger.info("session_stopped", extra={"path": summary["path"], "samples": summary["eeg_samples"]})
        return summary

    def sessions(self) -> list[dict]:
        return list_sessions(self.settings.data_dir)

    async def add_marker(self, name: str, source: str) -> dict:
        cleaned = name.strip()
        if not cleaned:
            raise InputError("marker name is empty")
        async with self.lock:
            event = Event(time.time(), "marker", cleaned, source=source)
            match = next((item for item in self.profile.markers if item.name == cleaned), None)
            event.color = match.color if match else "#d4a0ff"
            self._publish_event(event)
        return {"name": event.name, "t": event.t, "color": event.color}

    async def handle_socket(self, socket: WebSocket) -> None:
        await socket.accept()
        sink = _Sink()
        self.clients[socket] = sink
        sender = asyncio.create_task(self._sender(socket, sink), name="ws-sender")
        sink.push(json.dumps(_jsonable(self._hello()), separators=(",", ":")), False)
        try:
            while True:
                raw = await socket.receive_text()
                await self._on_client(raw)
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            self.clients.pop(socket, None)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender

    async def _on_client(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(message, dict) and message.get("type") == "marker":
            await self.add_marker(str(message.get("name") or ""), "ui")

    async def _sender(self, socket: WebSocket, sink: _Sink) -> None:
        while True:
            while not sink.items:
                sink.event.clear()
                if sink.items:
                    break
                await sink.event.wait()
            raw, _droppable = sink.items.popleft()
            await socket.send_text(raw)

    def publish(self, message: dict, droppable: bool = False) -> None:
        raw = json.dumps(_jsonable(message), separators=(",", ":"), allow_nan=False)
        for sink in self.clients.values():
            sink.push(raw, droppable)

    async def _consume(self) -> None:
        next_feature = time.monotonic()
        last_display = 0.0
        last_heartbeat = time.monotonic()
        while True:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=0.05)
            except TimeoutError:
                item = None
            outgoing: list[tuple[dict, bool]] = []
            async with self.lock:
                if item is not None:
                    self._handle(item)
                    self._drain()
                now = time.monotonic()
                if self._pending and now - last_display >= 0.05:
                    outgoing.append((self._display_message(), True))
                    self._pending.clear()
                    last_display = now
                if now >= next_feature:
                    feature = self._feature_message()
                    if feature is not None:
                        outgoing.append((feature, False))
                    next_feature = now + self.profile.hop_sec
                if now - last_heartbeat >= 2.0:
                    self._refresh_hr()
                    outgoing.append((self.status(), True))
                    last_heartbeat = now
            for message, droppable in outgoing:
                self.publish(message, droppable=droppable)

    def _drain(self) -> None:
        for _ in range(400):
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._handle(item)

    def _handle(self, item: Sample | Event) -> None:
        if isinstance(item, Event):
            self._accept_event(item)
            return
        if item.stream == "eeg":
            self._handle_eeg(item)
            return
        if item.stream == "ppg":
            if item.values:
                if item.rate and abs(item.rate - self.hr.fs) > 1:
                    self.hr.reset(item.rate)
                channel = item.values[1] if len(item.values) > 1 else item.values[0]
                self.hr.push(channel)
            self._record(item)
            return
        if item.stream == "quality":
            self.pipeline.set_quality(item.values)
            return
        if item.stream == "device_band":
            self.pipeline.set_device_band(item.channels, item.values)
            return
        if item.stream == "battery" and item.values:
            level = item.values[0]
            self.battery = level / 100.0 if level > 1.5 else level
            return
        if item.stream == "contact" and item.values:
            self.touching = item.values[0]
            return
        if item.stream in {"acc", "gyro", "optics"}:
            self._record(item)

    def _handle_eeg(self, item: Sample) -> None:
        if item.rate and self.samples_seen < 8 and abs(item.rate - self.pipeline.fs) / item.rate > 0.05:
            self.pipeline.set_fs(item.rate)
            self._decim = self._decimation()
        self.pipeline.add_eeg(item.t, item.channels, item.values)
        self._record(item)
        self.samples_seen += 1
        self._eeg_channels = item.channels
        self._decim_count += 1
        if self._decim_count >= self._decim:
            self._decim_count = 0
            self._pending.append((item.t, item.values))

    def _record(self, item: Sample) -> None:
        if self.recorder is not None and self.recorder.active:
            self.recorder.add_sample(item)

    def _accept_event(self, event: Event) -> None:
        if event.kind in {"blink", "jaw_clench"}:
            last = self._last_device.get(event.kind, 0.0)
            if event.t - last < 0.3:
                return
            self._last_device[event.kind] = event.t
            if not event.color:
                event.color = "#f2d479" if event.kind == "blink" else "#e08a5a"
        elif event.kind == "marker" and event.osc is not None:
            match = next((item for item in self.profile.markers if item.osc == event.osc), None)
            if match is not None:
                event.name = match.name
                event.color = match.color
            elif not event.color:
                event.color = "#d4a0ff"
        elif event.kind == "marker" and not event.color:
            match = next((item for item in self.profile.markers if item.name == event.name), None)
            event.color = match.color if match else "#d4a0ff"
        self._publish_event(event)

    def _publish_event(self, event: Event) -> None:
        onset = None
        if self.recorder is not None and self.recorder.active:
            self.recorder.add_event(event)
            if self.recorder.t0 is not None:
                onset = event.t - self.recorder.t0
        payload = {
            "type": "event",
            "t": event.t,
            "kind": event.kind,
            "name": event.name,
            "source": event.source,
            "color": event.color or "#d4a0ff",
            "onset": onset,
        }
        self.recent_events.append(payload)
        self.publish(payload, droppable=False)

    def _display_message(self) -> dict:
        samples = [[round(float(value), 3) for value in values] for _stamp, values in self._pending]
        return {
            "type": "display",
            "t": self._pending[-1][0],
            "eeg": {
                "channels": list(self._eeg_channels),
                "t0": self._pending[0][0],
                "fs": self.settings.display_hz,
                "samples": samples,
            },
            "quality": self.pipeline.quality,
            "hr": None if self._hr_value is None else round(self._hr_value, 1),
            "battery": self.battery,
            "touching": self.touching,
            "recording": self._recording_brief(),
        }

    def _feature_message(self) -> dict | None:
        self._refresh_hr()
        frame = self.pipeline.compute()
        if frame is None:
            return None
        feedback = self.feedback.update(frame)
        self.last_feedback = feedback
        if self.tone.enabled:
            self.tone.update(float(feedback["volume"]), float(feedback["frequency"]))
        return {"type": "features", "hr": self._hr_value, "feedback": feedback, **frame}

    def _refresh_hr(self) -> None:
        estimate = self.hr.estimate()
        if estimate is not None:
            self._hr_value = estimate

    def _recording_brief(self) -> dict | None:
        recorder = self.recorder
        if recorder is None or not recorder.active:
            return None
        elapsed = (datetime.now(UTC) - recorder.started_at).total_seconds()
        return {
            "session_id": recorder.session_id,
            "subject": recorder.subject,
            "task": recorder.task,
            "elapsed": round(max(0.0, elapsed), 1),
            "eeg_samples": recorder.eeg_count,
            "path": str(recorder.session_dir),
        }

    def _decimation(self) -> int:
        return max(1, int(round(self.pipeline.fs / max(1.0, self.settings.display_hz))))

    def _hello(self) -> dict:
        return {
            "type": "hello",
            "version": __version__,
            "profile": self.profile.model_dump(mode="json"),
            "config": self.settings.public_config(),
            "status": self.status(),
            "events": list(self.recent_events),
        }

    def status(self) -> dict:
        return {
            "type": "status",
            "version": __version__,
            "input": {
                "type": self.settings.input_type,
                "running": self.input_running,
                "error": self.last_error,
                "samples_seen": self.samples_seen,
                "dropped": self.dropped,
                "eeg_fs": self.pipeline.fs,
                "channels": list(self.pipeline.buffer.channels),
                "eeg_scale": self.settings.eeg_scale,
            },
            "recording": self._recording_brief(),
            "profile": self.profile.name,
            "clients": len(self.clients),
            "server_audio": self.tone.enabled,
            "feedback": self.last_feedback,
            "hr": self._hr_value,
        }

    def _sync_audio(self) -> None:
        if self.settings.server_audio and not self.tone.enabled:
            try:
                self.tone.enable()
            except RuntimeError as exc:
                self.last_error = str(exc)
                self.settings = self.settings.model_copy(update={"server_audio": False})
                logger.warning("server_audio_disabled", extra={"error": str(exc)})
        elif not self.settings.server_audio and self.tone.enabled:
            self.tone.disable()
