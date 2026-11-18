from __future__ import annotations

import engine.evaluation.replay as replay_mod
from engine.config import AppConfig, DetectorConfig
from engine.core import Detection
from engine.detectors import filtering
from engine.detectors.fake import FakeDetector
from engine.evaluation.replay import TruckTrack, replay, summarize
from engine.evaluation.synthetic import StreamSpec, make_stream
from tests.test_pipeline import Rig, make_video, truck


def test_replay_counts_clean_stream():
    cache, _ = make_stream(StreamSpec(n_trucks=3, n_cars=2, seconds=5.0))
    tracks = replay(cache, AppConfig())
    assert len(tracks) == 3
    for t in tracks:
        assert t.hits == len(t.boxes) > 0
        assert t.first_ts <= t.last_ts
        assert t.best_score == 0.7


def test_replay_fps_budget_skips_frames():
    cache, _ = make_stream(StreamSpec(seconds=3.0, fps=30.0))
    seen: list[float] = []
    real = replay_mod.prepare_detections

    def spy(raw, cfg, confidence):
        seen.append(1.0)
        return real(raw, cfg, confidence)

    replay_mod.prepare_detections = spy
    try:
        replay(cache, AppConfig(), fps_budget=10.0)
    finally:
        replay_mod.prepare_detections = real
    assert abs(len(seen) - len(cache.frames) / 3) <= 1


def test_replay_uses_prepare_detections(monkeypatch):
    cache, _ = make_stream(StreamSpec(seconds=1.0))
    calls: list[float] = []

    def spy(raw, cfg, confidence):
        calls.append(confidence)
        return []

    monkeypatch.setattr(replay_mod, "prepare_detections", spy)
    tracks = replay(cache, AppConfig(), confidence=0.55)
    assert len(calls) == len(cache.frames)
    assert set(calls) == {0.55}
    assert tracks == []


def test_summarize_empty_and_values():
    s = summarize([])
    assert s["trucks"] == 0 and s["median_s"] is None and s["max_s"] is None
    assert s["under_1s_pct"] is None and s["best_score_median"] is None
    box = (0.0, 0.0, 1.0, 1.0)
    tracks = [
        TruckTrack(1, 0.0, 0.5, 5, 0.6, [(0.0, box)]),
        TruckTrack(2, 0.0, 3.0, 50, 0.8, [(0.0, box)]),
    ]
    s = summarize(tracks)
    assert s["trucks"] == 2 and s["max_s"] == 3.0
    assert s["under_1s_pct"] == 50.0 and s["under_2s_pct"] == 50.0
    assert s["best_score_median"] == 0.7


def test_pipeline_uses_prepare_detections(tmp_path, monkeypatch):
    import engine.pipeline as pipeline_mod

    calls: list[tuple[int, float]] = []
    real = filtering.prepare_detections

    def spy(raw, cfg, confidence):
        calls.append((len(raw), confidence))
        return real(raw, cfg, confidence)

    monkeypatch.setattr(pipeline_mod, "prepare_detections", spy)
    video = make_video(tmp_path / "v.mp4", 12)
    det = FakeDetector(lambda i: [truck((20.0, 20.0, 80.0, 80.0))])
    rig = Rig(tmp_path, video, det, detector_cfg=DetectorConfig())
    try:
        rig.pipeline.start()
        assert rig.pipeline.wait(30) is True
        assert len(calls) == 12
        assert all(n == 1 and c == 0.4 for n, c in calls)
    finally:
        rig.close()
