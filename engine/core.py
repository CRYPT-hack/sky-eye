from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import numpy as np

DEFAULT_FPS = 30.0


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR
    ts: float  # seconds from source start
    wall_time: datetime
    index: int


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]
    score: float
    class_name: str


@dataclass(frozen=True)
class Track:
    track_id: int
    detection: Detection


@dataclass(frozen=True)
class TrackerResult:
    confirmed: list[Track]
    ended_ids: list[int]


class VideoSource(Protocol):
    def open(self) -> None: ...

    def read(self) -> Frame | None: ...

    def close(self) -> None: ...

    @property
    def fps(self) -> float: ...

    @property
    def ended(self) -> bool: ...

    @property
    def state(self) -> str:
        """Either "running" or "reconnecting" (a live source that lost its input)."""
        ...


class Detector(Protocol):
    def detect(self, image: np.ndarray) -> list[Detection]: ...


class Tracker(Protocol):
    def update(self, dets: list[Detection], frame: Frame) -> TrackerResult: ...
