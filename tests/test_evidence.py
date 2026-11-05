from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import av
import cv2
import numpy as np

from engine.core import Frame
from engine.events.evidence import (
    EvidenceRecorder,
    FrameRingBuffer,
    pick_h264_encoder,
    save_snapshot,
    write_clip,
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


def _clip_info(path):
    with av.open(str(path)) as c:
        st = c.streams.video[0]
        n = sum(1 for _ in c.decode(st))
        return n, st.codec_context.width, st.codec_context.height, float(st.average_rate)


def _run(tmp_path, n, fps, size=(320, 240)):
    rec = EvidenceRecorder(tmp_path, before_s=5, after_s=5)
    path = tmp_path / "c.mp4"
    for i in range(n):
        f = moving_frame(i, size=size)
        rec.push(Frame(f.image, i / fps, f.wall_time, i))
        if i == 0:
            rec.start_clip("k", 0.0, path, lambda p: None)
    rec.close()
    return _clip_info(path)


def test_60fps_input_capped_to_30fps(tmp_path):
    n, _, _, fps = _run(tmp_path, 120, 60.0)
    assert abs(n - 60) <= 2
    assert abs(fps - 30) <= 1


def test_large_frames_downscaled_to_1280(tmp_path):
    _, w, h, _ = _run(tmp_path, 10, 30.0, size=(1920, 1080))
    assert (w, h) == (1280, 720)


def test_2997fps_keeps_every_frame(tmp_path):
    n, _, _, _ = _run(tmp_path, 60, 29.97)
    assert n == 60


# ----- bounded clip jobs, atomic writes, fast stop -----

import logging  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

import engine.events.evidence as evidence_mod  # noqa: E402
from engine.events.evidence import write_clip  # noqa: E402


class GatedWriter:
    """Stand-in for write_clip: blocks every encode until release() is called;
    like the real encoder it gives up (ClipAborted) once should_stop() is true."""

    def __init__(self) -> None:
        self.started: list = []
        self.started_evt = threading.Event()
        self._gate = threading.Event()

    def __call__(self, frames, path, fps, should_stop=None) -> None:
        self.started.append(path)
        self.started_evt.set()
        deadline = time.monotonic() + 10
        while not self._gate.wait(0.01):
            if should_stop is not None and should_stop():
                raise evidence_mod.ClipAborted(str(path))
            assert time.monotonic() < deadline, "gate never released"
        path.write_bytes(b"clip")

    def release(self) -> None:
        self._gate.set()


def _fill(rec: EvidenceRecorder, n: int) -> None:
    for i in range(n):
        rec.push(moving_frame(i))


def test_clip_job_cap_skips_seventh(tmp_path, monkeypatch, caplog):
    writer = GatedWriter()
    monkeypatch.setattr(evidence_mod, "write_clip", writer)
    rec = EvidenceRecorder(tmp_path, before_s=0.5, after_s=0.5)  # default cap: 6
    done: list = []
    _fill(rec, 60)  # ring holds ts 0..1.97
    try:
        for k in range(3):  # complete in the ring: queued (one of them running)
            rec.start_clip(f"q{k}", 0.2, tmp_path / f"q{k}.mp4", done.append)
        assert writer.started_evt.wait(5)
        for k in range(3):  # end in the future: pending
            rec.start_clip(f"p{k}", 1.9, tmp_path / f"p{k}.mp4", done.append)
        with caplog.at_level(logging.WARNING, logger="engine.events.evidence"):
            rec.start_clip("seventh", 1.9, tmp_path / "seventh.mp4", done.append)
        assert any("seventh" in r.getMessage() for r in caplog.records)
    finally:
        writer.release()
        rec.close()
    assert len(done) == 6
    assert tmp_path / "seventh.mp4" not in done
    assert not (tmp_path / "seventh.mp4").exists()


def test_cap_frees_up_after_jobs_finish(tmp_path):
    rec = EvidenceRecorder(tmp_path, before_s=0.2, after_s=0.2, max_jobs=1)
    done: list = []
    _fill(rec, 30)
    rec.start_clip("a", 0.3, tmp_path / "a.mp4", done.append)
    rec.flush()
    rec.start_clip("b", 0.3, tmp_path / "b.mp4", done.append)
    rec.close()
    assert done == [tmp_path / "a.mp4", tmp_path / "b.mp4"]


def test_x264_threads_capped(tmp_path):
    if pick_h264_encoder() != "libx264":
        pytest.skip("thread cap only applies to libx264")
    path = tmp_path / "t.mp4"
    write_clip([moving_frame(i, size=(640, 480)) for i in range(10)], path, FPS)
    assert b"threads=2" in path.read_bytes()  # x264 records its settings in the stream


def test_clip_written_via_temp_file(tmp_path, monkeypatch):
    seen: list = []
    real = evidence_mod._encode

    def spy(encoder, images, path, fps, *rest):
        seen.append(path)
        real(encoder, images, path, fps, *rest)

    monkeypatch.setattr(evidence_mod, "_encode", spy)
    path = tmp_path / "c.mp4"
    write_clip([moving_frame(i) for i in range(5)], path, FPS)
    assert seen and seen[0] != path and seen[0].parent == path.parent
    assert path.is_file()
    assert not seen[0].exists()
    assert list(tmp_path.iterdir()) == [path]


def test_failed_encode_leaves_no_files(tmp_path, monkeypatch):
    def broken(encoder, images, path, fps, *rest):
        Path(path).write_bytes(b"half a clip")
        raise RuntimeError("encoder died")

    monkeypatch.setattr(evidence_mod, "_encode", broken)
    rec = EvidenceRecorder(tmp_path, before_s=0.2, after_s=0.2)
    done: list = []
    _fill(rec, 30)
    rec.start_clip("k", 0.3, tmp_path / "media" / "k.mp4", done.append)
    rec.close()
    assert done == []
    assert list((tmp_path / "media").iterdir()) == []  # no .mp4 and no temp file


def test_abort_drops_unstarted_jobs(tmp_path, monkeypatch):
    writer = GatedWriter()
    monkeypatch.setattr(evidence_mod, "write_clip", writer)
    rec = EvidenceRecorder(tmp_path, before_s=0.5, after_s=0.5)
    done: list = []
    _fill(rec, 60)
    rec.start_clip("running", 0.2, tmp_path / "running.mp4", done.append)
    assert writer.started_evt.wait(5)
    for k in range(3):
        rec.start_clip(f"queued{k}", 0.2, tmp_path / f"queued{k}.mp4", done.append)
    rec.start_clip("pending", 1.9, tmp_path / "pending.mp4", done.append)
    rec.abort()
    rec.start_clip("late", 1.9, tmp_path / "late.mp4", done.append)  # after abort: skipped
    t0 = time.monotonic()
    rec.close()  # the gate is never released: the running encode must give up
    assert time.monotonic() - t0 < 1.0
    assert done == []  # no on_done for the aborted encode nor the dropped clips
    assert writer.started == [tmp_path / "running.mp4"]
    assert list(tmp_path.iterdir()) == []


def test_close_drop_pending(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(evidence_mod, "write_clip", lambda f, p, fps, **k: calls.append(p))
    rec = EvidenceRecorder(tmp_path, before_s=0.5, after_s=0.5)
    done: list = []
    _fill(rec, 60)
    rec.start_clip("pending", 1.9, tmp_path / "pending.mp4", done.append)
    rec.close(drop_pending=True)
    assert done == [] and calls == []


@pytest.mark.parametrize(
    "box",
    [
        (500.0, 500.0, 600.0, 600.0),  # entirely right of / below the image
        (-50.0, -50.0, -10.0, -10.0),  # entirely above / left of it
        (100.0, 100.0, 100.0, 100.0),  # zero size
        (319.6, 239.6, 400.0, 400.0),  # starts on the last pixel
    ],
)
def test_snapshot_crop_clamped_to_at_least_one_pixel(tmp_path, box):
    full, crop = tmp_path / "full.jpg", tmp_path / "crop.jpg"
    save_snapshot(moving_frame(0), box, full, crop)
    img = cv2.imdecode(np.fromfile(str(crop), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[0] >= 1 and img.shape[1] >= 1


def test_snapshot_in_non_ascii_dir(tmp_path):
    folder = tmp_path / "трак 卡车"
    full, crop = folder / "full.jpg", folder / "crop.jpg"
    save_snapshot(moving_frame(0), (100, 100, 140, 120), full, crop)
    img = cv2.imdecode(np.fromfile(str(full), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[:2] == (240, 320)
    assert crop.is_file()


@pytest.mark.parametrize("stop_after", [0, 3])
def test_aborted_encode_leaves_no_files(tmp_path, stop_after):
    calls = [0]

    def should_stop() -> bool:
        calls[0] += 1
        return calls[0] > stop_after

    path = tmp_path / "a.mp4"
    with pytest.raises(evidence_mod.ClipAborted):
        write_clip([moving_frame(i) for i in range(20)], path, FPS, should_stop=should_stop)
    assert list(tmp_path.iterdir()) == []  # neither the clip nor its .part


def test_remove_partial_clips(tmp_path):
    from engine.storage import remove_partial_clips

    day = tmp_path / "2026-11-05"
    day.mkdir()
    (day / "1_3.mp4.part").write_bytes(b"half")
    (day / "1_2.mp4").write_bytes(b"clip")
    (day / "1_2_full.jpg").write_bytes(b"jpg")
    assert remove_partial_clips(tmp_path) == 1
    assert sorted(p.name for p in day.iterdir()) == ["1_2.mp4", "1_2_full.jpg"]


def _top_level_boxes(path) -> list[str]:
    data = Path(path).read_bytes()
    boxes, pos = [], 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8].decode("latin-1")
        header = 8
        if size == 1:
            size = int.from_bytes(data[pos + 8 : pos + 16], "big")
            header = 16
        elif size == 0:
            size = len(data) - pos
        assert size >= header
        boxes.append(kind)
        pos += size
    assert pos == len(data)
    return boxes


def test_clip_is_faststart(tmp_path):
    path = tmp_path / "c.mp4"
    write_clip([moving_frame(i) for i in range(20)], path, FPS)
    boxes = _top_level_boxes(path)
    assert "moov" in boxes and "mdat" in boxes
    assert boxes.index("moov") < boxes.index("mdat")
