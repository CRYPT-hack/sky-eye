from __future__ import annotations

import logging
import threading
from collections.abc import Callable
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

_HEARTBEAT_S = 5.0  # an active truck's last_seen is refreshed at most this often (frame time)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _media_url(path: str | None) -> str | None:
    return None if path is None else "/media/" + quote(path, safe="/")


def _versioned(path: str | None, best_score: float) -> str | None:
    url = _media_url(path)
    return None if url is None else f"{url}?v={int(round(best_score * 10000))}"


def truck_to_json(rec: TruckRecord) -> dict:
    return {
        "api": 1,
        "id": rec.id,
        "run_id": rec.run_id,
        "track_id": rec.track_id,
        "first_seen": _iso(rec.first_seen),
        "last_seen": _iso(rec.last_seen),
        "best_score": rec.best_score,
        "snapshot_url": _versioned(rec.snapshot_path, rec.best_score),
        "crop_url": _versioned(rec.crop_path, rec.best_score),
        "clip_url": _media_url(rec.clip_path),
        "lat": rec.lat,
        "lon": rec.lon,
        "geo_error_m": rec.geo_error_m,
        "geo_status": rec.geo_status,
        "clip_status": rec.clip_status,
    }


@dataclass
class _Active:
    truck_id: int
    first_ts: float
    best_score: float
    last_seen: datetime
    full_path: Path
    crop_path: Path
    heartbeat_ts: float  # frame.ts of the last last_seen write


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
        improved: list[int] = []  # trucks to publish truck.updated for (snapshot / heartbeat)
        created: list[Callable[[], None]] = []  # truck.new publish + clip start, after the lock
        ended: list[_Active] = []
        try:
            with self._lock:
                for track in result.confirmed:
                    state = self._active.get(track.track_id)
                    score = track.detection.score
                    if state is None:
                        created.append(self._create(frame, track.track_id, track.detection))
                        continue
                    state.last_seen = frame.wall_time
                    if frame.ts - state.heartbeat_ts >= _HEARTBEAT_S:
                        state.heartbeat_ts = frame.ts
                        self._repo.update_truck(state.truck_id, last_seen=state.last_seen)
                        if state.truck_id not in improved:
                            improved.append(state.truck_id)
                    if frame.ts - state.first_ts <= self._window and score > state.best_score:
                        try:
                            save_snapshot(
                                frame, track.detection.box, state.full_path, state.crop_path
                            )
                        except OSError as exc:
                            # the earlier snapshot stays; a later, better frame may still win
                            log.warning(
                                "could not update the snapshot of truck %d: %s", state.truck_id, exc
                            )
                            continue
                        state.best_score = score
                        self._repo.update_truck(state.truck_id, best_score=score)
                        if state.truck_id not in improved:
                            improved.append(state.truck_id)
                ended = [self._active.pop(t) for t in result.ended_ids if t in self._active]
                for state in ended:
                    self._repo.update_truck(state.truck_id, last_seen=state.last_seen)
        finally:
            # even when _create failed for a later track: earlier trucks are already
            # recorded and must still get truck.new and their clip
            for start in created:
                try:
                    start()
                except Exception:
                    log.exception("could not announce a new truck or start its clip")
        # nothing below runs under the lock: publishing may block or call back
        ended_ids = {state.truck_id for state in ended}
        for truck_id in improved:
            if truck_id not in ended_ids:
                self._publish("truck.updated", truck_id)
        for state in ended:
            self._publish("truck.updated", state.truck_id)

    def _create(self, frame: Frame, track_id: int, det) -> Callable[[], None]:
        # called with self._lock held; returns the step to run once it is released:
        # publish truck.new, then start the clip (so truck.new always comes first)
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
            truck_id, frame.ts, det.score, frame.wall_time, full_path, crop_path, frame.ts
        )
        self._total += 1
        log.info("truck %d created (track %d, score %.2f)", truck_id, track_id, det.score)
        clip_path = folder / f"{stem}.mp4"

        def on_done(path: Path, truck_id: int = truck_id) -> None:
            path = Path(path)
            self._repo.update_truck(truck_id, clip_path=self._rel(path), clip_status="saved")
            log.info("clip written for truck %d: %s", truck_id, path)
            cleared: list[int] = []
            try:
                deleted = enforce_retention(self._media_root, self._max_bytes, keep={path})
            except OSError as exc:
                log.warning("media retention failed: %s", exc)
            else:
                if deleted:
                    cleared = self._repo.clear_clip_paths([self._rel(p) for p in deleted])
            self._publish("truck.updated", truck_id)
            for other in cleared:
                if other != truck_id:
                    self._publish("truck.updated", other)

        def on_fail(status: str, truck_id: int = truck_id) -> None:
            # "skipped" / "failed"; may run on the recorder's worker thread, or while the
            # engine is stopping and the repository is already closing: never raise
            try:
                self._repo.update_truck(truck_id, clip_status=status)
                self._publish("truck.updated", truck_id)
            except Exception as exc:  # noqa: BLE001 - shutdown must not fail on this
                log.debug("could not record clip status %r of truck %d: %r", status, truck_id, exc)

        trigger_ts = frame.ts

        def start() -> None:
            self._publish("truck.new", truck_id)
            self._evidence.start_clip(stem, trigger_ts, clip_path, on_done, on_fail)

        return start

    def close(self) -> None:
        with self._lock:
            states = list(self._active.values())
            self._active.clear()
            for state in states:
                self._repo.update_truck(state.truck_id, last_seen=state.last_seen)
        self._evidence.close()
