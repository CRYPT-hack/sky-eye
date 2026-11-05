from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from engine.core import Frame, TrackerResult
from engine.events.bus import EventBus
from engine.events.evidence import EvidenceRecorder, save_snapshot
from engine.storage import (
    TruckRecord,
    TruckRepository,
    day_dir,
    enforce_retention,
    media_dir,
)

log = logging.getLogger(__name__)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _media_url(path: str | None) -> str | None:
    return None if path is None else "/media/" + quote(path, safe="/")


def truck_to_json(rec: TruckRecord) -> dict:
    return {
        "api": 1,
        "id": rec.id,
        "run_id": rec.run_id,
        "track_id": rec.track_id,
        "first_seen": _iso(rec.first_seen),
        "last_seen": _iso(rec.last_seen),
        "best_score": rec.best_score,
        "snapshot_url": _media_url(rec.snapshot_path),
        "crop_url": _media_url(rec.crop_path),
        "clip_url": _media_url(rec.clip_path),
        "lat": rec.lat,
        "lon": rec.lon,
        "geo_error_m": rec.geo_error_m,
        "geo_status": rec.geo_status,
    }


@dataclass
class _Active:
    truck_id: int
    first_ts: float
    best_score: float
    last_seen: datetime
    full_path: Path
    crop_path: Path


class EventManager:
    """Turns confirmed tracks into truck records, evidence and bus messages."""

    def __init__(
        self,
        repo: TruckRepository,
        evidence: EvidenceRecorder,
        bus: EventBus,
        run_id: int,
        data_dir: Path,
        snapshot_window_s: float,
        max_disk_bytes: int,
    ) -> None:
        self._repo = repo
        self._evidence = evidence
        self._bus = bus
        self._run_id = run_id
        self._data_dir = Path(data_dir)
        self._media_root = media_dir(self._data_dir)
        self._window = snapshot_window_s
        self._max_bytes = max_disk_bytes
        self._lock = threading.Lock()
        self._active: dict[int, _Active] = {}
        self._total = 0

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    @property
    def total_count(self) -> int:
        with self._lock:
            return self._total

    def _rel(self, path: Path) -> str:
        return path.relative_to(self._media_root).as_posix()

    def _publish(self, kind: str, truck_id: int) -> None:
        rec = self._repo.get_truck(truck_id)
        if rec is not None:
            self._bus.publish({"api": 1, "type": kind, "truck": truck_to_json(rec)})

    def on_frame(self, frame: Frame, result: TrackerResult) -> None:
        with self._lock:
            for track in result.confirmed:
                state = self._active.get(track.track_id)
                score = track.detection.score
                if state is None:
                    self._create(frame, track.track_id, track.detection)
                    continue
                state.last_seen = frame.wall_time
                if frame.ts - state.first_ts <= self._window and score > state.best_score:
                    save_snapshot(frame, track.detection.box, state.full_path, state.crop_path)
                    state.best_score = score
                    self._repo.update_truck(state.truck_id, best_score=score)
            ended = [self._active.pop(t) for t in result.ended_ids if t in self._active]
            for state in ended:
                self._repo.update_truck(state.truck_id, last_seen=state.last_seen)
        for state in ended:
            self._publish("truck.updated", state.truck_id)

    def _create(self, frame: Frame, track_id: int, det) -> None:
        # called with self._lock held; publishes truck.new after registering state
        folder = day_dir(self._data_dir, frame.wall_time)
        stem = f"{self._run_id}_{track_id}"
        full_path = folder / f"{stem}_full.jpg"
        crop_path = folder / f"{stem}_crop.jpg"
        save_snapshot(frame, det.box, full_path, crop_path)
        rec = TruckRecord(
            id=None,
            run_id=self._run_id,
            track_id=track_id,
            first_seen=frame.wall_time,
            last_seen=frame.wall_time,
            best_score=det.score,
            snapshot_path=self._rel(full_path),
            crop_path=self._rel(crop_path),
        )
        truck_id = self._repo.insert_truck(rec)
        self._active[track_id] = _Active(
            truck_id, frame.ts, det.score, frame.wall_time, full_path, crop_path
        )
        self._total += 1
        clip_path = folder / f"{stem}.mp4"

        def on_done(path: Path, truck_id: int = truck_id) -> None:
            self._repo.update_truck(truck_id, clip_path=self._rel(Path(path)))
            try:
                enforce_retention(self._media_root, self._max_bytes)
            except OSError as exc:
                log.warning("media retention failed: %s", exc)
            self._publish("truck.updated", truck_id)

        self._evidence.start_clip(stem, frame.ts, clip_path, on_done)
        self._publish("truck.new", truck_id)

    def close(self) -> None:
        with self._lock:
            states = list(self._active.values())
            self._active.clear()
            for state in states:
                self._repo.update_truck(state.truck_id, last_seen=state.last_seen)
        self._evidence.close()
