from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np
import supervision as sv
from trackers import ByteTrackTracker as _RfByteTrack

from engine.config import TrackerConfig
from engine.core import Detection, Frame, Track, Tracker, TrackerResult

# The trackers package expresses lost_track_buffer in 30 FPS frames; with
# timestamps it becomes a seconds budget of lost_track_buffer / 30.
_REFERENCE_FPS = 30.0
# Library budget = our lost timeout + this margin, so our rule always ends a
# track first (and removes it from the library, see update()).
_LIBRARY_MARGIN_S = 1.0


@dataclass
class _TrackState:
    hit_seqs: deque[int]
    last_seen_ts: float
    confirmed: bool = False


class ByteTrackTracker:
    """ByteTrack association plus our own confirmation and lost-timeout rules."""

    def __init__(
        self,
        confirm_hits: int,
        confirm_window: int,
        lost_timeout_s: float,
        fps: float,
    ) -> None:
        self._confirm_hits = confirm_hits
        self._confirm_window = confirm_window
        self._lost_timeout_s = lost_timeout_s
        self._tracker = _RfByteTrack(
            lost_track_buffer=math.ceil((lost_timeout_s + _LIBRARY_MARGIN_S) * _REFERENCE_FPS),
            frame_rate=fps,
            track_activation_threshold=0.0,  # no extra gate on starting a track
            # Only "high" detections can start a track, so make every detection
            # high; the detector's own confidence threshold is the only filter.
            high_conf_det_threshold=0.0,
            minimum_consecutive_frames=1,  # activate at once; we confirm ourselves
        )
        self._seq = 0
        self._states: dict[int, _TrackState] = {}
        # A new tracklet reports tracker_id -1 on its first frame; remember when
        # it was spawned so that frame still counts as a hit once it gets an id.
        self._spawned: dict[int, tuple[int, float]] = {}

    def update(self, dets: list[Detection], frame: Frame) -> TrackerResult:
        seq = self._seq
        self._seq += 1

        if dets:
            sv_dets = sv.Detections(
                xyxy=np.array([d.box for d in dets], dtype=np.float32),
                confidence=np.array([d.score for d in dets], dtype=np.float32),
                data={"det_index": np.arange(len(dets))},
            )
        else:
            sv_dets = sv.Detections.empty()
        # Pass the capture time: realtime mode skips a varying number of frames,
        # and a fixed per-call step would make the Kalman motion model (and the
        # association) fall apart, splitting one truck into many ids.
        out = self._tracker.update(sv_dets, timestamp=frame.ts)

        # Record spawn time of tracklets that have no id yet, and collect the
        # spawn info for tracklets that just received one.
        live = {id(t) for t in self._tracker.tracks}
        self._spawned = {k: v for k, v in self._spawned.items() if k in live}
        spawn_for_id: dict[int, tuple[int, float]] = {}
        for tl in self._tracker.tracks:
            if tl.tracker_id == -1:
                self._spawned.setdefault(id(tl), (seq, frame.ts))
            elif id(tl) in self._spawned:
                spawn_for_id[int(tl.tracker_id)] = self._spawned.pop(id(tl))

        confirmed: list[Track] = []
        seen: set[int] = set()
        if len(out) and out.tracker_id is not None:
            idx_arr = out.data["det_index"]
            for tid, di in zip(out.tracker_id, idx_arr):
                tid = int(tid)
                if tid < 0:
                    continue
                state = self._states.get(tid)
                if state is None:
                    state = _TrackState(
                        hit_seqs=deque(maxlen=self._confirm_window),
                        last_seen_ts=frame.ts,
                    )
                    spawn = spawn_for_id.get(tid)
                    if spawn is not None:
                        state.hit_seqs.append(spawn[0])
                    self._states[tid] = state
                state.hit_seqs.append(seq)
                state.last_seen_ts = frame.ts
                seen.add(tid)
                recent = sum(1 for s in state.hit_seqs if s > seq - self._confirm_window)
                if recent >= self._confirm_hits:
                    state.confirmed = True
                if state.confirmed:
                    confirmed.append(Track(track_id=tid, detection=dets[int(di)]))

        ended: list[int] = []
        for tid, state in list(self._states.items()):
            if tid in seen:
                continue
            if frame.ts - state.last_seen_ts > self._lost_timeout_s:
                del self._states[tid]
                # Make the library forget it too, so the id can never be revived
                # (its budget deliberately outlasts ours, see _LIBRARY_MARGIN_S).
                self._tracker.tracks = [
                    t for t in self._tracker.tracks if t.tracker_id != tid
                ]
                if state.confirmed:
                    ended.append(tid)
        return TrackerResult(confirmed=confirmed, ended_ids=ended)


def build_tracker(cfg: TrackerConfig, fps: float) -> Tracker:
    return ByteTrackTracker(
        confirm_hits=cfg.confirm_hits,
        confirm_window=cfg.confirm_window,
        lost_timeout_s=cfg.lost_timeout_s,
        fps=fps,
    )
