"""Draws confirmed tracks onto a copy of a frame for the live stream."""
from __future__ import annotations

import numpy as np
import supervision as sv

from engine.core import Frame, Track

_BOXES = sv.BoxAnnotator(color_lookup=sv.ColorLookup.TRACK)
_LABELS = sv.LabelAnnotator(color_lookup=sv.ColorLookup.TRACK)


def annotate(frame: Frame, tracks: list[Track]) -> np.ndarray:
    """Return a copy of frame.image with a box and "truck #<id> 0.87" per track.

    The frame itself is never modified: the evidence buffer shares its image.
    """
    image = frame.image.copy()
    if not tracks:
        return image
    detections = sv.Detections(
        xyxy=np.array([t.detection.box for t in tracks], dtype=np.float32),
        confidence=np.array([t.detection.score for t in tracks], dtype=np.float32),
        tracker_id=np.array([t.track_id for t in tracks], dtype=int),
    )
    labels = [f"truck #{t.track_id} {t.detection.score:.2f}" for t in tracks]
    image = _BOXES.annotate(image, detections)
    return _LABELS.annotate(image, detections, labels=labels)
