"""Validated YAML configuration for the sky-eye engine."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError

from engine.telemetry.connection import validate_connection


class ConfigError(Exception):
    """Raised when the config file is missing or invalid."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


STREAM_SCHEMES = ("rtsp", "rtsps", "rtmp", "rtmps", "udp", "http", "https")
_GO2RTC_SCHEMES = ("rtsp", "rtsps")


def _url_problem(url: str, schemes: tuple[str, ...]) -> str | None:
    """Why `url` is not a usable stream URL, or None. Never repeats the URL:
    it may contain a password."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "not a valid URL"
    if "://" not in url or not parts.scheme:
        return f"needs a scheme, one of: {', '.join(schemes)} (e.g. {schemes[0]}://host/path)"
    if parts.scheme == "srt":  # the PyAV wheels are built without libsrt
        return (
            "SRT is not supported directly; "
            "let a relay such as MediaMTX ingest it and read its RTSP output"
        )
    if parts.scheme not in schemes:
        return f"unsupported scheme {parts.scheme!r}; use one of: {', '.join(schemes)}"
    # urlsplit ends the authority at the first / ? or #; an "@" after that means
    # the user name or password holds one of them raw, and FFmpeg would split
    # the URL in the wrong place (and could log the password as the host)
    after_authority = url.partition("://")[2][len(parts.netloc):]
    if "@" in after_authority:
        return (
            "the user name or password contains a reserved character (/ ? #); "
            "percent-encode it: / as %2F, ? as %3F, # as %23"
        )
    if not parts.netloc:
        return "needs a host, e.g. rtsp://192.168.1.10:8554/live"
    return None


class Go2rtcConfig(_Model):
    """A stream relayed by a local go2rtc server, read from its RTSP output."""

    rtsp_base: str = "rtsp://127.0.0.1:8554"
    name: str | None = None  # the go2rtc stream name


class SourceConfig(_Model):
    type: Literal["file", "capture", "stream", "go2rtc"] = "file"
    path: Path | None = None
    device: int = Field(default=0, ge=0)
    url: str | None = None  # type: stream
    go2rtc: Go2rtcConfig = Field(default_factory=Go2rtcConfig)  # type: go2rtc
    realtime: bool = True
    loop: bool = False

    @model_validator(mode="after")
    def _check_type_fields(self) -> SourceConfig:
        # path may be left unset (e.g. supplied later by a CLI override)
        if self.type == "file" and self.path is not None and not self.path.is_file():
            raise ValueError(f"file source path does not exist: {self.path}")
        problem = self.stream_problem()
        if problem is not None:
            loc, msg = problem
            # A ValidationError raised here keeps its own loc, so the message
            # names the field (source.url, source.go2rtc.name), not just "source".
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [
                    InitErrorDetails(
                        type=PydanticCustomError("stream_source", msg), loc=loc, input=None
                    )
                ],
            )
        return self

    def stream_problem(self) -> tuple[tuple[str, ...], str] | None:
        """(field path below source, message) if a stream/go2rtc source is misconfigured."""
        if self.type == "stream":
            if not self.url:
                return ("url",), "required for type: stream"
            problem = _url_problem(self.url, STREAM_SCHEMES)
            return None if problem is None else (("url",), problem)
        if self.type == "go2rtc":
            if not self.go2rtc.name:
                return ("go2rtc", "name"), "required for type: go2rtc"
            problem = _url_problem(self.go2rtc.rtsp_base, _GO2RTC_SCHEMES)
            return None if problem is None else (("go2rtc", "rtsp_base"), problem)
        return None

    def stream_url(self) -> str:
        """The URL a stream/go2rtc source reads; ConfigError naming the bad field."""
        if self.type not in ("stream", "go2rtc"):
            raise ConfigError(f"source.type: {self.type} is not a stream source")
        problem = self.stream_problem()
        if problem is not None:
            loc, msg = problem
            raise ConfigError(f"source.{'.'.join(loc)}: {msg}")
        if self.type == "go2rtc":
            return f"{self.go2rtc.rtsp_base.rstrip('/')}/{self.go2rtc.name}"
        return str(self.url)


class DetectorConfig(_Model):
    type: Literal["rfdetr"] = "rfdetr"
    size: Literal["nano", "small", "medium"] = "small"
    weights: Path | None = None  # None = COCO-pretrained
    input_size: int = Field(default=512, gt=0)
    confidence: float = Field(default=0.4, gt=0, lt=1)
    device: Literal["cuda", "cpu"] = "cuda"
    classes: list[str] = Field(default_factory=lambda: ["truck"])


class TrackerConfig(_Model):
    confirm_hits: int = Field(default=3, ge=1)
    confirm_window: int = Field(default=5, ge=1)
    lost_timeout_s: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def _hits_within_window(self) -> TrackerConfig:
        if self.confirm_hits > self.confirm_window:
            raise ValueError("confirm_hits must be <= confirm_window")
        return self


class EvidenceConfig(_Model):
    data_dir: Path = Path("data")
    clip_before_s: float = Field(default=3, ge=0)
    clip_after_s: float = Field(default=5, gt=0)
    snapshot_window_s: float = Field(default=2, ge=0)
    max_disk_gb: float = Field(default=20, gt=0)


class AuthConfig(_Model):
    mode: Literal["none", "token"] = "none"

    @model_validator(mode="after")
    def _token_needs_env(self) -> AuthConfig:
        if self.mode == "token" and not os.environ.get("SKYEYE_API_TOKEN"):
            raise ValueError("auth mode 'token' requires env var SKYEYE_API_TOKEN")
        return self


class ApiConfig(_Model):
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    stream_fps: int = Field(default=15, ge=1)
    stream_max_width: int = Field(default=1280, ge=16)
    dashboard_dir: Path | None = None  # None = <repo>/dashboard/dist
    map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

    @field_validator("map_tile_url")
    @classmethod
    def _check_tile_url(cls, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise ValueError("map_tile_url must start with http:// or https://")
        missing = [t for t in ("{z}", "{x}", "{y}") if t not in url]
        if missing:
            raise ValueError(f"map_tile_url must contain {', '.join(missing)}")
        return url


class TelegramConfig(_Model):
    enabled: bool = False
    dashboard_url: str | None = None

    @model_validator(mode="after")
    def _enabled_needs_env(self) -> TelegramConfig:
        # the secrets stay in the environment; only their presence is checked here
        if self.enabled:
            for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
                if not os.environ.get(var):
                    raise ValueError(f"telegram alerts require env var {var}")
        return self


class MqttConfig(_Model):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=1883, ge=1, le=65535)
    topic: str = "sky-eye/events"
    username: str | None = None  # the password is env MQTT_PASSWORD
    tls: bool = False


class AlertsConfig(_Model):
    cooldown_s: float = Field(default=10, gt=0)
    desktop: bool = True
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    mqtt: MqttConfig = Field(default_factory=MqttConfig)

    def channels(self) -> list[str]:
        """Configured channel names, in a fixed order."""
        return [
            name
            for name, on in (
                ("desktop", self.desktop),
                ("telegram", self.telegram.enabled),
                ("mqtt", self.mqtt.enabled),
            )
            if on
        ]


class TelemetryConfig(_Model):
    type: Literal["none", "mavlink", "dji_srt"] = "none"
    connection: str = "udpin:0.0.0.0:14550"  # type: mavlink
    srt_path: Path | None = None  # type: dji_srt; default = the video's sibling .SRT
    max_age_s: float = Field(default=0.5, gt=0)
    stale_after_s: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def _udp_or_serial(self) -> TelemetryConfig:
        # only checked when used: a leftover link under another type is harmless
        if self.type != "mavlink":
            return self
        try:
            validate_connection(self.connection)
        except ValueError as e:
            # keeps the field's loc, so the message names telemetry.connection
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [InitErrorDetails(type=PydanticCustomError("telemetry_connection", str(e)),
                                  loc=("connection",), input=None)],
            ) from None
        return self


class CameraConfig(_Model):
    hfov_deg: float = Field(default=82.0, ge=10, le=170)
    # A fixed camera angle (-90 = straight down), used when the telemetry has no gimbal
    # pitch; None = such locations are "unreliable"
    pitch_deg: float | None = Field(default=None, ge=-90, le=0)


def _sibling_srt(video: Path) -> Path | None:
    """A file next to `video` with the same stem and a .srt suffix in any case."""
    try:
        for p in video.parent.iterdir():
            if p.stem == video.stem and p.suffix.lower() == ".srt" and p.is_file():
                return p
    except OSError:
        pass
    return None


class AppConfig(_Model):
    source: SourceConfig = Field(default_factory=SourceConfig)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    camera: CameraConfig = Field(default_factory=CameraConfig)

    @model_validator(mode="after")
    def _check_telemetry(self) -> AppConfig:
        tel = self.telemetry
        if tel.type != "dji_srt":
            return self
        if self.source.type != "file":
            raise ValueError("telemetry.type dji_srt requires source.type: file")
        if tel.srt_path is None:
            found = _sibling_srt(self.source.path) if self.source.path else None
            if found is None:
                raise ValueError(
                    "telemetry.srt_path is required: no .SRT file next to the source video"
                )
            tel.srt_path = found
        elif not tel.srt_path.is_file():
            raise ValueError(f"telemetry.srt_path does not exist: {tel.srt_path}")
        return self


_PATH_FIELDS = (("source", "path"), ("detector", "weights"), ("evidence", "data_dir"),
                ("api", "dashboard_dir"), ("telemetry", "srt_path"))


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
        text = path.read_text(encoding="utf-8-sig")  # tolerate a BOM
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"cannot read config file {path}: {e}") from e
    try:
        raw = yaml.safe_load(text)
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
