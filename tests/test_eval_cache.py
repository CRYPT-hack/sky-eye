from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from engine.core import Detection
from engine.evaluation.cache import (
    CachedFrame,
    DetectionCache,
    build_cache,
    load_cache,
    save_cache,
)


def _cache() -> DetectionCache:
    return DetectionCache(
        video="samples/x.mp4",
        width=640,
        height=360,
        fps=30.0,
        model="small",
        input_size=512,
        floor=0.05,
        classes=("car", "truck", "bus"),
        frames=[
            CachedFrame(0, 0.0, (Detection((1.0, 2.0, 30.0, 40.0), 0.5, "truck"),)),
            CachedFrame(1, 1 / 30, ()),
            CachedFrame(
                2,
                2 / 30,
                (
                    Detection((5.5, 6.5, 70.0, 80.0), 0.75, "car"),
                    Detection((9.0, 9.0, 19.0, 29.0), 0.25, "bus"),
                ),
            ),
        ],
    )


def test_cache_round_trip(tmp_path: Path):
    cache = _cache()
    path = tmp_path / "c.npz"
    save_cache(cache, path)
    loaded = load_cache(path)
    assert loaded == cache
    assert len(loaded.frames) == 3
    assert loaded.frames[1].detections == ()


class _OnePerFrame:
    def __init__(self):
        self.calls = 0

    def detect_raw(self, image, floor, class_names):
        self.calls += 1
        return [Detection((1.0, 2.0, 3.0, 4.0), 0.5, "truck")]


def test_build_cache_every_frame(tmp_path: Path):
    path = tmp_path / "v.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 24.0, (64, 48))
    for i in range(12):
        writer.write(np.full((48, 64, 3), i * 10, dtype=np.uint8))
    writer.release()

    det = _OnePerFrame()
    cache = build_cache(path, det, floor=0.05, classes=["truck"])
    assert len(cache.frames) == 12
    assert det.calls == 12
    assert (cache.width, cache.height) == (64, 48)
    for f in cache.frames:
        assert f.ts == f.index / cache.fps
        assert len(f.detections) == 1
    assert [f.index for f in cache.frames] == list(range(12))
