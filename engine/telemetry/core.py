"""Drone pose, a time-indexed pose buffer, and the telemetry source protocol."""
from __future__ import annotations

import bisect
import threading
from dataclasses import dataclass
from typing import Protocol

from engine.core import Frame

TRACK_MIN_SPACING_S = 1.0


@dataclass(frozen=True)
class DronePose:
    ts: float  # seconds, in the telemetry source's own time base
    lat: float
    lon: float
    alt_agl_m: float
    yaw_deg: float  # clockwise from true north
    cam_pitch_deg: float  # -90 = straight down
    cam_roll_deg: float = 0.0
    # True when the source had no reading and filled in a default (the location is then
    # not trusted as "ok"; see engine.geo.geolocate)
    pitch_assumed: bool = False
    yaw_assumed: bool = False
    alt_assumed: bool = False


class PoseBuffer:
    """Thread-safe poses ordered by `ts`, bounded to the newest `capacity`."""

    def __init__(self, max_age_s: float = 0.5, capacity: int = 600) -> None:
        self._max_age_s = max_age_s
        self._capacity = capacity
        self._poses: list[DronePose] = []
        self._keys: list[float] = []  # ts of each pose, for bisect
        self._lock = threading.Lock()

    def add(self, pose: DronePose) -> None:
        with self._lock:
            i = bisect.bisect_right(self._keys, pose.ts)
            self._keys.insert(i, pose.ts)
            self._poses.insert(i, pose)
            if len(self._poses) > self._capacity:
                del self._poses[0]
                del self._keys[0]

    def nearest(self, t: float) -> DronePose | None:
        """The pose closest in time to `t`, if within `max_age_s`."""
        with self._lock:
            if not self._poses:
                return None
            i = bisect.bisect_left(self._keys, t)
            candidates = self._poses[max(i - 1, 0):i + 1]
            best = min(candidates, key=lambda p: abs(p.ts - t))
        return best if abs(best.ts - t) <= self._max_age_s else None

    def latest(self) -> DronePose | None:
        with self._lock:
            return self._poses[-1] if self._poses else None

    def track(self, since_t: float) -> list[DronePose]:
        """Poses with ts >= since_t, at most one per second."""
        with self._lock:
            tail = self._poses[bisect.bisect_left(self._keys, since_t):]
        out: list[DronePose] = []
        for p in tail:
            if not out or p.ts - out[-1].ts >= TRACK_MIN_SPACING_S:
                out.append(p)
        return out


class Telemetry(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def pose_for(self, frame: Frame) -> DronePose | None:
        """The pose for this frame; the source picks the frame field it matches on."""
        ...

    def latest(self) -> DronePose | None: ...

    def track(self, seconds: float) -> list[DronePose]:
        """Poses from the last `seconds` seconds before the latest pose."""
        ...

    @property
    def state(self) -> str:
        """One of "none", "waiting", "ok", "stale"."""
        ...


class NoTelemetry:
    """Telemetry disabled: never has a pose."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def pose_for(self, frame: Frame) -> DronePose | None:
        return None

    def latest(self) -> DronePose | None:
        return None

    def track(self, seconds: float) -> list[DronePose]:
        return []

    @property
    def state(self) -> str:
        return "none"
