from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

from engine.core import DEFAULT_FPS, Frame


class FileSource:
    """Reads a video file; optionally paced to the file's FPS and/or looped."""

    def __init__(self, path: Path, realtime: bool, loop: bool) -> None:
        self._path = Path(path)
        self._realtime = realtime
        self._loop = loop
        self._cap: cv2.VideoCapture | None = None
        self._fps = DEFAULT_FPS
        self._index = 0
        self._start = 0.0
        self._ended = False

    def open(self) -> None:
        self._cap = cv2.VideoCapture(str(self._path))
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open video file: {self._path}")
        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._fps = fps if fps > 0 and not math.isnan(fps) else DEFAULT_FPS
        self._index = 0
        self._ended = False
        self._start = time.monotonic()

    def read(self) -> Frame | None:
        if self._cap is None or self._ended:
            return None
        ok, image = self._cap.read()
        if not ok and self._loop:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, image = self._cap.read()
        if not ok:
            self._ended = True
            return None
        ts = self._index / self._fps
        if self._realtime:
            delay = self._start + ts - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        frame = Frame(image, ts, datetime.now(timezone.utc), self._index)
        self._index += 1
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_size(self) -> tuple[int, int] | None:
        """(width, height) reported by the opened source, or None if unknown."""
        if self._cap is None:
            return None
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        return (w, h) if w > 0 and h > 0 else None

    @property
    def ended(self) -> bool:
        return self._ended
