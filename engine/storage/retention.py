from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def enforce_retention(media_root: Path, max_bytes: int) -> list[Path]:
    """Delete oldest .mp4 files until total media size fits max_bytes.

    Images are never deleted. Returns the deleted files.
    """
    files = [p for p in Path(media_root).rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    deleted: list[Path] = []
    if total <= max_bytes:
        return deleted
    videos = sorted((p for p in files if p.suffix.lower() == ".mp4"), key=lambda p: p.stat().st_mtime)
    for video in videos:
        if total <= max_bytes:
            break
        size = video.stat().st_size
        video.unlink()
        total -= size
        deleted.append(video)
    if total > max_bytes:
        logger.warning(
            "media still over limit (%d > %d bytes) with no .mp4 files left to delete",
            total,
            max_bytes,
        )
    return deleted
