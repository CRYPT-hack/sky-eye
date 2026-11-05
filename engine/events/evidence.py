"""Evidence capture: frame ring buffer, JPEG snapshots and H.264 MP4 clips."""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

import av
import cv2
import numpy as np

from engine.core import DEFAULT_FPS, Frame

log = logging.getLogger(__name__)

_ENCODER_CANDIDATES = ["libx264", "h264_nvenc", "libopenh264", "h264_mf"]
_encoder_lock = threading.Lock()
_encoder_cache: str | None = None


class FrameRingBuffer:
    """Keeps frames whose ts >= newest.ts - seconds."""

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self._frames: deque[Frame] = deque()

    def push(self, frame: Frame) -> None:
        self._frames.append(frame)
        cutoff = frame.ts - self._seconds
        while self._frames and self._frames[0].ts < cutoff:
            self._frames.popleft()

    def since(self, ts: float) -> list[Frame]:
        return [f for f in self._frames if f.ts >= ts]


def _even(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    return image[: h - (h % 2), : w - (w % 2)]


def _encode(encoder: str, images: list[np.ndarray], path: Path, fps: float) -> None:
    height, width = images[0].shape[:2]
    with av.open(str(path), "w", format="mp4") as container:
        stream = container.add_stream(encoder, rate=max(1, round(fps)))
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        if encoder == "libx264":
            stream.options = {"preset": "veryfast", "crf": "23"}
        for image in images:
            av_frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            for packet in stream.encode(av_frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def _encoder_works(name: str) -> bool:
    probe = np.zeros((256, 256, 3), dtype=np.uint8)
    probe[64:128, 64:128] = 255
    fd, tmp = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        _encode(name, [probe] * 5, Path(tmp), DEFAULT_FPS)
        return True
    except Exception:  # noqa: BLE001 - any failure means "unusable here"
        return False
    finally:
        Path(tmp).unlink(missing_ok=True)


def pick_h264_encoder() -> str:
    """First H.264 encoder that exists in this PyAV build and actually works."""
    global _encoder_cache
    with _encoder_lock:
        if _encoder_cache is not None:
            return _encoder_cache
        for name in _ENCODER_CANDIDATES:
            try:
                av.codec.Codec(name, "w")
            except Exception:  # noqa: BLE001 - unknown/unavailable encoder
                continue
            if _encoder_works(name):
                _encoder_cache = name
                return name
        raise RuntimeError(
            "no usable H.264 encoder; tried " + ", ".join(_ENCODER_CANDIDATES)
        )


def write_clip(frames: list[Frame], path: Path, fps: float) -> None:
    """Write frames as an H.264 yuv420p MP4 with even dimensions."""
    if not frames:
        raise ValueError("cannot write a clip with no frames")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _encode(pick_h264_encoder(), [_even(f.image) for f in frames], path, fps)


def save_snapshot(
    frame: Frame,
    box: tuple[float, float, float, float],
    full_path: Path,
    crop_path: Path,
) -> None:
    """Save the full frame and a crop padded 15% per side (clamped), JPEG q90."""
    params = [cv2.IMWRITE_JPEG_QUALITY, 90]
    image = frame.image
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    pad_x, pad_y = 0.15 * (x2 - x1), 0.15 * (y2 - y1)
    cx1 = max(0, int(round(x1 - pad_x)))
    cy1 = max(0, int(round(y1 - pad_y)))
    cx2 = min(w, int(round(x2 + pad_x)))
    cy2 = min(h, int(round(y2 + pad_y)))
    for p in (full_path, crop_path):
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(full_path), image, params):
        raise OSError(f"failed to write {full_path}")
    if not cv2.imwrite(str(crop_path), image[cy1:cy2, cx1:cx2], params):
        raise OSError(f"failed to write {crop_path}")


@dataclass
class _Job:
    key: str
    end_ts: float
    path: Path
    on_done: Callable[[Path], None]
    frames: list[Frame] = field(default_factory=list)


def _clip_fps(frames: list[Frame]) -> float:
    if len(frames) > 1:
        span = frames[-1].ts - frames[0].ts
        if span > 0:
            return (len(frames) - 1) / span
    return DEFAULT_FPS


class EvidenceRecorder:
    """Ring buffer plus background clip writer.

    push() runs on the capture thread, start_clip() on the inference thread;
    on_done callbacks run on the single worker thread.
    """

    def __init__(
        self,
        data_dir: Path,
        before_s: float,
        after_s: float,
        max_width: int = 1280,
        max_fps: float = 30.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self._max_width = max_width
        self._min_interval = 1.0 / max_fps - 1e-3
        self._last_ts: float | None = None
        self._before_s = before_s
        self._after_s = after_s
        self._ring = FrameRingBuffer(before_s + after_s + 1)
        self._lock = threading.Lock()
        self._pending: list[_Job] = []
        self._futures: list[Future] = []
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evidence")

    def _downscale(self, frame: Frame) -> Frame:
        h, w = frame.image.shape[:2]
        if w <= self._max_width:
            return frame
        size = (self._max_width, max(1, round(h * self._max_width / w)))
        return replace(frame, image=cv2.resize(frame.image, size, interpolation=cv2.INTER_AREA))

    def push(self, frame: Frame) -> None:
        with self._lock:
            if self._last_ts is not None and frame.ts - self._last_ts < self._min_interval:
                return
            self._last_ts = frame.ts
            frame = self._downscale(frame)
            self._ring.push(frame)
            finished = []
            for job in self._pending:
                job.frames.append(frame)
                if frame.ts >= job.end_ts:
                    finished.append(job)
            for job in finished:
                self._pending.remove(job)
                self._submit(job)

    def start_clip(
        self,
        key: str,
        trigger_ts: float,
        path: Path,
        on_done: Callable[[Path], None],
    ) -> None:
        job = _Job(
            key=key,
            end_ts=trigger_ts + self._after_s,
            path=Path(path),
            on_done=on_done,
        )
        with self._lock:
            job.frames = self._ring.since(trigger_ts - self._before_s)
            if job.frames and job.frames[-1].ts >= job.end_ts:
                self._submit(job)
            else:
                self._pending.append(job)

    def flush(self) -> None:
        """Finish pending clips with the frames they have; block until all done."""
        with self._lock:
            for job in self._pending:
                self._submit(job)
            self._pending.clear()
            futures = list(self._futures)
        for future in futures:
            future.result()
        with self._lock:
            self._futures = [f for f in self._futures if not f.done()]

    def close(self) -> None:
        self.flush()
        self._executor.shutdown(wait=True)

    def _submit(self, job: _Job) -> None:
        self._futures.append(self._executor.submit(self._run, job))

    @staticmethod
    def _run(job: _Job) -> None:
        if not job.frames:
            log.warning("clip %s has no frames; skipped", job.key)
            return
        try:
            write_clip(job.frames, job.path, _clip_fps(job.frames))
        except Exception:
            log.exception("failed to write clip %s", job.path)
            return
        try:
            job.on_done(job.path)
        except Exception:
            log.exception("on_done failed for clip %s", job.path)
