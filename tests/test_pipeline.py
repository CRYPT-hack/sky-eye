from __future__ import annotations

import logging
import queue
import sys
import time
import types
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from engine.annotate import annotate
from engine.config import TrackerConfig
from engine.core import Detection, Frame, Track
from engine.detectors.fake import FakeDetector
from engine.events.bus import EventBus
from engine.events.evidence import EvidenceRecorder
from engine.events.manager import EventManager
from engine.pipeline import Pipeline, RuntimeSettings
from engine.sources.file import FileSource
from engine.storage import TruckRepository
from engine.tracking import build_tracker
from tests.conftest import fake_frame, make_video

BOX_A = (20.0, 20.0, 80.0, 80.0)
BOX_B = (200.0, 140.0, 300.0, 220.0)
OOM_HINT = "lower detector.input_size or use size: nano"


def truck(box, score=0.8) -> Detection:
    return Detection(box, score, "truck")


def nothing(i: int) -> list[Detection]:
    return []


def raise_once_at(calls: dict[int, Exception]):
    """Script raising calls[i] the first time call index i is seen.

    FakeDetector does not advance its call index when the script raises, so a
    script that always raised at index i would raise forever.
    """
    pending = dict(calls)

    def script(i: int) -> list[Detection]:
        if i in pending:
            raise pending.pop(i)
        return []

    return script


def drain(q: queue.Queue) -> list[dict]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def clip_frame_count(path) -> int:
    cap = cv2.VideoCapture(str(path))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


