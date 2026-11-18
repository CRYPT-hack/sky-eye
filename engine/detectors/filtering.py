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


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def merge_class_duplicates(
    dets: Sequence[Detection], iou_min: float = 0.7
) -> list[tuple[Detection, dict[str, float]]]:
    """Merge several class guesses for one object into a single cluster.

    Greedy by descending score (input order breaks ties). A detection joins the first
    cluster whose top box it overlaps with IoU >= iou_min. Each cluster keeps its top
    Detection unchanged plus the max score per class name in the cluster. Output is
    ordered by descending top score.
    """
    order = sorted(range(len(dets)), key=lambda i: (-dets[i].score, i))
    clusters: list[tuple[Detection, dict[str, float]]] = []
    for i in order:
        d = dets[i]
        for top, per_class in clusters:
            if _iou(top.box, d.box) >= iou_min:
                per_class[d.class_name] = max(per_class.get(d.class_name, 0.0), d.score)
                break
        else:
            clusters.append((d, {d.class_name: d.score}))
    return clusters
