"""Permissive reference tracking, used only to build labelling aids.

One tracker run over every cached frame with a very loose confirmation rule, so
that a person can decide which reference tracks are the same real truck."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from engine.config import TrackerConfig
from engine.core import Frame
from engine.detectors.filtering import filter_detections
from engine.evaluation.cache import DetectionCache
from engine.evaluation.replay import TruckTrack
from engine.tracking import build_tracker

REF_FLOOR = 0.1
REF_LOST_TIMEOUT_S = 3.0
CANDIDATE_TRUCK_SCORE = 0.2

_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
_DUMMY_IMAGE = np.zeros((1, 1, 3), dtype=np.uint8)


@dataclass
class RefTrack:
    ref_id: str
    track: TruckTrack
    class_votes: dict[str, int] = field(default_factory=dict)
    best_truck_score: float = 0.0


def reference_tracks(cache: DetectionCache) -> list[RefTrack]:
    """All confirmed reference tracks, numbered R1, R2, ... by first confirmed time
    (ties by the tracker's own id). Deterministic."""
    cfg = TrackerConfig(confirm_hits=1, confirm_window=1, lost_timeout_s=REF_LOST_TIMEOUT_S)
    tracker = build_tracker(cfg, cache.fps)
    tracks: dict[int, TruckTrack] = {}
    votes: dict[int, Counter] = {}
    best_truck: dict[int, float] = {}
    for cf in cache.frames:
        frame = Frame(
            image=_DUMMY_IMAGE,
            ts=cf.ts,
            wall_time=_EPOCH + timedelta(seconds=cf.ts),
            index=cf.index,
        )
        dets = filter_detections(list(cf.detections), cache.classes, REF_FLOOR)
        result = tracker.update(dets, frame)
        for t in result.confirmed:
            d = t.detection  # the very object passed in, so class_name is its own
            tt = tracks.get(t.track_id)
            if tt is None:
                tt = tracks[t.track_id] = TruckTrack(t.track_id, cf.ts, cf.ts, 0, d.score)
                votes[t.track_id] = Counter()
                best_truck[t.track_id] = 0.0
            tt.last_ts = cf.ts
            tt.hits += 1
            tt.best_score = max(tt.best_score, d.score)
            tt.boxes.append((cf.ts, d.box))
            votes[t.track_id][d.class_name] += 1
            if d.class_name == "truck":
                best_truck[t.track_id] = max(best_truck[t.track_id], d.score)
    order = sorted(tracks.values(), key=lambda tt: (tt.first_ts, tt.track_id))
    return [
        RefTrack(f"R{i}", tt, dict(votes[tt.track_id]), best_truck[tt.track_id])
        for i, tt in enumerate(order, start=1)
    ]


def candidates(refs: list[RefTrack]) -> list[RefTrack]:
    """Refs with at least one truck-labelled box scoring >= 0.2."""
    return [r for r in refs if r.best_truck_score >= CANDIDATE_TRUCK_SCORE]


def save_refs(refs: list[RefTrack], path: Path) -> None:
    payload = [
        {
            "ref_id": r.ref_id,
            "track_id": r.track.track_id,
            "first_ts": r.track.first_ts,
            "last_ts": r.track.last_ts,
            "hits": r.track.hits,
            "best_score": r.track.best_score,
            "best_truck_score": r.best_truck_score,
            "class_votes": r.class_votes,
            "boxes": [[ts, *box] for ts, box in r.track.boxes],
        }
        for r in refs
    ]
    Path(path).write_text(json.dumps(payload, indent=1), encoding="utf-8")


def load_refs(path: Path) -> list[RefTrack]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    refs = []
    for r in raw:
        boxes = [
            (float(b[0]), (float(b[1]), float(b[2]), float(b[3]), float(b[4])))
            for b in r["boxes"]
        ]
        tt = TruckTrack(
            int(r["track_id"]),
            float(r["first_ts"]),
            float(r["last_ts"]),
            int(r["hits"]),
            float(r["best_score"]),
            boxes,
        )
        refs.append(
            RefTrack(r["ref_id"], tt, dict(r["class_votes"]), float(r["best_truck_score"]))
        )
    return refs
