from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)


PARTIAL_CLIP_GLOB = "*.mp4.part"


def remove_partial_clips(media_root: Path) -> int:
    """Delete clip temp files left by a crash or a forced exit; return how many."""
    removed = 0
    for part in Path(media_root).rglob(PARTIAL_CLIP_GLOB):
        try:
            part.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("could not delete partial clip %s: %s", part, exc)
    if removed:
        logger.info("removed %d partial clip file(s) left by an earlier run", removed)
    return removed


def enforce_retention(
    media_root: Path, max_bytes: int, keep: Iterable[Path] = frozenset()
) -> list[Path]:
    """Delete oldest .mp4 files until total media size fits max_bytes.

    Images are never deleted, nor are the files in ``keep`` (e.g. the clip that
    was just written). Returns the deleted files.
    """
    kept = {Path(p).resolve() for p in keep}
    files = [p for p in Path(media_root).rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    deleted: list[Path] = []
    if total <= max_bytes:
        return deleted
    videos = sorted(
        (p for p in files if p.suffix.lower() == ".mp4" and p.resolve() not in kept),
        key=lambda p: p.stat().st_mtime,
    )
    for video in videos:
        if total <= max_bytes:
            break
        try:
            size = video.stat().st_size
            video.unlink()
        except OSError as exc:  # e.g. open in a viewer on Windows: try the next one
            logger.warning("retention: could not delete %s: %s", video, exc)
            continue
        total -= size
        deleted.append(video)
        logger.info("retention: deleted %s (%d bytes) to stay under the disk limit", video, size)
    if total > max_bytes:
        logger.warning(
            "media still over limit (%d > %d bytes) with no deletable .mp4 files left",
            total,
            max_bytes,
        )
    return deleted
