"""Synthetic detection streams with exact ground truth, so each tracking fix
can be proven against known truck identities."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from engine.core import Detection
from engine.evaluation.cache import CachedFrame, DetectionCache


@dataclass
class StreamSpec:
    n_trucks: int = 3
    n_cars: int = 2
    seconds: float = 10.0
    fps: float = 30.0
    size_px: int = 40
    speed_px_s: float = 60.0
    pan_px_s: float = 0.0  # camera pan: shifts every box, junk included
    score: float = 0.7
    # (truth id, start s, end s, score during the run)
    low_score_runs: list[tuple[int, float, float, float]] = field(default_factory=list)
    # (truth id, start s, end s, class name)
    class_flips: list[tuple[int, float, float, str]] = field(default_factory=list)
    # (truth id, start s, end s): no detection
    dropouts: list[tuple[int, float, float]] = field(default_factory=list)
    junk_per_s: float = 0.0  # one-frame random boxes at score 0.15-0.35
    frame: tuple[int, int] = (1280, 720)


def _clip(x1: float, y1: float, size: float, w: int, h: int):
    """Clip a square to the frame; None once fully outside."""
    cx1, cy1 = max(x1, 0.0), max(y1, 0.0)
    cx2, cy2 = min(x1 + size, float(w)), min(y1 + size, float(h))
    if cx2 <= cx1 or cy2 <= cy1:
        return None
    return (cx1, cy1, cx2, cy2)


def _in_run(ts: float, start: float, end: float) -> bool:
    return start <= ts < end


def make_stream(
    spec: StreamSpec, seed: int = 0
) -> tuple[DetectionCache, dict[int, int]]:
    """Build a cache-shaped stream and the true identity of each detection.

    The identity map is keyed by ``id(detection)`` and is valid only while the
    returned cache is alive (ids can be reused after garbage collection).
    Trucks are truth ids 0..n_trucks-1, cars follow. Junk detections are
    absent from the map. Deterministic for a given seed.
    """
    rng = np.random.default_rng(seed)
    w, h = spec.frame
    size = float(spec.size_px)
    n_obj = spec.n_trucks + spec.n_cars
    lane_gap = size / 2.0
    lane_pitch = size + lane_gap
    lane_y0 = lane_gap
    # Each object: own lane (separate y), enters from the left edge area.
    start_x = [float(rng.uniform(0.0, 300.0)) for _ in range(n_obj)]
    n_frames = int(round(spec.seconds * spec.fps))
    junk_rate = spec.junk_per_s / spec.fps

    frames: list[CachedFrame] = []
    identity: dict[int, int] = {}
    for index in range(n_frames):
        ts = index / spec.fps
        pan = spec.pan_px_s * ts
        dets: list[Detection] = []
        for k in range(n_obj):
            if any(t == k and _in_run(ts, a, b) for t, a, b in spec.dropouts):
                continue
            x = start_x[k] + spec.speed_px_s * ts + pan
            y = lane_y0 + k * lane_pitch
            box = _clip(x, y, size, w, h)
            if box is None:
                continue
            cls = "truck" if k < spec.n_trucks else "car"
            for t, a, b, name in spec.class_flips:
                if t == k and _in_run(ts, a, b):
                    cls = name
            score = spec.score
            for t, a, b, s in spec.low_score_runs:
                if t == k and _in_run(ts, a, b):
                    score = s
            det = Detection(box, float(score), cls)
            identity[id(det)] = k
            dets.append(det)
        for _ in range(int(rng.poisson(junk_rate))):
            jx = float(rng.uniform(0.0, max(w - size, 1.0)))
            jy = float(rng.uniform(0.0, max(h - size, 1.0)))
            js = float(rng.uniform(0.15, 0.35))
            box = _clip(jx + pan, jy, size, w, h)
            if box is None:
                continue
            dets.append(Detection(box, js, "truck"))
        frames.append(CachedFrame(index, ts, tuple(dets)))

    cache = DetectionCache(
        video="synthetic",
        width=w,
        height=h,
        fps=spec.fps,
        model="synthetic",
        input_size=0,
        floor=0.0,
        classes=("car", "truck", "bus"),
        frames=frames,
    )
    return cache, identity
