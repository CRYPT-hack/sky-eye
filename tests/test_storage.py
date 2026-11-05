import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.storage import (
    TruckRecord,
    TruckRepository,
    day_dir,
    enforce_retention,
    media_dir,
)

T0 = datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc)


def make_rec(run_id: int, track_id: int = 1, first_seen: datetime = T0, **kw) -> TruckRecord:
    return TruckRecord(
        id=None,
        run_id=run_id,
        track_id=track_id,
        first_seen=first_seen,
        last_seen=first_seen + timedelta(seconds=5),
        best_score=0.87,
        **kw,
    )


@pytest.fixture
def repo(tmp_path):
    r = TruckRepository(tmp_path / "sky-eye.db")
    yield r
    r.close()


def test_insert_and_get_roundtrip(repo):
    run_id = repo.create_run("file:a.mp4", "{}")
    rec = make_rec(
        run_id,
        track_id=7,
        snapshot_path="2026-11-05/s.jpg",
        crop_path="2026-11-05/c.jpg",
        clip_path="2026-11-05/c.mp4",
        lat=12.5,
        lon=77.25,
        geo_error_m=3.5,
        geo_status="ok",
    )
    tid = repo.insert_truck(rec)
    got = repo.get_truck(tid)
    rec.id = tid
    assert got == rec
    assert got.first_seen.tzinfo is not None
    assert repo.get_truck(9999) is None


def test_list_newest_first_with_time_filter(repo):
    run_id = repo.create_run("s", "{}")
    ids = [
        repo.insert_truck(make_rec(run_id, i, T0 + timedelta(minutes=i))) for i in range(3)
    ]
    assert [t.id for t in repo.list_trucks()] == ids[::-1]
    got = repo.list_trucks(start=T0 + timedelta(minutes=1))
    assert [t.id for t in got] == [ids[2], ids[1]]
    got = repo.list_trucks(end=T0 + timedelta(minutes=2))
    assert [t.id for t in got] == [ids[1], ids[0]]
    assert [t.id for t in repo.list_trucks(limit=1, offset=1)] == [ids[1]]


def test_update_truck_fields(repo):
    run_id = repo.create_run("s", "{}")
    tid = repo.insert_truck(make_rec(run_id))
    new_seen = T0 + timedelta(minutes=1)
    repo.update_truck(tid, last_seen=new_seen, clip_path="2026-11-05/a.mp4")
    got = repo.get_truck(tid)
    assert got.last_seen == new_seen
    assert got.clip_path == "2026-11-05/a.mp4"


def test_update_unknown_field_rejected(repo):
    run_id = repo.create_run("s", "{}")
    tid = repo.insert_truck(make_rec(run_id))
    with pytest.raises(ValueError):
        repo.update_truck(tid, colour="red")
    with pytest.raises(ValueError):
        repo.update_truck(tid, run_id=2)


def test_threadsafe_inserts(repo):
    run_id = repo.create_run("s", "{}")

    def work(k):
        for i in range(25):
            repo.insert_truck(make_rec(run_id, k * 100 + i))

    threads = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert repo.count_trucks(run_id) == 100


def test_repo_in_dir_with_spaces(tmp_path):
    db = tmp_path / "auto detection" / "data" / "sky-eye.db"
    r = TruckRepository(db)
    try:
        run_id = r.create_run("s", "{}")
        assert r.get_truck(r.insert_truck(make_rec(run_id))) is not None
    finally:
        r.close()
    assert db.exists()


def test_media_dirs(tmp_path):
    assert media_dir(tmp_path) == tmp_path / "media"
    assert media_dir(tmp_path).is_dir()
    d = day_dir(tmp_path, T0)
    assert d == tmp_path / "media" / "2026-11-05"
    assert d.is_dir()


def test_retention_deletes_oldest_mp4_first(tmp_path):
    root = tmp_path
    for name in ("old.mp4", "new.mp4", "snap.jpg"):
        (root / name).write_bytes(b"x" * 600)
    now = time.time()
    os.utime(root / "snap.jpg", (now - 4 * 3600, now - 4 * 3600))
    os.utime(root / "old.mp4", (now - 3 * 3600, now - 3 * 3600))
    assert enforce_retention(root, 1300) == [root / "old.mp4"]
    assert (root / "snap.jpg").exists()
    assert (root / "new.mp4").exists()


def test_retention_warns_when_only_images_remain(tmp_path, caplog):
    (tmp_path / "a.jpg").write_bytes(b"x" * 600)
    with caplog.at_level("WARNING"):
        assert enforce_retention(tmp_path, 100) == []
    assert (tmp_path / "a.jpg").exists()
    assert caplog.records
