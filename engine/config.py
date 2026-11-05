"""Validated YAML configuration for the sky-eye engine."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(Exception):
    """Raised when the config file is missing or invalid."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceConfig(_Model):
    type: Literal["file", "capture"] = "file"
    path: Path | None = None
    device: int = 0
    realtime: bool = True
    loop: bool = False

    @model_validator(mode="after")
    def _file_needs_existing_path(self) -> SourceConfig:
        # path may be left unset (e.g. supplied later by a CLI override)
        if self.type == "file" and self.path is not None and not self.path.is_file():
            raise ValueError(f"file source path does not exist: {self.path}")
        return self


class DetectorConfig(_Model):
    type: Literal["rfdetr"] = "rfdetr"
    size: Literal["nano", "small", "medium"] = "small"
    weights: Path | None = None  # None = COCO-pretrained
    input_size: int = Field(default=512, gt=0)
    confidence: float = Field(default=0.4, gt=0, lt=1)
    device: Literal["cuda", "cpu"] = "cuda"
    classes: list[str] = Field(default_factory=lambda: ["truck"])


class TrackerConfig(_Model):
    confirm_hits: int = 3
    confirm_window: int = 5
    lost_timeout_s: float = 2.0

    @model_validator(mode="after")
    def _hits_within_window(self) -> TrackerConfig:
        if self.confirm_hits > self.confirm_window:
            raise ValueError("confirm_hits must be <= confirm_window")
        return self


class EvidenceConfig(_Model):
    data_dir: Path = Path("data")
    clip_before_s: float = 3
    clip_after_s: float = 5
    snapshot_window_s: float = 2
    max_disk_gb: float = 20


class AuthConfig(_Model):
    mode: Literal["none", "token"] = "none"

    @model_validator(mode="after")
    def _token_needs_env(self) -> AuthConfig:
        if self.mode == "token" and not os.environ.get("SKYEYE_API_TOKEN"):
            raise ValueError("auth mode 'token' requires env var SKYEYE_API_TOKEN")
        return self


class ApiConfig(_Model):
    host: str = "0.0.0.0"
    port: int = 8000
    auth: AuthConfig = Field(default_factory=AuthConfig)
    stream_fps: int = 15
    stream_max_width: int = 1280


class AppConfig(_Model):
    source: SourceConfig = Field(default_factory=SourceConfig)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)


_PATH_FIELDS = (("source", "path"), ("detector", "weights"), ("evidence", "data_dir"))


def _resolve_paths(raw: dict[str, Any], base: Path) -> None:
    for section, key in _PATH_FIELDS:
        sec = raw.get(section)
        if isinstance(sec, dict) and isinstance(sec.get(key), (str, os.PathLike)):
            p = Path(sec[key])
            if not p.is_absolute():
                sec[key] = base / p


def load_config(path: Path) -> AppConfig:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping: {path}")
    base = path.resolve().parent
    _resolve_paths(raw, base)
    try:
        cfg = AppConfig.model_validate(raw)
    except ValidationError as e:
        lines = [
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        ]
        raise ConfigError("\n".join(lines)) from e
    if not cfg.evidence.data_dir.is_absolute():  # default value was not in raw
        cfg.evidence.data_dir = base / cfg.evidence.data_dir
    return cfg
