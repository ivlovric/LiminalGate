"""Band profiles: builtins shipped with the app, overrides stored as YAML."""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from liminalgate.errors import ProfileError
from liminalgate.expressions import ExpressionError, eval_expr

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class Band(BaseModel):
    name: str
    f_low: float
    f_high: float
    color: str = "#8eb6ff"

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _IDENT.match(value):
            raise ValueError("band name must use letters, digits, and underscore")
        return value

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if not _COLOR.match(value):
            raise ValueError("color must be #RRGGBB")
        return value

    @model_validator(mode="after")
    def _range(self) -> Band:
        if not (0 <= self.f_low < self.f_high <= 200):
            raise ValueError("require 0 <= f_low < f_high <= 200")
        return self


class Ratio(BaseModel):
    name: str
    expression: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _IDENT.match(value):
            raise ValueError("ratio name must use letters, digits, and underscore")
        return value


class MarkerDef(BaseModel):
    name: str
    color: str = "#d4a0ff"
    hotkey: str = ""
    osc: int | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 40:
            raise ValueError("marker name must be 1–40 characters")
        return cleaned

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if not _COLOR.match(value):
            raise ValueError("color must be #RRGGBB")
        return value

    @field_validator("hotkey")
    @classmethod
    def _hotkey(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if len(cleaned) > 1:
            raise ValueError("hotkey must be a single character")
        return cleaned

    @field_validator("osc")
    @classmethod
    def _osc(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= int(value) <= 16:
            raise ValueError("OSC marker index must be between 1 and 16")
        return value


class FeedbackRule(BaseModel):
    metric: str
    source: str = "relative"
    scope: str = "mean"
    param: str = "volume"
    in_min: float = 0.0
    in_max: float = 1.0
    out_min: float = 0.0
    out_max: float = 1.0
    smooth: float = 0.35
    gate_metric: str | None = None
    gate_source: str = "relative"
    gate_scope: str = "mean"
    gate_threshold: float | None = None
    gate_mode: str = "none"

    @field_validator("source", "gate_source")
    @classmethod
    def _source(cls, value: str) -> str:
        if value not in {"relative", "absolute"}:
            raise ValueError("source must be relative or absolute")
        return value

    @field_validator("param")
    @classmethod
    def _param(cls, value: str) -> str:
        if value not in {"volume", "frequency"}:
            raise ValueError("param must be volume or frequency")
        return value

    @field_validator("gate_mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        if value not in {"none", "mute_above", "mute_below"}:
            raise ValueError("gate_mode must be none, mute_above, or mute_below")
        return value

    @field_validator("smooth")
    @classmethod
    def _smooth(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("smooth must be between 0 and 1")
        return value


class Profile(BaseModel):
    name: str
    description: str = ""
    bands: list[Band] = Field(default_factory=list)
    ratios: list[Ratio] = Field(default_factory=list)
    markers: list[MarkerDef] = Field(default_factory=list)
    feedback: list[FeedbackRule] = Field(default_factory=list)
    relative_range: tuple[float, float] = (1.0, 45.0)
    window_sec: float = 4.0
    hop_sec: float = 0.25

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = value.strip()
        if not _IDENT.match(cleaned):
            raise ValueError("profile name must use letters, digits, and underscore")
        return cleaned

    @field_validator("window_sec")
    @classmethod
    def _window(cls, value: float) -> float:
        if not 0.5 <= value <= 30:
            raise ValueError("window_sec must be between 0.5 and 30")
        return value

    @field_validator("hop_sec")
    @classmethod
    def _hop(cls, value: float) -> float:
        if not 0.05 <= value <= 5:
            raise ValueError("hop_sec must be between 0.05 and 5")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Profile:
        if len(self.bands) == 0:
            raise ValueError("a profile needs at least one band")
        names = [band.name for band in self.bands]
        if len(names) != len(set(names)):
            raise ValueError("band names must be unique")
        low, high = self.relative_range
        if not (0 <= low < high <= 200):
            raise ValueError("relative_range must satisfy 0 <= low < high <= 200")
        dummy = {name: 1.0 for name in names}
        for name in names:
            dummy[f"{name}_abs"] = 1.0
            dummy[f"{name}_rel"] = 0.5
        ratio_names = [ratio.name for ratio in self.ratios]
        if len(ratio_names) != len(set(ratio_names)):
            raise ValueError("ratio names must be unique")
        for ratio in self.ratios:
            try:
                eval_expr(ratio.expression, dummy)
            except ExpressionError as exc:
                raise ValueError(f"ratio {ratio.name}: {exc}") from exc
        hotkeys = [marker.hotkey for marker in self.markers if marker.hotkey]
        if len(hotkeys) != len(set(hotkeys)):
            raise ValueError("marker hotkeys must be unique")
        osc = [marker.osc for marker in self.markers if marker.osc is not None]
        if len(osc) != len(set(osc)):
            raise ValueError("OSC marker numbers must be unique")
        known = set(names) | set(ratio_names)
        for rule in self.feedback:
            if rule.metric not in known:
                raise ValueError(f"feedback metric {rule.metric} is not a band or ratio")
            if rule.gate_metric and rule.gate_metric not in known:
                raise ValueError(f"gate metric {rule.gate_metric} is not a band or ratio")
        return self


def _profile_from_text(text: str, label: str) -> Profile:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ProfileError(f"{label} is not a profile mapping")
    return Profile.model_validate(data)


def _read_yaml(path: Path) -> Profile:
    return _profile_from_text(path.read_text(encoding="utf-8"), path.name)


def builtin_profiles() -> dict[str, Profile]:
    root = files("liminalgate.profiles") / "defaults"
    found: dict[str, Profile] = {}
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".yaml"):
            continue
        profile = _profile_from_text(entry.read_text(encoding="utf-8"), entry.name)
        found[profile.name] = profile
    return found


class ProfileStore:
    """Builtins live in the package. Saves go to ``data/profiles`` and win on load."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._builtins = builtin_profiles()

    def names(self) -> list[dict[str, object]]:
        user = {path.stem for path in self.directory.glob("*.yaml")}
        names = set(self._builtins) | user
        rows = []
        for name in sorted(names):
            rows.append({"name": name, "builtin": name in self._builtins, "customized": name in user})
        return rows

    def load(self, name: str) -> Profile:
        path = self.directory / f"{name}.yaml"
        if path.is_file():
            return _read_yaml(path)
        if name in self._builtins:
            return self._builtins[name]
        raise ProfileError(f"unknown profile {name}")

    def save(self, profile: Profile) -> Path:
        path = self.directory / f"{profile.name}.yaml"
        payload = profile.model_dump(mode="json")
        path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path

    def delete(self, name: str) -> str:
        path = self.directory / f"{name}.yaml"
        if path.is_file():
            path.unlink()
            if name in self._builtins:
                return "reverted"
            return "deleted"
        if name in self._builtins:
            raise ProfileError(f"{name} is a builtin with no local override")
        raise ProfileError(f"unknown profile {name}")
