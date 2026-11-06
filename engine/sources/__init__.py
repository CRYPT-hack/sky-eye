from __future__ import annotations

from engine.config import ConfigError, SourceConfig
from engine.core import VideoSource
from engine.sources.capture import CaptureSource
from engine.sources.file import FileSource
from engine.sources.stream import StreamSource

__all__ = ["CaptureSource", "FileSource", "StreamSource", "build_source"]


def build_source(cfg: SourceConfig) -> VideoSource:
    if cfg.type == "capture":
        return CaptureSource(cfg.device)
    if cfg.type in ("stream", "go2rtc"):
        return StreamSource(cfg.stream_url())
    if cfg.path is None:
        raise ConfigError("source.path: required for type: file")
    return FileSource(cfg.path, realtime=cfg.realtime, loop=cfg.loop)
