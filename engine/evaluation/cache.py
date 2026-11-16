"""Run the detector once per video and keep the raw boxes, so tracking
experiments can replay them without the GPU."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from engine.core import DEFAULT_FPS, Detection


@dataclass(frozen=True)
class CachedFrame:
    index: int
    ts: float
    detections: tuple[Detection, ...]


@dataclass
class DetectionCache:
    video: str
    width: int
    height: int
    fps: float
    model: str
    input_size: int
    floor: float
    classes: tuple[str, ...]
    frames: list[CachedFrame]


def build_cache(
    video: Path,
    detector,
    floor: float,
    classes: Sequence[str],
    model: str = "",
    input_size: int = 0,
    progress: Callable[[int], None] | None = None,
) -> DetectionCache:
    """Detect on every frame in order (no pacing, no dropping); ts = index / fps."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError(f"cannot open video {video}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or DEFAULT_FPS
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frames: list[CachedFrame] = []
        index = 0
        while True:
            ok, image = cap.read()
            if not ok:
                break
            dets = detector.detect_raw(image, floor, list(classes))
            frames.append(CachedFrame(index, index / fps, tuple(dets)))
            index += 1
            if progress is not None and index % 100 == 0:
                progress(index)
    finally:
        cap.release()
    return DetectionCache(
        video=str(video),
        width=width,
        height=height,
        fps=fps,
        model=model,
        input_size=input_size,
        floor=floor,
        classes=tuple(classes),
        frames=frames,
    )


def save_cache(cache: DetectionCache, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    class_index = {c: i for i, c in enumerate(cache.classes)}
    det_frame: list[int] = []
    boxes: list[tuple[float, float, float, float]] = []
    scores: list[float] = []
    class_idx: list[int] = []
    for f in cache.frames:
        for d in f.detections:
            det_frame.append(f.index)
            boxes.append(d.box)
            scores.append(d.score)
            class_idx.append(class_index[d.class_name])
    header = {
        "video": cache.video,
        "width": cache.width,
        "height": cache.height,
        "fps": cache.fps,
        "model": cache.model,
        "input_size": cache.input_size,
        "floor": cache.floor,
        "classes": list(cache.classes),
    }
    np.savez_compressed(
        path,
        header=np.array(json.dumps(header)),
        frame_index=np.array([f.index for f in cache.frames], dtype=np.int64),
        ts=np.array([f.ts for f in cache.frames], dtype=np.float64),
        det_frame=np.array(det_frame, dtype=np.int64),
        boxes=np.array(boxes, dtype=np.float32).reshape(-1, 4),
        scores=np.array(scores, dtype=np.float32),
        class_idx=np.array(class_idx, dtype=np.int16),
    )


def load_cache(path: Path) -> DetectionCache:
    with np.load(Path(path), allow_pickle=False) as z:
        header = json.loads(str(z["header"]))
        frame_index = z["frame_index"]
        ts = z["ts"]
        det_frame = z["det_frame"]
        boxes = z["boxes"]
        scores = z["scores"]
        class_idx = z["class_idx"]
    classes = tuple(header["classes"])
    by_frame: dict[int, list[Detection]] = {int(i): [] for i in frame_index}
    for fi, box, score, ci in zip(det_frame, boxes, scores, class_idx):
        by_frame[int(fi)].append(
            Detection(tuple(float(v) for v in box), float(score), classes[int(ci)])
        )
    frames = [
        CachedFrame(int(i), float(t), tuple(by_frame[int(i)]))
        for i, t in zip(frame_index, ts)
    ]
    return DetectionCache(
        video=header["video"],
        width=header["width"],
        height=header["height"],
        fps=header["fps"],
        model=header["model"],
        input_size=header["input_size"],
        floor=header["floor"],
        classes=classes,
        frames=frames,
    )
