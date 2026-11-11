"""Process settings from the environment, with an optional saved overlay."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("liminalgate.settings")

CONFIG_FIELDS = (
    "input_type",
    "osc_port",
    "osc_host",
    "playback_path",
    "playback_speed",
    "playback_loop",
    "eeg_scale",
    "eeg_fs",
    "brainflow_board",
    "brainflow_mac",
    "brainflow_serial",
    "brainflow_other_info",
    "lsl_name",
    "server_audio",
    "profile",
    "powerline_hz",
    "display_hz",
)

_INPUTS = {"synthetic", "osc", "playback", "brainflow", "lsl"}


def _alias_input_env() -> None:
    """Accept ``LIMINALGATE_INPUT`` as well as ``LIMINALGATE_INPUT_TYPE``."""

    short = os.environ.get("LIMINALGATE_INPUT")
    if short and not os.environ.get("LIMINALGATE_INPUT_TYPE"):
        os.environ["LIMINALGATE_INPUT_TYPE"] = short


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIMINALGATE_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: Path = Path("data")
    token: str = ""
    input_type: str = "synthetic"
    osc_host: str = "0.0.0.0"
    osc_port: int = 5000
    playback_path: str = ""
    playback_speed: float = 1.0
    playback_loop: bool = True
    eeg_scale: float = 1.0
    eeg_fs: float = 256.0
    brainflow_board: str = "synthetic"
    brainflow_mac: str = ""
    brainflow_serial: str = ""
    brainflow_other_info: str = ""
    lsl_name: str = ""
    server_audio: bool = False
    log_level: str = "INFO"
    profile: str = "hypnagogic"
    powerline_hz: float = 50.0
    display_hz: float = 64.0
    static_dir: str = ""

    @field_validator("token", "playback_path", "brainflow_mac", "brainflow_serial", "lsl_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("input_type")
    @classmethod
    def _input(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in _INPUTS:
            raise ValueError(f"input_type must be one of {sorted(_INPUTS)}")
        return cleaned

    @field_validator("port", "osc_port")
    @classmethod
    def _port(cls, value: int) -> int:
        if not 1 <= int(value) <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return int(value)

    @field_validator("eeg_scale")
    @classmethod
    def _scale(cls, value: float) -> float:
        if not 0 < float(value) <= 1000:
            raise ValueError("eeg_scale must be between 0 and 1000")
        return float(value)

    @field_validator("eeg_fs")
    @classmethod
    def _fs(cls, value: float) -> float:
        if not 1 <= float(value) <= 2048:
            raise ValueError("eeg_fs must be between 1 and 2048")
        return float(value)

    @field_validator("playback_speed")
    @classmethod
    def _speed(cls, value: float) -> float:
        if float(value) < 0:
            raise ValueError("playback_speed must be >= 0 (0 means as fast as possible)")
        return float(value)

    @field_validator("display_hz")
    @classmethod
    def _display(cls, value: float) -> float:
        if not 1 <= float(value) <= 256:
            raise ValueError("display_hz must be between 1 and 256")
        return float(value)

    @field_validator("log_level")
    @classmethod
    def _level(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if cleaned not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("log_level must be DEBUG, INFO, WARNING, or ERROR")
        return cleaned

    def public_config(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in CONFIG_FIELDS}

    def config_path(self) -> Path:
        return self.data_dir / "config.json"


def load_settings() -> Settings:
    """Environment first, then ``data/config.json`` for fields the UI can change."""

    _alias_input_env()
    settings = Settings()
    path = settings.data_dir / "config.json"
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                updates = {key: saved[key] for key in CONFIG_FIELDS if key in saved}
                settings = settings.model_copy(update=updates)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            logger.warning("config_ignored", extra={"error": str(exc), "path": str(path)})
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def save_config(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    payload = settings.public_config()
    path = settings.config_path()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
