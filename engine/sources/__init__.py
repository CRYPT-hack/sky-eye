from __future__ import annotations

from engine.config import ConfigError, SourceConfig
from engine.core import VideoSource
from engine.sources.capture import CaptureSource
from engine.sources.file import FileSource

__all__ = ["CaptureSource", "FileSource", "build_source"]


def build_source(cfg: SourceConfig) -> VideoSource:
    if cfg.type == "capture":
        return CaptureSource(cfg.device)
    if cfg.path is None:
        raise ConfigError("source.path: required for type: file")
    return FileSource(cfg.path, realtime=cfg.realtime, loop=cfg.loop)