def wait_until(cond, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class ScriptedSource:
    """VideoSource whose read() returns (or raises) the scripted items, then ends."""

    def __init__(self, items: list) -> None:
        self._items = list(items)
        self._ended = False
        self.closed = False

    def open(self) -> None:
        pass

    def read(self) -> Frame | None:
        if not self._items:
            self._ended = True
            return None
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True

    @property
    def fps(self) -> float:
        return 30.0

    @property
    def ended(self) -> bool:
        return self._ended


class Rig:
    def __init__(
        self,
        tmp_path,
        video,
        detector: FakeDetector,
        *,
        realtime: bool = False,
        runtime: RuntimeSettings | None = None,
        stream_max_width: int = 1280,
        before_s: float = 0.3,
        after_s: float = 4.5,
    ):
        self.data_dir = tmp_path / "data"
        self.repo = TruckRepository(self.data_dir / "sky-eye.db")
        self.run_id = self.repo.create_run("test", "{}")
        self.bus = EventBus()
        self.q = self.bus.subscribe(maxsize=1000)
        self.recorder = EvidenceRecorder(self.data_dir, before_s=before_s, after_s=after_s)
        self.events = EventManager(
            self.repo, self.recorder, self.bus, self.run_id, self.data_dir, 2.0, 10**9
        )
        if hasattr(video, "read"):
            self.source = video
        else:
            self.source = FileSource(video, realtime=realtime, loop=False)
        self.source.open()
        self.detector = detector
        self.runtime = runtime if runtime is not None else RuntimeSettings()
        self.pipeline = Pipeline(
            self.source,
            detector,
            build_tracker(TrackerConfig(), self.source.fps),
            self.events,
            self.recorder,
            self.runtime,
            realtime=realtime,
            stream_max_width=stream_max_width,
        )

    def close(self):
        self.pipeline.stop()
        self.repo.close()


def test_end_to_end_two_trucks(tmp_path):
    video = make_video(tmp_path / "v.mp4", 180)

    def script(i: int) -> list[Detection]:
        dets = []
        if 10 <= i <= 120:
            dets.append(truck(BOX_A))
        if 60 <= i <= 179:
            dets.append(truck(BOX_B))
        return dets

    rig = Rig(tmp_path, video, FakeDetector(script))
    try:
        rig.pipeline.start()
        assert rig.pipeline.wait(30) is True
        assert rig.repo.count_trucks(rig.run_id) == 2
        a, b = sorted(rig.repo.list_trucks(), key=lambda r: r.first_seen)
        media = rig.data_dir / "media"
        for rec in (a, b):
            assert (media / rec.snapshot_path).is_file()
            assert (media / rec.crop_path).is_file()
            assert rec.clip_path is not None and (media / rec.clip_path).is_file()
        # B was still visible at the end: its clip stops at the last video frame
        assert 0 < clip_frame_count(media / b.clip_path) < clip_frame_count(media / a.clip_path)
        news = [m for m in drain(rig.q) if m["type"] == "truck.new"]
        assert len(news) == 2
        status = rig.pipeline.status()
        assert status.source_state == "ended" and status.frames_processed == 180
        assert status.trucks_total == 2
        assert status.trucks_now == 0  # all events closed at video end
    finally:
        rig.close()


def test_runtime_confidence_applied(tmp_path):
    assert RuntimeSettings().confidence == 0.4
    assert RuntimeSettings().sahi_enabled is False
    for bad in (0.0, 1.0, 1.5):
        with pytest.raises(ValidationError):
            RuntimeSettings(confidence=bad)

    video = make_video(tmp_path / "v.mp4", 10)
    runtime = RuntimeSettings(confidence=0.4)
    with pytest.raises(ValidationError):
        runtime.confidence = 1.0  # validated on assignment (the API mutates it in place)
    assert runtime.confidence == 0.4

    seen: list[float] = []

    def script(i: int) -> list[Detection]:
        seen.append(detector.confidence)
        if i == 0:
            runtime.confidence = 0.9  # e.g. changed from the dashboard mid-run
        return []

    detector = FakeDetector(script, confidence=0.1)
    rig = Rig(tmp_path, video, detector, runtime=runtime)
    try:
        rig.pipeline.start()
        assert rig.pipeline.wait(10) is True
        assert seen[0] == 0.4  # pipeline copied runtime.confidence into the detector
        assert seen[1] == 0.9  # ...and picked up the change on the next frame
    finally:
        rig.close()


def test_latest_jpeg_available(tmp_path):
    video = make_video(tmp_path / "v.mp4", 90, size=(640, 480))
    rig = Rig(
        tmp_path,
        video,
        FakeDetector(lambda i: [truck(BOX_A)]),
        realtime=True,
        stream_max_width=320,
    )
    try:
        assert rig.pipeline.latest_jpeg() is None
        rig.pipeline.start()
        assert wait_until(lambda: rig.pipeline.latest_jpeg() is not None, 2.0)
        data = rig.pipeline.latest_jpeg()
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert image is not None
        assert image.shape[1] <= 320
    finally:
        rig.close()


def test_realtime_drops_frames_not_lag(tmp_path):
    video = make_video(tmp_path / "v.mp4", 90)

    def slow(i: int) -> list[Detection]:
        time.sleep(0.1)
        return []

    rig = Rig(tmp_path, video, FakeDetector(slow), realtime=True)
    try:
        t0 = time.monotonic()
        rig.pipeline.start()
        assert rig.pipeline.wait(10) is True
        elapsed = time.monotonic() - t0
        status = rig.pipeline.status()
        assert elapsed < 4.5
        assert 0 < status.frames_processed < 90
        assert status.source_state == "ended"
    finally:
        rig.close()


def test_detector_exception_does_not_kill_pipeline(tmp_path, caplog):
    video = make_video(tmp_path / "v.mp4", 30)
    detector = FakeDetector(raise_once_at({5: RuntimeError("boom")}))
    rig = Rig(tmp_path, video, detector)
    try:
        with caplog.at_level(logging.ERROR):
            rig.pipeline.start()
            assert rig.pipeline.wait(10) is True
        status = rig.pipeline.status()
        assert status.source_state == "ended"
        assert status.frames_processed == 29  # only the failing frame was skipped
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR and r.exc_info]
        assert len(errors) == 1 and "boom" in str(errors[0].exc_info[1])
    finally:
        rig.close()


def test_cuda_oom_logs_hint_once(tmp_path, caplog, monkeypatch):
    # Stand-in for torch (importing the real one takes ~15 s); the pipeline only
    # looks at an already-imported torch, since only torch can raise its OOM error.
    class OutOfMemoryError(RuntimeError):
        pass

    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(OutOfMemoryError=OutOfMemoryError)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    video = make_video(tmp_path / "v.mp4", 20)
    detector = FakeDetector(
        raise_once_at({3: OutOfMemoryError("CUDA out of memory"), 6: OutOfMemoryError("again")})
    )
    rig = Rig(tmp_path, video, detector)
    try:
        with caplog.at_level(logging.ERROR):
            rig.pipeline.start()
            assert rig.pipeline.wait(10) is True
        hints = [r for r in caplog.records if OOM_HINT in r.getMessage()]
        assert len(hints) == 1
        assert rig.pipeline.status().frames_processed == 18
    finally:
        rig.close()


