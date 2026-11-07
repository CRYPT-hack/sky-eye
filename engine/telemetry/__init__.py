"""Drone telemetry: pose model, buffer and source protocol."""
from __future__ import annotations

from typing import TYPE_CHECKING

from engine.telemetry.core import DronePose, NoTelemetry, PoseBuffer, Telemetry

if TYPE_CHECKING:
    from engine.config import AppConfig

__all__ = ["DronePose", "NoTelemetry", "PoseBuffer", "Telemetry", "build_telemetry"]


def build_telemetry(cfg: AppConfig) -> Telemetry:
    """The telemetry source for `cfg`. Not started. The MAVLink and SRT modules are
    imported only when selected, so pymavlink is only needed for `type: mavlink`."""
    tel = cfg.telemetry
    if tel.type == "dji_srt":
        from engine.telemetry.dji_srt import DjiSrtTelemetry

        return DjiSrtTelemetry(tel.srt_path, tel.max_age_s, loop=cfg.source.loop)
    if tel.type == "mavlink":
        from engine.telemetry.mavlink import MavlinkTelemetry

        return MavlinkTelemetry(tel.connection, tel.max_age_s, tel.stale_after_s)
    return NoTelemetry()
