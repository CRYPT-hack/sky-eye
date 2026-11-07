from __future__ import annotations

import logging
import math
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import cv2

from engine.core import DEFAULT_FPS, Frame
from engine.sources.backoff import Backoff

log = logging.getLogger(__name__)

LOSS_TIMEOUT_S = 2.0  # read() failing this long means the device is gone (unplugged)


def open_capture_device(device: int) -> Any:
    """cv2.VideoCapture for a device index (DirectShow on Windows)."""
    if sys.platform == "win32":
        return cv2.VideoCapture(device, cv2.CAP_DSHOW)
    return cv2.VideoCapture(device)


class CaptureSource:
    """Live camera / capture device. Never 'ends' on its own.

    When read() has failed continuously for LOSS_TIMEOUT_S (e.g. the HDMI card
    was unplugged) the device is released, state turns "reconnecting", and
    read() reopens it after 1, 2, 4 ... 30 s, returning None in between
    without waiting. The first good frame makes it "running" again.
    failed_attempts counts those losses and failed reopens since the last frame.
    """

    def __init__(
        self,
        device: int,
        *,
        capture_factory: Callable[[int], Any] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._device = device
        self._factory = capture_factory or open_capture_device
        self._clock = clock
        self._backoff = Backoff()
        self._cap: Any = None
        self._opened = False
        self._fps = DEFAULT_FPS
        self._index = 0
        self._start = 0.0
        self._state = "running"
        self._failing_since: float | None = None
        self._retry_at = 0.0
        self._failed_attempts = 0

    def open(self) -> None:
        cap = self._factory(self._device)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"cannot open capture device {self._device}")
        self._cap = cap
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._fps = fps if fps > 0 and math.isfinite(fps) else DEFAULT_FPS
        self._index = 0
        self._start = self._clock()
        self._state = "running"
        self._failing_since = None
        self._failed_attempts = 0
        self._backoff.reset()
        self._opened = True

    def read(self) -> Frame | None:
        if not self._opened:
            return None
        if self._cap is None and not self._reopen_if_due():
            return None
        try:
            ok, image = self._cap.read()
        except cv2.error:
            ok, image = False, None
        now = self._clock()
        if not ok:
            self._on_failure(now)
            return None
        self._failing_since = None
        self._failed_attempts = 0
        if self._state != "running":
            self._state = "running"
            self._backoff.reset()
            log.info("capture device %d: receiving frames again", self._device)
        frame = Frame(image, now - self._start, datetime.now(timezone.utc), self._index)
        self._index += 1
        return frame

    def close(self) -> None:
        self._opened = False
        self._release()

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

    @property
    def state(self) -> str:
        return self._state

    @property
    def failed_attempts(self) -> int:
        """Device losses and failed reopens since the last frame (0 = none)."""
        return self._failed_attempts

    # ----- reconnect -----

    def _on_failure(self, now: float) -> None:
        if self._failing_since is None:
            self._failing_since = now
        if now - self._failing_since < LOSS_TIMEOUT_S:
            return
        self._release()
        self._failing_since = None
        self._failed_attempts += 1
        delay = self._schedule_retry(now)
        if self._state == "running":
            log.warning("capture device %d: no frames for %g s; reconnecting in %g s",
                        self._device, LOSS_TIMEOUT_S, delay)
        else:
            log.info("capture device %d: still no frames; next try in %g s", self._device, delay)
        self._state = "reconnecting"

    def _reopen_if_due(self) -> bool:
        now = self._clock()
        if now < self._retry_at:
            return False
        try:
            cap = self._factory(self._device)
            opened = cap.isOpened()
        except cv2.error:
            cap, opened = None, False
        if not opened:
            if cap is not None:
                cap.release()
            self._failed_attempts += 1
            delay = self._schedule_retry(now)
            log.info("capture device %d not available; next try in %g s", self._device, delay)
            return False
        self._cap = cap
        return True

    def _schedule_retry(self, now: float) -> float:
        delay = self._backoff.next_delay()
        self._retry_at = now + delay
        return delay

    def _release(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            try:
                cap.release()
            except cv2.error:
                log.debug("releasing capture device %d failed", self._device, exc_info=True)
