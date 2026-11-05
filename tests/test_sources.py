from __future__ import annotations

import time

import cv2
import pytest

from engine.config import ConfigError, SourceConfig
from engine.core import DEFAULT_FPS
from engine.sources import FileSource, build_source
from tests.conftest import make_video


def test_file_source_reads_all_frames(tmp_path):
    src = FileSource(make_video(tmp_path / "v.mp4", 45), realtime=False, loop=False)
    src.open()
    frames = []
    while (f := src.read()) is not None:
        frames.append(f)
    src.close()
    assert len(frames) == 45 and src.ended
    assert frames[1].ts == pytest.approx(1 / 30, abs=1e-3)
    assert [f.index for f in frames] == list(range(45))


def test_file_source_loop(tmp_path):
    src = FileSource(make_video(tmp_path / "v.mp4", 45), realtime=False, loop=True)
    src.open()
    frames = [src.read() for _ in range(60)]
    src.close()
    assert all(f is not None for f in frames)
    assert [f.index for f in frames] == list(range(60))
    assert frames[59].ts > frames[44].ts
    assert not src.ended


def test_fps_fallback_when_zero(tmp_path, monkeypatch):
    path = make_video(tmp_path / "v.mp4", 5)
    real_cls = cv2.VideoCapture

    class ZeroFpsCapture:
        def __init__(self, *a, **k):
            self._cap = real_cls(*a, **k)

        def get(self, prop):
            return 0.0 if prop == cv2.CAP_PROP_FPS else self._cap.get(prop)

        def __getattr__(self, name):
            return getattr(self._cap, name)

    monkeypatch.setattr(cv2, "VideoCapture", ZeroFpsCapture)
    src = FileSource(path, realtime=False, loop=False)
    src.open()
    assert src.fps == DEFAULT_FPS
    src.close()


def test_realtime_paces_reading(tmp_path):
    src = FileSource(make_video(tmp_path / "v.mp4", 15), realtime=True, loop=False)
    src.open()
    t0 = time.monotonic()
    n = 0
    while src.read() is not None:
        n += 1
    elapsed = time.monotonic() - t0
    src.close()
    assert n == 15
    assert elapsed >= 0.4


def test_build_source_from_config(tmp_path):
    path = make_video(tmp_path / "v.mp4", 3)
    src = build_source(SourceConfig(type="file", path=path, realtime=False))
    assert isinstance(src, FileSource)


def test_build_source_file_without_path():
    with pytest.raises(ConfigError, match=r"source\.path: required for type: file"):
        build_source(SourceConfig(type="file"))


def test_video_path_with_spaces(tmp_path):
    d = tmp_path / "auto detection"
    d.mkdir()
    src = FileSource(make_video(d / "my video.mp4", 4), realtime=False, loop=False)
    src.open()
    assert src.read() is not None
    src.close()
