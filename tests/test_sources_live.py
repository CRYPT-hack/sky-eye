"""Live sources: stream (PyAV), backoff, capture reconnect. No network, no hardware."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from engine.config import ConfigError, SourceConfig, load_config
from engine.core import DEFAULT_FPS
from engine.sources import CaptureSource, StreamSource, build_source
from engine.sources.backoff import Backoff
from tests.conftest import make_video

WAIT_S = 5.0  # hard limit for anything a background thread should do


def _wait_until(cond, timeout: float = WAIT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.002)
    return cond()


def _next_frame(src, timeout: float = WAIT_S, where=lambda frame: True):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (frame := src.read()) is not None and where(frame):
            return frame
        time.sleep(0.002)
    return None


def _img(value: int) -> np.ndarray:
    return np.full((24, 32, 3), value, dtype=np.uint8)


class FakeClock:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeStream:
    """What an opener returns: yields the scripted frames, then None ("no frame
    yet") until closed; `on_idle` runs before each None."""

    def __init__(self, frames, on_idle=None, fps: float | None = 25.0) -> None:
        self.fps = fps
        self.closed = False
        self.drained = threading.Event()
        self._frames = list(frames)
        self._on_idle = on_idle

    def __iter__(self):
        yield from self._frames
        self.drained.set()
        while not self.closed:
            if self._on_idle is not None:
                self._on_idle()
            time.sleep(0.002)
            yield None

    def close(self) -> None:
        self.closed = True


class ScriptedOpener:
    """Each call takes the next scripted result: an exception is raised, a
    FakeStream is returned. Records (url, options) per call."""

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, options: dict):
        self.calls.append((url, dict(options)))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def sources():
    """Closes every StreamSource a test opened, even when the test fails."""
    opened: list[StreamSource] = []
    yield opened
    for src in opened:
        src.close()


def test_backoff_sequence():
    b = Backoff()
    assert [b.next_delay() for _ in range(7)] == [1, 2, 4, 8, 16, 30, 30]
    b.reset()
    assert b.next_delay() == 1
    assert Backoff(initial=0.5, maximum=1.0).next_delay() == 0.5


def test_stream_returns_latest_frame_only(sources):
    stream = FakeStream([_img(i) for i in (10, 20, 30, 40, 50)])
    src = StreamSource("rtsp://cam/live", opener=ScriptedOpener(stream))
    sources.append(src)
    src.open()
    assert stream.drained.wait(WAIT_S)
    frame = src.read()
    assert frame is not None and int(frame.image[0, 0, 0]) == 50
    assert frame.index == 0 and frame.ts >= 0
    assert src.read() is None
    assert src.state == "running" and not src.ended
    assert src.fps == 25.0


def test_stream_read_never_blocks(sources):
    src = StreamSource("rtsp://cam/live", opener=ScriptedOpener(FakeStream([])))
    sources.append(src)
    src.open()
    for _ in range(20):
        t0 = time.perf_counter()
        assert src.read() is None
        assert time.perf_counter() - t0 < 0.05


def test_stream_reconnects_with_backoff(sources):
    sleeps: list[tuple[float, str]] = []
    good = FakeStream([_img(7)])
    opener = ScriptedOpener(OSError("refused"), OSError("refused"), good)
    src = StreamSource(
        "rtsp://cam/live", opener=opener, sleep=lambda d: sleeps.append((d, src.state))
    )
    sources.append(src)
    src.open()  # an unreachable stream does not raise
    assert good.drained.wait(WAIT_S)
    assert sleeps == [(1.0, "reconnecting"), (2.0, "reconnecting")]
    assert len(opener.calls) == 3
    assert _wait_until(lambda: src.state == "running")
    frame = src.read()
    assert frame is not None and int(frame.image[0, 0, 0]) == 7
    assert not src.ended


def test_stream_unreachable_open_starts_reconnecting(sources):
    src = StreamSource(
        "rtsp://cam/live", opener=ScriptedOpener(OSError("refused")), sleep=lambda d: time.sleep(0.01)
    )
    sources.append(src)
    src.open()  # does not raise
    assert src.state == "reconnecting"
    assert src.read() is None and not src.ended


def test_stream_read_timeout_triggers_reconnect(sources):
    clock = FakeClock()
    sleeps: list[tuple[float, str]] = []

    def stall():  # frames stopped: time passes, nothing arrives
        clock.t += 1.0

    first = FakeStream([_img(1), _img(2)], on_idle=stall)
    second = FakeStream([_img(3)])
    opener = ScriptedOpener(first, second)
    src = StreamSource(
        "rtsp://cam/live",
        read_timeout_s=2.5,
        opener=opener,
        clock=clock,
        sleep=lambda d: sleeps.append((d, src.state)),
    )
    sources.append(src)
    src.open()
    assert second.drained.wait(WAIT_S)
    assert len(opener.calls) == 2  # reopened
    assert sleeps == [(1.0, "reconnecting")]
    assert first.closed
    assert _wait_until(lambda: src.state == "running")
    frame = src.read()
    assert frame is not None and int(frame.image[0, 0, 0]) == 3
    assert frame.ts >= 3.0  # clock-based, monotonic across the reconnect


