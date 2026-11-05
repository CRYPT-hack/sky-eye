from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import cv2

from engine.core import DEFAULT_FPS, Frame


class CaptureSource:
    """Live camera / capture device. Never 'ends' on its own."""

    def __init__(self, device: int) -> None:
        self._device = device
        self._cap: cv2.VideoCapture | None = None
        self._fps = DEFAULT_FPS
        self._index = 0
        self._start = 0.0

    def open(self) -> None:
        if sys.platform == "win32":
            self._cap = cv2.VideoCapture(self._device, cv2.CAP_DSHOW)
        else:
            self._cap = cv2.VideoCapture(self._device)
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open capture device {self._device}")
        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._fps = fps if fps > 0 and fps == fps else DEFAULT_FPS
        self._index = 0
        self._start = time.monotonic()

    def read(self) -> Frame | None:
        if self._cap is None:
            return None
        ok, image = self._cap.read()
        if not ok:
            return None
        frame = Frame(
            image, time.monotonic() - self._start, datetime.now(timezone.utc), self._index
        )
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
        return False
