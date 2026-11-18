from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from engine.core import Detection
from engine.evaluation.cache import CachedFrame, DetectionCache
from engine.evaluation.reference import (
    candidates,
    load_refs,
    reference_tracks,
)
from engine.evaluation.sheets import sheets
from engine.evaluation.synthetic import StreamSpec, make_stream
from engine.evaluation.truth import load_truth


def test_reference_tracks_are_deterministic():
    cache, _ = make_stream(StreamSpec(n_trucks=3, n_cars=2, seconds=4.0, junk_per_s=3.0), seed=1)
    a = reference_tracks(cache)
    b = reference_tracks(cache)
    assert [r.ref_id for r in a] == [r.ref_id for r in b]
    assert [r.ref_id for r in a][:3] == ["R1", "R2", "R3"]
    assert [r.track.boxes for r in a] == [r.track.boxes for r in b]
    firsts = [r.track.first_ts for r in a]
    assert firsts == sorted(firsts)


def test_candidates_need_a_truck_label():
    cache, _ = make_stream(StreamSpec(n_trucks=2, n_cars=3, seconds=3.0))
    refs = reference_tracks(cache)
    assert len(refs) == 5
    cands = candidates(refs)
    assert len(cands) == 2
    for r in cands:
        assert r.class_votes.get("truck", 0) > 0
        assert r.best_truck_score >= 0.25
        assert r.track.hits >= 8
    # a car track carries no truck score
    car = [r for r in refs if "truck" not in r.class_votes]
    assert car and all(r.best_truck_score == 0.0 for r in car)


def _truth(tmp_path, **over):
    data = {
        "video": "v.mp4",
        "frames": 10,
        "fps": 30.0,
        "cache": "v.npz",
        "reference": {"floor": 0.1, "lost_timeout_s": 3.0},
        "candidates": ["R1", "R2", "R3"],
        "trucks": [{"id": "T1", "refs": ["R1", "R3"], "note": "white"}],
        "not_truck": ["R2"],
        "unsure": [],
        "labelled_by": "me",
        "labelled_on": "2026-11-18",
    }
    data.update(over)
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_truth_accepts_complete(tmp_path):
    t = load_truth(_truth(tmp_path))
    assert t.candidates == ["R1", "R2", "R3"]
    assert t.trucks[0].id == "T1" and t.trucks[0].refs == ["R1", "R3"]
    assert t.not_truck == ["R2"] and t.unsure == []
    assert t.fps == 30.0


def test_load_truth_rejects_missing_candidate(tmp_path):
    with pytest.raises(ValueError, match="R3"):
        load_truth(_truth(tmp_path, trucks=[{"id": "T1", "refs": ["R1"], "note": ""}]))


def test_load_truth_rejects_duplicate(tmp_path):
    with pytest.raises(ValueError, match="R2.*twice|twice.*R2"):
        load_truth(_truth(tmp_path, unsure=["R2"]))


def test_load_truth_rejects_unknown_ref(tmp_path):
    with pytest.raises(ValueError, match="R9"):
        load_truth(_truth(tmp_path, not_truck=["R2", "R9"]))


def test_load_truth_rejects_empty_refs(tmp_path):
    with pytest.raises(ValueError, match="T2"):
        load_truth(
            _truth(
                tmp_path,
                trucks=[
                    {"id": "T1", "refs": ["R1", "R3"], "note": ""},
                    {"id": "T2", "refs": [], "note": ""},
                ],
            )
        )


def test_sheets_writes_pngs_and_template(tmp_path):
    fps, w, h, n = 10.0, 320, 240, 40
    video = tmp_path / "tiny.mp4"
    vw = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert vw.isOpened()
    for _ in range(n):
        vw.write(np.full((h, w, 3), 90, dtype=np.uint8))
    vw.release()
    frames = []
    for i in range(n):
        dets = []
        for k in range(5):  # 5 trucks -> 2 sheets
            x = 10 + i * 2
            y = 8 + k * 40
            dets.append(Detection((x, y, x + 30, y + 30), 0.8, "truck"))
        frames.append(CachedFrame(i, i / fps, tuple(dets)))
    cache = DetectionCache("tiny.mp4", w, h, fps, "m", 0, 0.05, ("truck", "car"), frames)
    out = tmp_path / "review"
    n_refs, n_cands, n_sheets = sheets(cache, video, out, cache_name="tiny__m.npz")
    assert (n_refs, n_cands, n_sheets) == (5, 5, 2)
    for name in ("sheet_001.png", "sheet_002.png", "timeline.png"):
        img = cv2.imread(str(out / name))
        assert img is not None and img.size > 0
    tpl = json.loads((out / "truth_template.json").read_text(encoding="utf-8"))
    assert tpl["candidates"] == ["R1", "R2", "R3", "R4", "R5"]
    assert tpl["video"] == "tiny.mp4" and tpl["frames"] == n
    assert tpl["reference"]["candidate_min_hits"] == 8
    assert tpl["trucks"] == [] and tpl["cache"] == "tiny__m.npz"
    refs = load_refs(out / "refs.json")
    assert [r.ref_id for r in refs] == tpl["candidates"]
    # the tracker confirms a new track from its second frame on
    assert refs[0].class_votes == {"truck": n - 1}
    assert len(refs[0].track.boxes) == n - 1


def test_candidates_gate_hits_span_and_merged_truck_evidence():
    # one vehicle: car 0.6 on top, truck 0.3 duplicate underneath, for 1 s
    # a second one lasts only 4 frames; a third is a plain car
    fps = 10.0
    frames = []
    for i in range(10):
        dets = [
            Detection((10, 10, 50, 50), 0.6, "car"),
            Detection((10, 10, 50, 48), 0.3, "truck"),
            Detection((200, 200, 240, 240), 0.9, "car"),
        ]
        if i < 4:
            dets.append(Detection((400, 100, 440, 140), 0.8, "truck"))
        frames.append(CachedFrame(i, i / fps, tuple(dets)))
    cache = DetectionCache("x", 640, 480, fps, "m", 0, 0.05, ("car", "truck"), frames)
    refs = reference_tracks(cache)
    assert len(refs) == 3
    cands = candidates(refs)
    assert len(cands) == 1
    assert cands[0].class_votes == {"car": 9}
    assert cands[0].best_truck_score == 0.3
