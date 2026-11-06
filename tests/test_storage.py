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


def test_clear_clip_paths(repo):
    run_id = repo.create_run("s", "{}")
    a = repo.insert_truck(make_rec(run_id, 1, clip_path="2026-11-05/a.mp4"))
    b = repo.insert_truck(make_rec(run_id, 2, clip_path="2026-11-05/b.mp4"))
    c = repo.insert_truck(make_rec(run_id, 3, clip_path="2026-11-05/c.mp4"))
    assert sorted(repo.clear_clip_paths(["2026-11-05/a.mp4", "2026-11-05/c.mp4", "x.mp4"])) == [a, c]
    assert repo.get_truck(a).clip_path is None
    assert repo.get_truck(b).clip_path == "2026-11-05/b.mp4"
    assert repo.get_truck(c).clip_path is None
    assert repo.clear_clip_paths([]) == []


def test_retention_never_deletes_kept_file(tmp_path):
    root = tmp_path
    for name in ("old.mp4", "new.mp4"):
        (root / name).write_bytes(b"x" * 600)
    now = time.time()
    os.utime(root / "old.mp4", (now - 3600, now - 3600))
    # over the limit, but the only other video is the one we must keep
    assert enforce_retention(root, 100, keep={root / "old.mp4"}) == [root / "new.mp4"]
    assert (root / "old.mp4").exists()


def test_retention_skips_undeletable_file(tmp_path, monkeypatch):
    for name in ("locked.mp4", "other.mp4"):
        (tmp_path / name).write_bytes(b"x" * 600)
    now = time.time()
    os.utime(tmp_path / "locked.mp4", (now - 3600, now - 3600))
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self.name == "locked.mp4":
            raise PermissionError("in use")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert enforce_retention(tmp_path, 700) == [tmp_path / "other.mp4"]
    assert (tmp_path / "locked.mp4").exists()


def test_clip_status_default_and_cleared(repo):
    run_id = repo.create_run("s", "{}")
    a = repo.insert_truck(make_rec(run_id, 1, clip_path="2026-11-05/a.mp4"))
    assert repo.get_truck(a).clip_status == "pending"
    repo.update_truck(a, clip_status="saved")
    assert repo.get_truck(a).clip_status == "saved"
    repo.clear_clip_paths(["2026-11-05/a.mp4"])
    got = repo.get_truck(a)
    assert got.clip_path is None and got.clip_status == "removed"


def _old_db(path):
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
            source TEXT NOT NULL, config_json TEXT NOT NULL);
        CREATE TABLE trucks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id),
            track_id INTEGER NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            best_score REAL NOT NULL, snapshot_path TEXT, crop_path TEXT, clip_path TEXT,
            lat REAL, lon REAL, geo_error_m REAL, geo_status TEXT NOT NULL DEFAULT 'unknown');
        INSERT INTO runs (started_at, source, config_json)
            VALUES ('2026-11-05T10:00:00+00:00', 's', '{}');
        INSERT INTO trucks (run_id, track_id, first_seen, last_seen, best_score, clip_path)
            VALUES (1, 1, '2026-11-05T10:00:00+00:00', '2026-11-05T10:00:05+00:00', 0.9,
                    '2026-11-05/a.mp4');
        INSERT INTO trucks (run_id, track_id, first_seen, last_seen, best_score)
            VALUES (1, 2, '2026-11-05T10:01:00+00:00', '2026-11-05T10:01:05+00:00', 0.8);
        """
    )
    conn.commit()
    conn.close()


def test_clip_status_migration(tmp_path):
    db = tmp_path / "old.db"
    _old_db(db)
    for _ in range(2):  # reopening must be a no-op (idempotent)
        r = TruckRepository(db)
        try:
            by_track = {t.track_id: t for t in r.list_trucks()}
            assert by_track[1].clip_status == "saved"
            assert by_track[2].clip_status == "failed"
            new = r.insert_truck(make_rec(1, 3))
            assert r.get_truck(new).clip_status == "pending"
        finally:
            r.close()


def test_migration_leaves_new_pending_row_alone_and_rolls_back(tmp_path):
    db = tmp_path / "old.db"
    _old_db(db)
    r = TruckRepository(db)
    new = r.insert_truck(make_rec(1, 9))
    r.close()
    r = TruckRepository(db)  # already migrated: must not touch the pending row
    try:
        assert r.get_truck(new).clip_status == "pending"
    finally:
        r.close()


def test_migration_is_one_transaction(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "old.db"
    _old_db(db)
    real = sqlite3.Connection

    class Boom(real):
        def execute(self, sql, *a):
            if sql.startswith("UPDATE trucks SET clip_status"):
                raise sqlite3.OperationalError("crash")
            return super().execute(sql, *a)

    real_connect = sqlite3.connect
    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Boom, **k)
    )
    with pytest.raises(sqlite3.OperationalError):
        TruckRepository(db)
    monkeypatch.undo()
    conn = sqlite3.connect(str(db))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trucks)")}
    conn.close()
    assert "clip_status" not in cols  # ALTER rolled back with the failed backfill

