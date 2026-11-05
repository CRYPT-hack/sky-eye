from __future__ import annotations

from datetime import datetime
from pathlib import Path

from engine.storage.repository import TruckRecord, TruckRepository
from engine.storage.retention import enforce_retention, remove_partial_clips


def media_dir(data_dir: Path) -> Path:
    path = Path(data_dir) / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def day_dir(data_dir: Path, when: datetime) -> Path:
    path = media_dir(data_dir) / when.strftime("%Y-%m-%d")
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = [
    "TruckRecord",
    "TruckRepository",
    "media_dir",
    "day_dir",
    "enforce_retention",
    "remove_partial_clips",
]
