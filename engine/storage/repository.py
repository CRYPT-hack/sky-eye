from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class TruckRecord:
    id: int | None
    run_id: int
    track_id: int
    first_seen: datetime
    last_seen: datetime
    best_score: float
    snapshot_path: str | None = None
    crop_path: str | None = None
    clip_path: str | None = None
    lat: float | None = None
    lon: float | None = None
    geo_error_m: float | None = None
    geo_status: str = "unknown"
    # "pending" | "saved" | "skipped" | "failed" | "removed"
    clip_status: str = "pending"


_COLUMNS = [f.name for f in fields(TruckRecord)]
_DATETIME_FIELDS = {"first_seen", "last_seen"}
_UPDATABLE = set(_COLUMNS) - {"id", "run_id"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    source TEXT NOT NULL,
    config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trucks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    track_id INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    best_score REAL NOT NULL,
    snapshot_path TEXT,
    crop_path TEXT,
    clip_path TEXT,
    lat REAL,
    lon REAL,
    geo_error_m REAL,
    geo_status TEXT NOT NULL DEFAULT 'unknown',
    clip_status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_trucks_first_seen ON trucks(first_seen);
CREATE INDEX IF NOT EXISTS idx_trucks_run ON trucks(run_id);
"""


def _to_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _from_text(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_to_record(row: sqlite3.Row) -> TruckRecord:
    data = dict(row)
    for name in _DATETIME_FIELDS:
        data[name] = _from_text(data[name])
    return TruckRecord(**data)


class TruckRepository:
    """Thread-safe SQLite store for runs and trucks (one shared connection)."""

    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        # called with self._lock held; idempotent
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(trucks)")}
        if "clip_status" not in cols:
            self._conn.execute(
                "ALTER TABLE trucks ADD COLUMN clip_status TEXT NOT NULL DEFAULT 'pending'"
            )
            # rows from before the column existed are all settled: a clip or no clip
            self._conn.execute(
                "UPDATE trucks SET clip_status = "
                "CASE WHEN clip_path IS NOT NULL THEN 'saved' ELSE 'failed' END"
            )

    def create_run(self, source: str, config_json: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs (started_at, source, config_json) VALUES (?, ?, ?)",
                (_to_text(datetime.now(timezone.utc)), source, config_json),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def insert_truck(self, rec: TruckRecord) -> int:
        cols = [c for c in _COLUMNS if c != "id"]
        values = [
            _to_text(getattr(rec, c)) if c in _DATETIME_FIELDS else getattr(rec, c)
            for c in cols
        ]
        sql = f"INSERT INTO trucks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        with self._lock:
            cur = self._conn.execute(sql, values)
            self._conn.commit()
            return int(cur.lastrowid)

    def update_truck(self, truck_id: int, **fields_: object) -> None:
        unknown = set(fields_) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update field(s): {sorted(unknown)}")
        if not fields_:
            return
        names = list(fields_)
        values = [
            _to_text(v) if n in _DATETIME_FIELDS else v for n, v in fields_.items()
        ]
        sql = f"UPDATE trucks SET {', '.join(f'{n} = ?' for n in names)} WHERE id = ?"
        with self._lock:
            self._conn.execute(sql, [*values, truck_id])
            self._conn.commit()

    def clear_clip_paths(self, rel_paths: list[str]) -> list[int]:
        """Set clip_path to NULL where it is one of rel_paths; return those truck ids."""
        if not rel_paths:
            return []
        marks = ", ".join("?" * len(rel_paths))
        sql = f"UPDATE trucks SET clip_path = NULL, clip_status = 'removed' WHERE clip_path IN ({marks}) RETURNING id"
        with self._lock:
            rows = self._conn.execute(sql, list(rel_paths)).fetchall()
            self._conn.commit()
        return [int(r[0]) for r in rows]

    def get_truck(self, truck_id: int) -> TruckRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM trucks WHERE id = ?", (truck_id,)).fetchone()
        return _row_to_record(row) if row else None

    def list_trucks(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TruckRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if start is not None:
            clauses.append("first_seen >= ?")
            params.append(_to_text(start))
        if end is not None:
            clauses.append("first_seen < ?")
            params.append(_to_text(end))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM trucks {where} ORDER BY first_seen DESC, id DESC LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._conn.execute(sql, [*params, limit, offset]).fetchall()
        return [_row_to_record(r) for r in rows]

    def count_trucks(self, run_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM trucks WHERE run_id = ?", (run_id,)
            ).fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
