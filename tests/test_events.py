from __future__ import annotations

import queue
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from engine.core import Detection, Track, TrackerResult
from engine.events.bus import EventBus
from engine.events.evidence import EvidenceRecorder
from engine.events.manager import EventManager, truck_to_json
from engine.storage import TruckRecord, TruckRepository
from tests.conftest import fake_frame

BOX = (50.0, 50.0, 120.0, 100.0)


def result(score: float, track_id: int = 1, ended=()) -> TrackerResult:
    confirmed = [Track(track_id, Detection(BOX, score, "truck"))] if score is not None else []
    return TrackerResult(confirmed=confirmed, ended_ids=list(ended))


def drain(q: queue.Queue) -> list[dict]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


class Rig:
    def __init__(self, data_dir, window=2.0, max_bytes=10**9):
        self.data_dir = data_dir
        self.repo = TruckRepository(data_dir / "sky-eye.db")
        self.run_id = self.repo.create_run("test", "{}")
        self.evidence = EvidenceRecorder(data_dir, before_s=1.0, after_s=1.0)
        self.bus = EventBus()
        self.q = self.bus.subscribe()
        self.mgr = EventManager(
            self.repo, self.evidence, self.bus, self.run_id, data_dir, window, max_bytes
        )

    def feed(self, index: int, ts: float, res: TrackerResult):
        frame = fake_frame(index, ts)
        self.evidence.push(frame)
        self.mgr.on_frame(frame, res)

    def close(self):
        self.mgr.close()
        self.repo.close()


def test_bus_fanout():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish({"api": 1, "type": "x"})
    assert a.get_nowait()["type"] == "x"
    assert b.get_nowait()["type"] == "x"
    bus.unsubscribe(a)
    bus.publish({"api": 1, "type": "y"})
    assert a.empty()
    assert b.get_nowait()["type"] == "y"


def test_bus_slow_subscriber_drops_oldest():
    bus = EventBus()
    q = bus.subscribe(maxsize=2)
    for i in (1, 2, 3):
        bus.publish({"n": i})
    assert [m["n"] for m in drain(q)] == [2, 3]


def test_new_confirmed_track_creates_one_record(tmp_path):
    rig = Rig(tmp_path)
    try:
        for i in range(10):
            rig.feed(i, i / 10, result(0.8))
        assert rig.repo.count_trucks(rig.run_id) == 1
        news = [m for m in drain(rig.q) if m["type"] == "truck.new"]
        assert len(news) == 1
        assert news[0]["api"] == 1
        assert news[0]["truck"]["api"] == 1
        assert rig.mgr.active_count == 1
        assert rig.mgr.total_count == 1
    finally:
        rig.close()


