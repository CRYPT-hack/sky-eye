from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from engine.core import Frame


def make_video(path: Path, n_frames: int, fps: float = 30, size=(320, 240)) -> Path:
    """Write an mp4 where frame i has a white rectangle at x = 10 + 2*i."""
    w, h = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert writer.isOpened(), f"cannot open writer for {path}"
    for i in range(n_frames):
        img = np.zeros((h, w, 3), dtype=np.uint8)
        x = 10 + 2 * i
        cv2.rectangle(img, (x, 100), (x + 30, 130), (255, 255, 255), -1)
        writer.write(img)
    writer.release()
    return path


def fake_frame(index: int, ts: float) -> Frame:
    return Frame(
        image=np.zeros((240, 320, 3), dtype=np.uint8),
        ts=ts,
        wall_time=datetime.now(timezone.utc),
        index=index,
    )
