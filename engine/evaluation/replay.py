"""Replay cached detections through exactly the code the live engine uses
(prepare_detections + the tracker), so tracking changes can be measured offline."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from engine.config import AppConfig
from engine.core import Frame
from engine.detectors.filtering import prepare_detections
from engine.evaluation.cache import DetectionCache
from engine.tracking import build_tracker

_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
_DUMMY_IMAGE = np.zeros((1, 1, 3), dtype=np.uint8)


@dataclass
class TruckTrack:
    track_id: int
    first_ts: float
    last_ts: float
    hits: int  # frames where the track was confirmed
    best_score: float
    # (ts, box) for every frame where the track was confirmed
    boxes: list[tuple[float, tuple[float, float, float, float]]] = field(default_factory=list)


def replay(
    cache: DetectionCache,
    cfg: AppConfig,
    confidence: float | None = None,
    fps_budget: float | None = None,
) -> list[TruckTrack]:
    """Track the cached frames; return every track that was ever confirmed.

    fps_budget simulates a slow machine: frame k is processed only if
    ts_k >= next_slot, and then next_slot = ts_k + 1 / fps_budget.
    """
    conf = cfg.detector.confidence if confidence is None else confidence
    tracker = build_tracker(cfg.tracker, cache.fps)
    tracks: dict[int, TruckTrack] = {}
    next_slot = float("-inf")
    for cf in cache.frames:
        if fps_budget is not None:
            if cf.ts + 1e-9 < next_slot:  # tolerance: 3/30 s vs 0.1 s float noise
                continue
            next_slot = cf.ts + 1.0 / fps_budget
        frame = Frame(
            image=_DUMMY_IMAGE,
            ts=cf.ts,
            wall_time=_EPOCH + timedelta(seconds=cf.ts),
            index=cf.index,
        )
        detections = prepare_detections(list(cf.detections), cfg.detector, conf)
        result = tracker.update(detections, frame)
        for t in result.confirmed:
            tt = tracks.get(t.track_id)
            if tt is None:
                tt = tracks[t.track_id] = TruckTrack(
                    t.track_id, cf.ts, cf.ts, 0, t.detection.score
                )
            tt.last_ts = cf.ts
            tt.hits += 1
            tt.best_score = max(tt.best_score, t.detection.score)
            tt.boxes.append((cf.ts, t.detection.box))
    return list(tracks.values())


def summarize(tracks: list[TruckTrack]) -> dict:
    """Counts and confirmed-lifetime statistics; timing fields are None with no tracks."""
    if not tracks:
        return {
            "trucks": 0,
            "median_s": None,
            "under_1s_pct": None,
            "under_2s_pct": None,
            "max_s": None,
            "best_score_median": None,
        }
    spans = [t.last_ts - t.first_ts for t in tracks]
    n = len(tracks)
    return {
        "trucks": n,
        "median_s": statistics.median(spans),
        "under_1s_pct": 100.0 * sum(s < 1.0 for s in spans) / n,
        "under_2s_pct": 100.0 * sum(s < 2.0 for s in spans) / n,
        "max_s": max(spans),
        "best_score_median": statistics.median(t.best_score for t in tracks),
    }
