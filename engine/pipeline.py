"""Two-thread pipeline: capture -> (detect, track, events, live JPEG)."""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import cv2
from pydantic import BaseModel, ConfigDict, Field

from engine.annotate import annotate
from engine.core import Detector, Frame, Track, Tracker, VideoSource
from engine.events.evidence import EvidenceRecorder
from engine.events.manager import EventManager

log = logging.getLogger(__name__)

_QUEUE_SIZE = 8  # realtime=False: capture may run this many frames ahead
_RETRY_S = 0.005  # read() gave None but the source has not ended (live device)
_FPS_WINDOW_S = 2.0
_JPEG_QUALITY = 80
_OOM_HINT = "CUDA out of memory: lower detector.input_size or use size: nano"
_FAIL_SUMMARY_S = 10.0  # repeated frame failures: at most one log line per this period


@dataclass
class PipelineStatus:
    source_state: str  # "starting" | "running" | "ended" | "error"
    fps: float  # processed frames/s over the last 2 s
    frames_processed: int
    trucks_now: int
    trucks_total: int
    started_at: datetime
    telemetry_state: str = "none"


class RuntimeSettings(BaseModel):
    """Settings the API may change while the pipeline runs (mutated in place)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    confidence: float = Field(default=0.4, gt=0, lt=1)
    sahi_enabled: bool = False  # stored only; used in Plan 6


class _FrameChannel:
    """Hands frames from the capture thread to the inference thread.

    latest_only=True keeps one slot that each new frame overwrites (live: frames
    are dropped, never queued); otherwise a FIFO of _QUEUE_SIZE whose put()
    blocks while full, so every frame is processed.
    """

    def __init__(self, latest_only: bool) -> None:
        self._latest_only = latest_only
        self._capacity = 1 if latest_only else _QUEUE_SIZE
        self._items: deque[Frame] = deque()
        self._cond = threading.Condition()
        self._closed = False  # producer finished: get() drains, then returns None
        self._cancelled = False  # stop requested: put()/get() give up at once

    def put(self, frame: Frame) -> bool:
        """Hand over a frame; False once the channel is cancelled."""
        with self._cond:
            if self._latest_only:
                self._items.clear()
            while len(self._items) >= self._capacity and not self._cancelled:
                self._cond.wait()
            if self._cancelled:
                return False
            self._items.append(frame)
            self._cond.notify_all()
            return True

    def get(self) -> Frame | None:
        """Next frame, or None when the producer is done or the channel cancelled."""
        with self._cond:
            while not self._items and not self._closed and not self._cancelled:
                self._cond.wait()
            if self._cancelled or not self._items:
                return None
            frame = self._items.popleft()
            self._cond.notify_all()
            return frame

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def cancel(self) -> None:
        with self._cond:
            self._cancelled = True
            self._items.clear()
            self._cond.notify_all()


def _is_cuda_oom(exc: BaseException) -> bool:
    # Only torch raises its OOM error, so a torch that is not imported yet cannot
    # be involved; never import it here (slow, and not needed by fake detectors).
    torch = sys.modules.get("torch")
    oom = getattr(getattr(torch, "cuda", None), "OutOfMemoryError", None)
    return isinstance(oom, type) and isinstance(exc, oom)


class Pipeline:
    """Capture thread feeds the inference thread; status() and latest_jpeg()
    may be called from any thread.

    The caller opens the source (the tracker needs its fps); the pipeline closes
    it, and calls events.close() exactly once when it finishes.
    """

    def __init__(
        self,
        source: VideoSource,
        detector: Detector,
        tracker: Tracker,
        events: EventManager,
        recorder: EvidenceRecorder,
        runtime: RuntimeSettings,
        realtime: bool,
        stream_max_width: int = 1280,
    ) -> None:
        self._source = source
        self._detector = detector
        self._tracker = tracker
        self._events = events
        self._recorder = recorder
        self._runtime = runtime
        self._stream_max_width = stream_max_width
        self._channel = _FrameChannel(latest_only=realtime)
        self._stop_event = threading.Event()
        self._done = threading.Event()
        self._lifecycle = threading.Lock()
        self._started = False
        self._capture_thread: threading.Thread | None = None
        self._infer_thread: threading.Thread | None = None
        self._oom_hinted = False
        # frame-failure logging (inference thread only)
        self._fail_logged_at: float | None = None
        self._fail_suppressed = 0
        self._fail_latest = ""
        # guarded by self._lock
        self._lock = threading.Lock()
        self._state = "starting"
        self._frames_processed = 0
        self._processed_at: deque[float] = deque()
        self._jpeg: bytes | None = None
        self._started_at = datetime.now(timezone.utc)

    # ----- control -----

    def start(self) -> None:
        with self._lifecycle:
            if self._started:
                raise RuntimeError("a pipeline can only be started once")
            self._started = True
            with self._lock:
                self._started_at = datetime.now(timezone.utc)
            self._capture_thread = threading.Thread(
                target=self._capture_loop, name="sky-eye-capture", daemon=True
            )
            self._infer_thread = threading.Thread(
                target=self._infer_loop, name="sky-eye-inference", daemon=True
            )
            self._capture_thread.start()
            self._infer_thread.start()

    def stop(self) -> None:
        """Stop both threads and wait for them; harmless if already finished.

        Clips that have not started encoding are dropped (fast stop); the end
        of a file source instead finishes every clip.
        """
        with self._lifecycle:
            never_started = not self._started
            self._started = True  # a stopped pipeline cannot be started
        self._stop_event.set()
        self._channel.cancel()
        self._recorder.abort()
        if never_started:
            self._close_source()
            self._finish()
            return
        for thread in (self._capture_thread, self._infer_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join()

    def wait(self, timeout: float | None = None) -> bool:
        """True once the pipeline has finished (events closed)."""
        return self._done.wait(timeout)

    # ----- observers (any thread) -----

    def status(self) -> PipelineStatus:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            fps = len(self._processed_at) / _FPS_WINDOW_S
            state, processed, started_at = self._state, self._frames_processed, self._started_at
        return PipelineStatus(
            source_state=state,
            fps=fps,
            frames_processed=processed,
            trucks_now=self._events.active_count,
            trucks_total=self._events.total_count,
            started_at=started_at,
        )

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    # ----- capture thread -----

    def _capture_loop(self) -> None:
        running = False
        try:
            while not self._stop_event.is_set():
                frame = self._source.read()
                if frame is None:
                    if self._source.ended:
                        log.info("video source ended")
                        break
                    self._stop_event.wait(_RETRY_S)
                    continue
                if not running:
                    running = True
                    with self._lock:
                        if self._state == "starting":
                            self._state = "running"
                # every captured frame, so clips keep full rate when inference skips
                self._recorder.push(frame)
                if not self._channel.put(frame):
                    break
        except Exception:
            log.exception("video capture failed; the pipeline stops")
            with self._lock:
                self._state = "error"
        finally:
            self._close_source()
            self._channel.close()

    def _close_source(self) -> None:
        try:
            self._source.close()
        except Exception:
            log.exception("closing the video source failed")

    # ----- inference thread -----

    def _infer_loop(self) -> None:
        try:
            while (frame := self._channel.get()) is not None:
                try:
                    self._process(frame)
                except Exception as exc:
                    self._log_failure(frame, exc)
                    if _is_cuda_oom(exc) and not self._oom_hinted:
                        self._oom_hinted = True
                        log.error(_OOM_HINT)
                else:
                    self._summarise_failures(time.monotonic())
        finally:
            self._summarise_failures(time.monotonic(), force=True)
            # On stop the capture thread may still be running: release it, and let
            # it finish its last recorder.push() before events.close() closes the
            # recorder.
            self._channel.cancel()
            if self._capture_thread is not None:
                self._capture_thread.join()
            self._finish()

    def _log_failure(self, frame: Frame, exc: Exception) -> None:
        """Full traceback for the first failure; while failures repeat, a count
        at most every _FAIL_SUMMARY_S (called from an except block)."""
        now = time.monotonic()
        last = self._fail_logged_at
        if last is None or (self._fail_suppressed == 0 and now - last >= _FAIL_SUMMARY_S):
            log.exception("processing frame %d failed; frame skipped", frame.index)
            self._fail_logged_at = now
            return
        self._fail_suppressed += 1
        self._fail_latest = f"frame {frame.index}: {exc!r}"
        self._summarise_failures(now)

    def _summarise_failures(self, now: float, force: bool = False) -> None:
        if not self._fail_suppressed or self._fail_logged_at is None:
            return
        elapsed = now - self._fail_logged_at
        if not force and elapsed < _FAIL_SUMMARY_S:
            return
        log.error(
            "%d more frame(s) failed in the last %.0f s and were skipped (latest %s)",
            self._fail_suppressed,
            elapsed,
            self._fail_latest,
        )
        self._fail_logged_at = now
        self._fail_suppressed = 0

    def _process(self, frame: Frame) -> None:
        self._detector.confidence = self._runtime.confidence
        detections = self._detector.detect(frame.image)
        result = self._tracker.update(detections, frame)
        self._events.on_frame(frame, result)
        jpeg = self._encode(frame, result.confirmed)
        now = time.monotonic()
        with self._lock:
            if jpeg is not None:
                self._jpeg = jpeg
            self._frames_processed += 1
            self._processed_at.append(now)
            self._prune(now)

    def _encode(self, frame: Frame, tracks: list[Track]) -> bytes | None:
        image = annotate(frame, tracks)
        h, w = image.shape[:2]
        if w > self._stream_max_width:
            size = (self._stream_max_width, max(1, round(h * self._stream_max_width / w)))
            image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
        return buf.tobytes() if ok else None

    def _finish(self) -> None:
        try:
            self._events.close()
        except Exception:
            log.exception("closing the event manager failed")
        with self._lock:
            if self._state != "error":
                self._state = "ended"
        self._done.set()

    def _prune(self, now: float) -> None:
        # called with self._lock held
        cutoff = now - _FPS_WINDOW_S
        while self._processed_at and self._processed_at[0] < cutoff:
            self._processed_at.popleft()
