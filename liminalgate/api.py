"""REST API for sessions, profiles, markers, and the live input."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, ValidationError

from liminalgate.brainflow_scan import list_boards, scan_headsets
from liminalgate.catalog import discover_lsl, playback_files
from liminalgate.errors import ProfileError
from liminalgate.hub import Hub
from liminalgate.profiles import Profile

router = APIRouter(prefix="/api")


class Health(BaseModel):
    status: str = "ok"


class MarkerIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class SessionStart(BaseModel):
    subject: str = "01"
    task: str = "rest"
    notes: str = ""


class ActivateIn(BaseModel):
    name: str


class ConfigUpdate(BaseModel):
    input_type: str | None = None
    osc_port: int | None = None
    osc_host: str | None = None
    playback_path: str | None = None
    playback_speed: float | None = None
    playback_loop: bool | None = None
    eeg_scale: float | None = None
    eeg_fs: float | None = None
    brainflow_board: str | None = None
    brainflow_mac: str | None = None
    brainflow_serial: str | None = None
    brainflow_other_info: str | None = None
    lsl_name: str | None = None
    server_audio: bool | None = None
    profile: str | None = None
    powerline_hz: float | None = None
    display_hz: float | None = None


def get_hub(request: Request) -> Hub:
    return request.app.state.hub


@router.get("/health", response_model=Health, tags=["system"], summary="Liveness probe")
def health() -> Health:
    """Unauthenticated probe used by the container healthcheck."""

    return Health()


@router.get("/status", tags=["system"], summary="Live input, recording, and profile")
def status(hub: Hub = Depends(get_hub)) -> dict:
    return hub.status()


@router.get("/lsl/streams", tags=["config"], summary="LSL streams visible on this machine")
async def lsl_streams() -> dict:
    """Short resolve. Missing pylsl returns an empty list and a message, not an error status."""

    return await asyncio.to_thread(discover_lsl, 1.2)


@router.get("/playback/files", tags=["config"], summary="CSV files the playback picker can offer")
def list_playback_files(hub: Hub = Depends(get_hub)) -> dict:
    examples = Path("examples")
    return {"files": playback_files(hub.settings.data_dir, examples if examples.is_dir() else None)}


@router.get("/brainflow/boards", tags=["config"], summary="BrainFlow boards for the Setup dropdown")
def brainflow_boards() -> dict:
    return {"boards": list_boards()}


@router.get("/brainflow/devices", tags=["config"], summary="Nearby Muse and other BrainFlow BLE headsets")
async def brainflow_devices(timeout: float = 5.0) -> dict:
    """Scan with bleak or simpleble. A missing adapter names compose.host.yaml."""

    bounded = min(8.0, max(1.0, float(timeout)))
    return await asyncio.to_thread(scan_headsets, bounded)


@router.get("/config", tags=["config"], summary="Runtime input configuration")
def get_config(hub: Hub = Depends(get_hub)) -> dict:
    return hub.settings.public_config()


@router.put("/config", tags=["config"], summary="Change input, scale, or host audio")
async def put_config(body: ConfigUpdate, hub: Hub = Depends(get_hub)) -> dict:
    """Applies immediately and is saved to ``data/config.json``.

    A saved file overrides environment variables on the next start.
    ``eeg_scale`` multiplies raw EEG. Use it when BrainFlow and muse-lsl
    disagree (about 4.5× has been reported for Muse Athena).
    """

    return await hub.apply_config(body.model_dump(exclude_none=True))


@router.get("/profiles", tags=["profiles"], summary="List builtin and saved profiles")
def list_profiles(hub: Hub = Depends(get_hub)) -> list[dict]:
    rows = hub.store.names()
    for row in rows:
        row["active"] = row["name"] == hub.profile.name
    return rows


@router.get("/profiles/{name}", tags=["profiles"], summary="Fetch one profile")
def get_profile(name: str, hub: Hub = Depends(get_hub)) -> dict:
    profile = hub.store.load(name)
    payload = profile.model_dump(mode="json")
    payload["active"] = profile.name == hub.profile.name
    return payload


@router.put("/profiles/{name}", tags=["profiles"], summary="Save a JSON or YAML profile")
async def put_profile(name: str, request: Request, hub: Hub = Depends(get_hub)) -> dict:
    raw = await request.body()
    content_type = request.headers.get("content-type", "")
    try:
        if "yaml" in content_type or "yml" in content_type:
            data = yaml.safe_load(raw) or {}
        else:
            data = json.loads(raw or b"{}")
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ProfileError(f"could not parse profile: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("profile body must be a mapping")
    data["name"] = name
    try:
        profile = Profile.model_validate(data)
    except ValidationError as exc:
        raise ProfileError(exc.errors()[0]["msg"] if exc.errors() else str(exc)) from exc
    activate = request.query_params.get("activate", "true").lower() != "false"
    saved = hub.save_profile(profile, activate=activate)
    return saved.model_dump(mode="json")


@router.delete("/profiles/{name}", tags=["profiles"], summary="Delete a saved override")
def delete_profile(name: str, hub: Hub = Depends(get_hub)) -> dict:
    result = hub.store.delete(name)
    if name == hub.profile.name:
        hub.activate_profile(hub.store.load(name if result == "reverted" else "hypnagogic"))
    return {"name": name, "result": result}


@router.get("/active-profile", tags=["profiles"], summary="Profile currently driving bands and feedback")
def active_profile(hub: Hub = Depends(get_hub)) -> dict:
    return hub.profile.model_dump(mode="json")


@router.post("/active-profile", tags=["profiles"], summary="Activate a saved profile by name")
def activate_profile(body: ActivateIn, hub: Hub = Depends(get_hub)) -> dict:
    profile = hub.activate_profile(hub.store.load(body.name))
    return profile.model_dump(mode="json")


@router.post("/sessions", tags=["recording"], summary="Start a CSV + EDF session")
async def start_session(body: SessionStart, hub: Hub = Depends(get_hub)) -> dict:
    return await hub.start_session(body.subject, body.task, body.notes)


@router.post("/sessions/stop", tags=["recording"], summary="Stop the session and finalize EDF+")
async def stop_session(hub: Hub = Depends(get_hub)) -> dict:
    summary = await hub.stop_session()
    return summary or {}


@router.get("/sessions", tags=["recording"], summary="List recorded sessions")
def sessions(hub: Hub = Depends(get_hub)) -> list[dict]:
    return hub.sessions()


@router.post("/markers", tags=["markers"], summary="Insert a marker into the live stream")
async def post_marker(body: MarkerIn, hub: Hub = Depends(get_hub)) -> dict:
    return await hub.add_marker(body.name, "rest")


@router.get("/openapi", include_in_schema=False)
def openapi_redirect() -> Response:
    return Response(status_code=307, headers={"Location": "/openapi.json"})