def test_stop_mid_video_is_prompt(tmp_path):
    video = make_video(tmp_path / "v.mp4", 300)  # 10 s at realtime pace
    rig = Rig(tmp_path, video, FakeDetector(nothing), realtime=True)
    closes: list[int] = []
    original_close = rig.events.close

    def counting_close():
        closes.append(1)
        original_close()

    rig.events.close = counting_close
    try:
        rig.pipeline.start()
        assert wait_until(lambda: rig.pipeline.status().frames_processed >= 3, 3.0)
        t0 = time.monotonic()
        rig.pipeline.stop()
        assert time.monotonic() - t0 < 1.0
        assert rig.pipeline.wait(0) is True
        assert rig.pipeline.status().source_state == "ended"
        rig.pipeline.stop()  # second stop is harmless
        assert closes == [1]
    finally:
        rig.close()
    assert closes == [1]


def test_none_from_live_source_is_not_end_of_stream(tmp_path):
    # a capture device can return None transiently without ever ending
    frames = [fake_frame(i, i / 30) for i in range(3)]
    source = ScriptedSource([None, None, frames[0], None, frames[1], frames[2]])
    rig = Rig(tmp_path, source, FakeDetector(nothing))
    try:
        rig.pipeline.start()
        assert rig.pipeline.wait(10) is True
        status = rig.pipeline.status()
        assert status.frames_processed == 3
        assert status.source_state == "ended"
        assert source.closed
    finally:
        rig.close()


def test_capture_crash_sets_error_state(tmp_path, caplog):
    frames = [fake_frame(i, i / 30) for i in range(2)]
    source = ScriptedSource([frames[0], frames[1], RuntimeError("device lost")])
    rig = Rig(tmp_path, source, FakeDetector(nothing))
    try:
        with caplog.at_level(logging.ERROR):
            rig.pipeline.start()
            assert rig.pipeline.wait(10) is True
        status = rig.pipeline.status()
        assert status.source_state == "error"
        assert status.frames_processed == 2  # frames already captured are still processed
        assert source.closed
        assert any(r.exc_info and "device lost" in str(r.exc_info[1]) for r in caplog.records)
    finally:
        rig.close()


def test_annotate_draws_without_mutating_frame():
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    frame = Frame(image, 0.0, datetime.now(timezone.utc), 0)
    out = annotate(frame, [Track(7, truck(BOX_B, 0.87))])
    assert out.shape == image.shape
    assert out.any()  # box and label were drawn
    assert not image.any()  # the frame (shared with the evidence buffer) is untouched
    assert np.array_equal(annotate(frame, []), image)


def test_stop_with_clip_backlog_is_prompt(tmp_path, monkeypatch):
    import threading

    import engine.events.evidence as evidence_mod

    started = threading.Event()

    def slow_write(frames, path, fps, should_stop=None):
        started.set()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if should_stop is not None and should_stop():
                raise evidence_mod.ClipAborted(str(path))
            time.sleep(0.01)
        path.write_bytes(b"clip")

    monkeypatch.setattr(evidence_mod, "write_clip", slow_write)
    boxes = [(x, 0.0, x + 40.0, 30.0) for x in (0.0, 60.0, 120.0, 180.0, 240.0)]
    boxes.append((0.0, 120.0, 40.0, 150.0))
    video = make_video(tmp_path / "v.mp4", 300)  # 10 s at realtime pace
    rig = Rig(
        tmp_path,
        video,
        FakeDetector(lambda i: [truck(b) for b in boxes]),
        realtime=True,
        after_s=0.2,
    )
    try:
        rig.pipeline.start()
        assert wait_until(lambda: rig.pipeline.status().trucks_total == 6, 5.0)
        assert started.wait(5.0)
        t0 = time.monotonic()
        rig.pipeline.stop()  # a full flush would encode 6 x 0.5 s
        assert time.monotonic() - t0 < 1.0  # even the running encode gives up
        recs = rig.repo.list_trucks()
        assert len(recs) == 6
        media = rig.data_dir / "media"
        with_clip = [r for r in recs if r.clip_path is not None]
        assert len(with_clip) < 6  # unfinished clips were dropped
        for r in recs:
            stem = f"{r.run_id}_{r.track_id}.mp4"
            if r.clip_path is None:
                assert not list(media.rglob(stem))
    finally:
        rig.close()


