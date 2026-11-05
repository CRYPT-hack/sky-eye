from __future__ import annotations

from datetime import datetime, timezone

import av
import cv2
import numpy as np

from engine.core import Frame
from engine.events.evidence import (
    EvidenceRecorder,
    FrameRingBuffer,
    pick_h264_encoder,
    save_snapshot,
)

FPS = 30.0


def moving_frame(index: int, size=(320, 240)) -> Frame:
    w, h = size
    img = np.full((h, w, 3), 40, dtype=np.uint8)
    x = (10 + 3 * index) % (w - 40)
    cv2.rectangle(img, (x, 100), (x + 30, 130), (255, 255, 255), -1)
    return Frame(
        image=img,
        ts=index / FPS,
        wall_time=datetime.now(timezone.utc),
        index=index,
    )


def count_frames(path):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        name = stream.codec_context.name
        n = sum(1 for _ in container.decode(stream))
    return name, n


def test_ring_keeps_last_seconds():
    ring = FrameRingBuffer(seconds=3)
    for i in range(120):
        ring.push(moving_frame(i))
    frames = ring.since(0)
    newest = frames[-1].ts
    assert newest == 119 / FPS
    assert frames[0].ts >= newest - 3
    assert frames[0].ts < newest - 3 + 1 / FPS + 1e-9
    assert [f.ts for f in frames] == sorted(f.ts for f in frames)
    assert all(f.ts >= 2.0 for f in ring.since(2.0))


def test_h264_encoder_available():
    assert pick_h264_encoder() in ["libx264", "h264_nvenc", "libopenh264", "h264_mf"]


def test_clip_has_before_and_after(tmp_path):
    rec = EvidenceRecorder(tmp_path, before_s=3, after_s=5)
    done = []
    path = tmp_path / "clip.mp4"
    for i in range(300):
        rec.push(moving_frame(i))
        if i == 120:
            rec.start_clip("k", 4.0, path, done.append)
    rec.flush()
    rec.close()
    assert done == [path]
    name, n = count_frames(path)
    assert name == "h264"
    assert abs(n - 240) <= 2


def test_clip_shorter_when_video_ends(tmp_path):
    rec = EvidenceRecorder(tmp_path, before_s=3, after_s=5)
    done = []
    path = tmp_path / "short.mp4"
    for i in range(0, 91):
        rec.push(moving_frame(i))
        if i == 30:
            rec.start_clip("k", 1.0, path, done.append)
    rec.flush()
    assert done == [path]
    _, n = count_frames(path)
    assert abs(n - 91) <= 2
    rec.close()


def test_snapshot_and_padded_crop(tmp_path):
    full, crop = tmp_path / "full.jpg", tmp_path / "crop.jpg"
    save_snapshot(moving_frame(0), (100, 100, 140, 120), full, crop)
    assert full.exists() and crop.exists()
    h, w = cv2.imread(str(crop)).shape[:2]
    assert abs(w - 52) <= 1 and abs(h - 26) <= 1
    assert cv2.imread(str(full)).shape[:2] == (240, 320)


def test_odd_frame_size_clip(tmp_path):
    rec = EvidenceRecorder(tmp_path, before_s=1, after_s=1)
    done = []
    path = tmp_path / "odd.mp4"
    for i in range(60):
        rec.push(moving_frame(i, size=(321, 241)))
        if i == 10:
            rec.start_clip("k", 10 / FPS, path, done.append)
    rec.close()
    assert done == [path]
    assert count_frames(path)[1] > 0