def test_stream_rtsp_options(sources):
    rtsp = ScriptedOpener(FakeStream([]))
    src = StreamSource("rtsp://cam:8554/live", read_timeout_s=5.0, opener=rtsp)
    sources.append(src)
    src.open()
    url, opts = rtsp.calls[0]
    assert url == "rtsp://cam:8554/live"
    assert opts["rtsp_transport"] == "tcp"
    assert opts["fflags"] == "nobuffer" and opts["flags"] == "low_delay"
    assert opts["max_delay"] == "500000"
    assert opts["timeout"] == "5000000"  # RTSP socket I/O timeout, microseconds

    rtmp = ScriptedOpener(FakeStream([]))
    src2 = StreamSource("rtmp://127.0.0.1/live/drone", read_timeout_s=3.0, opener=rtmp)
    sources.append(src2)
    src2.open()
    _, opts2 = rtmp.calls[0]
    assert opts2["rw_timeout"] == "3000000"
    assert "rtsp_transport" not in opts2
    assert "timeout" not in opts2  # for rtmp "timeout" would mean listen mode
    assert opts2["fflags"] == "nobuffer"


def test_stream_bad_url_raises(tmp_path):
    # URL validation lives in SourceConfig / build_source; StreamSource itself
    # accepts any string (test_stream_decodes_real_file passes a file path).
    p = tmp_path / "c.yaml"
    p.write_text("source:\n  type: stream\n  url: drone-feed\n")
    with pytest.raises(ConfigError, match=r"source\.url"):
        load_config(p)
    unchecked = SourceConfig.model_construct(type="stream", url="drone-feed")
    with pytest.raises(ConfigError, match=r"source\.url"):
        build_source(unchecked)


def test_stream_close_stops_thread():
    src = StreamSource("rtsp://cam/live", opener=ScriptedOpener(FakeStream([_img(1)])))
    src.open()
    thread = src._thread
    assert thread is not None and thread.is_alive()
    t0 = time.perf_counter()
    src.close()
    assert time.perf_counter() - t0 < 2.0
    assert not thread.is_alive()

    # also while waiting out a reconnect delay (default sleep is interruptible)
    failing = StreamSource("rtsp://cam/live", opener=ScriptedOpener(OSError("down")))
    failing.open()
    assert failing.state == "reconnecting"
    thread = failing._thread
    t0 = time.perf_counter()
    failing.close()
    assert time.perf_counter() - t0 < 2.0
    assert not thread.is_alive()
    failing.close()  # idempotent


def _write_blue_video(path: Path, n: int, fps: float = 30.0) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 240))
    assert writer.isOpened()
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    img[:] = (255, 0, 0)  # BGR blue
    for _ in range(n):
        writer.write(img)
    writer.release()
    return path


def test_stream_decodes_real_file(tmp_path, sources):
    path = _write_blue_video(tmp_path / "blue clip.mp4", 30)
    src = StreamSource(str(path))  # real PyAV opener
    sources.append(src)
    src.open()
    frame = _next_frame(src)
    assert frame is not None
    assert frame.image.shape == (240, 320, 3) and frame.image.dtype == np.uint8
    # fflags=nobuffer drops the packets FFmpeg read while probing, so this mpeg4
    # file decodes grey until its next keyframe (H.264/H.265 decoders hold
    # frames back until a keyframe instead); wait for a complete picture.
    frame = _next_frame(src, where=lambda f: f.image[..., 0].mean() > 200)
    assert frame is not None
    assert frame.image[..., 2].mean() < 60  # BGR order: blue is channel 0
    assert src.fps == pytest.approx(30.0)
    assert src.frame_size == (320, 240)
    # conftest video too: white rectangle on black
    src2 = StreamSource(str(make_video(tmp_path / "v.mp4", 10)))
    sources.append(src2)
    src2.open()
    assert _next_frame(src2) is not None