def test_source_end_reported_while_clips_still_flush(tmp_path, monkeypatch):
    # The dashboard shows "Video finished" from source_state: it must not wait for
    # the clip queue, which can take a minute on a loaded machine.
    import threading

    import engine.events.evidence as evidence_mod

    encoding = threading.Event()
    release = threading.Event()

    def blocked_write(frames, path, fps, should_stop=None):
        encoding.set()
        assert release.wait(10)
        path.write_bytes(b"clip")

    monkeypatch.setattr(evidence_mod, "write_clip", blocked_write)
    video = make_video(tmp_path / "v.mp4", 30)
    rig = Rig(tmp_path, video, FakeDetector(lambda i: [truck(BOX_A)]), after_s=0.2)
    try:
        rig.pipeline.start()
        assert encoding.wait(5)
        assert wait_until(lambda: rig.pipeline.status().source_state == "ended", 5.0)
        assert rig.pipeline.wait(0.2) is False  # wait() still means "pipeline finished"
        release.set()
        assert rig.pipeline.wait(10) is True
        assert rig.pipeline.status().source_state == "ended"
        assert rig.repo.list_trucks()[0].clip_path is not None
    finally:
        release.set()
        rig.close()


def test_repeated_frame_failures_are_summarised(tmp_path, caplog, monkeypatch):
    import engine.pipeline as pipeline_mod

    clock = [0.0]
    monkeypatch.setattr(pipeline_mod, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))

    def always_fails(i: int) -> list[Detection]:
        clock[0] += 1.0  # every frame takes "1 s"
        raise RuntimeError(f"boom {i}")

    video = make_video(tmp_path / "v.mp4", 30)
    rig = Rig(tmp_path, video, FakeDetector(always_fails))
    try:
        with caplog.at_level(logging.ERROR, logger="engine.pipeline"):
            rig.pipeline.start()
            assert rig.pipeline.wait(10) is True
        errors = [r for r in caplog.records if r.name == "engine.pipeline"]
        tracebacks = [r for r in errors if r.exc_info]
        summaries = [r for r in errors if not r.exc_info]
        assert len(tracebacks) == 1  # full traceback for the first failure only
        assert "boom 0" in str(tracebacks[0].exc_info[1])
        # 29 more failures over 29 s: one line per 10 s, plus the rest at the end
        assert 2 <= len(summaries) <= 4
        counts = [int(r.getMessage().split()[0]) for r in summaries]
        assert sum(counts) == 29
        assert rig.pipeline.status().frames_processed == 0
    finally:
        rig.close()


class LiveFakeSource:
    """Live-style source whose state and frames the test controls."""

    def __init__(self) -> None:
        self.state = "reconnecting"
        self._frames: queue.Queue = queue.Queue()
        self._ended = False
        self.closed = False

    def push(self, frame) -> None:
        self._frames.put(frame)

    def finish(self) -> None:
        self._frames.put(None)

    def open(self) -> None:
        pass

    def read(self):
        try:
            item = self._frames.get(timeout=0.01)
        except queue.Empty:
            return None
        if item is None:
            self._ended = True
        return item

    def close(self) -> None:
        self.closed = True

    @property
    def fps(self) -> float:
        return 30.0

    @property
    def ended(self) -> bool:
        return self._ended


def test_pipeline_reports_reconnecting(tmp_path):
    source = LiveFakeSource()
    rig = Rig(tmp_path, source, FakeDetector(nothing), realtime=True)
    try:
        rig.pipeline.start()
        time.sleep(0.1)
        # no frame yet: still starting, whatever the source says
        assert rig.pipeline.status().source_state == "starting"
        source.state = "running"
        source.push(fake_frame(0, 0.0))
        assert wait_until(lambda: rig.pipeline.status().source_state == "running", 5.0)
        source.state = "reconnecting"
        assert wait_until(lambda: rig.pipeline.status().source_state == "reconnecting", 5.0)
        source.state = "running"
        assert wait_until(lambda: rig.pipeline.status().source_state == "running", 5.0)
        source.finish()
        assert wait_until(lambda: rig.pipeline.status().source_state == "ended", 5.0)
        source.state = "reconnecting"
        assert rig.pipeline.status().source_state == "ended"  # never overwritten
    finally:
        rig.close()
