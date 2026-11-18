from __future__ import annotations

from collections.abc import Sequence

from engine.config import DetectorConfig
from engine.core import Detection


def filter_detections(
    raw: list[Detection], classes: Sequence[str], min_score: float
) -> list[Detection]:
    """Keep detections of the given classes scoring at least min_score, in input order.

    Shared by the live pipeline and the offline replay tool so both filter identically.
    """
    wanted = set(classes)
    return [d for d in raw if d.class_name in wanted and d.score >= min_score]


def prepare_detections(
    raw: list[Detection], cfg: DetectorConfig, confidence: float
) -> list[Detection]:
    """The one detection step shared by the live pipeline and offline replay.

    Later tracking fixes extend this function, so both paths stay identical.
    """
    return filter_detections(raw, cfg.classes, confidence)