def _write_av_clip(path: Path, video_frames: int, audio_s: float) -> Path:
    """mp4 whose audio track runs on after the video stops."""
    import av

    with av.open(str(path), "w") as out:
        vs = out.add_stream("mpeg4", rate=30)
        vs.width, vs.height, vs.pix_fmt = 64, 48, "yuv420p"
        aus = out.add_stream("aac", rate=48000)
        for i in range(video_frames):
            vf = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="bgr24")
            vf.pts = i
            out.mux(vs.encode(vf))
        out.mux(vs.encode())
        samples = 1024
        for i in range(int(audio_s * 48000 / samples)):
            af = av.AudioFrame.from_ndarray(
                np.zeros((1, samples * 2), np.float32), format="flt", layout="stereo"
            )
            af.sample_rate, af.pts = 48000, i * samples
            out.mux(aus.encode(af))
        out.mux(aus.encode())
    return path


def test_pyav_opener_yields_none_for_non_video_packets(tmp_path):
    # video stops, audio flows on: the reader must still see items, or the
    # no-frame timeout could never fire
    from engine.sources.stream import open_pyav

    path = _write_av_clip(tmp_path / "av.mp4", video_frames=3, audio_s=2.0)
    stream = open_pyav(str(path), {}, open_timeout_s=5.0, read_timeout_s=5.0)
    try:
        items = list(stream)
    finally:
        stream.close()
    last_image = max(i for i, item in enumerate(items) if item is not None)
    assert sum(item is not None for item in items) >= 1
    assert sum(item is None for item in items[last_image + 1 :]) >= 10


CREDENTIAL_URLS = [
    # (url, the secrets it holds)
    ("rtsp://pilot:s3cret@cam/live?token=abc", ["s3cret", "abc"]),
    ("rtsp://u:pa#ss@cam/live", ["pa#ss"]),
    ("rtsp://u:pa/ss@cam/live", ["pa/ss"]),
    ("rtsp://u:pa?ss@cam/live", ["pa?ss"]),
    ("rtsp://u:pa@ss@cam/live", ["pa@ss"]),
    ("rtsp://u:pa\\ss@cam/live", ["pa\\ss"]),
    ("rtsp://u:pa'ss@cam/live", ["pa'ss"]),
    ('rtsp://u:pa"ss@cam/live', ['pa"ss']),
]


@pytest.mark.parametrize(("url", "secrets"), CREDENTIAL_URLS)
def test_stream_logs_hide_credentials(sources, caplog, url, secrets):
    def refuse(u, opts):  # like PyAV: the repr of the URL in the message
        raise OSError(f"[Errno 5] cannot open {u!r}; also tried {u}")

    src = StreamSource(url, opener=refuse, sleep=lambda d: time.sleep(0.01))
    sources.append(src)
    with caplog.at_level("INFO", logger="engine.sources.stream"):
        src.open()
        assert _wait_until(lambda: len(caplog.records) >= 2)
    text = caplog.text
    assert "rtsp://***@cam/live" in text
    for secret in secrets:
        assert secret not in text and repr(secret)[1:-1] not in text


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("rtsp://u:pa?ss@h/live", "rtsp://***@h/live"),
        ("rtsp://u:pa#ss@h/live", "rtsp://***@h/live"),
        ("rtsp://u:pa/ss@h/live", "rtsp://***@h/live"),
        ("rtsp://u:p@ss@h:8554/live?x=1", "rtsp://***@h:8554/live?***"),
        ("rtmp://h/live/key?secret=1", "rtmp://h/live/key?***"),
        ("rtsp://cam:8554/live", "rtsp://cam:8554/live"),
        ("udp://@239.0.0.1:1234", "udp://@239.0.0.1:1234"),  # no userinfo
        # an exception quoting repr(url) of a password with a backslash and a quote
        (f"error: {'rtsp://u:x' + chr(92) + chr(39) + 'y@h/a'!r} failed",
         "error: \"rtsp://***@h/a\" failed"),
    ],
)
def test_redact_url(raw, shown):
    from engine.sources.stream import redact_url

    assert redact_url(raw) == shown


def test_stream_state_reconnecting_until_first_frame(sources):
    gate = threading.Event()

    class GatedStream(FakeStream):
        def __iter__(self):
            while not gate.is_set() and not self.closed:
                time.sleep(0.002)
                yield None
            yield from super().__iter__()

    stream = GatedStream([_img(9)])
    src = StreamSource("rtsp://cam/live", opener=ScriptedOpener(stream))
    sources.append(src)
    assert src.state == "reconnecting"
    src.open()  # connected, but no picture yet
    assert src.state == "reconnecting"
    time.sleep(0.05)
    assert src.state == "reconnecting" and src.read() is None
    gate.set()
    assert stream.drained.wait(WAIT_S)
    assert src.state == "running"
    frame = src.read()
    assert frame is not None and int(frame.image[0, 0, 0]) == 9