def test_snapshot_improves_within_window(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.feed(0, 0.0, result(0.5))
        rig.feed(1, 1.0, result(0.9))
        rig.feed(2, 2.5, result(0.99))
        rec = rig.repo.list_trucks()[0]
        assert rec.best_score == 0.9
        assert (tmp_path / "media" / rec.snapshot_path).exists()
        assert (tmp_path / "media" / rec.crop_path).exists()
    finally:
        rig.close()


def test_ended_track_sets_last_seen_and_publishes_update(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.feed(0, 0.0, result(0.8))
        later = fake_frame(1, 1.0)
        later = replace(later, wall_time=datetime.now(timezone.utc) + timedelta(seconds=30))
        rig.evidence.push(later)
        rig.mgr.on_frame(later, result(0.8))
        drain(rig.q)
        rig.feed(2, 1.1, TrackerResult(confirmed=[], ended_ids=[1]))
        rec = rig.repo.list_trucks()[0]
        assert rec.last_seen == later.wall_time
        msgs = drain(rig.q)
        assert [m["type"] for m in msgs] == ["truck.updated"]
        assert rig.mgr.active_count == 0
        assert rig.mgr.total_count == 1
    finally:
        rig.close()


def test_clip_path_saved_when_clip_done(tmp_path):
    rig = Rig(tmp_path)
    try:
        for i in range(60):
            rig.feed(i, i / 30, result(0.8))
        rig.evidence.flush()
        rec = rig.repo.list_trucks()[0]
        assert rec.clip_path.endswith(".mp4")
        assert (tmp_path / "media" / rec.clip_path).exists()
        updates = [m for m in drain(rig.q) if m["type"] == "truck.updated"]
        assert any(m["truck"]["clip_url"] for m in updates)
    finally:
        rig.close()


def test_media_urls_quoted(tmp_path):
    data = tmp_path / "data dir"
    rig = Rig(data)
    try:
        for i in range(60):
            rig.feed(i, i / 30, result(0.8))
        rig.evidence.flush()
        rec = rig.repo.list_trucks()[0]
        js = truck_to_json(rec)
        for key in ("snapshot_url", "crop_url", "clip_url"):
            assert js[key].startswith("/media/")
            assert " " not in js[key]
        assert js["snapshot_url"].endswith("?v=8000")
        assert js["first_seen"].endswith("Z")
        assert js["api"] == 1
    finally:
        rig.close()


def test_truck_to_json_without_media():
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    rec = TruckRecord(id=3, run_id=1, track_id=7, first_seen=now, last_seen=now, best_score=0.5)
    js = truck_to_json(rec)
    assert js["clip_url"] is None and js["snapshot_url"] is None and js["crop_url"] is None
    assert js["first_seen"] == "2026-01-02T03:04:05Z"


def test_retention_clears_db_clip_path_of_deleted_clip(tmp_path):
    rig = Rig(tmp_path, max_bytes=1)  # every older clip must go
    try:
        for i in range(40):
            rig.feed(i, i / 30, result(0.8, track_id=1))
        rig.evidence.flush()
        first = rig.repo.list_trucks()[0]
        assert first.clip_path is not None  # the clip just written is never deleted
        assert (tmp_path / "media" / first.clip_path).exists()
        drain(rig.q)
        for i in range(40, 90):
            rig.feed(i, i / 30, result(0.8, track_id=2))
        rig.evidence.flush()
        old = rig.repo.get_truck(first.id)
        new = next(r for r in rig.repo.list_trucks() if r.id != first.id)
        assert old.clip_path is None
        assert not (tmp_path / "media" / first.clip_path).exists()
        assert new.clip_path is not None and (tmp_path / "media" / new.clip_path).exists()
        updates = [m for m in drain(rig.q) if m["type"] == "truck.updated"]
        assert any(m["truck"]["id"] == first.id and m["truck"]["clip_url"] is None for m in updates)
    finally:
        rig.close()


def test_truck_created_logged(tmp_path, caplog):
    rig = Rig(tmp_path)
    try:
        with caplog.at_level("INFO", logger="engine.events.manager"):
            rig.feed(0, 0.0, result(0.8, track_id=7))
        rec = rig.repo.list_trucks()[0]
        assert any(
            f"truck {rec.id} created" in r.getMessage() and "track 7" in r.getMessage()
            for r in caplog.records
        )
    finally:
        rig.close()


def test_snapshot_url_has_version(tmp_path):
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    rec = TruckRecord(
        id=1, run_id=1, track_id=1, first_seen=now, last_seen=now, best_score=0.9,
        snapshot_path="2026-01-02/a b_full.jpg", crop_path="2026-01-02/a b_crop.jpg",
        clip_path="2026-01-02/a b.mp4",
    )
    js = truck_to_json(rec)
    assert js["snapshot_url"] == "/media/2026-01-02/a%20b_full.jpg?v=9000"
    assert js["crop_url"] == "/media/2026-01-02/a%20b_crop.jpg?v=9000"
    assert js["clip_url"] == "/media/2026-01-02/a%20b.mp4"
    assert "?" not in js["clip_url"]


def test_better_snapshot_publishes_update(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.feed(0, 0.0, result(0.5))
        rig.feed(1, 1.0, result(0.9))
        rig.feed(2, 1.1, result(0.9))  # not better: no further update
        msgs = [m for m in drain(rig.q) if m["type"] in ("truck.new", "truck.updated")]
        assert msgs[0]["type"] == "truck.new"
        assert msgs[0]["truck"]["snapshot_url"].endswith("?v=5000")
        updates = [m for m in msgs if m["type"] == "truck.updated"]
        assert len(updates) == 1
        assert updates[0]["truck"]["snapshot_url"].endswith("?v=9000")
    finally:
        rig.close()


def test_truck_new_published_before_clip_start(tmp_path):
    rig = Rig(tmp_path)
    try:
        seen: list[list[str]] = []
        original = rig.evidence.start_clip

        def spy(*args, **kwargs):
            seen.append([m["type"] for m in drain(rig.q)])
            return original(*args, **kwargs)

        rig.evidence.start_clip = spy
        rig.feed(0, 0.0, result(0.8))
        assert seen == [["truck.new"]]
    finally:
        rig.close()


def test_failed_snapshot_update_keeps_old_snapshot(tmp_path, monkeypatch, caplog):
    # e.g. Windows still refuses the rename because a viewer holds the old file
    import engine.events.manager as manager_mod

    real = manager_mod.save_snapshot
    calls = [0]

    def flaky(*args, **kwargs):
        calls[0] += 1
        if calls[0] > 1:
            raise PermissionError("file in use")
        real(*args, **kwargs)

    monkeypatch.setattr(manager_mod, "save_snapshot", flaky)
    rig = Rig(tmp_path)
    try:
        rig.feed(0, 0.0, result(0.5))
        drain(rig.q)
        with caplog.at_level("WARNING", logger="engine.events.manager"):
            rig.feed(1, 1.0, result(0.9))  # does not raise: the frame is not lost
        rec = rig.repo.list_trucks()[0]
        assert rec.best_score == 0.5  # still describes the saved files
        assert (tmp_path / "media" / rec.snapshot_path).exists()
        assert not [m for m in drain(rig.q) if m["type"] == "truck.updated"]
        assert any("snapshot" in r.getMessage() for r in caplog.records)
        assert rig.mgr.active_count == 1
    finally:
        rig.close()