def test_stream_first_frame_gets_open_timeout_then_read_timeout(sources):
    # a long-GOP camera: the first picture may take up to max(read, open) timeout
    clock = FakeClock(100.0)
    reconnects: list[float] = []

    class Scripted(FakeStream):
        def __init__(self, script):
            super().__init__([])
            self.script = script

        def __iter__(self):
            for item in self.script:
                if item is None:
                    clock.t += 1.0
                yield item
            yield from super().__iter__()

    first = Scripted([None] * 5 + [_img(1)] + [None] * 3)  # 5 s to the first frame, then stalls
    second = FakeStream([_img(2)])
    opener = ScriptedOpener(first, second)
    src = StreamSource(
        "rtsp://cam/live",
        open_timeout_s=6.0,
        read_timeout_s=2.0,
        opener=opener,
        clock=clock,
        sleep=lambda d: reconnects.append(clock.t),
    )
    sources.append(src)
    src.open()
    assert second.drained.wait(WAIT_S)
    assert len(opener.calls) == 2
    # no reconnect during the 5 s wait for the first frame (< 6 s), but one
    # 3 s into the stall after it (> 2 s)
    assert reconnects == [108.0]


@pytest.mark.parametrize(
    ("reported", "expected"),
    [(25.0, 25.0), (1.0, 1.0), (240.0, 240.0), (0.5, DEFAULT_FPS), (1000.0, DEFAULT_FPS),
     (90000.0, DEFAULT_FPS), (float("nan"), DEFAULT_FPS), (None, DEFAULT_FPS)],
)
def test_stream_fps_clamped(sources, reported, expected):
    src = StreamSource("rtsp://cam/live", opener=ScriptedOpener(FakeStream([], fps=reported)))
    sources.append(src)
    src.open()
    assert src.fps == expected


class FakeDevice:
    """A capture device scripted on the fake clock: it fails between
    lost_from and lost_until, like an unplugged HDMI card."""

    def __init__(self, clock: FakeClock, lost_from: float, lost_until: float) -> None:
        self.clock = clock
        self.lost_from, self.lost_until = lost_from, lost_until
        self.captures: list[FakeCapture] = []

    def factory(self, device: int) -> FakeCapture:
        cap = FakeCapture(self)
        self.captures.append(cap)
        return cap


class FakeCapture:
    def __init__(self, dev: FakeDevice) -> None:
        self.dev = dev
        self.released = False
        # an unplugged device does not come back on the same handle
        self.broken = False

    def isOpened(self) -> bool:
        return not self.released

    def read(self):
        if self.released:
            return False, None
        if self.dev.lost_from <= self.dev.clock() < self.dev.lost_until:
            self.broken = True
        if self.broken:
            return False, None
        return True, np.zeros((24, 32, 3), dtype=np.uint8)

    def get(self, prop):
        return 25.0 if prop == cv2.CAP_PROP_FPS else 0.0

    def release(self) -> None:
        self.released = True


def test_capture_reopens_after_device_loss():
    clock = FakeClock(0.0)
    dev = FakeDevice(clock, lost_from=1.0, lost_until=3.5)  # 2.5 s of failures
    src = CaptureSource(0, capture_factory=dev.factory, clock=clock)
    src.open()
    assert src.fps == 25.0 and src.state == "running"
    states, got = [], []
    while clock.t < 8.0:
        t0 = time.perf_counter()
        frame = src.read()
        assert time.perf_counter() - t0 < 0.05
        states.append(src.state)
        got.append(frame is not None)
        clock.t = round(clock.t + 0.1, 6)
    src.close()
    assert "reconnecting" in states
    first_reconnect = states.index("reconnecting")
    assert 30 <= first_reconnect <= 31  # after 2 s of continuous failures (t = 3.0)
    assert states[-1] == "running" and got[-1]
    assert "running" in states[first_reconnect:]
    assert len(dev.captures) == 2  # reopened once
    assert dev.captures[0].released and dev.captures[1].released  # close() released it
    assert not src.ended


def test_capture_timestamps_use_clock():
    clock = FakeClock(10.0)
    dev = FakeDevice(clock, lost_from=1e9, lost_until=1e9)
    src = CaptureSource(0, capture_factory=dev.factory, clock=clock)
    src.open()
    clock.t = 10.5
    frame = src.read()
    src.close()
    assert frame is not None and frame.ts == pytest.approx(0.5)


def test_capture_open_failure_still_raises():
    class Closed:
        def isOpened(self):
            return False

        def release(self):
            pass

    src = CaptureSource(3, capture_factory=lambda d: Closed())
    with pytest.raises(RuntimeError, match="capture device 3"):
        src.open()


def test_file_source_state_is_running(tmp_path):
    from engine.sources import FileSource

    src = FileSource(make_video(tmp_path / "v.mp4", 3), realtime=False, loop=False)
    src.open()
    assert src.state == "running"
    src.close()
